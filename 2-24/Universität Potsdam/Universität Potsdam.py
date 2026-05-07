import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "University_of_Potsdam",
    "ROOT_DOMAIN": "https://www.uni-potsdam.de",
    "LIST_URL": "https://www.uni-potsdam.de/de/studium/studienangebot/studienangebot-a-z/A",
    "ADMISSION_URL": "https://www.uni-potsdam.de/de/studium/zugang/bewerbung-master/konsekutiv",
    "OUTPUT_DIR": "Uni_Potsdam_Data",
    "HEADERS": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    },
    "TIMEOUT": 30,
    "DELAY": 1.2
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path_name(name, is_folder=True):
        """清洗路径名：物理剔除竖线等禁忌字符，截断长度"""
        if not name: return "Unknown_Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 50
        return name[:limit]

    @staticmethod
    def clean_and_complete_html(element):
        """清洗 HTML 并补全相对超链接"""
        if element is None: return ""

        # 补全超链接
        for a_tag in element.xpath('.//a'):
            href = a_tag.get('href')
            if href and href.startswith('/'):
                # 使用 urljoin 自动拼接根域名
                absolute_url = urljoin(CONFIG["ROOT_DOMAIN"], href)
                a_tag.set('href', absolute_url)

        # 移除干扰标签
        for tag in ['script', 'style', 'font', 'img', 'noscript', 'nav', 'header', 'footer', 'button']:
            for node in element.xpath(f'.//{tag}'):
                parent = node.getparent()
                if parent is not None: parent.remove(node)

        return etree.tostring(element, encoding='unicode', method='html')

    @staticmethod
    def to_markdown(html_source):
        """将清洗后的 HTML 转化为 Markdown 并执行排版修复。"""
        if not html_source: return ""
        # 剥离翻译标签残留
        html_source = re.sub(r'</?font[^>]*>', '', html_source, flags=re.IGNORECASE)
        markdown_text = md(html_source, heading_style="ATX")
        # 移除行首空格
        lines = [line.strip() for line in markdown_text.split('\n')]
        content = '\n'.join(lines)
        # 处理冒号换行
        content = re.sub(r'(?<!http)(?<!https):\s*', ':\n\n', content)
        return re.sub(r'\n{3,}', '\n\n', content).strip()


class PotsdamScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CONFIG["HEADERS"])
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

        self.common_appendix_md = ""
        self.specific_requirements_cache = {}

    def run(self):
        print(f"启动波茨坦大学采集")

        # 预提取通用准入说明
        self.fetch_admission_appendix()

        # 获取专业列表
        print(f"定位硕士专业列表")
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=CONFIG["TIMEOUT"])
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)
        except Exception as e:
            print(f"访问列表页失败：{e}")
            return

        sidebar_xpath = '//li[contains(@class, "up-subpagenav-entry") and .//a[contains(@href, "/studium/studienangebot/master")]]//ul[contains(@class, "up-subpagenav-level-2")]//a'
        major_nodes = tree.xpath(sidebar_xpath)

        tasks = []
        for node in major_nodes:
            name = node.xpath('string(.)').strip()
            href = node.get('href')
            if href:
                tasks.append({"name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href)})

        print(f"共有 {len(tasks)} 个硕士专业")

        # 3. 遍历详情
        for idx, task in enumerate(tasks):
            print(f"\n[项目 {idx + 1}/{len(tasks)}] 正在解析并补全链接：{task['name']}")
            self.process_major_detail(task)
            time.sleep(CONFIG["DELAY"])

        print(f"\n任务全部圆满完成。所有 MD 中的链接现在均可直接点击访问")

    def fetch_admission_appendix(self):
        """预提取并补全通用信息与特定专业要求"""
        print(f"正在提取准入说明页")
        try:
            res = self.session.get(CONFIG["ADMISSION_URL"], timeout=CONFIG["TIMEOUT"])
            tree = etree.HTML(res.text)

            # 提取通用部分
            common_node = tree.xpath('//div[@id="c788769"]')
            if common_node:
                # clean_and_complete_html 会补全
                self.common_appendix_md = CrawlerUtils.to_markdown(CrawlerUtils.clean_and_complete_html(common_node[0]))
                print("    通用申请信息补全完毕")

            # 提取特定要求
            items = tree.xpath('//div[@id="c788763"]//div[contains(@class, "up-accordion-item")]')
            if items:
                for item in items:
                    title = item.xpath('string(.//div[contains(@class, "header")]//h3)').strip()
                    content_node = item.xpath('.//div[contains(@class, "up-accordion-item-content")]')
                    if title and content_node:
                        # 补全链接后转为 MD
                        md_text = CrawlerUtils.to_markdown(CrawlerUtils.clean_and_complete_html(content_node[0]))
                        self.specific_requirements_cache[title.lower()] = md_text
                print(f"    已缓存并补全 {len(self.specific_requirements_cache)} 个专业的特定要求")
        except Exception as e:
            print(f"    预提取过程出错：{e}")

    def process_major_detail(self, task):
        """解析详情并执行 marker 切片与链接修复"""
        try:
            res = self.session.get(task["url"], timeout=CONFIG["TIMEOUT"])
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)

            url_id = task["url"].strip("/").split("/")[-1][:12]
            safe_folder = f"{CrawlerUtils.sanitize_path_name(task['name'], True)}_{url_id}"
            major_path = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_path): os.makedirs(major_path)

            # 内容切片
            content_root = tree.xpath('//*[@id="up_content"]')
            if not content_root: return

            extracted_nodes = []
            for child in content_root[0].getchildren():
                captions = child.xpath('.//caption')
                hit_marker = False
                for cap in captions:
                    if "STUDIENINHALTE UND LEISTUNGSUMFANG" in cap.xpath('string(.)').upper():
                        hit_marker = True
                        break
                if hit_marker: break
                extracted_nodes.append(child)

            # 对切片出的每一个节点执行链接补全
            main_md_list = []
            for node in extracted_nodes:
                main_md_list.append(CrawlerUtils.to_markdown(CrawlerUtils.clean_and_complete_html(node)))

            main_content_md = "\n\n".join(main_md_list)

            # 匹配逻辑
            matched_req_md = ""
            current_major_name = task["name"].lower()
            for req_title, req_content in self.specific_requirements_cache.items():
                if req_title in current_major_name or current_major_name in req_title:
                    matched_req_md = f"\n\n---\n## 额外入学要求 (Specific Requirements)\n\n{req_content}"
                    break

            # 拼接并保存
            final_md = f"# {task['name']}\n\n- **Source URL**: {task['url']}\n\n"
            final_md += main_content_md
            final_md += matched_req_md
            final_md += f"\n\n---\n## 通用申请信息\n\n{self.common_appendix_md}"

            safe_file_name = f"{CrawlerUtils.sanitize_path_name(task['name'], False)}.md"
            with open(os.path.join(major_path, safe_file_name), "w", encoding="utf-8") as f:
                f.write(final_md)

        except Exception as e:
            print(f"    处理详情页出错：{e}")


if __name__ == "__main__":
    PotsdamScraper().run()