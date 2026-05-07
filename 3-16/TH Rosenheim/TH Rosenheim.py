import os
import re
import time
import random
import requests
import unicodedata
from datetime import datetime
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "TH_Rosenheim",
    "ROOT_DOMAIN": "https://www.th-rosenheim.de",
    "START_URL": "https://www.th-rosenheim.de/studium-und-weiterbildung/studienangebot-der-th-rosenheim/masterstudiengaenge",
    "OUTPUT_DIR": "TH_Rosenheim_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # PDF 匹配正则：支持 &shy; 处理
    "PDF_KEYWORD_REGEX": r"Studien-.*und.*Prüfungs.*ordnung",
    # 终止哨兵关键词 (仅在清洗后的 H2 标签中匹配)
    "SENTINEL_STOP_H2": ["Kontakt", "Du hast noch Fragen?", "Further Information"]
}

GERMAN_MONTHS = {
    "januar": "1", "februar": "2", "märz": "3", "april": "4", "mai": "5", "juni": "6",
    "juli": "7", "august": "8", "september": "9", "oktober": "10", "november": "11", "dezember": "12"
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
    def clean_element(element):
        if element is None: return None
        import copy
        el = copy.deepcopy(element)

        noise = ['aside', 'blockquote', 'img', 'video', 'picture', 'svg', 'button', 'nav', 'script', 'style', 'iframe']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        return el

    @staticmethod
    def to_markdown(cleaned_nodes):
        if not cleaned_nodes: return ""
        full_html = "".join([etree.tostring(n, encoding='unicode', method='html') for n in cleaned_nodes])
        full_html = full_html.replace('&shy;', '').replace('\xad', '')

        content = md(full_html, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()

    # 清洗其中遇到的日期为标准类型，方便之后进行比对
    @staticmethod
    def parse_german_date(date_str):
        date_str = date_str.lower().strip()
        # 格式：21.02.2024
        match_std = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', date_str)
        if match_std:
            return datetime(int(match_std.group(3)), int(match_std.group(2)), int(match_std.group(1)))
        # 格式：2Juli 2018
        match_text = re.search(r'(\d{1,2})\.\s+([a-zä]+)\s+(\d{4})', date_str)
        if match_text:
            day = int(match_text.group(1))
            month = int(GERMAN_MONTHS.get(match_text.group(2), 1))
            return datetime(int(match_text.group(3)), month, day)
        return datetime(1900, 1, 1)


class THRosenheimScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_major_list()
        if not tasks:
            print(" 无法锁定列表页内容 ")
            return

        print(f" 成功捕捉 {len(tasks)} 个专业 开始详情采集")

        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 挖掘：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_major_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["START_URL"], timeout=30)
            tree = etree.HTML(res.text)
            items = tree.xpath('//li[contains(@class, "education-offer")]')
            for item in items:
                name = "".join(item.xpath('.//p[contains(@class, "__title")]/text()')).strip()
                href = item.xpath('.//a[contains(@class, "__link")]/@href')
                degree = "".join(item.xpath('.//p[contains(@class, "__degree")]/text()')).strip()
                info_nodes = item.xpath('.//ul[contains(@class, "__info")]/li/text()')
                info_text = " | ".join([it.strip() for it in info_nodes if it.strip()])
                if href and name:
                    collected.append({
                        "name": name,
                        "url": urljoin(CONFIG["ROOT_DOMAIN"], href[0]),
                        "meta": f"{degree} | {info_text}"
                    })
        except Exception as e:
            print(f"     列表页解析异常: {e}")
        return collected

    def process_detail(self, task):
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

            # PDF 审计 (净化前，全局扫描)
            pdf_candidates = []
            for a in main_node.xpath('.//a'):
                link_text = "".join(a.xpath('.//text()')).replace('\xad', '').strip()
                if re.search(CONFIG["PDF_KEYWORD_REGEX"], link_text, re.IGNORECASE):
                    href = a.get('href')
                    if href:
                        dt = CrawlerUtils.parse_german_date(link_text)
                        pdf_candidates.append({"url": urljoin(task['url'], href), "date": dt})

            best_pdf = max(pdf_candidates, key=lambda x: x["date"]) if pdf_candidates else None

            # 增强型块级提取
            blocks_to_scan = []
            # 首先获取 header-banner
            banner = main_node.xpath('./div[contains(@class, "header-banner")]')
            if banner: blocks_to_scan.append(banner[0])

            # 然后尝试深入探测 article 内部的 section (内容主体)
            # 这样就把巨大的 sidebar-grid 拆解成了多个独立的 section 块
            # 在这个多统计块中线性获取-熔断
            sections = main_node.xpath('.//article/*')
            if sections:
                blocks_to_scan.extend(sections)
            else:
                # 保底：如果不存在 article，则按原样获取 main 下的直接子元素
                blocks_to_scan.extend(main_node.xpath('./*'))

            collected_cleaned_nodes = []
            found_stop = False

            for block in blocks_to_scan:
                # 先清洗当前块 (移除 aside 以防其中的关键词干扰哨兵判定)
                cleaned_block = CrawlerUtils.clean_element(block)
                if cleaned_block is None: continue

                # 在清洗后的结果中检查哨兵 H2
                h2s = cleaned_block.xpath('.//h2')
                if cleaned_block.tag == 'h2': h2s.append(cleaned_block)

                for h2 in h2s:
                    h2_text = "".join(h2.xpath('.//text()')).strip()
                    if any(kw.lower() in h2_text.lower() for kw in CONFIG["SENTINEL_STOP_H2"]):
                        print(f"    命中哨兵停止点 [{h2_text.strip()}]")
                        found_stop = True
                        break

                if found_stop: break

                collected_cleaned_nodes.append(cleaned_block)

            # 归档
            os.makedirs(major_dir, exist_ok=True)
            if best_pdf:
                url_id = best_pdf["url"].split('/')[-1].split('.')[0][-8:]
                self.download_pdf(best_pdf["url"], major_dir, f"Regulation_{url_id}.pdf")

            final_md = CrawlerUtils.to_markdown(collected_cleaned_nodes)
            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n**{task['name']} | {task['meta']}**\n\n---\n\n{final_md}")
            print(f"    成功 数据归档 ")

        except Exception as e:
            print(f"    失败 {task['name']} 处理异常: {e}")

    def download_pdf(self, url, folder, filename):
        try:
            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                with open(os.path.join(folder, filename), 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF捕捉成功 {filename}")
        except:
            pass


if __name__ == "__main__":
    THRosenheimScraper().run()