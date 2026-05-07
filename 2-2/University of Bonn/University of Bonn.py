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

BASE_URL = "https://www.uni-bonn.de"
URL_TEMPLATE = "https://www.uni-bonn.de/en/studying/degree-programs/degree-programs-a-z?active_layout=table&leading_letter=&SearchableText=&submit=&graduation:list=theological_magister&graduation:list=master_of_arts&graduation:list=master_of_education&graduation:list=master_of_science&graduation:list=master_further_forming&restrictions:list=nationwide_anton&restrictions:list=open_admission&form.wigdet.uni-email=&tigger=&b_start:int={start}"
ROOT_DIR = "Uni_Bonn_Data"

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
    """文件名清洗"""
    if not name: return "Subject_Info"
    name = re.sub(r'\s+', ' ', str(name))
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return name[:100]


def safe_request(url):
    time.sleep(random.uniform(2.0, 4.0))
    try:
        res = session.get(url, headers=HEADERS, timeout=60)
        res.raise_for_status()
        return res
    except Exception as e:
        return None


def download_pdf(url, folder):
    """下载PDF，平级存放"""
    path = os.path.join(folder, "Module_Handbook.pdf")
    if os.path.exists(path): return
    print(f"       正在尝试下载手册...")
    res = safe_request(url)
    if res and res.status_code == 200:
        with open(path, 'wb') as f:
            f.write(res.content)


def clean_md(text):
    """清理转换后的MD中的多余的空行"""
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def process_keyfacts_natively(keyfacts_node):

    lines = []

    # 提取 Header
    header = keyfacts_node.xpath('.//div[contains(@class, "header")]//label/text()')
    if header:
        lines.append(f"## {header[0].strip()}\n")

    # 遍历 keyfacts-1 和 keyfacts-2 内部的 course-keyfact
    fact_items = keyfacts_node.xpath('.//div[contains(@class, "course-keyfact")]')
    for item in fact_items:
        # 获取 Label (排除 icon)
        label_text = "".join(item.xpath('./label/span/text() | ./label/text()')).strip()
        if not label_text: continue

        # 获取 Value 节点
        collapsible = item.xpath('.//div[contains(@class, "collapsible")]')
        if collapsible:
            # 对于可折叠区域，使用 markdownify 保留内部的 p, a 标签
            val_html = etree.tostring(collapsible[0], encoding='unicode', method='html')
            val_md = md(val_html, heading_style="ATX").strip()
            lines.append(f"**{label_text}**:\n{val_md}\n")
        else:
            spans = item.xpath('./span/text()')
            if not spans:
                spans = item.xpath('.//span/text()')

            val_text = ", ".join([s.strip() for s in spans if s.strip()])
            lines.append(f"**{label_text}**: {val_text}\n")

    return "\n".join(lines)


def process_sitebar_natively(sitebar_node):
    # 侧边栏处理
    lines = ["## Additional Information & Application\n"]

    # 处理按钮
    buttons = sitebar_node.xpath('.//div[contains(@class, "course-button")]/a')
    for btn in buttons:
        btn_text = btn.xpath('string(.)').strip()
        btn_url = urljoin(BASE_URL, btn.get('href'))
        lines.append(f"[{btn_text}]({btn_url})\n")

    # 处理信息项
    infos = sitebar_node.xpath('.//div[contains(@class, "information")]')
    for info in infos:
        label = info.xpath('string(./label)').strip()
        # 转换内容，保留链接
        content_html = etree.tostring(info, encoding='unicode', method='html')
        content_html = re.sub(r'<label>.*?</label>', '', content_html, flags=re.DOTALL)
        content_md = md(content_html).strip()
        if label:
            lines.append(f"**{label}**: {content_md}")

    return "\n".join(lines)


def process_detail_page(url, folder, major_name):
    md_path = os.path.join(folder, f"{sanitize(major_name)}.md")
    res = safe_request(url)
    if not res: return

    tree = etree.HTML(res.text)
    wrapper = tree.xpath('//*[@id="course-content-wrapper"]')
    if not wrapper: return

    node = wrapper[0]
    full_parts = [f"# {major_name}\n"]

    # 主描述内容获取
    content_area = node.xpath('.//div[@id="course-grid-content"]')
    if content_area:
        for btn in content_area[0].xpath('.//button'):
            btn.getparent().remove(btn)
        html_str = etree.tostring(content_area[0], encoding='unicode', method='html')
        html_str = re.sub(r'<img.*?>', '', html_str)
        full_parts.append(md(html_str, heading_style="ATX"))

    sitebar = node.xpath('.//div[@id="course-grid-sitebar"]')
    if sitebar:
        full_parts.append(process_sitebar_natively(sitebar[0]))

    # Keyfacts 原生态内容
    keyfacts = node.xpath('.//div[@id="course-grid-keyfacts"]')
    if keyfacts:
        full_parts.append(process_keyfacts_natively(keyfacts[0]))

    # 保存最终文本
    final_text = "\n\n---\n\n".join(full_parts)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(clean_md(final_text))
    print(f"    原生态 MD 已生成: {major_name}")

    # 4. PDF 抓取
    pdf_links = node.xpath('.//a[contains(@href, ".pdf") or contains(@href, "download")]')
    keywords = ["handbook", "handbuch", "regulations", "ordnung", "curriculum"]
    for p in pdf_links:
        p_url = urljoin(url, p.get('href'))
        p_text = p.xpath('string(.)').lower()
        if any(kw in p_text or kw in p_url.lower() for kw in keywords):
            download_pdf(p_url, folder)
            break


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)
    current_start = 0
    total = 0

    print(f"正在抓取波恩大学...")
    while True:
        url = URL_TEMPLATE.format(start=current_start)
        res = safe_request(url)
        if not res: break

        tree = etree.HTML(res.text)
        cards = tree.xpath('//*[@id="course-search"]//a[contains(@class, "content-row")]')
        if not cards: break

        for card in cards:
            name_node = card.xpath('.//label[contains(@class, "title")]/text()')
            if not name_node: continue
            major_name = name_node[0].strip()
            major_url = urljoin(BASE_URL, card.get('href'))

            print(f"\n进度 [{total + 1}] 专业: {major_name}")
            major_dir = os.path.join(ROOT_DIR, sanitize(major_name))
            os.makedirs(major_dir, exist_ok=True)

            try:
                process_detail_page(major_url, major_dir, major_name)
                total += 1
            except Exception as e:
                print(f" 错误 {e}")

        current_start += 30
        if total > 500: break  # 安全阈值


if __name__ == "__main__":
    main()