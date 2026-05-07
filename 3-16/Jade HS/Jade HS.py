import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "Jade_Hochschule",
    "ROOT_DOMAIN": "https://www.jade-hs.de",
    "START_URL": "https://www.jade-hs.de/apps/studiengang/index.php",
    "OUTPUT_DIR": "Jade_Hochschule_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # PDF 包含关键词
    "PDF_INCLUDE": ["Zugangsordnung", "Zulassungsordnung", "Immatrikulationsordnung", "eligibility", "admission",
                    "entrance"],
    # PDF 排除关键词
    "PDF_EXCLUDE": ["Prüfungsordnung Teil A"]
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Major_Info"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 50 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_html_content(html_str, base_url):
        if not html_str: return ""
        tree = etree.HTML(html_str)
        if tree is None: return ""

        noise_selectors = [
            '//blockquote', '//img', '//video', '//svg', '//button', '//nav',
            '//script', '//style', '//footer', '//header',
            '//*[contains(@class, "bx-wrapper")]',
            '//*[@id="back-top"]',
            '//div[contains(@class, "breadcrumb")]'
        ]
        for xpath in noise_selectors:
            for node in tree.xpath(xpath):
                p = node.getparent()
                if p is not None: p.remove(node)

        for a in tree.xpath('.//a'):
            href = a.get('href')
            if href:
                a.set('href', urljoin(base_url, href))

        raw_html = etree.tostring(tree, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        markdown_text = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class JadeScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_list_via_playwright()
        if not tasks:
            print("未捕捉到专业列表。")
            return

        print(f" 锁定 {len(tasks)} 个专业。开始采集...")

        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 挖掘：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(1.5, 3.0))

    def fetch_list_via_playwright(self):
        collected = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"])
            page = context.new_page()
            page.goto(CONFIG["START_URL"], wait_until="networkidle")

            try:
                page.locator('label:has-text("Master")').click()
                time.sleep(1)
                submit_btn = page.locator('input[type="submit"][value="Studiengänge finden"]').last
                submit_btn.click()
                page.wait_for_selector('.list_element', timeout=1500)
                time.sleep(2)
            except Exception as e:
                print(f"    列表筛选异常: {e}")

            links = page.locator('a:has(.list_element)').all()
            for link in links:
                try:
                    href = link.get_attribute("href")
                    title_attr = link.get_attribute("title")
                    meta_text = link.locator('.list_element p').inner_text()
                    if href:
                        name = title_attr.split(",")[0].strip() if title_attr else "Major"
                        collected.append({
                            "name": name,
                            "full_name": title_attr,
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "meta": meta_text.strip()
                        })
                except:
                    continue
            browser.close()
        return collected

    def process_detail(self, task):
        """侧边栏+main审计PDF，contentSide/main提取文本"""
        try:
            safe_major_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_major_name)
            if os.path.exists(major_dir):
                print(f"    跳过 {safe_major_name}")
                return

            res = self.session.get(task["url"], timeout=30)
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)

            #  文本提取策略：精准锁定 contentSide (优先)，main (保底)
            side_node = tree.xpath('//*[@id="contentSide"]')
            main_node = tree.xpath('//main')

            extract_node = None
            if side_node:
                extract_node = side_node[0]
            elif main_node:
                extract_node = main_node[0]
            else:
                extract_node = tree.xpath('//*[@id="contentInner"] | //body')[0]

            raw_html = etree.tostring(extract_node, encoding='unicode', method='html')
            purified_html = CrawlerUtils.clean_html_content(raw_html, task['url'])
            clean_md = CrawlerUtils.to_markdown(purified_html)

            #  PDF 审计域策略：检查 sidebar + main + contentInner
            os.makedirs(major_dir, exist_ok=True)

            # 审计根节点集合
            audit_roots = tree.xpath('//*[@id="sidebar"] | //main | //*[@id="contentInner"]')

            # 扫描并下载 PDF
            for root in audit_roots:
                #  扫描当前节点内的所有直接链接
                self.scan_and_download_links(root, task['url'], major_dir)

                if root.xpath('.//iframe[contains(@src, "infos-downloads")]'):
                    iframe_src = root.xpath('.//iframe[contains(@src, "infos-downloads")]/@src')[0]
                    iframe_url = urljoin(task['url'], iframe_src)
                    self.audit_iframe_content(iframe_url, major_dir)

            #  保存 Markdown
            md_file_path = os.path.join(major_dir, f"{safe_major_name}.md")
            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n")
                f.write(f"**{task['full_name']} | {task['meta']}**\n\n")
                f.write("---\n\n")
                f.write(clean_md)

            print(f"    成功, 归档：{safe_major_name}")

        except Exception as e:
            print(f"     失败, {task['name']}: {e}")

    def scan_and_download_links(self, element, base_url, save_dir):
        """通用 A 标签扫描 """
        links = element.xpath('.//a')
        for link in links:
            link_text = "".join(link.xpath('.//text()')).strip()
            href = link.get('href')
            if not href or not link_text: continue

            # 排除逻辑
            if any(ex.lower() in link_text.lower() for ex in CONFIG["PDF_EXCLUDE"]):
                continue

            # 包含逻辑
            if any(inc.lower() in link_text.lower() for inc in CONFIG["PDF_INCLUDE"]):
                pdf_url = urljoin(base_url, href)
                self.download_pdf(pdf_url, link_text, save_dir)

    def audit_iframe_content(self, iframe_url, save_dir):
        """针对 iframe 的穿透 """
        try:
            res = self.session.get(iframe_url, timeout=15)
            iframe_tree = etree.HTML(res.text)
            if iframe_tree is not None:
                # 在 iframe 内容中再次执行扫描
                self.scan_and_download_links(iframe_tree, iframe_url, save_dir)
        except:
            pass

    def download_pdf(self, url, label, save_dir):
        try:
            with self.session.get(url, stream=True, timeout=20) as r:
                r.raise_for_status()
                clean_label = re.sub(r'[\\/*?:"<>|]', "_", label).strip()[:80]
                filename = clean_label if clean_label.lower().endswith('.pdf') else f"{clean_label}.pdf"
                save_path = os.path.join(save_dir, filename)
                if os.path.exists(save_path): return
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                print(f"       PDF获取 {filename}")
        except:
            pass


if __name__ == "__main__":
    JadeScraper().run()