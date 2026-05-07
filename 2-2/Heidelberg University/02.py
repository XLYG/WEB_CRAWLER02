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

BASE_URL = "https://www.uni-heidelberg.de"
START_URL = "https://www.uni-heidelberg.de/en/study/all-subjects?study_finder--2506--filter-field_subject_shapes=31&study_finder--2506--filter-field_subject_shapes=28&study_finder--2506--filter-field_subject_shapes=30&study_finder--2506--filter-field_subject_shapes=370&study_finder--2506--filter-field_subject_shapes=79&study_finder--2506--filter-field_subject_shapes=650&study_finder--2506--filter-field_subject_shapes=597&study_finder--2506--filter-field_subject_shapes=600"
ROOT_DIR = "Heidelberg_University_Data"
LOG_FILE = "failed_assets.txt"  # 失败记录文件

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
}


def create_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=[502, 503, 504]
    )
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
    return session


session = create_session()


def log_failure(msg):
    """记录失败信息"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def sanitize(name):
    return re.sub(r'[\\/*?:"<>|]', "_", str(name)).strip()


def safe_request(url,timeout=10):
    try:
        # 设置较短的timeout，避免在死链上浪费时间
        response = session.get(url, headers=HEADERS, timeout=(10, timeout))
        response.raise_for_status()
        return response
    except Exception as e:
        return None


def download_pdf(url, folder, filename, subject_name):
    save_name = sanitize(filename) + ".pdf"
    path = os.path.join(folder, save_name)

    if os.path.exists(path):
        print(f"      PDF 已存在: {save_name}")
        return

    print(f"      PDF: {url}")
    res = safe_request(url)  # PDF 下载给多点时间
    if res and res.status_code == 200:
        with open(path, 'wb') as f:
            f.write(res.content)
        print(f"      已保存")
    else:
        print(f"      链接无效或超时")
        log_failure(f"PDF FAILED | 专业: {subject_name} | URL: {url}")


def process_degree_detail(url, folder, d_name, s_name):
    md_path = os.path.join(folder, f"{sanitize(d_name)}.md")

    res = safe_request(url)
    if not res:
        log_failure(f"PAGE FAILED | 专业: {s_name} | 分支: {d_name} | URL: {url}")
        return

    tree = etree.HTML(res.text)

    # 保存 MD
    if not os.path.exists(md_path):
        main_node = tree.xpath('//main')
        if main_node:
            raw_html = etree.tostring(main_node[0], encoding='unicode', method='html')
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md(raw_html, heading_style="ATX"))
            print(f"    MD 已保存")
    else:
        print(f"    MD 详情已存在")

    # PDF 提取
    pdf_links = tree.xpath('//a[contains(@class, "link-file") or contains(@class, "link-document")]')
    keywords = ["Module Handbook", "Modulhandbuch", "模块手册"]

    for a in pdf_links:
        full_text = "".join(a.xpath('.//text()')).strip()
        if any(kw in full_text for kw in keywords):
            pdf_url = urljoin(BASE_URL, a.get('href'))
            download_pdf(pdf_url, folder, "Module Handbook (DE)", s_name)


def process_subject_page(url, s_name):
    print(f"\n正在处理: {s_name}")
    s_dir = os.path.join(ROOT_DIR, sanitize(s_name))
    os.makedirs(s_dir, exist_ok=True)

    res = safe_request(url)
    if not res:
        log_failure(f"SUBJECT MAIN FAILED | 专业: {s_name} | URL: {url}")
        return

    tree = etree.HTML(res.text)

    # 概览 MD
    desc_path = os.path.join(s_dir, "Subject_Description.md")
    if not os.path.exists(desc_path):
        nodes = tree.xpath('//*[@id="mainContent"]/*')
        content = []
        for n in nodes:
            if any(m in (n.get('class', '') or '') for m in ['Wrapper_nFoVH', 'Wrapper_I0u1l']): break
            content.append(etree.tostring(n, encoding='unicode', method='html'))
        if content:
            with open(desc_path, 'w', encoding='utf-8') as f:
                f.write(md("".join(content), heading_style="ATX"))

    # 分支
    links = tree.xpath('//*[@id="mainContent"]//a[contains(@class, "Link_-cwD-")]')
    seen = set()
    for l in links:
        d_url = urljoin(BASE_URL, l.get('href'))
        if d_url in seen: continue
        seen.add(d_url)

        d_name_node = l.xpath('.//p[contains(@class, "LinkTitle_T17k-")]/text()')
        d_name = d_name_node[0].strip() if d_name_node else "Detail"

        d_dir = os.path.join(s_dir, sanitize(d_name))
        os.makedirs(d_dir, exist_ok=True)
        process_degree_detail(d_url, d_dir, d_name, s_name)


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)

    print("正在获取专业列表...")
    res = safe_request(START_URL)
    if not res: return

    tree = etree.HTML(res.text)
    container = tree.xpath('/html/body/div[1]/main/div[1]/div[4]/div/div[2]')
    if not container: return

    elements = container[0].xpath('.//a[@aria-label and .//h4]')
    targets = [(urljoin(BASE_URL, e.get('href')), e.get('aria-label').strip()) for e in elements]

    print(f"发现 {len(targets)} 个专业，开始执行...")
    for i, (url, name) in enumerate(targets):
        print(f"\n进度: {i + 1}/{len(targets)}")
        try:
            process_subject_page(url, name)
        except Exception as e:
            log_failure(f"CRITICAL ERROR | 专业: {name} | {e}")

    print("\n请检查 failed_assets.txt 查看未能下载的链接。")


if __name__ == "__main__":
    main()