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
    "UNIVERSITY_NAME": "THGA",
    "ROOT_DOMAIN": "https://www.thga.de",
    "LIST_URL": "https://www.thga.de/studienangebot/master/ueberblick",
    "OUTPUT_DIR": "THGA_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # PDF 匹配正则
    "PDF_PATTERNS": [r"Zulassungsordnung", r"FPO_Master_"]
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
    def clean_html_node(element, base_url):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)
        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and not href.startswith(('http', 'mailto', '#')):
                a.set('href', urljoin(base_url, href))
        raw_html = etree.tostring(el, encoding='unicode', method='html')
        raw_html = raw_html.replace('\xad', '').replace('&shy;', '')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        content = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class THGA_Scraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_list()
        if not tasks:
            print(" 无法获取专业列表，请检查网络或 URL ")
            return
        print(f"获取成功：共锁定 {len(tasks)} 个硕士专业 ")

        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=30)
            tree = etree.HTML(res.text)
            # 用全网页 teasertile 探测，并结合 URL 过滤
            tiles = tree.xpath('//div[contains(@class, "teasertile")]')
            seen_urls = set()
            for tile in tiles:
                # 提取“了解更多”链接
                links = tile.xpath('.//a[contains(., "erfahren") or contains(., "mehr")]/@href')
                if not links: links = tile.xpath('.//a[h3]/@href')

                if links:
                    full_url = urljoin(CONFIG["ROOT_DOMAIN"], links[0])
                    # 过滤逻辑：必须包含 master 路径且未抓取过
                    if "/master/" in full_url.lower() and full_url not in seen_urls:
                        name_node = tile.xpath('.//h3/text()')
                        if name_node:
                            collected.append({"name": name_node[0].strip(), "url": full_url})
                            seen_urls.add(full_url)
            return collected
        except:
            return []

    def process_major(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过 {safe_name}");
                return

            res = self.session.get(task["url"], timeout=30)
            tree = etree.HTML(res.text)

            #  PDF 查找 (在主 main 容器中全文扫描)
            pdf_url = None
            all_links = tree.xpath('//a')
            for pattern in CONFIG["PDF_PATTERNS"]:
                for a in all_links:
                    link_text = "".join(a.xpath('.//text()')).strip()
                    href = a.get('href')
                    if href and re.search(pattern, link_text):
                        pdf_url = urljoin(task['url'], href)
                        break
                if pdf_url: break

            os.makedirs(major_dir, exist_ok=True)
            if pdf_url: self.download_pdf(pdf_url, major_dir)

            #  指定内容获取逻辑
            final_content_html = []
            processed_frame_ids = set()  # 用于防重

            # 提取首部介绍块
            # 逻辑：找第一个带有 ce-bodytext 的 div
            intro_blocks = tree.xpath('//div[contains(@class, "ce-bodytext")]')
            if intro_blocks:
                node = intro_blocks[0]
                frame = node.xpath('./ancestor::div[contains(@class, "frame")][1]')
                if frame:
                    frame_id = frame[0].get('id')
                    print(f"     提取首部块 ({frame_id})")
                    final_content_html.append(CrawlerUtils.clean_html_node(frame[0], task['url']))
                    if frame_id: processed_frame_ids.add(frame_id)

            # 提取 Berufsbegleitendes 兼职学习内容
            beruf_h2 = tree.xpath('//h2[contains(., "Berufsbegleitendes")]')
            if beruf_h2:
                frame = beruf_h2[0].xpath('./ancestor::div[contains(@class, "frame")][1]')
                if frame:
                    frame_id = frame[0].get('id')
                    if frame_id not in processed_frame_ids:
                        print(f"     提取兼职模块 ({frame_id})")
                        final_content_html.append(CrawlerUtils.clean_html_node(frame[0], task['url']))
                        if frame_id: processed_frame_ids.add(frame_id)

            # 提取 Kurzinfos 折叠面板
            kurz_btn = tree.xpath('//button[contains(., "Kurzinfos")]')
            if kurz_btn:
                # 向上找容器，向下找 body
                card = kurz_btn[0].xpath(
                    './ancestor::div[contains(@class, "col") or contains(@class, "accordion-record")][1]')
                if card:
                    body = card[0].xpath('.//div[contains(@class, "card-body")]')
                    if body:
                        print("     提取折叠面板数据")
                        final_content_html.append(CrawlerUtils.clean_html_node(body[0], task['url']))

            #  组装与保底逻辑
            content_str = "".join(final_content_html)
            final_md = CrawlerUtils.to_markdown(content_str)

            # 如果指定块全落空，执行保底全量拉取
            if not final_md.strip():
                print("    警告，指定内容缺失，执行全量 main 抓取保底 ")
                main_box = tree.xpath('//main')
                if main_box:
                    final_md = CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(main_box[0], task['url']))

            # 保存文件
            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n# {task['name']}\n\n{final_md}")
            print(f"    成功, 数据归档 ")

        except Exception as e:
            print(f"   失败,{task['name']} 处理异常: {e}")

    def download_pdf(self, url, folder):
        try:
            url_filename = url.split('/')[-1].split('?')[0]
            filename = CrawlerUtils.sanitize_path(url_filename, False)
            if not filename.lower().endswith('.pdf'): filename += ".pdf"
            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                with open(os.path.join(folder, filename), 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF下载 {filename}")
        except:
            pass


if __name__ == "__main__":
    THGA_Scraper().run()