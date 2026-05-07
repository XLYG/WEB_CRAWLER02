import os
import requests
import re
from lxml import etree
from markdownify import markdownify as md
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.fau.eu"
START_URL = "https://www.fau.eu/studying/degree-programs/all-degree-programs/?search=&degree%5B%5D=Master&display=table#degree_program_results"
ROOT_DIR = "FAU_Erlangen_Data"

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


def clean_to_md(html_node):
    """仅转换传入的节点，不涉及子查询，防止重复"""
    if html_node is None: return ""
    if isinstance(html_node, list):
        html_str = "".join([etree.tostring(t, encoding='unicode', method='html') for t in html_node])
    else:
        html_str = etree.tostring(html_node, encoding='unicode', method='html')

    # 清洗：移除图片、font标签
    html_str = re.sub(r'<img.*?>', '', html_str, flags=re.DOTALL)
    html_str = re.sub(r'</?font.*?>', '', html_str, flags=re.IGNORECASE)

    return md(html_str, heading_style="ATX").strip()


def process_part2_strictly(div4_node):
    """
    提取顶部的静态介绍 (h3 和 description)并深入 accordion 内部，按标题过滤折叠项
    """
    md_segments = []

    # 提取顶部静态部分
    # 找 div.program-details 下不是折叠容器的所有直接子元素
    static_intro = div4_node.xpath(
        './/div[contains(@class, "program-details")]/*[not(contains(@class, "collapsibles"))]')
    if static_intro:
        md_segments.append(clean_to_md(static_intro))

    # 提取并过滤折叠面板
    # 定位最内层的折叠单项
    accordions = div4_node.xpath('.//div[contains(@class, "wp-block-rrze-elements-collapse")]')

    keep_list = ["Design and structure", "Fields of study", "qualities and skills"]
    drop_list = ["Why should I study", "career prospects"]

    for acc in accordions:
        # 提取按钮内的纯文本标题
        title_raw = acc.xpath('string(.//button[contains(@class, "accordion-toggle")])')
        title_clean = re.sub(r'\s+', ' ', title_raw).strip()

        if not title_clean: continue

        # 匹配判定
        is_bad = any(kw.lower() in title_clean.lower() for kw in drop_list)
        is_good = any(kw.lower() in title_clean.lower() for kw in keep_list)

        if is_bad:
            print(f"      剔除面板标题: {title_clean}")
            continue

        if is_good:
            print(f"     目标面板标题: {title_clean}")
            # 仅转换这一个面板的内容
            md_segments.append(clean_to_md(acc))
        else:
            # 既不在黑名单也不在白名单（如可能存在的第6个面板还有一些不规则的特殊面板），默认保留
            md_segments.append(clean_to_md(acc))

    return "\n\n".join(md_segments)


def process_detail_page(url, folder, major_name):
    res = session.get(url, headers=HEADERS, timeout=30)
    if res.status_code != 200: return
    tree = etree.HTML(res.text)

    # 锁定根容器
    content_root = tree.xpath('//article[contains(@class, "type-degree-program")]/div/section/div')
    if not content_root:
        content_root = tree.xpath('//main//section/div')

    if not content_root:
        print(f"    无法定位内容根节点: {major_name}")
        return

    # 识别当前页面下所有的 div 块
    all_blocks = content_root[0].xpath('./div')

    md_final = [f"# {major_name}\n"]

    # 第一部分：div[2]
    if len(all_blocks) >= 2:
        md_final.append(clean_to_md(all_blocks[1]))

    # 第二部分：div[4]
    if len(all_blocks) >= 4:
        md_final.append(process_part2_strictly(all_blocks[3]))

    # 第三部分：div[7]
    if len(all_blocks) >= 7:
        md_final.append("\n---\n## Admission & Application Details\n")
        md_final.append(clean_to_md(all_blocks[6]))

    # 保存
    if len(md_final) > 1:
        file_path = os.path.join(folder, f"{sanitize(major_name)}.md")
        with open(file_path, 'w', encoding='utf-8') as f:
            content = "\n\n---\n\n".join(md_final)
            # 压缩连续空行
            f.write(re.sub(r'\n{3,}', '\n\n', content))
        print(f"    已整合所有指定内容")


def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)

    print(f"正在抓取 FAU 大学专业列表...")
    res = session.get(START_URL, headers=HEADERS, timeout=30)
    tree = etree.HTML(res.text)

    rows = tree.xpath('//table[contains(@class, "degree-program-table")]/tbody/tr')
    print(f"成功识别到 {len(rows)} 个专业")

    all_seen = set()
    count = 0
    for row in rows:
        link = row.xpath('.//a[contains(@class, "program-title")]')
        if not link: continue

        m_name = link[0].xpath('string(.)').strip()
        m_url = link[0].get('href')
        m_degree = row.xpath('string(./td[2])').replace("程度：", "").strip()

        if m_url in all_seen: continue
        all_seen.add(m_url)

        count += 1
        m_dir = os.path.join(ROOT_DIR, sanitize(f"{m_name}_{m_degree}"))
        os.makedirs(m_dir, exist_ok=True)

        print(f"\n>>> [{count}/{len(rows)}] 项目: {m_name}")
        try:
            process_detail_page(m_url, m_dir, m_name)
        except Exception as e:
            print(f"   失败 {e}")


if __name__ == "__main__":
    main()