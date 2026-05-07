import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "University_of_Bremen",
    "LIST_URL": "https://www.uni-bremen.de/master/fachmaster",
    "GENERAL_INFO_URL": "https://www.uni-bremen.de/studium/orientieren-bewerben/studienplatzbewerbung/master",
    "OUTPUT_DIR": "University_of_Bremen_Data",
    "HEADERS": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    },
    "TIMEOUT": 30
}

# 需要提取的通用区块 ID，这个是指定不变的所以可以使用
GENERAL_DIV_IDS = ["c600023", "c600036", "c600050", "c600053"]


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, limit=50):
        """转义德语，移除非法字符，强制截断。"""
        if not name: return "Untitled"
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = re.sub(r'\s+', "_", name).strip("._")
        return name[:limit]

    @staticmethod
    def clean_and_convert(element):
        """清洗并转化"""
        if element is None: return ""
        # 移除无用标签
        for tag in ['script', 'style', 'nav', 'button', 'svg', 'noscript']:
            for node in element.xpath(f'.//{tag}'):
                parent = node.getparent()
                if parent is not None: parent.remove(node)

        # 转化为字符串
        raw_html = etree.tostring(element, encoding='unicode', method='html')
        raw_html = re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

        # 转化
        content = md(raw_html, heading_style="ATX")
        # 移除行首空格 & 冒号换行
        lines = [line.strip() for line in content.split('\n')]
        content = '\n'.join(lines)
        content = re.sub(r'(?<!http)(?<!https):\s*', ':\n\n', content)
        return re.sub(r'\n{3,}', '\n\n', content).strip()


class BremenScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CONFIG["HEADERS"])
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.general_appendix = ""

    def run(self):
        print(f"[正在启动不来梅大学采集")

        # 预抓取通用信息
        self.fetch_general_appendix()

        # 抓取专业列表
        print(f"正在连接专业列表页：{CONFIG['LIST_URL']}")
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=CONFIG["TIMEOUT"])
            res.raise_for_status()
            tree = etree.HTML(res.text)
        except Exception as e:
            print(f"访问列表页失败：{e}")
            return

        # 锁定所有的 panel
        panels = tree.xpath('//div[contains(@class, "panel-default")]')
        print(f"成功解析，共发现 {len(panels)} 个专业区块。")

        for idx, panel in enumerate(panels):
            try:
                # 提取专业名称
                major_name = panel.xpath('string(.//button//span)').strip()
                if not major_name: continue

                print(f"[{idx + 1}/{len(panels)}] 正在归档：{major_name}")

                # 提取面板 Body
                body_node = panel.xpath('.//div[contains(@class, "panel-body")]')
                if not body_node: continue

                major_md0 = CrawlerUtils.clean_and_convert(body_node[0])
                major_md = "url : https://www.uni-bremen.de/master/fachmaster\n\n"+major_md0

                # 建立文件夹 专业名截断
                safe_folder = f"{CrawlerUtils.sanitize_path(major_name, 60)}"
                major_dir = os.path.join(self.output_dir, safe_folder)
                if not os.path.exists(major_dir): os.makedirs(major_dir)

                # 寻找并下载第一个 PDF
                pdf_links = body_node[0].xpath('.//a[contains(@href, ".pdf")]')
                if pdf_links:
                    pdf_href = pdf_links[0].get('href')
                    pdf_url = urljoin(CONFIG["LIST_URL"], pdf_href)
                    pdf_text = panel.xpath('string(.//a[contains(@href, ".pdf")])').strip()
                    pdf_name = f"Rules_{CrawlerUtils.sanitize_path(pdf_text, 40)}.pdf"

                    print(f"    PDF正在下载：{pdf_name}")
                    self.download_file(pdf_url, os.path.join(major_dir, pdf_name))

                # 组装并保存 MD
                final_md = f"# {major_name}\n\n{major_md}\n\n"
                final_md += "\n\n---\n## General Information\n\n" + self.general_appendix

                md_filename = f"{CrawlerUtils.sanitize_path(major_name, 50)}.md"
                with open(os.path.join(major_dir, md_filename), "w", encoding="utf-8") as f:
                    f.write(final_md)

            except Exception as e:
                print(f"    处理区块时出错：{e}")

        print(f"所有任务圆满完成。")

    def fetch_general_appendix(self):
        """一次性抓取并缓存通用信息"""
        print(f"正在预提取通用信息区块")
        try:
            res = self.session.get(CONFIG["GENERAL_INFO_URL"], timeout=CONFIG["TIMEOUT"])
            tree = etree.HTML(res.text)
            combined_md = ""
            for div_id in GENERAL_DIV_IDS:
                node = tree.xpath(f'//div[@id="{div_id}"]')
                if node:
                    combined_md += CrawlerUtils.clean_and_convert(node[0]) + "\n\n"
            self.general_appendix = combined_md
            print(f"通用区块已就绪")
        except:
            print(f"通用信息页抓取失败。")

    def download_file(self, url, path):
        try:
            with self.session.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(8192): f.write(chunk)
        except:
            pass


if __name__ == "__main__":
    BremenScraper().run()