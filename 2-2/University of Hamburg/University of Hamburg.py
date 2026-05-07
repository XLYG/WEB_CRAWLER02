import os
import re
import time
import random
from lxml import etree
from markdownify import markdownify as md
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class HamburgFullScraper:
    def __init__(self):
        self.university_name = "Hamburg_Data"
        self.base_url = "https://www.uni-hamburg.de/campuscenter/"
        # 定死网址，不要修改，有指定的筛选条件在
        self.list_url = "https://www.uni-hamburg.de/campuscenter/studienangebot.html?etcc_cmp=startseite-de&etcc_med=home-de&et_cmp_seg3=studienangebot&et_cmp_seg5=&mtm_campaign=studienangebot&mtm_medium=home&mtm_content=#65,98,115,99,104,108,117,115,115,61,111,112,116,50,38,70,97,107,117,108,116,97,101,116,61,38,115,111,114,116,61,38,105,110,100,101,120,61"
        self.failed_log = "failed_log.txt"

        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--window-size=1920,1080")
        self.driver = webdriver.Chrome(options=chrome_options)

    def clean_name(self, name):
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    def parse_detail_page(self, detail_url, major_full_name):
        """解析详情页"""
        try:
            self.driver.get(detail_url)
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="hauptinhalt"]'))
            )
            time.sleep(2.5)

            tree = etree.HTML(self.driver.page_source)
            container = tree.xpath('//*[@id="hauptinhalt"]')
            if not container: return
            content_node = container[0]

            # 清洗与排除逻辑
            etree.strip_tags(content_node, 'font')
            for tag in ['script', 'style', 'img', 'nav', 'figure', 'noscript']:
                for el in content_node.xpath(f'.//{tag}'):
                    if el.getparent() is not None: el.getparent().remove(el)

            # 剔除 Kontakt 之后的内容
            for section in content_node.xpath('.//*[contains(@class, "accordion") or self::h2 or self::h3]'):
                text = "".join(section.xpath('.//text()')).strip()
                if any(k in text for k in ["Kontakt", "Contact", "Angebote zur Studienorientierung"]):
                    parent = section.getparent()
                    if parent is not None:
                        for sibling in section.xpath('./following-sibling::*'):
                            parent.remove(sibling)
                        parent.remove(section)

            markdown_text = md(etree.tostring(content_node, encoding='unicode'), heading_style="ATX")
            markdown_text = re.sub(r'\n\s*\n', '\n\n', markdown_text)

            folder_path = os.path.join(self.university_name, self.clean_name(major_full_name))
            os.makedirs(folder_path, exist_ok=True)
            with open(os.path.join(folder_path, f"{self.clean_name(major_full_name)}.md"), "w", encoding="utf-8") as f:
                f.write(f"# {major_full_name}\n\nURL: {detail_url}\n\n{markdown_text}")
            print(f"  已完成: {major_full_name}")

        except Exception as e:
            with open(self.failed_log, "a", encoding="utf-8") as f:
                f.write(f"Detail Error: {detail_url} | {str(e)}\n")

    def run(self):
        print(f"正在访问列表页...")
        self.driver.get(self.list_url)

        try:
            # 延长等待时间，让 JS 完成过滤
            time.sleep(8)

            # 直接使用 Selenium 获取所有 tr
            all_rows = self.driver.find_elements(By.XPATH, '//*[@id="studiengaenge"]/tbody/tr')

            visible_majors = []
            print(f"检测到 DOM 中共有 {len(all_rows)} 个行元素，正在筛选可见专业...")

            for row in all_rows:
                # 只保留页面上真正显示的专业
                if row.is_displayed():
                    try:
                        link_node = row.find_element(By.XPATH, './td[@class="Studiengang"]/a')
                        href = link_node.get_attribute('href')

                        # 获取名称（排除 span 里的学位文字，单独处理）
                        full_text = link_node.text
                        degree_nodes = link_node.find_elements(By.TAG_NAME, 'span')
                        degree_text = degree_nodes[0].text if degree_nodes else ""

                        name_text = full_text.replace(degree_text, "").strip()
                        full_name = f"{name_text} ({degree_text})" if degree_text else name_text

                        visible_majors.append((full_name, href))
                    except:
                        continue

            print(f"筛选完成！符合当前网址过滤条件的可见专业共：{len(visible_majors)} 个")

            # 校验是否对齐 218（或接近 218，取决于学校实时更新，此数字为ai计算，实际可能为全量数量）
            if len(visible_majors) != 218:
                print(f"提示：可见专业数 ({len(visible_majors)}) 与预期 (218) 略有出入，将按实际可见项抓取。")

            # 开始批量抓取
            for name, url in visible_majors:
                folder = os.path.join(self.university_name, self.clean_name(name))
                if os.path.exists(os.path.join(folder, f"{self.clean_name(name)}.md")):
                    continue

                self.parse_detail_page(url, name)
                time.sleep(random.uniform(2.5, 4.0))

        finally:
            self.driver.quit()


if __name__ == "__main__":
    scraper = HamburgFullScraper()
    scraper.run()