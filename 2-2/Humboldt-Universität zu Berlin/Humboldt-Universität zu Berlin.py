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

BASE_URL = "https://www.hu-berlin.de"
URL_TEMPLATE = "https://www.hu-berlin.de/en/study/study-programme?tx_hubstg%5Baction%5D=search&tx_hubstg%5Bcontroller%5D=StudyOffer&tx_hubstg%5BcurrentPage%5D={page}&tx_hubstg%5Bsearch%5D%5BdegreeTypes%5D%5B0%5D=3&tx_hubstg%5Bsearch%5D%5BdegreeTypes%5D%5B1%5D=8&tx_hubstg%5Bsearch%5D%5BdegreeTypes%5D%5B2%5D=11&tx_hubstg%5Bsearch%5D%5BdegreeTypes%5D%5B3%5D=12&tx_hubstg%5Bsearch%5D%5BdisableSecondaryOffers%5D=&tx_hubstg%5Bsort%5D=relevance"
ROOT_DIR = "HU_Berlin_Data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
}


def create_session():
    session = requests.Session()
    session.trust_env = False
    retry_strategy = Retry(total=5, backoff_factor=3, status_forcelist=[502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    return session


session = create_session()


def sanitize(name):
    """清洗文件名：合并空白符、移除非法字符、截断长度"""
    if not name: return "Branch_Info"
    name = re.sub(r'\s+', ' ', str(name))  # 压缩换行符和空格
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return name[:120]


def safe_request(url):
    time.sleep(random.uniform(2.0, 3.5))
    try:
        res = session.get(url, headers=HEADERS, timeout=60)
        res.raise_for_status()
        return res
    except Exception as e:
        return None


def download_pdf(url, folder, link_text):
    """
    下载 PDF。每个分支只要是 Module Handbook 或相关的，就进行下载。
    """
    # 如果链接文本包含 handbook 相关词汇，就下载
    save_name = "Module_Handbook.pdf"
    path = os.path.join(folder, save_name)

    # 如果已经存在 Module_Handbook.pdf，就不再下载其他的了
    if os.path.exists(path):
        return

    print(f"      正在保存: {save_name} (原始名称: {link_text[:30]}...)")
    res = safe_request(url)
    if res and res.status_code == 200:
        with open(path, 'wb') as f:
            f.write(res.content)
        print(f"      PDF 已保存")


def process_single_branch(url, branch_dir, branch_label):
    """处理详情页：截断内容并抓取PDF"""
    md_path = os.path.join(branch_dir, f"{sanitize(branch_label)}.md")

    res = safe_request(url)
    if not res: return
    tree = etree.HTML(res.text)

    # 查找锚点
    anchor = tree.xpath('//*[@id="course-chapter-downloads"]')
    content_html = ""

    if anchor:
        target_block = anchor[0]
        # 获取锚点之前的所有兄弟节点
        prev_nodes = target_block.xpath('./preceding-sibling::*')
        for node in prev_nodes:
            content_html += etree.tostring(node, encoding='unicode', method='html')
        # 加上锚点自身内容
        content_html += etree.tostring(target_block, encoding='unicode', method='html')

        # 在下载区内寻找 PDF 链接
        # 放宽条件：只要是 PDF 链接且文本包含核心词，或者它是该区域唯一的 PDF
        pdf_links = target_block.xpath('.//a[contains(@href, ".pdf") or contains(@href, "download")]')
        pdf_keywords = ["handbook", "handbuch", "modul", "admission", "study", "exam", "regulations", "handreichung"]

        found_pdf = False
        for p in pdf_links:
            p_url = urljoin(url, p.get('href'))
            p_text = p.xpath('string(.)').lower()  # 获取所有嵌套文本并转小写

            # 如果链接文本或 URL 包含关键词，且不是社交分享链接
            if any(kw in p_text or kw in p_url.lower() for kw in pdf_keywords):
                if "facebook" not in p_url and "twitter" not in p_url:
                    download_pdf(p_url, branch_dir, p_text)
                    found_pdf = True
                    break  # 每个分支下载一个最相关的

        if not found_pdf and pdf_links:
            # 如果没匹配到关键词但确实有 PDF，下载第一个作为保险（最常见情况，但是没有问题）
            first_pdf_url = urljoin(url, pdf_links[0].get('href'))
            download_pdf(first_pdf_url, branch_dir, "Document")

    else:
        # 备选：无锚点时获取 main
        main_node = tree.xpath('//main')
        if main_node: content_html = etree.tostring(main_node[0], encoding='unicode', method='html')

    # 保存 Markdown
    if content_html:
        clean_html = re.sub(r'<img.*?>', '', content_html)
        clean_html = re.sub(r'</?font.*?>', '', clean_html)
        markdown_text = md(clean_html, heading_style="ATX")
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(markdown_text)
        print(f"    MD保存: {branch_label}")


def handle_subject_flow(major_url, subject_dir):
    """分支导航处理"""
    res = safe_request(major_url)
    if not res: return
    tree = etree.HTML(res.text)

    nav_xpath = '//*[@id="c7567"]/div[1]/ul[@class="list-inline d-flex flex-column flex-md-row flex-wrap gap-4"]'
    nav_ul = tree.xpath(nav_xpath)

    if not nav_ul:
        print(f"    单专业处理")
        process_single_branch(major_url, subject_dir, "General_Info")
        return

    items = nav_ul[0].xpath('./li')
    for li in items:
        # 穿透标签获取分支名称
        label_raw = li.xpath('string(.//span[@class="link-text"])').strip()
        if not label_raw: label_raw = li.xpath('string(.)').strip()

        branch_label = sanitize(label_raw)

        link_tag = li.xpath('.//a')
        active_tag = li.xpath('.//div[contains(@class, "active")]')

        branch_dir = os.path.join(subject_dir, branch_label)
        os.makedirs(branch_dir, exist_ok=True)

        if active_tag:
            process_single_branch(major_url, branch_dir, branch_label)
        elif link_tag:
            branch_url = urljoin(BASE_URL, link_tag[0].get('href'))
            # 确保是内部专业详情链接
            if "/details/" in branch_url:
                process_single_branch(branch_url, branch_dir, branch_label)


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)
    for page in range(1, 10):  # 93条，每页12条，共8页，给到9页
        print(f"\n===== 处理第 {page} 页 =====")
        res = safe_request(URL_TEMPLATE.format(page=page))
        if not res: continue

        tree = etree.HTML(res.text)
        cards = tree.xpath('//*[@id="offer-container-7579"]/div[2]/div/div[contains(@class, "col-12")]')

        if not cards: break  # 没卡片了就退出

        for card in cards:
            name_nodes = card.xpath('.//h3[contains(@class, "list-item-title")]/text()')
            if not name_nodes: continue
            subject_name = name_nodes[0].strip()

            first_link = card.xpath('.//div[contains(@class, "offer-group-items")]/a[1]')
            if not first_link: continue
            major_url = urljoin(BASE_URL, first_link[0].get('href'))

            print(f"\n正在处理专业: {subject_name}")
            subject_dir = os.path.join(ROOT_DIR, sanitize(subject_name))
            os.makedirs(subject_dir, exist_ok=True)

            try:
                handle_subject_flow(major_url, subject_dir)
            except Exception as e:
                print(f" 严重错误:{subject_name}: {e}")


if __name__ == "__main__":
    main()