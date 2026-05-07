import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "HKA_Karlsruhe",
    "ROOT_DOMAIN": "https://www.h-ka.de",
    "COMMON_INFO_URL": "https://www.h-ka.de/en/study/academic-life/getting-started/information-on-registration-contact-persons",
    "LIST_URL": "https://www.h-ka.de/en/study/study-in-german/master",
    "REGULATION_URL": "https://www.h-ka.de/studieren/studium-organisieren/studienordnungen-satzungen/zulassungssatzungen",
    # 指定通用保底 PDF 链接
    "FALLBACK_PDF_URL": "https://www.h-ka.de/fileadmin/Hochschule_Karlsruhe_HKA/Satzungen_Ordnungen/HKA_ZH_Zulassungs-Immatrikulations-O_V4_2022-07-22.pdf",
    "OUTPUT_DIR": "HKA_Karlsruhe_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "WAIT_TIME": 2,
}

# 哨兵关键词
SENTINEL_KEYWORDS = ["Downloads", "Related links", "Weiterführende Links"]

# 德英学科对撞表，绝大部分都是德语，但是部分除此打开默认为英语页面-保险
DISCIPLINE_MAP = {
    "architecture": ["architektur"],
    "civil engineering": ["bauingenieurwesen"],
    "automotive systems": ["fahrzeugtechnologie", "automotive"],
    "business administration": ["betriebswirtschaft", "bwl"],
    "computer science": ["informatik"],
    "electrical engineering": ["elektro-", "informationstechnik"],
    "geomatics": ["geomatik", "geodäsie"],
    "mechanical engineering": ["maschinenbau"],
    "mechatronics": ["mechatronik"],
    "sensor systems": ["sensorsystemtechnik"],
    "technology management": ["technologiemanagement"],
    "data science": ["data science"],
    "geoinformation": ["geoinformations"],
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
        return name[:60 if is_folder else 40]

    @staticmethod
    def clean_html(element):
        if element is None: return ""
        noise = ['script', 'style', 'font', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav']
        for tag in noise:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)
        return etree.tostring(element, encoding='unicode', method='html')


class HkaScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})
        self.common_info_md = ""
        self.major_registry = []  # 存储专业名、目录以及是否已匹配 PDF 的标记

    def run(self):
        print(f"[*] 正在启动 HKA 精准采集引擎 V70.0...")

        #  缓存通用信息
        self.fetch_common_info()

        # 采集专业与详情
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()
            self.collect_majors(page)
            browser.close()

        # 匹配条例 + 自动补全保底条例
        self.match_and_fill_regulations()

    def fetch_common_info(self):
        print("预取通用注册信息")
        try:
            res = self.session.get(CONFIG["COMMON_INFO_URL"])
            tree = etree.HTML(res.text)
            node = tree.xpath('//*[@id="main-content"]')
            if node:
                self.common_info_md = md(CrawlerUtils.clean_html(node[0]))
                print("    通用信息已缓存 ")
        except:
            pass

    def collect_majors(self, page):
        print(f" 正在解析专业详情")
        page.goto(CONFIG["LIST_URL"], wait_until="networkidle")
        time.sleep(CONFIG["WAIT_TIME"])

        heads = page.locator('tr.row-details__head').all()
        for idx, head in enumerate(heads):
            try:
                major_name = head.locator('.table__cell-title').inner_text().strip()
                print(f"    [{idx + 1}/{len(heads)}] 处理：{major_name}")

                # 展开元数据
                btn = head.locator('button.has-readmore').first
                btn.click()
                time.sleep(CONFIG["WAIT_TIME"])

                content_id = btn.get_attribute("data-studyfinder-show-profile")
                content_row = page.locator(f'tr[data-row-details-content-id="{content_id}"]').first
                meta_text = content_row.locator('table.table--horizontal-lines').first.inner_text().replace('\n', ' | ')

                detail_anchor = content_row.locator('a.btn--default:has-text("Details")').first
                detail_url = urljoin(CONFIG["ROOT_DOMAIN"], detail_anchor.get_attribute("href"))

                # 详情切片
                major_md = self.fetch_slice(detail_url)

                # 建立目录
                safe_folder = CrawlerUtils.sanitize_path(major_name, True)
                major_dir = os.path.join(self.output_dir, safe_folder)
                if not os.path.exists(major_dir): os.makedirs(major_dir)

                # 注册以便后续匹配 PDF
                self.major_registry.append({
                    "name": major_name,
                    "dir": major_dir,
                    "has_specific_regulation": False
                })

                # 保存文件
                final_md = f"URL: {detail_url}\n\n**{major_name} | {meta_text}**\n\n---\n\n{major_md}"
                if self.common_info_md:
                    final_md += "\n\n---\n## General Information\n\n" + self.common_info_md

                with open(os.path.join(major_dir, f"{CrawlerUtils.sanitize_path(major_name, False)}.md"), "w",
                          encoding="utf-8") as f:
                    f.write(final_md)

            except:
                continue

    def fetch_slice(self, url):
        try:
            res = self.session.get(url, timeout=30)
            tree = etree.HTML(res.text)
            main_node = tree.xpath('//main')[0]
            collected = []
            for child in main_node.xpath('./*'):
                h2_text = "".join(child.xpath('.//h2//text()')).strip().lower()
                if any(k.lower() in h2_text for k in SENTINEL_KEYWORDS):
                    break
                collected.append(CrawlerUtils.clean_html(child))
            return md("".join(collected))
        except:
            return ""

    def match_and_fill_regulations(self):
        """ 匹配特定条例，未匹配到的专业加一份通用条例"""
        print(f"\n 正在分发准入条例")
        try:
            res = self.session.get(CONFIG["REGULATION_URL"])
            tree = etree.HTML(res.text)
            items = tree.xpath('//div[contains(@class, "ce-bodytext")]//li')

            # 尝试匹配硕士条例
            for li in items:
                li_text = li.xpath('string(.)').strip()
                if "master" not in li_text.lower(): continue

                for major in self.major_registry:
                    matched = False
                    if major["name"].lower() in li_text.lower() or li_text.lower() in major["name"].lower():
                        matched = True
                    else:
                        for eng_k, ger_ks in DISCIPLINE_MAP.items():
                            if eng_k in major["name"].lower() and any(gk in li_text.lower() for gk in ger_ks):
                                matched = True;
                                break

                    if matched:
                        pdf_nodes = li.xpath('.//a[contains(@href, ".pdf")]')
                        if pdf_nodes:
                            pdf_url = urljoin(CONFIG["ROOT_DOMAIN"], pdf_nodes[0].get('href'))
                            pdf_name = f"Latest_Specific_Regulation_{CrawlerUtils.sanitize_path(li_text, False)}.pdf"
                            self.download_pdf(pdf_url, os.path.join(major["dir"], pdf_name))
                            major["has_specific_regulation"] = True
                            print(f"    成功匹配 {major['name']} <- {li_text[:30]}...")
                        break

            # 检查缺失专业，补充通用条例
            print(f"\n 正在为未匹配到特定条例的专业补充通用条例")
            for major in self.major_registry:
                if not major["has_specific_regulation"]:
                    gen_pdf_name = "General_Admission_and_Enrollment_Regulation.pdf"
                    save_path = os.path.join(major["dir"], gen_pdf_name)
                    if not os.path.exists(save_path):
                        self.download_pdf(CONFIG["FALLBACK_PDF_URL"], save_path)
                        print(f"    [补充通用PDF] {major['name']}")

        except Exception as e:
            print(f"    条例分发环节失败：{e}")

    def download_pdf(self, url, path):
        try:
            with self.session.get(url, stream=True, timeout=30) as r:
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(8192): f.write(chunk)
        except:
            pass


if __name__ == "__main__":
    HkaScraper().run()