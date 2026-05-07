import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "Leipzig_University",
    "START_URL": "https://www.uni-leipzig.de/studium/vor-dem-studium/studienangebot?mksearch%5Bterm%5D=master&mksearch%5Bt3study%5D%5Bdegree%5D=&mksearch%5Bt3study%5D%5Bbranch%5D=&mksearch%5Bt3study%5D%5Brestriction%5D=&mksearch%5Bt3study%5D%5Btype%5D=&mksearch%5Bt3study%5D%5Bstartdate%5D=&mksearch%5Bt3study%5D%5Bform%5D=&mksearch%5Bt3study%5D%5Bfaculty%5D=&mksearch%5Bt3study%5D%5Badvanced_training%5D=&mksearch%5Bt3study%5D%5Binternational%5D=&mksearch%5Bt3study%5D%5Blanguage%5D=&mksearch%5Bsubmit%5D=%E7%AD%9B%E9%80%89&mksearch%5BsearchSubmitted%5D=1",
    "OUTPUT_DIR": "Leipzig_University_Data",
    "HEADERS": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    "TIMEOUT": 30,
    "DELAY": 1.2
}

# 准入要求白名单
REQUIREMENT_WHITELIST = [
    "Musikwissenschaft",
    "Buddhist Studies",
    "Chinese Studies",
    "Modernes Südasien",
    "Religionswissenschaft"
]


class CrawlerUtils:
    @staticmethod
    def sanitize_filename(name, limit=40):
        """清洗文件名并执行严格长度截断"""
        if not name: return "Untitled"
        # 转义德语字符
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        # ASCII化处理
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        # 移除非法字符
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = re.sub(r'\s+', "_", name).strip("._")
        # 严格截断
        return name[:limit]

    @staticmethod
    def clean_html(element):
        if element is None: return ""
        for tag in ['script', 'style', 'font', 'img', 'noscript', 'nav', 'header', 'footer']:
            for node in element.xpath(f'.//{tag}'):
                parent = node.getparent()
                if parent is not None: parent.remove(node)
        raw_html = etree.tostring(element, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html):
        if not html: return ""
        markdown_text = md(html, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        content = '\n'.join(lines)
        content = re.sub(r'(?<!http)(?<!https):\s*', ':\n\n', content)
        return re.sub(r'\n{3,}', '\n\n', content).strip()


class LeipzigScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CONFIG["HEADERS"])
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        print("正在启动莱比锡大学采集程序")

        try:
            response = self.session.get(CONFIG["START_URL"], timeout=CONFIG["TIMEOUT"])
            response.raise_for_status()
            tree = etree.HTML(response.text)
        except Exception as e:
            print(f"连接主列表失败：{e}")
            return

        major_nodes = tree.xpath('//div[contains(@class, "results")]/a[contains(@class, "blocklink")]')
        print(f"扫描完毕，发现 {len(major_nodes)} 个专业项目。开始采集")

        for node in major_nodes:
            try:
                name = node.xpath('string(.//h3)').strip()
                href = node.get('href')
                full_url = urljoin("https://www.uni-leipzig.de", href)
                # 提取末尾 ID
                url_id = href.split('/')[-1][:20]  # ID 也限制一下长度

                is_on_whitelist = any(keyword.lower() in name.lower() for keyword in REQUIREMENT_WHITELIST)

                print(f"\n[解析中] {name}")
                self.process_major_detail(name, full_url, url_id, is_on_whitelist)
                time.sleep(CONFIG["DELAY"])
            except:
                continue

    def process_major_detail(self, name, url, url_id, is_whitelisted):
        try:
            res = self.session.get(url, timeout=CONFIG["TIMEOUT"])
            tree = etree.HTML(res.text)

            # 文件夹名限制为 50 个字符
            safe_name_folder = CrawlerUtils.sanitize_filename(name, limit=50)
            major_folder = os.path.join(self.output_dir, f"{safe_name_folder}_{url_id}")
            if not os.path.exists(major_folder): os.makedirs(major_folder)

            # 提取主内容
            main_article = tree.xpath('//article[contains(@class, "contentWithSidebar")]')
            if not main_article: return

            article_html = CrawlerUtils.clean_html(main_article[0])
            markdown_body = f"# {name}\n\n- **Source URL**: {url}\n\n"
            markdown_body += CrawlerUtils.to_markdown(article_html)

            # 准入要求跳转逻辑
            req_links = tree.xpath(
                '//div[@id="accordion-requirements"]//a[contains(@class, "textlink") and not(contains(@href, "mailto:"))]')

            for link_node in req_links:
                link_href = link_node.get('href')
                if not link_href: continue
                sub_url = urljoin(url, link_href)

                print(f"    进入准入要求子页采集")
                sub_text = self.handle_subpage(sub_url, major_folder, is_whitelisted)

                if sub_text:
                    markdown_body += f"\n\n---\n## 附加准入说明\n\n{sub_text}"
                break

            # 文件名限制为 40 个字符
            safe_name_file = CrawlerUtils.sanitize_filename(name, limit=40)
            md_path = os.path.join(major_folder, f"{safe_name_file}.md")

            with open(md_path, "w", encoding="utf-8") as f:
                f.write(markdown_body)
            print(f"    保存完毕。")

        except Exception as e:
            print(f"    处理详情页失败：{e}")

    def handle_subpage(self, url, folder, is_whitelisted):
        try:
            res = self.session.get(url, timeout=CONFIG["TIMEOUT"])
            tree = etree.HTML(res.text)

            # 提取文本
            sub_article_node = tree.xpath('//article')
            sub_md = ""
            if sub_article_node:
                sub_md = CrawlerUtils.to_markdown(CrawlerUtils.clean_html(sub_article_node[0]))

            # PDF 名单差异化下载
            pdf_links = tree.xpath('//a[contains(@href, ".pdf")]/@href')
            target_pdf_url = None

            if is_whitelisted:
                if len(pdf_links) >= 1: target_pdf_url = urljoin(url, pdf_links[0])
            else:
                if len(pdf_links) >= 2: target_pdf_url = urljoin(url, pdf_links[1])

            if target_pdf_url:
                # PDF 文件名也进行严格截断
                raw_pdf_name = target_pdf_url.split('/')[-1]
                safe_pdf_name = f"Req_{CrawlerUtils.sanitize_filename(raw_pdf_name, limit=30)}"
                if not safe_pdf_name.lower().endswith('.pdf'): safe_pdf_name += ".pdf"

                save_path = os.path.join(folder, safe_pdf_name)
                print(f"    PDF已下载：{safe_pdf_name}")

                with self.session.get(target_pdf_url, stream=True) as r:
                    r.raise_for_status()
                    with open(save_path, 'wb') as f:
                        for chunk in r.iter_content(8192): f.write(chunk)

            return sub_md
        except:
            return None


if __name__ == "__main__":
    LeipzigScraper().run()