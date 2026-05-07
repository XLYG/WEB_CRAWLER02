import csv
import os
import re
import time
import requests
from bs4 import BeautifulSoup

# 提取指定的折叠栏版块
TARGET_SECTIONS = [
    "Program details",
    "Career prospects",
    "Characteristic features of the degree program",
    "Admission and language requirements"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
}


def clean_text(text):
    # 统一的文本清洗函数：去除首尾空格，将连续空格合并为一个
    if not text:
        return ""
    return ' '.join(text.split())


# 加粗处理
def process_span(span):
    # 处理 span 标签
    classes = span.get('class', [])
    text = span.get_text(" ", strip=True)

    if not text:
        return ""

    # 若包含 steckbrief则加粗
    if any(cls in ['steckbrief', 'steckbrief2'] for cls in classes):
        return f"**{text}** "

    # nowrap-steckbrief 嵌套中的加粗对象
    elif 'nowrap-steckbrief' in classes:
        inner_steckbrief = span.find('span', class_='steckbrief')
        if inner_steckbrief:
            steckbrief_text = inner_steckbrief.get_text(" ", strip=True)
            remaining_text = text.replace(steckbrief_text, '').strip()
            if remaining_text:
                return f"**{steckbrief_text}** {remaining_text} "
            return f"**{steckbrief_text}** "

    # 其他span
    return f"{text} "


def process_element(element):
    # 递归处理 HTML 元素，转换为 Markdown 格式文本
    result = ""
    # 遍历
    for child in element.contents:
        if isinstance(child, str):
            # 处理纯文本节点
            text = child.strip()
            if text:
                result += f"{text} "
        elif hasattr(child, 'name'):
            # 处理子标签
            if child.name == 'br':
                result += "\n"
            elif child.name in ['strong', 'b']:
                child_text = child.get_text(" ", strip=True)
                if child_text:
                    result += f"**{child_text}** "
            elif child.name == 'span':
                result += process_span(child)
            elif child.name == 'a':
                result += f"{child.get_text(' ', strip=True)} "
            else:
                # 如果还有其他标签，则继续写入
                result += f"{child.get_text(' ', strip=True)} "
    return result


# 整体分为两部分，头部的基本信息，折叠页的指定区块信息

def extract_header_info(soup, processed_content):
    # 提取页面头部的基本信息（Degree, Length, Grid内容)
    markdown_part = ""

    # 通过唯一的 grid-steckbrief 反向查找父容器，避免写入顺序错误和没找到
    grid_container = soup.find('div', class_='grid-steckbrief')
    if not grid_container:
        return ""

    header_container = grid_container.parent
    if not header_container:
        return ""

    # 提取网格之前的 P 标签 (Degree, ECTS 等)
    for elem in header_container.contents:
        # 找到网格就结束
        if elem == grid_container:
            break
        if elem.name == 'p':
            p_text = clean_text(process_element(elem))
            if p_text and p_text not in processed_content:
                processed_content.add(p_text)
                markdown_part += f"{p_text}\n\n"

    # 提取网格内容
    for item in grid_container.children:
        if item.name == 'div':
            classes = item.get('class', [])  #
            item_text = process_element(item).strip()
            if not item_text:
                continue

            # 左侧标签：不换行，加粗
            if any(c in ['intro', 'intro2'] for c in classes):
                if "**" not in item_text:
                    item_text = f"**{item_text}**"
                markdown_part += f"{item_text} "

            # 右侧内容：换行
            elif any(c in ['main', 'main2'] for c in classes):
                # 保留内部原本的换行结构，但清洗每行多余空格
                lines = [line.strip() for line in item_text.split('\n') if line.strip()]
                # 将处理过的内容加入去重集合
                cleaned_block_text = '\n'.join(lines)
                # 注意：processed_content 最好也存清洗后的，以防万一
                processed_content.add(cleaned_block_text)
                markdown_part += f"{cleaned_block_text}\n\n"

    return markdown_part


def extract_toggle_sections(soup, processed_content):
    # 提取页面下方的折叠面板内容
    markdown_part = ""
    all_toggles = soup.find_all('div', class_='toggle')

    # 遍历所有的toggle
    for toggle in all_toggles:
        # 找标题
        toggle_head = toggle.find('div', class_='toggle-head')
        if not toggle_head: continue

        h3 = toggle_head.find('h3', class_='heading')
        if not h3: continue

        h3_text = h3.get_text(strip=True)

        # 匹配指定四个板块
        if h3_text not in TARGET_SECTIONS:
            continue

        print(f"找到目标板块: {h3_text}")
        if h3_text not in processed_content:
            processed_content.add(h3_text)
            markdown_part += f"## {h3_text}\n\n"

        # 找内容
        toggle_body = toggle.find('div', class_='toggle-body')
        if not toggle_body: continue
        toggle_content = toggle_body.find('div', class_='toggle-content')
        if not toggle_content: continue

        # 遍历 KIT_section
        kit_sections = toggle_content.find_all('div', class_='KIT_section')
        for kit_section in kit_sections:
            text_div = kit_section.find('div', class_='text')
            if not text_div: continue

            # 创建列表存储制定的特殊标签保证最终的顺序
            content_elements = text_div.find_all(['h4', 'h5', 'h6', 'p', 'ul', 'ol'])

            for elem in content_elements:
                # 跳过嵌套元素，保证对应顺序
                if elem.name in ['p', 'ul', 'ol'] and elem.find_parent(['ul', 'ol']):
                    continue

                if elem.name in ['h4', 'h5', 'h6']:
                    h_text = elem.get_text(" ", strip=True)
                    if h_text and h_text not in processed_content:
                        processed_content.add(h_text)
                        markdown_part += f"### {h_text}\n\n"

                elif elem.name == 'p':
                    p_text = clean_text(process_element(elem))
                    if p_text and p_text not in processed_content:
                        processed_content.add(p_text)
                        markdown_part += f"{p_text}\n\n"

                elif elem.name == 'ul':
                    for li in elem.find_all('li', recursive=False):
                        li_text = clean_text(process_element(li))
                        if li_text: markdown_part += f"- {li_text}\n"
                    markdown_part += "\n"

                elif elem.name == 'ol':
                    for i, li in enumerate(elem.find_all('li', recursive=False), 1):
                        li_text = clean_text(process_element(li))
                        if li_text: markdown_part += f"{i}. {li_text}\n"
                    markdown_part += "\n"

    return markdown_part


# 调用函数
def html_to_markdown(html_content, title):
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'html.parser')
    markdown = f"# {title}\n\n"

    # 使用 set 进行全局去重，保证每次写入的内容都是唯一的
    processed_content = set()

    # 分为两部分进行内容获取，保证不会混淆
    # 1. 提取头部信息
    markdown += extract_header_info(soup, processed_content)

    # 2. 提取折叠面板信息
    markdown += extract_toggle_sections(soup, processed_content)

    return markdown


def get_page_content(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"获取 {url} 失败: {str(e)}")
        return None


def main():
    csv_file = 'results.csv'
    if not os.path.exists(csv_file):
        print(f"错误: 找不到 {csv_file}")
        return

    projects = []
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            projects = list(reader)  # [:2]
    except Exception:
        pass

    print(f"共读取到 {len(projects)} 个项目")

    for i, project in enumerate(projects, 1):
        program_name = project.get('Degree Program', 'Unknown')
        degree = project.get('Degree', 'Unknown')
        url = project.get('URL')

        print(f"\n处理: {program_name}")

        content = get_page_content(url)
        if content:
            md = html_to_markdown(content, program_name)
            folder_path = "D:/daima/2025/12-20/results"
            os.makedirs(folder_path, exist_ok=True)

            # 生成文件名并保存
            filename = os.path.join(folder_path, f"{program_name}_{degree}.md".replace('/', ''))
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(md)
            print(f"已保存: {filename}")

        time.sleep(5)


if __name__ == "__main__":
    main()
