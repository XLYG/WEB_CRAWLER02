import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "BHT_Berlin",
    "START_URL": "https://www.bht-berlin.de/studiengaenge",
    "BHT_DOMAIN": "https://www.bht-berlin.de",
    "OUTPUT_DIR": "BHT_Berlin_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "WAIT_TIME": 2,
    "TIMEOUT": 30,
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Unknown_Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 50
        return name[:limit]

    @staticmethod
    def is_master(degree_text, title_text):
        keys = ['master', 'm.sc', 'm.eng', 'mba', 'm.a', '硕士', '理学硕士', '工程硕士', '工商管理硕士', '掌握',
                '马萨诸塞州']
        full_text = (str(degree_text) + " " + str(title_text)).lower()
        return any(k in full_text for k in keys)

    @staticmethod
    def surgical_clean_node(element):
        if element is None: return ""
        noise = ['script', 'style', 'font', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'header',
                 'footer', 'noscript', 'aside']
        for tag in noise:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        for a in element.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["BHT_DOMAIN"], href))

        return etree.tostring(element, encoding='unicode', method='html')


class BHTHybridScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        # Playwright 解决动态表格加载
        major_tasks = self.fetch_list_via_playwright()
        if not major_tasks:
            print("未能获取专业列表，请检查网络")
            return

        print(f" 成功锁定 {len(major_tasks)} 个硕士项目")

        # Req解析详情
        for idx, task in enumerate(major_tasks):
            print(f"\n[{idx + 1}/{len(major_tasks)}] 正在解析：{task['name']}")
            self.fetch_detail_content(task)

        print(f"\n 任务全部完成-结果保存在：{self.output_dir}")

    def fetch_list_via_playwright(self):
        """Playwright 负责：列表筛选与 URL 收集"""
        tasks = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page(user_agent=CONFIG["USER_AGENT"])
            page.goto(CONFIG["START_URL"], wait_until="networkidle")
            # 移除 Cookie 弹窗
            page.evaluate(
                "() => { const b=document.querySelector('#usercentrics-root') || document.querySelector('.cookie-notice'); if(b) b.remove(); }")
            time.sleep(CONFIG["WAIT_TIME"])

            try:
                page.wait_for_selector('table#sortedTable tbody tr', timeout=15000)
                rows = page.locator('table#sortedTable tbody tr').all()
                for row in rows:
                    if not row.is_visible(): continue
                    tds = row.locator('td').all()
                    if len(tds) < 4: continue

                    title_link = tds[0].locator('a').first
                    name = title_link.inner_text().strip()
                    url = urljoin(CONFIG["BHT_DOMAIN"], title_link.get_attribute('href'))
                    degree = tds[1].inner_text().strip()
                    start = tds[2].inner_text().strip()
                    comment = tds[3].inner_text().strip()

                    if CrawlerUtils.is_master(degree, name):
                        tasks.append({"name": name, "url": url, "degree": degree, "start": start, "comment": comment})
            except Exception as e:
                print(f" 列表提取出错: {e}")
            browser.close()
        return tasks

    def fetch_detail_content(self, task):
        """执行线性扫描-解决重复写入问题"""
        try:
            res = self.session.get(task["url"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            # 准备目录
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_path = os.path.join(self.output_dir, safe_name)
            if not os.path.exists(major_path): os.makedirs(major_path)

            # 这能确保嵌套结构只被抓取一次
            all_top_blocks = tree.xpath("""
                //div[contains(@class, "mod_article")]/descendant-or-self::h1[1] | 
                //div[contains(@class, "frame") and not(ancestor::div[contains(@class, "frame")])]
            """)

            extracted_html_list = []
            recording = False

            for block in all_top_blocks:
                # 检查块内是否潜伏着 H1
                if not recording:
                    if block.tag == 'h1' or block.xpath('.//h1'):
                        recording = True

                # 检查块内是否潜伏着 Studienplan
                # 只要块内部任何标题包含关键字，就终止
                inner_headers = block.xpath('.//h1 | .//h2 | .//h3 | .//h4 | .//h5')
                found_sentinel = False
                for h in inner_headers:
                    h_text = "".join(h.xpath('.//text()')).lower()
                    if "studienplan" in h_text or "学习计划" in h_text:
                        found_sentinel = True
                        break

                if found_sentinel:
                    print("    切片终点-停止提取")
                    break

                if recording:
                    # 清洗并收集 HTML 片段
                    cleaned = CrawlerUtils.surgical_clean_node(block)
                    if cleaned:
                        extracted_html_list.append(cleaned)

            # 强制写入专业 URL
            header_md = f"URL: {task['url']}\n\n"

            # 格式化元数据行
            comment_text = f" | {task['comment'].strip()}" if task['comment'].strip() else ""
            meta_line = f"**{task['name']} | {task['degree']} | {task['start']}{comment_text}**"
            header_md += meta_line + "\n\n---\n\n"

            # 转换正文
            body_md_raw = md("".join(extracted_html_list), heading_style="ATX")

            # 左对齐 + 压缩多余空行
            fixed_lines = [line.strip() for line in body_md_raw.split('\n')]
            body_md = re.sub(r'\n{3,}', '\n\n', '\n'.join(fixed_lines)).strip()

            final_md_output = header_md + body_md

            # 存储文件
            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_path, safe_file_name), "w", encoding="utf-8") as f:
                f.write(final_md_output)
            print(f"    存储成功 {safe_file_name}")

        except Exception as e:
            print(f"    详情解析故障：{e}")


if __name__ == "__main__":
    BHTHybridScraper().run()