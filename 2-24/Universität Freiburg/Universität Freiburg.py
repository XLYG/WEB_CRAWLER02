import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "University_of_Fribourg",
    "START_URL": "https://studies.unifr.ch/de/studienangebot/courses/?ba=0&ma=1&do=0",
    "BASE_URL": "https://studies.unifr.ch/de/studienangebot/",
    "OUTPUT_DIR": "University_of_Fribourg_Data",
    "HEADERS": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8"
    },
    "TIMEOUT": 30,
    "POLITE_WAIT": 1.5
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path_name(name, is_folder=True):
        """清洗路径与文件名"""
        if not name or len(name.strip()) == 0:
            return "Major_Document"
        chars = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in chars.items():
            name = name.replace(k, v)

        # 移除括号及其内部内容（如“(B)”, “(M)”）
        name = re.sub(r'\(.*?\)', '', name)

        # 隔离非拉丁字符（移除中文）
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')

        # 移除非法文件字符并压缩空格
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = re.sub(r'\s+', "_", name).strip("._")

        # 如果处理后变为空，给一个保底名称
        if not name: name = "Unknown_Major"

        # 长度截断
        limit = 60 if is_folder else 50
        return name[:limit]

    @staticmethod
    def surgical_html_cleaning(html_element):
        """清洗，直接剔除不需要的干扰标签"""
        if html_element is None: return ""
        # 彻底移除不需要的干扰标签
        noise_tags = [
            './/script', './/style', './/nav', './/button',
            './/img', './/svg', './/font', './/iframe', './/noscript'
        ]

        for tag in noise_tags:
            for node in html_element.xpath(tag):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)

        # 转回 HTML 并转化为 MD
        raw_html = etree.tostring(html_element, encoding='unicode', method='html')
        markdown_text = md(raw_html, heading_style="ATX")
        # 压缩多余空行
        return re.sub(r'\n{3,}', '\n\n', markdown_text).strip()


class FribourgScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CONFIG["HEADERS"])
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def run(self):
        print(f"正在获取弗里堡大学专业")

        # 获取专业列表
        print(f"正在连接列表主页：{CONFIG['START_URL']}")
        try:
            res = self.session.get(CONFIG["START_URL"], timeout=CONFIG["TIMEOUT"])
            res.raise_for_status()
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)
        except Exception as e:
            print(f"无法访问列表页{e}")
            return

        rows = tree.xpath('//table[@class="studies_list"]//tr')
        tasks = []
        print(f"成功获取列表，共 {len(rows)} 条数据")

        for row in rows:
            # 锁定带有 btn-primary 类的 Master 链接
            master_link = row.xpath('.//td[contains(@class, "master")]/a[contains(@class, "btn-primary")]/@href')

            if master_link:
                # 列表页提取名称
                list_name = row.xpath('string(.//td[@class="offerlist-title"]//a[@class="subject"])').strip()
                if not list_name:
                    list_name = row.xpath('string(.//td[@class="offerlist-title"])').strip()

                full_url = urljoin(CONFIG["BASE_URL"], master_link[0])
                tasks.append({"list_name": list_name, "url": full_url})

        print(f"筛选完毕共 {len(tasks)} 个目标专业，开始抓取")

        # 遍历详情
        for i, task in enumerate(tasks):
            print(f"\n[ {i + 1}/{len(tasks)}] 正在解析详情页：{task['list_name']}")
            self.fetch_and_save_detail(task)
            time.sleep(CONFIG["POLITE_WAIT"])

        print(f"\n弗里堡大学的所有采集任务均已完成")

    def fetch_and_save_detail(self, task):
        """解析详情页并按照 <h2> 内容命名保存"""
        try:
            res = self.session.get(task["url"], timeout=CONFIG["TIMEOUT"])
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)

            # 获取唯一 ID 用于文件夹区分
            url_id = "_".join(task["url"].strip("/").split("/")[-2:])

            # 文件夹命名
            safe_folder_name = f"{CrawlerUtils.sanitize_path_name(task['list_name'], True)}_{url_id}"
            major_path = os.path.join(self.output_dir, safe_folder_name)
            if not os.path.exists(major_path):
                os.makedirs(major_path)

            # 定位核心内容区块
            content_node = tree.xpath('//div[contains(@class, "col-md-8") and contains(@class, "inner-10")]')
            if not content_node:
                content_node = tree.xpath('//div[@class="col-md-8"]')

            if content_node:
                # 优先从 <h2> 提取专业全称
                # 寻找区块内的第一个 h2 标签
                actual_major_name = content_node[0].xpath('string(.//h2[1])').strip()

                # 如果 h2 提取失败，则回退使用列表页名称
                final_name = actual_major_name if actual_major_name else task["list_name"]

                # 清洗文件名
                clean_file_name = CrawlerUtils.sanitize_path_name(final_name, False)

                # 深度清洗 HTML 并转为 MD
                markdown_text = CrawlerUtils.surgical_html_cleaning(content_node[0])

                # 组装最终正文
                final_output = f"# {final_name}\n\n"
                final_output += f"- **Official URL**: {task['url']}\n"
                final_output += f"- **Internal ID**: {url_id}\n\n"
                final_output += markdown_text

                # 保存文件-[文件夹]/[专业全称].md
                full_file_path = os.path.join(major_path, f"{clean_file_name}.md")

                with open(full_file_path, "w", encoding="utf-8") as f:
                    f.write(final_output)
                print(f"      成功保存：{clean_file_name}.md")
            else:
                print(f"      未在详情页找到内容区块，跳过。")

        except Exception as e:
            print(f"     处理该专业时发生错误：{e}")


if __name__ == "__main__":
    scraper = FribourgScraper()
    scraper.run()