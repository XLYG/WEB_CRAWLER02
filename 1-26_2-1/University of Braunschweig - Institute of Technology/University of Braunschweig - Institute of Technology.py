import requests
from bs4 import BeautifulSoup, NavigableString
import os
import re
import time
from urllib.parse import urljoin

BASE_LIST_URL = "https://www.tu-braunschweig.de/en/degree-programmes?tx_kesearch_pi1%5Bsword%5D=&tx_kesearch_pi1%5Bfilter_5%5D=abschlussmaster&tx_kesearch_pi1%5Bfilter_6%5D=&tx_kesearch_pi1%5Bfilter_7%5D=&tx_kesearch_pi1%5Bfilter_35%5D=&tx_kesearch_pi1%5Bfilter_23%5D=&tx_kesearch_pi1%5Bfilter_85%5D=&id=2973&tx_kesearch_pi1%5Bpage%5D={page}&tx_kesearch_pi1%5BresetFilters%5D=0&tx_kesearch_pi1%5BsortByField%5D=&tx_kesearch_pi1%5BsortByDir%5D="
DOMAIN = "https://www.tu-braunschweig.de"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
# 缩短根目录名称以释放路径空间
ROOT_DIR = "TUBS_Archive"

if not os.path.exists(ROOT_DIR):
    os.makedirs(ROOT_DIR)


class TUBS_ResilientScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def sanitize(self, name, max_length=50):
        """
        移除 Windows 禁用字符： \ / : * ? " < > |
        限制长度防止路径溢出
        """
        clean = re.sub(r'[\\/*?:"<>|]', "_", name)
        clean = re.sub(r'\.+', '.', clean).strip()
        # 设置最大长度
        if len(clean) > max_length:
            clean = clean[:max_length].strip()
        return clean

    def download_pdf(self, url, folder, filename):
        # 下载pdf，带路径长度保护
        try:
            full_url = urljoin(DOMAIN, url)
            # 清洗并缩短文件名
            safe_filename = self.sanitize(filename, max_length=60)
            if not safe_filename.lower().endswith(".pdf"):
                safe_filename += ".pdf"

            # 最终物理路径
            path = os.path.join(folder, safe_filename)

            res = self.session.get(full_url, stream=True, timeout=30)
            res.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192) if hasattr((r := res), 'iter_content') else res:
                    f.write(chunk)
            return True
        except Exception as e:
            print(f"      下载失败: {url} -> {e}")
            return False

    def crawl_documents_page(self, url, program_folder):
        """解析 Documents 页并创建层级子目录"""
        print(f"    深入层级文档页: {url}")
        try:
            res = self.session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')

            # 定位折叠页
            accordions = soup.find_all("div", class_="accordion")
            for acc in accordions:
                header = acc.find("a", class_="accordion__header")
                if not header: continue

                # 缩短子文件夹名
                sub_folder_name = self.sanitize(header.get_text(strip=True), max_length=40)
                sub_folder_path = os.path.join(program_folder, sub_folder_name)

                # 确保子目录存在
                os.makedirs(sub_folder_path, exist_ok=True)

                content_div = acc.find("div", class_="accordion__content")
                if content_div:
                    pdf_links = content_div.find_all("a", href=True)
                    for pdf in pdf_links:
                        href = pdf['href']
                        # 仅抓取 PDF
                        if ".pdf" in href.lower() or "action-link--download" in pdf.get('class', []):
                            raw_name = pdf.get_text(strip=True) or href.split("/")[-1]
                            # 过滤掉无效的空名称
                            if not raw_name.strip(): continue

                            print(f"      正在下载PDF: {raw_name[:30]}...")
                            self.download_pdf(href, sub_folder_path, raw_name)
        except Exception as e:
            print(f"    无法解析文档页: {url} -> {e}")

    def element_to_markdown(self, element):
        """递归 HTML 转 Markdown，排除冗余块"""
        if element is None: return ""
        if isinstance(element, NavigableString):
            return str(element)

        tag = element.name
        # 排除 Contact 和 More Information
        if tag in ['h2', 'h3']:
            text = element.get_text().lower()
            if any(k in text for k in ["contact", "more information", "related links", "your contact"]):
                return "---EXCLUDE---"

        content = ""
        for child in element.children:
            child_md = self.element_to_markdown(child)
            if child_md == "---EXCLUDE---": return ""
            content += child_md

        if tag in ['h1', 'h2', 'h3']:
            return f"\n\n{'#' * int(tag[1])} {content.strip()}\n"
        elif tag == 'p':
            return f"\n{content.strip()}\n"
        elif tag == 'a':
            href = element.get('href')
            return f" [{content.strip()}]({urljoin(DOMAIN, href)}) " if href else content
        elif tag in ['strong', 'b']:
            return f"**{content.strip()}**"
        elif tag == 'li':
            return f"\n- {content.strip()}"
        else:
            return content

    def process_detail_page(self, url):
        """详情页处理主函数"""
        try:
            res = self.session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')

            title_tag = soup.find("h1")
            if not title_tag: return

            raw_title = title_tag.get_text(strip=True)
            # 缩短专业名文件夹
            safe_title = self.sanitize(raw_title, max_length=40)
            program_dir = os.path.join(ROOT_DIR, safe_title)
            os.makedirs(program_dir, exist_ok=True)

            print(f"\n正在解析专业: {raw_title}")

            # 1. 寻找文档页链接 (Admission Regulations)
            reg_link = None
            # 寻找包含 admission regulations 文本且属于该模块的链接
            reg_a = soup.find("a", href=True, string=re.compile(r"admission regulations", re.I))
            if reg_a:
                reg_link = urljoin(DOMAIN, reg_a['href'])

            # 2. 抓取层级 PDF
            if reg_link:
                self.crawl_documents_page(reg_link, program_dir)

            # 3. 保存 Markdown
            # 定位主体内容
            content_main = soup.find("div", class_="content-main") or soup.find("main")
            if content_main:
                md_text = self.element_to_markdown(content_main)
                # 移除由于排除块导致的空行
                md_text = re.sub(r'\n\s*\n', '\n\n', md_text)
                with open(os.path.join(program_dir, f"{safe_title}.md"), "w", encoding="utf-8") as f:
                    f.write(f"# {raw_title}\n\nURL: {url}\n\n{md_text}")

            print(f"    [OK] 详情抓取完毕。")

        except Exception as e:
            print(f"  [Error] 详情页崩溃: {url} -> {e}")

    def run(self):
        page = 1
        while True:
            print(f"列表扫描: 第 {page} 页")
            try:
                res = self.session.get(BASE_LIST_URL.format(page=page), timeout=20)
                soup = BeautifulSoup(res.text, 'html.parser')
                teasers = soup.find_all("div", class_="search-teaser")
                if not teasers: break

                for teaser in teasers:
                    a = teaser.find("a", href=True)
                    if a:
                        self.process_detail_page(urljoin(DOMAIN, a['href']))
                        time.sleep(1)
                page += 1
            except Exception as e:
                print(f"列表页错误: {e}")
                break


if __name__ == "__main__":
    bot = TUBS_ResilientScraper()
    bot.run()