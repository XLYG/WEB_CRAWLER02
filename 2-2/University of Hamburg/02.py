import os
import re
import time
import random
import requests
from lxml import etree
from markdownify import markdownify as md
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import urljoin


class HamburgAdmissionScraper:
    def __init__(self):
        self.root_dir = "Hamburg_Data"
        self.part2_url = "https://www.uni-hamburg.de/campuscenter/bewerbung/master/zugangsvoraussetzungen/weitere"
        self.base_url = "https://www.uni-hamburg.de"
        self.failed_log = "failed_log_part2.txt"

        # 初始化 Selenium
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=chrome_options)

        # 初始化 Requests Session
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        }

    def clean_name(self, name):
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    def get_id_from_url(self, url):
        """解析 URL 携带的 ID (例如 1525352964)"""
        match = re.search(r'\?(\d+)', str(url))
        return match.group(1) if match else None

    def index_existing_data(self):
        id_folder_map = {}
        if not os.path.exists(self.root_dir):
            print(f" 未找到根目录 {self.root_dir}，请确保第一部分已执行。")
            return id_folder_map

        print("正在匹配本地已抓取专业数据...")
        for folder_name in os.listdir(self.root_dir):
            folder_path = os.path.join(self.root_dir, folder_name)
            if not os.path.isdir(folder_path): continue

            # 查找该文件夹下的专业描述 MD 文件
            for file in os.listdir(folder_path):
                if file.endswith(".md") and "Admission" not in file:
                    try:
                        with open(os.path.join(folder_path, file), 'r', encoding='utf-8') as f:
                            content = f.read(1000)  # 读取前1000字符
                            # 提取 MD 开头的 URL: ... 处的 ID
                            m_id = self.get_id_from_url(content)
                            if m_id:
                                id_folder_map[m_id] = folder_path
                    except:
                        pass
        print(f"索引完成，共匹配到 {len(id_folder_map)} 个现有专业文件夹。")
        return id_folder_map

    def process_pdf_resource(self, url, folder):
        """PDF 下载"""
        save_path = os.path.join(folder, "Admission_Requirements.pdf")
        if os.path.exists(save_path): return

        try:
            r = self.session.get(url, timeout=60, stream=True)
            if r.status_code == 200:
                with open(save_path, "wb") as f:
                    f.write(r.content)
                print(f"    已存入文件夹")
        except Exception as e:
            print(f"    下载失败: {e}")

    def process_html_resource(self, url, folder):
        """处理网页类型的入学要求，清洗并存为 MD"""
        save_path = os.path.join(folder, "Admission_Information.md")
        if os.path.exists(save_path): return

        try:
            resp = self.session.get(url, headers=self.headers, timeout=60)
            resp.encoding = 'utf-8'
            tree = etree.HTML(resp.text)

            # 定位主内容区
            main_node = tree.xpath('//article[contains(@class, "spalte links")]')
            if not main_node: return
            node = main_node[0]

            # 移除干扰元素
            for tag in ['nav', 'script', 'style', 'img', 'noscript']:
                for el in node.xpath(f'.//{tag}'):
                    if el.getparent() is not None: el.getparent().remove(el)

            # 转化 Markdown (保留 klappbox 的层级结构)
            markdown_text = md(etree.tostring(node, encoding='unicode'), heading_style="ATX")
            markdown_text = re.sub(r'\n\s*\n', '\n\n', markdown_text)

            with open(save_path, "w", encoding="utf-8") as f:
                f.write(f"# Admission Information\n\nSource: {url}\n\n")
                f.write(markdown_text)
            print(f"    [MD] 网页内容已转换并存入")
        except Exception as e:
            print(f"    [MD Error] 解析网页失败: {e}")

    def run(self):
        #  建立印证映射
        id_map = self.index_existing_data()
        if not id_map: return

        # 访问第二部分列表页
        print(f"正在抓取第二部分列表: {self.part2_url}")
        self.driver.get(self.part2_url)
        try:
            # 等待表格渲染，其有时间过程
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located(
                    (By.XPATH, '//*[@id="hauptinhalt"]/section[1]/article/div[2]/div/table/tbody/tr'))
            )
            tree = etree.HTML(self.driver.page_source)
            rows = tree.xpath('//*[@id="hauptinhalt"]/section[1]/article/div[2]/div/table/tbody/tr')

            print(f"找到补充项共 {len(rows)} 行，开始匹配并抓取...")

            for row in rows:
                cols = row.xpath('./td')
                if len(cols) < 3: continue

                # 第一列：专业链接 -> 提取 ID
                link_tag = cols[0].xpath('.//a/@href')
                if not link_tag: continue
                m_id = self.get_id_from_url(link_tag[0])

                # 印证匹配：检查该 ID 是否在第一部分的文件夹库中
                if m_id in id_map:
                    target_folder = id_map[m_id]
                    major_name = os.path.basename(target_folder)
                    print(f"\n正在处理匹配项: {major_name}")

                    # 第三列：资源链接
                    res_tag = cols[2].xpath('.//a/@href')
                    if not res_tag: continue

                    resource_url = urljoin(self.base_url, res_tag[0])

                    # 资源分流
                    if resource_url.lower().endswith('.pdf'):
                        self.process_pdf_resource(resource_url, target_folder)
                    else:
                        self.process_html_resource(resource_url, target_folder)

                    time.sleep(random.uniform(1.5, 3.0))
                else:
                    # 未匹配到的项（可能是本科专业或其他排除项）
                    continue

        finally:
            self.driver.quit()
            print("\n--- 汉堡大学第二部分全量补充任务结束 ---")


if __name__ == "__main__":
    scraper = HamburgAdmissionScraper()
    scraper.run()