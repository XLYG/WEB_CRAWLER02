import os
import re
import time
import random
import asyncio
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "THM",
    "ROOT_DOMAIN": "https://www.thm.de",
    "LIST_URL": "https://www.thm.de/site/studium/sie-wollen-studieren/studiengaenge.html",
    "FEES_URL": "https://www.thm.de/site/hochschule/zentrale-bereiche/studiensekretariat/immatrikulation.html#semesterbeitrag",
    "OUTPUT_DIR": "THM_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # PDF 匹配正则 (针对 Type 1 侧边栏)
    "PDF_PO_REGEX": r"Prüfungsordnung|Examination.*regulations",
    # 穿透页面关键词 (Type 2 标识符)
    "LINK_ZUGANG": "Zugangsvoraussetzungen",
    "LINK_PO_MH": "Prüfungsordnung und Modulhandbuch"
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
        limit = 50 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_html_node(element, base_url):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe',
                 'noscript']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)
        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and not href.startswith(('http', 'mailto', '#')):
                a.set('href', urljoin(base_url, href))
        raw_html = etree.tostring(el, encoding='unicode', method='html')
        raw_html = raw_html.replace('\xad', '').replace('&shy;', '')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        content = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()

    @staticmethod
    def find_main_landmark(tree):
        """
        指定标签id：兼容 Plone, FSZ, JLU 联动页面等异构结构
        """
        if tree is None: return None
        for xp in (
                '//*[@role="main"]',
                '//main',
                '//section[@id="portal-column-content"]',
                '//article[@id="content"]',
                '//div[@id="content"]'
        ):
            nodes = tree.xpath(xp)
            if nodes: return nodes[0]
        return None


class THMScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})
        self.semester_fees_info = ""

    async def run(self):
        #  预取通用学期费信息
        self.fetch_semester_fees()

        # Playwright 抓取列表
        tasks = await self.fetch_list_via_playwright()
        if not tasks:
            print(" 无法获取专业列表，请检查网络或交互逻辑 ")
            return

        print(f"  捕捉成功：共锁定 {len(tasks)} 个硕士专业 开始采集")

        # Phase 2: Requests 详情采集
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 挖掘：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.2))

    def fetch_semester_fees(self):
        print("  预取通用学期费信息")
        try:
            res = self.session.get(CONFIG["FEES_URL"], timeout=20)
            tree = etree.HTML(res.text)
            fee_anchor = tree.xpath('//a[@id="slider-semesterbeitrag"]')
            if fee_anchor:
                target = fee_anchor[0].getparent().getparent()
                self.semester_fees_info = "\n\n---\n### Universal Information: Semesterbeitrag\n\n" + \
                                          CrawlerUtils.to_markdown(
                                              CrawlerUtils.clean_html_node(target, CONFIG["FEES_URL"]))
                print("     缓存就绪 ")
        except:
            pass

    async def fetch_list_via_playwright(self):
        collected = []
        async with async_playwright() as p:
            print("  启动浏览器处理 Master 筛选")
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(user_agent=CONFIG["USER_AGENT"])
            page = await context.new_page()
            try:
                await page.goto(CONFIG["LIST_URL"], wait_until="networkidle")
                # 点击 Master label 触发 onchange
                await page.locator('label[for="abschluss_2"]').click()
                await asyncio.sleep(3)
                await page.wait_for_selector('#thm-courses-table tbody tr', timeout=15000)

                content = await page.content()
                tree = etree.HTML(content)
                rows = tree.xpath('//table[@id="thm-courses-table"]/tbody/tr')
                for row in rows:
                    link_node = row.xpath('.//td[contains(@class, "thm-title")]//a')
                    if link_node:
                        name = "".join(link_node[0].xpath('.//text()')).strip()
                        href = link_node[0].get('href')
                        meta_campus = "".join(row.xpath('.//td[@data-label="Campus"]//div/text()')).strip()
                        meta_degree = "".join(row.xpath('.//td[@data-label="Abschluss"]//div/text()')).strip()
                        collected.append({
                            "name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "meta": f"Degree: {meta_degree} | Campus: {meta_campus}"
                        })
                await browser.close()
            except Exception as e:
                print(f"     列表抓取失败: {e}")
                await browser.close()
        return collected

    def process_major(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过： {safe_name}");
                return

            res = self.session.get(task["url"], timeout=30)
            res.encoding = res.apparent_encoding or "utf-8"
            tree = etree.HTML(res.text)
            comp_div = tree.xpath('//div[@role="complementary"]')
            main_el = CrawlerUtils.find_main_landmark(tree)

            os.makedirs(major_dir, exist_ok=True)
            final_md_parts = []
            page_handled = False

            # 优先探测 Type 2
            has_type2_markers = False
            if comp_div:
                comp_text = "".join(comp_div[0].xpath('.//text()'))
                if CONFIG["LINK_ZUGANG"] in comp_text and CONFIG["LINK_PO_MH"] in comp_text:
                    has_type2_markers = True

            if has_type2_markers:
                print("      识别为 Type 2: 穿透分层页面")
                #  穿透 Zugangsvoraussetzungen
                z_link = comp_div[0].xpath(f'.//a[.//span[contains(text(), "{CONFIG["LINK_ZUGANG"]}")]]/@href')
                if z_link:
                    z_url = urljoin(task['url'], z_link[0])
                    z_tree = etree.HTML(self.session.get(z_url).text)
                    z_main = CrawlerUtils.find_main_landmark(z_tree)
                    if z_main is not None:
                        final_md_parts.append(f"## Admission Requirements\nSource: {z_url}\n\n")
                        final_md_parts.append(CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(z_main, z_url)))

                #  穿透 Prüfungsordnung (执行二级跳，保证一定可以定位到对应pdf)
                p_link = comp_div[0].xpath(f'.//a[.//span[contains(text(), "{CONFIG["LINK_PO_MH"]}")]]/@href')
                if p_link:
                    p_url = urljoin(task['url'], p_link[0])
                    p_tree = etree.HTML(self.session.get(p_url).text)
                    p_main = CrawlerUtils.find_main_landmark(p_tree)
                    if p_main is not None:
                        # 严格文本匹配：寻找通往下载中转页的链接
                        strict_a = p_main.xpath('.//a[normalize-space(text())="Prüfungsordnung"]')
                        if strict_a:
                            jump_url = urljoin(p_url, strict_a[0].get('href'))
                            # 二级跳：进入真正的下载详情页
                            last_tree = etree.HTML(self.session.get(jump_url).text)
                            last_main = CrawlerUtils.find_main_landmark(last_tree)
                            if last_main is not None:
                                # 在最终页面寻找包含关键字的 PDF
                                real_pdf = last_main.xpath('.//a[contains(text(), "Prüfungsordnung")]/@href')
                                if real_pdf:
                                    self.download_pdf(urljoin(jump_url, real_pdf[0]), major_dir)
                page_handled = True

            # 探测 Type 1 (标准侧栏 PDF 模式)
            if not page_handled and comp_div:
                found_po_pdf = None
                for a in comp_div[0].xpath('.//a'):
                    txt = "".join(a.xpath('.//text()')).strip()
                    if re.search(CONFIG["PDF_PO_REGEX"], txt, re.IGNORECASE):
                        found_po_pdf = urljoin(task['url'], a.get('href'))
                        break

                if found_po_pdf:
                    print("      识别为 Type 1: 标准侧栏PDF页面")
                    self.download_pdf(found_po_pdf, major_dir)
                    # 抓取折叠页 slider-bewerbung
                    slider = tree.xpath('//a[@id="slider-bewerbung"]')
                    if slider:
                        target = slider[0].getparent().getparent()
                        final_md_parts.append(
                            CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(target, task['url'])))
                    elif main_el is not None:
                        final_md_parts.append(
                            CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(main_el, task['url'])))
                    page_handled = True

            # 降级保底 (Type 3: 异构/联动页面)
            if not page_handled:
                print("      识别为 Type 3: 联动/异构保底提取")
                if main_el is not None:
                    final_md_parts.append(CrawlerUtils.to_markdown(CrawlerUtils.clean_html_node(main_el, task['url'])))
                else:
                    print(f"        失败！无法在页面找到内容区域: {task['url']}")

            # 归档
            final_md = "".join(final_md_parts) + self.semester_fees_info
            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n**{task['name']} | {task['meta']}**\n\n---\n\n{final_md}")
            print(f"    成功， 数据归档 ")

        except Exception as e:
            print(f"    失败， {task['name']} 异常: {e}")

    def download_pdf(self, url, folder):
        """流式下载并具备 Content-Type 预检防误抓 HTML"""
        try:
            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                # 隔绝 HTML 伪装成 PDF
                ctype = res.headers.get('Content-Type', '').lower()
                if 'html' in ctype: return

                url_parts = url.split('/')
                fname = url_parts[-2] if "download.html" in url_parts[-1] else url_parts[-1].split('.')[0]
                filename = f"Regulation_{CrawlerUtils.sanitize_path(fname, False)}.pdf"

                with open(os.path.join(folder, filename), 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF下载成功 {filename}")
        except:
            pass


if __name__ == "__main__":
    scraper = THMScraper()
    asyncio.run(scraper.run())