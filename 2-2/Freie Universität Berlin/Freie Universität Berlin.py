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

BASE_URL = "https://www.fu-berlin.de"
START_URL = "https://www.fu-berlin.de/en/studium/studienangebot/master/index.html"
ROOT_DIR = "FU_Berlin_Master_Data"
LOG_FILE = "fu_berlin_crawler_log.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}


def create_session():
    session = requests.Session()
    session.trust_env = False
    retry_strategy = Retry(
        total=5,
        backoff_factor=3,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
    return session


session = create_session()


def log_msg(msg):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def sanitize(name):
    return re.sub(r'[\\/*?:"<>|]', "_", str(name)).strip()


def safe_request(url):
    time.sleep(random.uniform(2.0, 4.0))
    try:
        response = session.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()
        return response
    except Exception as e:
        print(f"       访问失败: {url} - 错误: {e}")
        return None


def download_pdf(url, folder, filename):
    """下载 PDF 到专业文件夹"""
    # 清理并确保后缀正确
    clean_name = sanitize(filename)
    if not clean_name.lower().endswith('.pdf'):
        clean_name += ".pdf"

    path = os.path.join(folder, clean_name)
    if os.path.exists(path):
        print(f"       PDF 已存在: {clean_name}")
        return

    print(f"      正在保存: {clean_name}")
    res = safe_request(url)
    if res and res.status_code == 200:
        with open(path, 'wb') as f:
            f.write(res.content)
        print(f"      PDF 保存完成")
    else:
        log_msg(f"PDF FAILED | URL: {url}")


def convert_to_markdown(html_node):
    """格式转化，去除图片和转化表格"""
    if html_node is None: return ""

    for img in html_node.xpath('.//img'):
        img.getparent().remove(img)

    # 针对list-group div 结构进行表格增强处理
    div_tables = html_node.xpath('.//div[contains(@class, "box-institution-table")]')
    for table in div_tables:
        rows = table.xpath('.//div[contains(@class, "list-group-item")]')
        md_table_placeholder = "\n| 信息项目 | 内容 |\n| --- | --- |\n"
        for row in rows:
            cols = row.xpath('./div')
            if len(cols) >= 2:
                k = "".join(cols[0].xpath('.//text()')).strip()
                v = "".join(cols[1].xpath('.//text()')).strip()
                md_table_placeholder += f"| {k} | {v} |\n"


    raw_html = etree.tostring(html_node, encoding='unicode', method='html')
    markdown_text = md(raw_html, heading_style="ATX")
    # 清理自动翻译的残留
    markdown_text = re.sub(r'</?font.*?>', '', markdown_text)
    return markdown_text


def process_detail_page(url, major_folder, major_name):
    """处理详细页面内容"""
    md_file_name = f"{sanitize(major_name)}.md"
    md_path = os.path.join(major_folder, md_file_name)

    res = safe_request(url)
    if not res: return

    tree = etree.HTML(res.text)
    # 锁定目标 XPath 容器
    content_list = tree.xpath('/html/body/div[2]/div[4]/div[2]/div/main/div/div')
    if not content_list:
        content_list = tree.xpath('//div[contains(@class, "box-studienangebot")]')

    if content_list:
        node = content_list[0]
        full_md = f"# {major_name}\n\n"

        # 顶部基础信息 (非折叠部分)
        top_elements = node.xpath('./*[not(contains(@class, "cms-accordion"))]')
        for elem in top_elements:
            full_md += convert_to_markdown(elem) + "\n"

        # 折叠面板处理
        panels = node.xpath('.//div[contains(@class, "panel-default")]')
        for panel in panels:
            h_node = panel.xpath('.//div[contains(@class, "panel-heading")]')
            heading = "".join(h_node[0].xpath('.//text()')).strip() if h_node else ""

            b_node = panel.xpath('.//div[contains(@class, "panel-body")]')
            if b_node:
                full_md += f"\n## {heading}\n\n"
                full_md += convert_to_markdown(b_node[0]) + "\n"

                admission_keywords = ["Admission requirements", "Admission", "Zugangsvoraussetzungen"]
                if any(kw.lower() in heading.lower() for kw in admission_keywords):
                    # 寻找 PDF
                    pdf_links = b_node[0].xpath('.//a[contains(@href, ".pdf") or contains(@href, "download")]')
                    for p_link in pdf_links:
                        link_text = "".join(p_link.xpath('.//text()')).strip()
                        if not link_text: link_text = "Admission_Requirement_Document"
                        pdf_url = urljoin(url, p_link.get('href'))
                        # 下载至当前专业文件夹
                        download_pdf(pdf_url, major_folder, link_text)

        # 保存 Md 文件
        if not os.path.exists(md_path):
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(full_md)
            print(f"    md 文件已生成")
        else:
            print(f"    md 文件已存在")
    else:
        print(f"    无法定位内容区: {major_name}")


def main():
    if not os.path.exists(ROOT_DIR):
        os.makedirs(ROOT_DIR)

    print(f"正在访问柏林自由大学专业列表")
    res = safe_request(START_URL)
    if not res:
        print("无法获取起始列表页")
        return

    tree = etree.HTML(res.text)
    # 定位列表容器
    container = tree.xpath('//*[@id="cms-id-row-2"]')
    if not container:
        print("无法找到专业列表容器。")
        return

    # 提取所有专业链接
    major_nodes = container[0].xpath('.//li/a')
    targets = []
    for m in major_nodes:
        href = m.get('href')
        name = "".join(m.xpath('.//text()')).strip()
        if href and name:
            targets.append((urljoin(BASE_URL, href), name))

    print(f"识别到 {len(targets)} 个硕士专业")

    for i, (m_url, m_name) in enumerate(targets):
        print(f"\n进度: {i + 1}/{len(targets)} | {m_name}")

        # 建立专业主文件夹
        major_path = os.path.join(ROOT_DIR, sanitize(m_name))
        os.makedirs(major_path, exist_ok=True)

        try:
            process_detail_page(m_url, major_path, m_name)
        except Exception as e:
            print(f"  跳过该专业: {e}")
            log_msg(f"CRITICAL ERROR | {m_name} | {e}")

    print("\n 数据已整理。")


if __name__ == "__main__":
    main()