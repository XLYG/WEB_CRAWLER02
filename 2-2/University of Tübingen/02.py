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

BASE_URL = "https://uni-tuebingen.de"

# 使用明确 [graduation]=1 筛选参数的初始链接
START_URL = "https://uni-tuebingen.de/en/study/finding-a-course/degree-programs-available/?tx_in2utcourses_list%5Bfilter%5D%5Bsearch%5D=&tx_in2utcourses_list%5Bfilter%5D%5BfieldOfStudy%5D=&tx_in2utcourses_list%5Bfilter%5D%5Bgraduation%5D=1&tx_in2utcourses_list%5Bfilter%5D%5BadmissionRestriction%5D=&tx_in2utcourses_list%5Bfilter%5D%5BstartOfStudy%5D=&tx_in2utcourses_list%5Bfilter%5D%5BlanguageOfInstruction%5D=&tx_in2utcourses_list%5Bfilter%5D%5Bfaculty%5D="

ROOT_DIR = "Uni_Tuebingen_Data"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,de;q=0.7",
    "Connection": "keep-alive",
}


def create_session():
    session = requests.Session()
    session.trust_env = True  # 支持代理
    retry_strategy = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    return session


session = create_session()


def sanitize(name):
    """清理路径中的非法字符，处理换行"""
    if not name: return "Unknown"
    name = re.sub(r'<[^>]+>', '', str(name))
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return name[:120]


def safe_request(url, referer=None):
    """发送请求，动态更新 Referer"""
    print(f"     正在请求-{url}")
    current_headers = HEADERS.copy()
    if referer:
        current_headers['Referer'] = referer

    time.sleep(random.uniform(2.0, 3.5))
    try:
        res = session.get(url, headers=current_headers, timeout=40)
        res.raise_for_status()
        print(f"      成功 状态: {res.status_code}")
        return res
    except Exception as e:
        print(f"     失败-{e}")
        return None


def process_detail_page(url, folder, major_display_name, list_page_url):
    """解析详情页：原生态提取 div 2-5"""
    md_path = os.path.join(folder, f"{sanitize(major_display_name)}.md")

    if os.path.exists(md_path): return

    res = safe_request(url, referer=list_page_url)
    if not res: return

    tree = etree.HTML(res.text)
    # 严格定位 XPath
    target_divs = tree.xpath('//*[@id="c981363"]/div/div/div[position() >= 2 and position() <= 5]')

    if target_divs:
        full_html = ""
        for div in target_divs:
            full_html += etree.tostring(div, encoding='unicode', method='html')

        # 转化为原生态 Markdown
        clean_html = re.sub(r'</?font.*?>', '', full_html)
        clean_html = re.sub(r'<img.*?>', '', clean_html)
        markdown_content = md(clean_html, heading_style="ATX")

        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {major_display_name}\n\n{markdown_content}")
        print(f"    MD文件已生成")
    else:
        print(f"    未能定位到 div[2-5] 详情内容")


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)

    current_url = START_URL
    visited_urls = set()
    page_num = 1

    print(f"开始爬取图宾根大学...")

    while current_url and current_url not in visited_urls:
        print(f"\n--- 正在处理第 {page_num} 页 ---")
        visited_urls.add(current_url)

        res = safe_request(current_url)
        if not res: break

        tree = etree.HTML(res.text)
        # 列表条目定位
        cards = tree.xpath('//*[@id="c981354"]/div/div[contains(@class, "ut-box")]')

        if not cards:
            print(" 页面未发现专业条目。")
            break

        print(f"  此页找到 {len(cards)} 个专业")

        for card in cards:
            # 提取名称
            major_name = card.xpath('string(.//h3)').strip()
            # 提取 ID
            course_id = card.get('data-in2utcourses-id', 'noID')
            # 提取详情链接
            link_node = card.xpath('.//a[contains(@class, "ut-btn")]/@href')

            if not major_name or not link_node: continue

            detail_url = urljoin(BASE_URL, link_node[0])
            # 文件夹命名：名称 + ID
            folder_name = f"{major_name}_{course_id}"
            major_folder = os.path.join(ROOT_DIR, sanitize(folder_name))

            print(f"\n>>> 处理专业: {folder_name}")
            if not os.path.exists(major_folder): os.makedirs(major_folder)

            try:
                # 传入当前列表页 URL 作为 Referer，模拟真实点击行为
                process_detail_page(detail_url, major_folder, major_name, current_url)
            except Exception as e:
                print(f"  详情页抓取失败: {e}")

        print(f"\n  正在寻找服务器签名的下一页链接...")
        next_href = tree.xpath('//a[contains(@class, "ut-pager-link--next")]/@href')

        if next_href:
            next_url = urljoin(BASE_URL, next_href[0])
            if next_url not in visited_urls:
                current_url = next_url
                page_num += 1
                print(f"  成功获取下一页 (含 cHash)")
            else:
                print("  检测到 URL 回环。")
                current_url = None
        else:
            print("  未发现下一页按钮。")
            current_url = None

    print(f"\n抓取总页数: {page_num}")


if __name__ == "__main__":
    main()