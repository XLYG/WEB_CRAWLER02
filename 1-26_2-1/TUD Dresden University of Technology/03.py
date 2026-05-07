import requests
from bs4 import BeautifulSoup, NavigableString
import os
import re
import time
from urllib.parse import urljoin

BASE_URL_TEMPLATE = "https://tu-dresden.de/studium/vor-dem-studium/studienangebot/sins/sins_start_results?abschluss=3&studienart=1&studienform=1&b_start:int={offset}"
DOMAIN = "https://tu-dresden.de"
SAVE_DIR = "TU_Dresden_Master_Final"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)


class TUDresdenPrecisionScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def normalize_string(self, text):
        if not text: return ""
        return re.sub(r'\s+', ' ', text).strip()

    def element_to_markdown(self, element, current_page_url):
       # current_page_url 确保了 a 标签拼接时的路径正确性
        if element is None: return ""
        if isinstance(element, NavigableString):
            return str(element)

        tag = element.name
        content = "".join(self.element_to_markdown(child, current_page_url) for child in element.children)

        if tag in ['h1', 'h2', 'h3', 'h4']:
            return f"\n\n{'#' * int(tag[1])} {content.strip()}\n"
        elif tag == 'p':
            return f"\n{content.strip()}\n"
        elif tag == 'li':
            return f"\n- {content.strip()}"
        elif tag in ['strong', 'b']:
            return f"**{content.strip()}**"
        elif tag == 'a':
            href = element.get('href')
            # 关键修复点：使用当前页面 URL 作为 Base，自动补全中间路径
            full_url = urljoin(current_page_url, href) if href else ""
            return f" [{content.strip()}]({full_url}) "
        elif tag == 'br':
            return "\n"
        else:
            return content

    def get_sub_page_content_direct(self, url):
        # 直接定位 #main article 并获取文字
        print(f"    [>>> 穿透抓取] 进入二级页面: {url}")
        try:
            res = self.session.get(url, timeout=15)
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'html.parser')
            # 锁定目标容器
            article_box = soup.select_one("#main article")
            if article_box:
                # 子页面内部的链接也需要基于子页面 URL 拼接，这里暂传子页面 URL
                return self.element_to_markdown(article_box, url)
            return "*(二级页面未发现正文容器 #main article)*"
        except Exception as e:
            return f"*(无法访问二级页面: {e})*"

    def fetch_content_after_anchor(self, soup, anchor_id, current_page_url, deep_crawl=False):
        # 扫描锚点后的兄弟节点
        collected_md = ""
        anchor = soup.find(id=anchor_id)
        if not anchor:
            return ""

        for sibling in anchor.find_next_siblings():
            if sibling.name in ['h1', 'h2', 'h3', 'h4']:
                break

            collected_md += self.element_to_markdown(sibling, current_page_url)

            if deep_crawl:
                links = sibling.find_all("a", href=True)
                for a in links:
                    href = a['href']
                    # 识别是否为详情跳转链接
                    if "sins_eignungsfeststellung_detail" in href or "autoid=" in href:
                        # 关键点：使用当前页面 URL 拼接链接
                        sub_url = urljoin(current_page_url, href)
                        sub_content = self.get_sub_page_content_direct(sub_url)
                        collected_md += f"\n\n--- \n> ### {a.get_text(strip=True)}\n> **URL**: {sub_url}\n\n{sub_content}\n---\n"

        return collected_md

    def get_links(self):
        links = []
        for offset in range(0, 72, 9):
            url = BASE_URL_TEMPLATE.format(offset=offset)
            print(f"扫描分页 Offset: {offset}")
            res = self.session.get(url)
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.select("#sins-results a.teaser"):
                href = a.get('href')
                if href: links.append(urljoin(DOMAIN, href))
        return list(dict.fromkeys(links))

    def process_program(self, url):
        try:
            # 这里的 allow_redirects 很重要，获取重定向后的最终地址
            res = self.session.get(url, timeout=20, allow_redirects=True)
            final_url = res.url  # 获取最终的完整地址用于路径拼接
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'html.parser')

            h1 = soup.select_one("h1.highlight-quick-info")
            p_name = self.normalize_string(
                h1.get_text(separator=" ").replace("Studiengang", "").replace("Degree program", ""))
            print(f"\n正在执行 {p_name}")

            # Quick Info
            quick_info = {}
            for dl in soup.select("dl.quick-info-list"):
                for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                    quick_info[self.normalize_string(dt.get_text())] = self.normalize_string(dd.get_text())

            md = f"# {p_name}\n\n**原始页面**: {final_url}\n\n## 1. Quick Info\n\n"
            targets = {"Abschluss": "Abschluss", "Regelstudienzeit": "Regelstudienzeit", "Unterrichtssprache": "Unterrichtssprache",
                       "Studienbeginn": "Studienbeginn"}
            for k, label in targets.items():
                val = next((v for kn, v in quick_info.items() if k in kn), "N/A")
                md += f"- **{label}**: {val}\n"

            # 指定目标板块
            segments = [
                ("admission_req", "Admission Requirements", False),
                ("eignung", "Aptitude Assessment Procedure", True),
                ("gen_inf", "General Information", False),
                ("study_con", "Study Contents", False)
            ]

            for anchor_id, title, deep in segments:
                section_data = self.fetch_content_after_anchor(soup, anchor_id, final_url, deep_crawl=deep)
                if section_data:
                    md += f"\n## {title}\n{section_data}"

            # 保存
            safe_name = re.sub(r'[\\/*?:"<>|]', "_", p_name)[:80]
            with open(os.path.join(SAVE_DIR, f"{safe_name}.md"), "w", encoding="utf-8") as f:
                f.write(md)
            print(f"    {safe_name}.md 已生成")

        except Exception as e:
            print(f"    {url}: {e}")


if __name__ == "__main__":
    bot = TUDresdenPrecisionScraper()
    all_links = bot.get_links()

    for link in all_links[:]:
        bot.process_program(link)
        time.sleep(1)