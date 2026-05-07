import os
import re
import time
import random
import requests
from lxml import etree
from markdownify import markdownify as md
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from urllib.parse import urljoin


class KonstanzFullScraper:
    def __init__(self):
        self.university_name = "Konstanz_Data"
        self.base_url = "https://www.uni-konstanz.de"
        self.failed_log = "failed_log.txt"

        self.session = requests.Session()
        self.session.trust_env = False
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,de;q=0.8"
        }

    def clean_filename(self, name):
        """路径防御机制：处理非法字符并截断长度"""
        name = re.sub(r'[\\/*?:"<>|]', "_", name)
        return name.strip()[:100]

    def safe_request(self, url):
        try:
            response = self.session.get(url, headers=self.headers, timeout=60)
            response.encoding = 'utf-8'
            if response.status_code == 200:
                return response.text
            return None
        except Exception as e:
            with open(self.failed_log, "a", encoding="utf-8") as f:
                f.write(f"Error: {url} | Reason: {str(e)}\n")
            return None

    def parse_detail_page(self, detail_url, major_name):
        """内容截断、深度清洗、MD转换"""
        html_content = self.safe_request(detail_url)
        if not html_content: return

        tree = etree.HTML(html_content)
        # 定位主内容区域
        container_list = tree.xpath('//*[@id="c913378"]/div/div[1]')
        if not container_list:
            # 备用定位逻辑
            container_list = tree.xpath('//div[contains(@class, "c-in2studyfinder-detail")]')

        if not container_list:
            print(f" 无法定位内容区域: {major_name}")
            return

        container = container_list[0]

        # 截断逻辑：在 #contact 之前停止
        contact_nodes = container.xpath('.//*[@id="contact"]')
        if contact_nodes:
            target = contact_nodes[0]
            # 移除 contact 节点自身及其之后的所有兄弟节点
            for sibling in target.xpath('./following-sibling::*'):
                sibling.getparent().remove(sibling)
            # 向上溯源，非破坏性地移除父级的后续兄弟
            curr = target
            while curr is not None and curr != container:
                parent = curr.getparent()
                if parent is not None:
                    for sibling in curr.xpath('./following-sibling::*'):
                        parent.remove(sibling)
                curr = parent
            # 移除 contact 节点本身
            if target.getparent() is not None:
                target.getparent().remove(target)

        # 深度清洗：剥离脚本、样式、图片及不必要的全局标签
        for tag in ['script', 'style', 'picture', 'figure', 'noscript', 'header', 'footer', 'nav']:
            for el in container.xpath(f'.//{tag}'):
                if el.getparent() is not None: el.getparent().remove(el)

        # Markdown 转化
        html_str = etree.tostring(container, encoding='unicode')
        markdown_text = md(html_str, heading_style="ATX", strip=['img'])

        # 清洗
        markdown_text = re.sub(r'\n\s*\n', '\n\n', markdown_text)  # 压缩多余空行
        markdown_text = markdown_text.replace('(/en/', f'({self.base_url}/en/')  # 修复相对链接

        # 文件夹规范存放
        major_folder = os.path.join(self.university_name, self.clean_filename(major_name))
        os.makedirs(major_folder, exist_ok=True)

        file_path = os.path.join(major_folder, f"{self.clean_filename(major_name)}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# {major_name}\n\nSource: {detail_url}\n\n")
            f.write(markdown_text)
        print(f"  {major_name}")

    def run(self):
        print(f"开始执行康斯坦茨大学全量抓取任务...")

        # 列表页分页
        list_pages = [
            "https://www.uni-konstanz.de/en/study/before-you-study/study-programmes/masters-degree/#page=1",
            "https://www.uni-konstanz.de/en/study/before-you-study/study-programmes/masters-degree/#page=2"
        ]

        all_majors = []
        for url in list_pages:
            html = self.safe_request(url)
            if not html: continue
            tree = etree.HTML(html)
            # 列表定位
            items = tree.xpath(
                '//*[@id="c913717"]/div/div[1]/div[2]/ul[2]//a[contains(@class, "c-in2studyfinder__item")]')
            for item in items:
                name = "".join(item.xpath('.//h4//text()')).strip()
                href = urljoin(self.base_url, item.get('href'))
                all_majors.append((name, href))

        print(f"共发现 {len(all_majors)} 个硕士专业。")

        # 遍历专业，应用断点续爬逻辑
        for name, href in all_majors:
            major_clean_name = self.clean_filename(name)
            target_md = os.path.join(self.university_name, major_clean_name, f"{major_clean_name}.md")

            # 断点续爬检查
            if os.path.exists(target_md):
                print(f"  已存在: {name}")
                continue

            self.parse_detail_page(href, name)

            # 随机延迟
            time.sleep(random.uniform(2.0, 4.0))

        print("--- 康斯坦茨大学任务全部完成 ---")


if __name__ == "__main__":
    scraper = KonstanzFullScraper()
    scraper.run()