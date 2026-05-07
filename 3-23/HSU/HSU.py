import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "THU_Ulm",
    "ROOT_DOMAIN": "https://www.thu.de",
    "START_URL": "https://www.thu.de/de/Seiten/UebersichtMaster.aspx",
    "OUTPUT_DIR": "THU_Ulm_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # 核心内容容器
    "CONTENT_XPATH": '//*[@id="DeltaPlaceHolderMain"]/div[1]',
    # PDF 匹配正则
    "PDF_PATTERN": r"Zulassungssatzung|Admission.*Regulations",
    # 穿透磁贴匹配关键词
    "JUMP_KEYWORDS": ["Informationen zum Studiengang", "Zur Bewerbung", "Application"]
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
    def clean_html_node(element):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe',
                 'noscript']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        raw_html = etree.tostring(el, encoding='unicode', method='html')
        raw_html = raw_html.replace('\xad', '').replace('&shy;', '')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        content = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class THU_Scraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_list()
        if not tasks:
            print(" 无法锁定专业列表，请检查网络。")
            return

        print(f" 捕捉成功：已锁定 {len(tasks)} 个硕士专业。开始详情采集流程")

        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["START_URL"], timeout=30)
            tree = etree.HTML(res.text)
            # 定位硕士
            items = tree.xpath('//div[contains(@class, "hsu-Master")]')
            for item in items:
                link_node = item.xpath('.//a[h4]')
                if not link_node : link_node = item.xpath('.//div[@href]')

                if link_node:
                    href = link_node[0].get('href')
                    name = "".join(item.xpath('.//h4/text()')).strip()
                    desc = "".join(item.xpath('.//span[contains(@class, "caption")]/text()')).strip()

                    if name and href:
                        collected.append({
                            "name": name,
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "desc": desc
                        })
            return collected
        except Exception as e:
            print(f" 列表页解析异常: {e}")
        return []

    def process_major(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过 {safe_name}")
                return

            os.makedirs(major_dir, exist_ok=True)

            # 主页 -> 穿透页
            all_content_md = []
            visited_urls = set()
            urls_to_process = [task['url']]

            md_file_path = os.path.join(major_dir, f"{safe_name}.md")

            while urls_to_process:
                current_url = urls_to_process.pop(0)
                if current_url in visited_urls: continue
                visited_urls.add(current_url)

                print(f"    正在拉取内容: {current_url}")
                res = self.session.get(current_url, timeout=30)
                res.encoding = res.apparent_encoding
                tree = etree.HTML(res.text)

                # 提取核心容器内容
                content_box = tree.xpath(CONFIG["CONTENT_XPATH"])
                if content_box:
                    # PDF查询
                    self.audit_pdfs(content_box[0], current_url, major_dir)

                    # 转换 Markdown
                    html_snippet = CrawlerUtils.clean_html_node(content_box[0])
                    page_md = f"URL: {current_url}\n\n" + CrawlerUtils.to_markdown(html_snippet)
                    all_content_md.append(page_md)

                    # 探测穿透链接
                    # 寻找包含 href 的 div.hsu-kachelImageWrap，且内容匹配关键词
                    jump_tiles = tree.xpath('//div[contains(@class, "hsu-kachelImageWrap")][@href]')
                    for tile in jump_tiles:
                        tile_text = "".join(tile.xpath('.//text()')).strip()
                        if any(kw.lower() in tile_text.lower() for kw in CONFIG["JUMP_KEYWORDS"]):
                            jump_url = urljoin(current_url, tile.get('href'))
                            if jump_url not in visited_urls:
                                urls_to_process.append(jump_url)

            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n")
                f.write(f"**{task['name']} | 简介: {task['desc']}**\n\n")
                f.write("---\n\n")
                f.write("\n\n---\n\n".join(all_content_md))

            print(f"    成功，数据已归档至文件夹。")

        except Exception as e:
            print(f"    失败，{task['name']} 异常: {e}")

    def audit_pdfs(self, element, base_url, save_dir):
        """扫描并下载 第一个 准入条例 PDF"""
        links = element.xpath('.//a')
        for a in links:
            text = "".join(a.xpath('.//text()')).strip()
            href = a.get('href')

            if href and re.search(CONFIG["PDF_PATTERN"], text, re.IGNORECASE):
                pdf_url = urljoin(base_url, href)
                self.download_pdf(pdf_url, text, save_dir)
                return

    def download_pdf(self, url, label, folder):
        try:
            # 生成文件名
            url_id = url.split('/')[-1].split('.')[0][-6:]
            safe_label = CrawlerUtils.sanitize_path(label, False)
            filename = f"Regulation_{safe_label}_{url_id}.pdf"

            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                with open(os.path.join(folder, filename), 'wb') as f:
                    for chunk in res.iter_content(8192):
                        f.write(chunk)
                print(f"       PDF捕捉成功 {filename}")

        except:
            pass


if __name__ == "__main__":
    THU_Scraper().run()
