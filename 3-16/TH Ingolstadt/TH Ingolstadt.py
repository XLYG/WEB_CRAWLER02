import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "THI",
    "ROOT_DOMAIN": "https://www.thi.de",
    "START_URL": "https://www.thi.de/studium/studium-an-der-thi",
    "OUTPUT_DIR": "THI_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "SENTINEL_END_TEXT": "Blick in die Labore",
    # PDF 包含关键词扩展
    "PDF_KEYWORDS": ["Modulhandbuch", "Module handbook"]
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 50 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_element(element):
        if element is None: return ""
        noise_tags = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style',
                      'iframe', 'noscript']
        for tag in noise_tags:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        raw_html = etree.tostring(element, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_list, base_url):
        full_html = "".join(html_list)
        if not full_html: return ""

        tree = etree.HTML(full_html)
        if tree is not None:
            for a in tree.xpath('.//a'):
                href = a.get('href')
                if href and href.startswith('/'):
                    a.set('href', urljoin(base_url, href))
            full_html = etree.tostring(tree, encoding='unicode', method='html')

        markdown_text = md(full_html, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class THIScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_list_via_playwright()
        if not tasks:
            print("无法获取专业列表")
            return

        print(f"已捕捉到 {len(tasks)} 个专业-开始执行哨兵切片采集")

        # 详情采集 (Requests)
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(2.0, 4.0))

    def fetch_list_via_playwright(self):
        collected = []
        with sync_playwright() as p:
            print("启动浏览器处理 Master 筛选")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"])
            page = context.new_page()

            page.goto(CONFIG["START_URL"], wait_until="networkidle")

            #  处理 Cookie
            try:
                accept_btn = page.locator('button[data-in2-modal-accept-button]')
                if accept_btn.count() > 0:
                    accept_btn.click()
                    time.sleep(1.5)
            except:
                pass

            #  选择 Master 并等待重载
            print("    正在筛选 Master")
            try:
                page.select_option('select#faceFilterGraduation', value="1")
                # 等待加载动画消失并确认结果渲染
                time.sleep(3)
                page.wait_for_selector('a.c-framed-box', timeout=30000)
            except Exception as e:
                print(f"   筛选失败: {e}")
                browser.close()
                return []

            #  提取卡片
            cards = page.locator('a.c-framed-box').all()
            for card in cards:
                try:
                    href = card.get_attribute("href")
                    title = card.locator('.c-framed-box__title').inner_text().strip()
                    desc = card.locator('.c-framed-box__text').inner_text().strip()
                    options = card.locator('.c-framed-box__options').inner_text().replace('\n', ' | ').strip()

                    if href:
                        collected.append({
                            "name": title,
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "meta": f"{desc} | {options}"
                        })
                except:
                    continue

            print(f"    成功捕捉到 {len(collected)} 条专业信息")
            browser.close()
        return collected

    def process_detail(self, task):
        """哨兵切片：从第一个 H1 线性扫描至指定的 H2，保留表格结构"""
        try:
            safe_major_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_major_name)

            if os.path.exists(major_dir):
                print(f"    续爬 - 跳过已存在文件夹：{safe_major_name}")
                return

            res = self.session.get(task["url"], timeout=30)
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)

            #   执行哨兵切片
            h1_nodes = tree.xpath('//h1')
            if not h1_nodes:
                print(f"    缺失 H1 起点：{task['url']}")
                return

            start_node = h1_nodes[0]
            collected_html = []

            curr = start_node
            while curr is not None:
                # 熔断哨兵：h2 包含 "Blick in die Labore"
                if curr.tag == 'h2':
                    text_val = "".join(curr.xpath('.//text()')).strip()
                    if CONFIG["SENTINEL_END_TEXT"] in text_val:
                        print(f"   命中熔断点 [{CONFIG['SENTINEL_END_TEXT']}]")
                        break

                # 物理净化并收集
                collected_html.append(CrawlerUtils.clean_element(curr))
                curr = curr.getnext()

            #   PDF 审计与保存
            os.makedirs(major_dir, exist_ok=True)
            all_links = tree.xpath('//a')
            for link in all_links:
                link_text = "".join(link.xpath('.//text()')).strip()
                href = link.get('href')
                if not href: continue

                # 匹配 Modulhandbuch 或 Module handbook
                if any(kw.lower() in link_text.lower() for kw in CONFIG["PDF_KEYWORDS"]):
                    pdf_url = urljoin(task['url'], href)
                    # 命名 - 标题 + URL 摘要
                    url_hash = pdf_url.split('/')[-1].split('.')[0][-6:]
                    self.download_pdf(pdf_url, f"{link_text}_{url_hash}", major_dir)

            #   写入 Markdown
            clean_md = CrawlerUtils.to_markdown(collected_html, task['url'])
            md_file_path = os.path.join(major_dir, f"{safe_major_name}.md")
            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n")
                f.write(f"**{task['name']} | {task['meta']}**\n\n")
                f.write("---\n\n")
                f.write(clean_md)

            print(f"    成功 数据归档 ")

        except Exception as e:
            print(f"     失败  {task['name']} 解析异常: {e}")

    def download_pdf(self, url, label, save_dir):
        try:
            with self.session.get(url, stream=True, timeout=20) as r:
                r.raise_for_status()
                clean_label = re.sub(r'[\\/*?:"<>|]', "_", label).strip()[:80]
                filename = clean_label if clean_label.lower().endswith('.pdf') else f"{clean_label}.pdf"
                save_path = os.path.join(save_dir, filename)
                if os.path.exists(save_path): return
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                print(f"       PDF捕捉 {filename}")
        except:
            pass


if __name__ == "__main__":
    THIScraper().run()