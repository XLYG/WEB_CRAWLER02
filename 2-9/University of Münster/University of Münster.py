import os
import requests
import re
import time
import random
from lxml import etree
from markdownify import markdownify as md
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

START_URL = "https://www.uni-muenster.de/ZSB/studienfuehrer/"
BASE_URL = "https://www.uni-muenster.de"
ROOT_DIR = "Uni_Muenster_Data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8",
}


def create_session():
    session = requests.Session()
    session.trust_env = True
    retry_strategy = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    return session


session = create_session()


def sanitize(name):
    if not name: return "Unknown"
    name = re.sub(r'<[^>]+>', '', str(name))
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return name[:120]


def clean_to_md(html_node):
    if html_node is None: return ""
    html_str = etree.tostring(html_node, encoding='unicode', method='html')
    html_str = re.sub(r'<img.*?>', '', html_str, flags=re.DOTALL)
    html_str = re.sub(r'</?font.*?>', '', html_str, flags=re.IGNORECASE)
    return md(html_str, heading_style="ATX").strip()


def safe_get(url):
    time.sleep(random.uniform(0.6, 1.2))
    try:
        res = session.get(url, headers=HEADERS, timeout=30)
        res.encoding = res.apparent_encoding
        res.raise_for_status()
        return res
    except Exception as e:
        print(f"       {url} | {e}")
        return None


def download_pdf(url, folder, filename):
    """执行PDF下载"""
    path = os.path.join(folder, sanitize(filename) + ".pdf")
    if os.path.exists(path): return

    print(f"       正在保存 PDF: {filename[:30]}...")
    res = safe_get(url)
    if res and res.status_code == 200:
        with open(path, 'wb') as f:
            f.write(res.content)
        print(f"      PDF文件已下载")


def process_regulation_page(reg_url, folder):
    """
    访问准入规定子页面并提取PDF。
    """
    res = safe_get(reg_url)
    if not res: return

    tree = etree.HTML(res.text)
    # 锁定 class="download" 的 PDF 链接
    pdf_links = tree.xpath('//a[contains(@class, "download") and contains(@href, ".pdf")]')

    for link in pdf_links:
        pdf_href = urljoin(reg_url, link.get('href'))
        pdf_title = "".join(link.xpath('.//text()')).strip()
        if not pdf_title: pdf_title = "Admission_Regulations"
        download_pdf(pdf_href, folder, pdf_title)


def process_major_detail(detail_url, folder, major_display_name):
    """提取2个卡片 + 子页PDF获取"""
    md_file_path = os.path.join(folder, f"{sanitize(major_display_name)}.md")

    res = safe_get(detail_url)
    if not res: return
    tree = etree.HTML(res.text)

    # 提取前两个 Card 内容
    cards = tree.xpath('//section[@id="inhalt"]//div[contains(@class, "card")][position() <= 2]')
    if cards:
        md_output = [f"# {major_display_name}\n"]
        for card in cards:
            md_output.append(clean_to_md(card))

        with open(md_file_path, 'w', encoding='utf-8') as f:
            f.write("\n\n---\n\n".join(md_output))
        print(f"     原生态 MD 已生成")

        # 找“准入/入学规定”链接进行深度爬取
        # 关键词对照：准入 (Zugang), 入学 (Einschreibung), 规定 (Ordnung)
        reg_links = tree.xpath(
            '//ul[contains(@class, "linklistestudienfuehrer")]//a[contains(text(), "Zugang") or contains(text(), "准入")]')

        for r_link in reg_links:
            reg_url = urljoin(detail_url, r_link.get('href'))
            print(f"     发现准入规定页面，正在获取...")
            process_regulation_page(reg_url, folder)
    else:
        print(f"     详情页结构偏移，未发现 card 模块")


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)

    print(f"正在抓取明斯特大学专业列表...")
    res = safe_get(START_URL)
    if not res: return

    tree = etree.HTML(res.text)
    table = tree.xpath('//table[contains(@summary, "Studiengänge")]')
    if not table: return

    rows = table[0].xpath('.//tbody/tr')
    print(f" 发现 {len(rows)} 个专业行")

    processed_count = 0
    for row in rows:
        subject_name = row.xpath('string(./td[1])').strip()
        master_links = row.xpath('./td[3]//a[@class="int"]')

        if not master_links: continue

        for link in master_links:
            degree_label = link.xpath('string(.)').strip()
            detail_url = urljoin(BASE_URL, link.get('href'))

            full_name = f"{subject_name}_{degree_label}"
            major_dir = os.path.join(ROOT_DIR, sanitize(full_name))
            os.makedirs(major_dir, exist_ok=True)

            print(f"\n>>> [{processed_count + 1}] 处理项目: {full_name}")
            try:
                process_major_detail(detail_url, major_dir, full_name)
                processed_count += 1
            except Exception as e:
                print(f"  错误: {e}")

    print(f"\n累计抓取专业数: {processed_count}")


if __name__ == "__main__":
    main()