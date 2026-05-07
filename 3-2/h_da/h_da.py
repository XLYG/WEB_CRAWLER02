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
    "UNIVERSITY_NAME": "h_da_Darmstadt",
    "ROOT_DOMAIN": "https://h-da.de",
    "START_URL": "https://h-da.de/en/studies/study-programmes?tx_hdaacademicprogram_program%5Baction%5D=all&tx_hdaacademicprogram_program%5Bcontroller%5D=Program&cHash=5c3767b81300a191908e1041306790a8",
    "OUTPUT_DIR": "h_da_Darmstadt_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "WAIT_TIME": 2,
    "TIMEOUT": 30,
}

# 语义匹配关键字
SEMANTIC_HEADERS = {
    "Content": ["Content", "Inhalte", "内容"],
    "Perspectives": ["Perspectives", "Perspektiven", "视角"],
    "Dual study programme": ["Dual study programme", "Duales Studienprogramm", "双元制学习项目"],
    "Access": ["Access", "Zugang", "Voraussetzungen", "入学要求"],
    "Deadline": ["Registration deadline", "Einschreibefrist", "注册截止日期"]
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清洗路径：处理德语符号，转ASCII，强制截断解决 Windows 路径报错。"""
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_node(element):
        if element is None: return ""
        for a in element.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))
        noise = ['script', 'style', 'font', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'noscript']
        for tag in noise:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)
        return etree.tostring(element, encoding='unicode', method='html')

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        markdown_text = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class HdaScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        print(f" 正在开始采集 {CONFIG['UNIVERSITY_NAME']} ")

        # Playwright 获取专业列表
        tasks = self.get_major_list_via_playwright()
        if not tasks:
            print("未能成功获取专业列表")
            return

        print(f"成功，共锁定 {len(tasks)} 个硕士专业")

        # request遍历详情页
        for idx, task in enumerate(tasks):
            print(f"\n[{idx + 1}/{len(tasks)}] 正在：{task['name']}")
            self.process_major_detail(task)
            time.sleep(1)

        print(f"\n[*] 全部采集任务已结束")

    def get_major_list_via_playwright(self):
        """利用 Playwright 处理 Master 筛选和结果提取"""
        collected_tasks = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)  # 调试建议 False
            page = browser.new_page(user_agent=CONFIG["USER_AGENT"])
            page.goto(CONFIG["START_URL"], wait_until="networkidle")

            # 清理 Cookie
            page.evaluate(
                "() => { const b=document.querySelector('#usercentrics-root') || document.querySelector('.cookie-notice'); if(b) b.remove(); }")

            print(" 正在选择 'Master'")
            try:
                # 锁定 Academic degrees 下拉框并选择 Master (直接value=8)
                page.select_option('select[name="tx_hdaacademicprogram_program[degree]"]', value="8")
                time.sleep(1)
                # 点击 search 按钮
                page.click('input.btn-secondary[value="search"]')
                # 等待表格 tbody 刷新
                page.wait_for_selector('tbody[aria-live="polite"]', timeout=20000)
                time.sleep(CONFIG["WAIT_TIME"])

                # 扫描表格行获取作为元数据进行文件填入
                rows = page.locator('tbody[aria-live="polite"] tr').all()
                for row in rows:
                    tds = row.locator('td').all()
                    if len(tds) < 7: continue

                    anchor = tds[0].locator('a').first
                    name = anchor.inner_text().strip()
                    href = anchor.get_attribute("href")

                    # 提取列表元数据
                    meta = {
                        "degree": tds[1].inner_text().strip(),
                        "semesters": tds[2].inner_text().strip(),
                        "start": tds[3].inner_text().strip().replace('\n', ' '),
                        "nc": tds[4].inner_text().strip(),
                        "dual": tds[5].inner_text().strip(),
                        "lang": tds[6].inner_text().strip()
                    }

                    if href:
                        collected_tasks.append({
                            "name": name,
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "meta": meta
                        })
            except Exception as e:
                print(f"[筛选过程出错：{e}")

            browser.close()
        return collected_tasks

    def process_major_detail(self, task):
        try:
            res = self.session.get(task["url"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            # 文件夹
            url_slug = task["url"].strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_dir = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            # 第一行：URL
            # 第二行：元数据
            final_md = f"URL: {task['url']}\n\n"
            m = task['meta']
            final_md += f"**{task['name']} | {m['degree']} | Semesters: {m['semesters']} | Start: {m['start']} | NC: {m['nc']} | Dual: {m['dual']} | Lang: {m['lang']}**\n\n---\n\n"

            # 提取 <h1>
            h1_text = tree.xpath('string(//h1)').strip()
            final_md += f"# {h1_text}\n\n"

            # 提取关键事实表格
            fact_table = tree.xpath(
                '//div[contains(@class, "table-wrapper")]/table[contains(@class, "academicsSingle")]')
            if fact_table:
                final_md += "## Key Facts\n\n" + CrawlerUtils.to_markdown(
                    CrawlerUtils.clean_node(fact_table[0])) + "\n\n"

            # 切片提取 (h2, h4)
            frames = tree.xpath(
                '//div[contains(@class, "frame-text")] | //div[contains(@class, "frame-textmedia")] | //div[contains(@class, "frame-shortcut")]')

            print(f"   正在获取页面区块，进行语义匹配")
            for frame in frames:
                # 检查该块内是否有标题（h2 或 h4）
                headers = frame.xpath('.//h2 | .//h4')
                for h in headers:
                    h_text = "".join(h.xpath('.//text()')).strip()

                    # 进行多语种模糊匹配
                    matched = False
                    for category, keywords in SEMANTIC_HEADERS.items():
                        if any(k.lower() in h_text.lower() for k in keywords):
                            matched = True
                            print(f"       匹配到：{h_text}")
                            break

                    if matched:
                        # 如果该块内有 PDF，执行下载
                        self.extract_pdfs_from_node(frame, major_dir)
                        final_md += f"## {h_text}\n\n" + CrawlerUtils.to_markdown(
                            CrawlerUtils.clean_node(frame)) + "\n\n"
                        break

            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, safe_file_name), "w", encoding="utf-8") as f:
                f.write(final_md)
            print(f"    -存储完毕")

        except Exception as e:
            print(f"     详情页处理失败：{e}")

    def extract_pdfs_from_node(self, node, save_dir):
        """扫描节点内的所有 PDF 并下载"""
        pdf_links = node.xpath('.//a[contains(@href, ".pdf")]')
        for link in pdf_links:
            pdf_url = urljoin(CONFIG["ROOT_DOMAIN"], link.get('href'))
            pdf_label = "".join(link.xpath('.//text()')).strip()
            pdf_name = f"Doc_{CrawlerUtils.sanitize_path(pdf_label if pdf_label else 'info', False)}.pdf"

            save_path = os.path.join(save_dir, pdf_name)
            if not os.path.exists(save_path):
                try:
                    r = self.session.get(pdf_url, stream=True, timeout=20)
                    if r.status_code == 200:
                        with open(save_path, 'wb') as f:
                            for chunk in r.iter_content(8192): f.write(chunk)
                        print(f"       PDF 已下载：{pdf_name}")
                except:
                    pass


if __name__ == "__main__":
    HdaScraper().run()