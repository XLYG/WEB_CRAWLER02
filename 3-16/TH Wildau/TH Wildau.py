import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "TH_Wildau",
    "ROOT_DOMAIN": "https://www.th-wildau.de",
    "LIST_URL": "https://www.th-wildau.de/studieren-und-weiterbilden-1/studiengaenge",
    "ADMISSION_FALLBACK_URL": "https://www.th-wildau.de/studieren-und-weiterbilden-1/bewerbung/zugangsvoraussetzungen",
    "OUTPUT_DIR": "TH_Wildau_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # 终止哨兵关键词
    "SENTINEL_STOP_H3": ["Wissenswertes zum Studium", "Sustainable Development Goals",
                         "Ziele für Nachhaltige Entwicklung"],
    "SENTINEL_STOP_SMALL": "Verantwortlich für diese Seite:",
    # 通用 PDF 正则
    "PDF_PATTERN": r"Studien-.*Prüfungsordnung|Module.*handbook|Zugangsordnung|Examination.*regulations",
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
    def clean_element(element, strip_tabs=False):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)

        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe',
                 'noscript']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        # 删除 data-tab-content 且 style 包含 display: none 的学生选项卡
        if strip_tabs:
            hidden_panels = el.xpath('.//*[@data-tab-content and contains(@style, "display: none")]')
            for panel in hidden_panels:
                p = panel.getparent()
                if p is not None: p.remove(panel)

        # 补全链接
        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        raw_html = etree.tostring(el, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_list):
        full_html = "".join(html_list)
        if not full_html: return ""
        content = md(full_html, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class THWildauScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})
        self.admission_fallback_content = ""

    def run(self):
        self.fetch_fallback_admission()
        tasks = self.fetch_list_via_requests()
        if not tasks:
            print(" 无法获取专业列表")
            return

        print(f"  成功：已锁定 {len(tasks)} 个硕士专业。")

        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_fallback_admission(self):
        try:
            res = self.session.get(CONFIG["ADMISSION_FALLBACK_URL"], timeout=20)
            tree = etree.HTML(res.text)
            node = tree.xpath('//*[@id="page-start"]/main/div[2]/div/div/div[1]/div[6]/div/div/div[2]')
            if node:
                self.admission_fallback_content = CrawlerUtils.to_markdown([CrawlerUtils.clean_element(node[0])])
                print("  通用入学要求背景已就绪")
        except:
            pass

    def fetch_list_via_requests(self):
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)
            # 动态探测 Master 列表 ID
            target_id = ""
            for a in tree.xpath('//ul[contains(@class, "c-m-tabs__navigation")]//a'):
                if "Master" in "".join(a.xpath('.//text()')):
                    target_id = a.get('data-tab', '').replace('#', '')
                    break

            final_id = target_id if target_id else "c-m-tabs__element_85487_85493"
            master_panel = tree.xpath(f'//*[@id="{final_id}"]')
            if not master_panel: return []

            rows = master_panel[0].xpath('.//div[contains(@class, "wrap-inner")][1]//table//tr')
            tasks = []
            for row in rows:
                link = row.xpath('.//td[1]//a')
                if link:
                    href = link[0].get('href')
                    name = "".join(link[0].xpath('.//text()')).strip()
                    if href and name:
                        tasks.append({"name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href)})
            return tasks
        except:
            return []

    def process_detail(self, task):
        """核心详情处理：基于面板重定位的线性提取"""
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过 {safe_name}");
                return

            res = self.session.get(task["url"], timeout=30)
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)
            main_node = tree.xpath('//main')
            if not main_node: return
            main_node = main_node[0]

            # 净化前全文正则扫描获取PDF
            pdf_url = None
            for a in main_node.xpath('.//a'):
                lt = "".join(a.xpath('.//text()')).strip()
                if re.search(CONFIG["PDF_PATTERN"], lt, re.IGNORECASE):
                    pdf_url = urljoin(task['url'], a.get('href'))
                    break

            # 确定真实的起始元素
            start_el = None
            # 探测：激活的选项卡 (准学生)
            t1_btn = main_node.xpath(
                './/a[contains(@class, "tab-active") and (contains(text(), "Studieninteressierte") or contains(@aria-label, "准学生"))]')
            if t1_btn:
                # 关键：提取 data-tab 绑定的内容面板 ID，直接跳到该面板作为起点
                panel_id = t1_btn[0].get('data-tab', '').replace('#', '')
                panel_node = main_node.xpath(f'//*[@id="{panel_id}"]')
                if panel_node:
                    start_el = panel_node[0]

            # 若不是选项卡，探测 Type 2 起点
            if start_el is None:
                t2_h2 = main_node.xpath(
                    './/h2[contains(text(), "Herzlich Willkommen") or contains(text(), "Auf einen Blick")]')
                if t2_h2: start_el = t2_h2[0]

            # 保底起点为 main 本身
            if start_el is None: start_el = main_node

            # 线性顶级块扫描 (带哨兵保护)
            collected_html = []
            top_level_blocks = main_node.xpath('./*')

            # 定位起点所在的顶级块索引
            start_idx = 0
            temp = start_el
            target_block = None
            while temp is not None:
                if temp.getparent() == main_node:
                    target_block = temp;
                    break
                temp = temp.getparent()

            if target_block is not None:
                try:
                    start_idx = top_level_blocks.index(target_block)
                except:
                    pass

            found_stop = False
            for i in range(start_idx, len(top_level_blocks)):
                curr_block = top_level_blocks[i]

                # 核心修正：哨兵判定必须跳过“起始块”本身，防止误触发导致内容为空
                if i > start_idx and self.is_sentinel_strict(curr_block):
                    print(f"    -> 命中哨兵，停止切片。")
                    break

                # 执行物理净化，此处执行双属性隐藏剔除
                snippet = CrawlerUtils.clean_element(curr_block, strip_tabs=True)
                if snippet:
                    if snippet not in "".join(collected_html):
                        collected_html.append(snippet)

            # 归档与 PDF 保存
            os.makedirs(major_dir, exist_ok=True)
            has_pdf = False
            if pdf_url:
                url_id = pdf_url.split('/')[-1].split('.')[0][-8:]
                pdf_name = f"Regulation_{url_id}.pdf"
                self.download_pdf(pdf_url, major_dir, pdf_name)
                has_pdf = True

            final_md = CrawlerUtils.to_markdown(collected_html)
            # 如果没下到 PDF，追加通用要求
            if not has_pdf and self.admission_fallback_content:
                final_md += "\n\n---\n### General Admission Requirements (Fallback)\n\n" + self.admission_fallback_content

            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n# {task['name']}\n\n{final_md}")
            print(f"    成功, 数据归档。")

        except Exception as e:
            print(f"    失败, {task['name']} 处理异常: {e}")

    def is_sentinel_strict(self, block):
        """仅检测特定的 H3 标题或 Small 署名"""
        # 检查特定的 H3 标题
        h3s = block.xpath('.//h3')
        for h3 in h3s:
            t = "".join(h3.xpath('.//text()')).strip().lower()
            if any(kw.lower() in t for kw in CONFIG["SENTINEL_STOP_H3"]): return True
        # 检查特定 Small 标签
        smalls = block.xpath('.//small')
        for sm in smalls:
            t = "".join(sm.xpath('.//text()')).strip().lower()
            if CONFIG["SENTINEL_STOP_SMALL"].lower() in t: return True
        return False

    def download_pdf(self, url, folder, filename):
        try:
            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                with open(os.path.join(folder, filename), 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF获取成功 {filename}")
        except:
            pass


if __name__ == "__main__":
    THWildauScraper().run()
