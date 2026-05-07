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
    "UNIVERSITY_NAME": "TH_Koeln",
    "ROOT_DOMAIN": "https://www.th-koeln.de",
    "START_URL": "https://www.th-koeln.de/studium/alle-studiengaenge-auf-einen-blick_76.php?courseofstudies_degree_de%5B%5D=Master",
    "OUTPUT_DIR": "TH_Koeln_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "TIMEOUT": 30,
    # 容器
    "CONTENT_ID": "content",
    "EXTERNAL_CONTENT_XPATH": '//*[@id="block-custom-content"]',
    # 匹配
    "INTERNAL_JUMP_KEYWORDS": ["Zulassung", "Admission", "voraussetzungen"],
    "EXTERNAL_JUMP_TRIGGER": "Mehr"  # 逻辑 C 必须包含的文字
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
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe',
                 'header', 'footer']
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


class THKoelnScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_major_list()
        if not tasks:
            print("无法锁定专业列表，请检查 ID: filterlistresult 是否在源码中存在 ")
            return

        print(f" 成功捕捉到 {len(tasks)} 个硕士专业 开始详情穿透采集")

        # 详情处理
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.8))

    def fetch_major_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["START_URL"], timeout=CONFIG["TIMEOUT"])
            tree = etree.HTML(res.text)
            # 锁定列表
            links = tree.xpath('//*[@id="filterlistresult"]//div[contains(@class, "form-result")]//h2/a')
            for a in links:
                name = "".join(a.xpath('.//text()')).strip()
                href = a.get('href')
                if name and href:
                    collected.append({
                        "name": name,
                        "url": urljoin(CONFIG["ROOT_DOMAIN"], href)
                    })
            return collected
        except Exception as e:
            print(f"    列表页解析失败: {e}")
            return []

    def process_major(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过 {safe_name}")
                return

            res = self.session.get(task["url"], timeout=CONFIG["TIMEOUT"])
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            # 锁定正文容器
            content_node = tree.xpath(f'//*[@id="{CONFIG["CONTENT_ID"]}"]')
            main_node = tree.xpath('//main')
            target_node = content_node[0] if content_node else (main_node[0] if main_node else None)

            if target_node is None:
                print(f"    警告！ 在页面中找不到任何内容容器: {task['url']}")
                return

            os.makedirs(major_dir, exist_ok=True)

            found_anything = False
            internal_append_md = ""
            external_append_md = ""

            #  扫描 PDF 下载
            pdf_links = target_node.xpath('.//a[contains(@class, "download") and contains(@href, ".pdf")]')
            for pl in pdf_links:
                pdf_url = urljoin(task['url'], pl.get('href'))
                pdf_title = "".join(pl.xpath('.//text()')).strip()
                self.download_pdf(pdf_url, major_dir, pdf_title)
                found_anything = True
                break  # 单个专业仅下载一个核心手册

            #  探测内部准入穿透 (逻辑 B)
            jump_internal = target_node.xpath('.//a[contains(@class, "link internal")]')
            for jl in jump_internal:
                jl_text = "".join(jl.xpath('.//text()')).strip()
                if any(kw.lower() in jl_text.lower() for kw in CONFIG["INTERNAL_JUMP_KEYWORDS"]):
                    internal_url = urljoin(task['url'], jl.get('href'))
                    print(f"     穿透准入页 (逻辑B): {internal_url}")
                    internal_append_md = self.fetch_jump_page(internal_url, is_external=False)
                    if internal_append_md: found_anything = True
                    break

            #  探测外部链接跳转 (逻辑 C - 必须包含 "Mehr")
            jump_external = target_node.xpath('.//a[contains(@class, "external")]')
            for exl in jump_external:
                # 获取该链接内的所有文本（包括 font 标签内的）
                exl_text = "".join(exl.xpath('.//text()')).strip()
                if CONFIG["EXTERNAL_JUMP_TRIGGER"].lower() in exl_text.lower():
                    external_url = urljoin(task['url'], exl.get('href'))
                    print(f"     穿透外部页 (逻辑C): {external_url}")
                    external_append_md = self.fetch_jump_page(external_url, is_external=True)
                    if external_append_md: found_anything = True
                    break

            if not found_anything:
                print(f"     {task['name']} 无特定 PDF 或跳转，采集当前页主内容 ")

            # 文件写入
            main_md = CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(target_node, task['url']))

            md_file_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n")
                f.write(f"# {task['name']}\n\n")
                f.write(main_md)
                if internal_append_md:
                    f.write("\n\n---\n## Admission Details (Internal Appendix)\n\n")
                    f.write(internal_append_md)
                if external_append_md:
                    f.write("\n\n---\n## External Program Info (Jump Appendix)\n\n")
                    f.write(external_append_md)

            print(f"    成功, 数据归档 ")

        except Exception as e:
            print(f"    失败, {task['name']} 采集出错: {e}")

    def fetch_jump_page(self, url, is_external=False):
        """穿透获取子页正文"""
        try:
            res = self.session.get(url, timeout=CONFIG["TIMEOUT"])
            tree = etree.HTML(res.text)
            target = None

            if is_external:
                # 逻辑 C 优先寻找外部专用容器
                ext_box = tree.xpath(CONFIG["EXTERNAL_CONTENT_XPATH"])
                if ext_box: target = ext_box[0]

            # 保底锁定 id="content" 或 main
            if target is None:
                content_box = tree.xpath(f'//*[@id="{CONFIG["CONTENT_ID"]}"]')
                main_box = tree.xpath('//main')
                target = content_box[0] if content_box else (main_box[0] if main_box else None)

            if target is not None:
                return f"Source: {url}\n\n" + CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(target, url))
        except:
            pass
        return ""

    def download_pdf(self, url, folder, title):
        """PDF 唯一性下载"""
        try:
            # 提取唯一尾部
            url_id = url.split('/')[-1]
            safe_title = CrawlerUtils.sanitize_path(title, False)
            filename = f"Flyer_{safe_title}"
            if not filename.lower().endswith('.pdf'): filename += f"_{url_id}"
            if not filename.lower().endswith('.pdf'): filename += ".pdf"

            save_path = os.path.join(folder, filename)
            if os.path.exists(save_path): return

            res = self.session.get(url, timeout=CONFIG["TIMEOUT"], stream=True)
            if res.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF下载 {filename}")
        except:
            pass


if __name__ == "__main__":
    THKoelnScraper().run()