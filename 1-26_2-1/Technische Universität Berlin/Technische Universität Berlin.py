import requests
from bs4 import BeautifulSoup
import os
import re
import time
from urllib.parse import urljoin

# --- 配置中心 ---
# 基础 URL，注意 {page} 是翻页占位符
BASE_URL_TEMPLATE = "https://www.tu.berlin/en/studying/study-programs/all-programs-offered/{page}?tx_tubstudypaths_studypathlist%5Bfilter%5D%5B0%5D=degreeType%3AMaster&cHash=c4303a3c4d1183bb24e5a51eef0281d9"
DOMAIN = "https://www.tu.berlin"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"
}
OUTPUT_FOLDER = "TU_Berlin_Data"

if not os.path.exists(OUTPUT_FOLDER):
    os.makedirs(OUTPUT_FOLDER)


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def get_all_detail_links():
    all_links = []
    page = 1
    print("获取列表所有项...")

    while True:
        url = BASE_URL_TEMPLATE.format(page=page)
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # 定位列表项
            items = soup.find_all("div", class_="studypaths__listItem")

            if not items:
                print(f"第 {page} 页没有内容")
                break

            for item in items:
                link_tag = item.find("a", href=True)
                if link_tag:
                    full_url = urljoin(DOMAIN, link_tag['href'])
                    all_links.append(full_url)

            print(f"    成功获取第 {page} 页，当前链接总数: {len(all_links)}")
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"在第 {page} 页中断: {e}")
            break
    return all_links


def process_course_detail(url):
    # 单页面深度解析与定向保存。
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. 提取课程标题
        title_tag = soup.find("h1")
        if not title_tag:
            return
        raw_title = title_tag.get_text(strip=True)
        safe_title = sanitize_filename(raw_title)

        # 创建专属文件夹
        course_dir = os.path.join(OUTPUT_FOLDER, safe_title)
        if not os.path.exists(course_dir):
            os.makedirs(course_dir)

        print(f"正在处理: {raw_title}")

        # 生成 Markdown 详情内容
        overview_div = soup.find("div", class_="studypaths__overview")
        if overview_div:
            # 提取描述
            desc_div = overview_div.find("div", class_="grid__column--lg-6")
            description = desc_div.get_text(strip=True) if desc_div else ""

            # 提取表格数据
            table = overview_div.find("table")
            table_md = "| Feature | Value |\n| --- | --- |\n"
            if table:
                for tr in table.find_all("tr"):
                    th = tr.find("th").get_text(strip=True)
                    td = tr.find("td").get_text(strip=True)
                    table_md += f"| {th} | {td} |\n"

            md_content = f"# {raw_title}\n\n## Program overview\n{description}\n\n## Details\n{table_md}\n\nURL: {url}"

            # 命名规范：课程名字.md
            md_path = os.path.join(course_dir, f"{safe_title}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

        # 下载 PDF (Aptitude Assessment)
        # 指定在 "Study and examination regulations" 区域寻找
        stupo_area = soup.find("div", class_="frame--type-stupo-downloadlist")
        if stupo_area:
            links = stupo_area.find_all("li", class_="stupoDownloadList")
            target_pdf_url = None

            for li in links:
                a_tag = li.find("a", href=True)
                info_text = a_tag.get_text()
                # 必须是德语，通常是列表中第一个符合条件的且是最新的
                if "German" in info_text:
                    target_pdf_url = urljoin(DOMAIN, a_tag['href'])
                    break

            if target_pdf_url:
                # 命名规范为-课程名_aptitude_assessment.pdf
                pdf_filename = f"{safe_title}_aptitude_assessment.pdf"
                pdf_path = os.path.join(course_dir, pdf_filename)

                pdf_res = requests.get(target_pdf_url, headers=HEADERS, stream=True)
                with open(pdf_path, 'wb') as f:
                    for chunk in pdf_res.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"    [OK] PDF 已保存为: {pdf_filename}")

    except Exception as e:
        print(f"    [Error] 处理 {url} 时出错: {e}")


if __name__ == "__main__":
    all_links = get_all_detail_links()

    print(f"\n开始获取所有项目...\n" + "=" * 40)
    for link in all_links[:]:
        process_course_detail(link)
        time.sleep(1)

    print("\n" + "=" * 40 + "\n完成")