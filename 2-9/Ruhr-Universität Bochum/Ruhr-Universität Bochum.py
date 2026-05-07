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

BASE_URL = "https://studienangebot.ruhr-uni-bochum.de"
LIST_URL = "https://studienangebot.ruhr-uni-bochum.de/de/uebersicht?field_sg_abschluss_value%5B2%5D=2"
ROOT_DIR = "RUB_Bochum_Data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
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


def safe_get(url):
    time.sleep(random.uniform(0.5, 1.5))
    try:
        res = session.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        return res
    except Exception as e:
        print(f"      访问失败: {url} | {e}")
        return None


def process_html_to_clean_md(html_source):
    """彻底移除评价块"""
    tree = etree.HTML(html_source)
    if tree is None: return ""

    # 切除评价块容器
    bad_xpaths = [
        "//div[contains(@class, 'l-container--teaser-box') and .//div[contains(@class, 'testimonial-img')]]",
        "//div[contains(@class, 'l-container--teaser-box') and .//a[contains(@class, 'cboxElement')]]",
        "//article[contains(@class, 'c-teaser') and .//img[contains(@src, 'testi')]]"
    ]
    for xpath in bad_xpaths:
        for node in tree.xpath(xpath):
            parent = node.getparent()
            if parent is not None: parent.remove(node)

    content_html = etree.tostring(tree, encoding='unicode', method='html')
    content_html = re.sub(r'<(script|style|img).*?>.*?</\1>|<img.*?>', '', content_html,
                          flags=re.DOTALL | re.IGNORECASE)
    content_html = re.sub(r'</?font.*?>', '', content_html, flags=re.IGNORECASE)

    return md(content_html, heading_style="ATX").strip()


def extract_adaptive_content(res_text):
    """自适应正文提取"""
    tree = etree.HTML(res_text)
    if tree is None: return ""
    candidates = [
        '//*[@id="block-rub-sip-theme-content"]//article/div',
        '//main',
        '//div[contains(@class, "node__content")]'
    ]
    for xpath in candidates:
        nodes = tree.xpath(xpath)
        if nodes and len(nodes[0].xpath('string(.)').strip()) > 100:
            return etree.tostring(nodes[0], encoding='unicode', method='html')
    return etree.tostring(tree.xpath('//body')[0], encoding='unicode', method='html') if tree.xpath('//body') else ""


def fetch_combined_detail(url, major_name):
    """抓取主详情+自动合并跳转申请页"""
    res = safe_get(url)
    if not res: return ""

    main_html = extract_adaptive_content(res.text)
    md_output = [f"# {major_name}\n", process_html_to_clean_md(main_html)]

    # 提取“了解更多”链接
    temp_tree = etree.HTML(main_html)
    # 针对 RUB 特有的 internationale 申请按钮
    adm_links = temp_tree.xpath('.//a[contains(@href, "internationale") or contains(@href, "bewerbung")]/@href')

    for link in adm_links:
        full_ext_url = urljoin(url, link)
        if "bewerbung" in full_ext_url or "zugang" in full_ext_url:
            print(f"       抓取子页内容...")
            ext_res = safe_get(full_ext_url)
            if ext_res:
                ext_html = extract_adaptive_content(ext_res.text)
                md_output.append("\n\n---\n## International Application Details\n")
                md_output.append(process_html_to_clean_md(ext_html))
            break

    return "\n\n".join(md_output)


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)

    print(f"[1/2] 正在分析列表并进行物理去重...")
    res = safe_get(LIST_URL)
    if not res: return
    tree = etree.HTML(res.text)
    raw_items = tree.xpath('//div[contains(@class, "views-view-responsive-grid__item")]')

    # 预洗选
    unique_majors = []
    seen_urls = set()
    for item in raw_items:
        link_node = item.xpath('.//span[contains(@class, "field-sg-studienfach")]//a')
        if not link_node: continue

        url = urljoin(BASE_URL, link_node[0].get('href'))
        if url in seen_urls: continue

        name = link_node[0].xpath('string(.)').strip()
        degree = item.xpath(
            'string(.//span[contains(@class, "field-sg-abschluss")]/span[@class="field-content"])').strip()

        seen_urls.add(url)
        unique_majors.append({"name": name, "url": url, "degree": degree})

    total = len(unique_majors)
    print(f"[2/2] 识别到 {total} 个唯一专业。开始精准处理...")

    for i, major in enumerate(unique_majors, 1):
        folder_identity = f"{major['name']}_{major['degree']}"
        major_dir = os.path.join(ROOT_DIR, sanitize(folder_identity))
        os.makedirs(major_dir, exist_ok=True)

        md_file = os.path.join(major_dir, f"{sanitize(folder_identity)}.md")
        if os.path.exists(md_file):
            print(f"    [{i}/{total}] 跳过: {folder_identity}")
            continue

        print(f"\n>>> [{i}/{total}] 处理: {folder_identity}")
        content = fetch_combined_detail(major['url'], major['name'])
        if content:
            with open(md_file, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"    [成功]")


if __name__ == "__main__":
    main()