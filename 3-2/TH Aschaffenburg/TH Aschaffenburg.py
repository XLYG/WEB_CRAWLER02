import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "TH_Aschaffenburg",
    "ROOT_DOMAIN": "https://www.th-ab.de",
    "LIST_URL": "https://www.th-ab.de/en/education/degree-programmes",
    "OUTPUT_DIR": "TH_Aschaffenburg_Data",
    "HEADERS": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    },
    "TIMEOUT": 30,
    "DELAY": 1.2
}

# 目标版块关键词
TARGET_KEYWORDS = ["Wichtigste", "essentials", "glance", "一览", "Profil", "Aufbau", "programme", "结构", "轮廓",
                   "Career", "Promotion", "Gut zu wissen", "apply?", "申请", "Dokumente", "Downloads", "Contacts",
                   "Related"]


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清洗路径名，解决 Windows 长度限制。"""
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_html(element):
        """补全链接，物理移除 blockquote、图片和冗余标签"""
        if element is None: return ""

        for a in element.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style',
                 'noscript']
        for tag in noise:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        raw_html = etree.tostring(element, encoding='unicode', method='html')
        # 移除翻译标签残留
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        """转化为 Markdown 并执行严格格式对齐。"""
        if not html_content: return ""
        markdown_text = md(html_content, heading_style="ATX")
        # 消除行首空格，防止代码块误判，压缩多余换行
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class AschaffenburgScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CONFIG["HEADERS"])
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        print(f"正在进行阿沙芬堡应用技术大学采集")

        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=CONFIG["TIMEOUT"])
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)
        except Exception as e:
            print(f"访问列表页失败：{e}")
            return

        # 锁定专业列表区域，这个id不用怕失效
        target_sections = tree.xpath('//section[@id="c6379"] | //section[@id="c6377"]')
        tasks = []
        for section in target_sections:
            for a in section.xpath('.//a'):
                href = a.get('href')
                name = "".join(a.xpath('.//text()')).strip()
                if href and len(name) > 3 and "show-all" not in href:
                    tasks.append({"name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href)})

        unique_tasks = {t['url']: t for t in tasks}.values()
        print(f"成功锁定 {len(unique_tasks)} 个硕士专业。开始进行详细采集")

        for idx, task in enumerate(unique_tasks):
            print(f"\n[{idx + 1}/{len(unique_tasks)}] 正在解析：{task['name']}")
            self.process_major(task)
            time.sleep(CONFIG["DELAY"])

        print(f"\n全部采集任务执行完毕。")

    def process_major(self, task):
        try:
            res = self.session.get(task["url"], timeout=CONFIG["TIMEOUT"])
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            one_pager_link = tree.xpath('//p[@class="imageSlider__text"]//a/@href')
            if one_pager_link:
                new_url = urljoin(CONFIG["ROOT_DOMAIN"], one_pager_link[0])
                print(f"   执行 OnePager 跳转：{new_url}")
                res = self.session.get(new_url, timeout=CONFIG["TIMEOUT"])
                tree = etree.HTML(res.text)
                current_url = new_url
            else:
                current_url = task["url"]

            # 目录准备
            url_id = current_url.strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_id}"
            major_path = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_path): os.makedirs(major_path)

            md_content = f"URL: {current_url}\n\n# {task['name']}\n\n---\n\n"

            print("    正在抓取侧边栏信息...")
            sidebar_items = tree.xpath('//aside[contains(@class, "sidebar")]//b-mount[@type="accordionItem"]')[:2]
            # 兼容性保证
            if not sidebar_items:
                sidebar_items = tree.xpath(
                    '//aside[contains(@class, "sidebar")]//li[contains(@class, "accordion__item")]')[:2]

            for item in sidebar_items:
                cleaned_sidebar = CrawlerUtils.to_markdown(CrawlerUtils.clean_html(item))
                if cleaned_sidebar:
                    md_content += "## Sidebar Detail\n" + cleaned_sidebar + "\n\n"

            # 主内容切片
            main_node = tree.xpath('//main[@id="main"]')[0] if tree.xpath('//main[@id="main"]') else None
            if main_node is not None:
                faq_sentinel = main_node.xpath('//div[contains(@class, "faq") and contains(@class, "container")]')

                if faq_sentinel:
                    faq_node = faq_sentinel[0]
                    # 提取 FAQ 之前的内容
                    print("    执行正文切片")
                    before_html_parts = []
                    for child in main_node.xpath('./*'):
                        # 包含性判定逻辑
                        if child == faq_node or faq_node in child.xpath('.//*'):
                            break
                        before_html_parts.append(CrawlerUtils.clean_html(child))

                    md_content += "## Introduction & Overview\n" + CrawlerUtils.to_markdown(
                        "".join(before_html_parts)) + "\n\n"

                    # 提取 FAQ
                    print("    提取常见问题解答...")
                    md_content += "## FAQ\n" + CrawlerUtils.to_markdown(CrawlerUtils.clean_html(faq_node))
                else:
                    print("    未发现 FAQ，失败")
                    md_content += CrawlerUtils.to_markdown(CrawlerUtils.clean_html(main_node))

            # 保存 Markdown
            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_path, safe_file_name), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"   写入完成")

        except Exception as e:
            print(f"   解析详情时发生错误：{e}")


if __name__ == "__main__":
    AschaffenburgScraper().run()