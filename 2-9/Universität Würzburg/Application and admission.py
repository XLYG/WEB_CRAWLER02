import os
import re
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urlparse

RULES_URL = "https://www.paedagogik.uni-wuerzburg.de/studium/master-adult-education-and-management-in-lifelong-education-ma/application-and-admission/"
# 专业数据所在的主文件夹
MAIN_DATA_DIR = "Wuerzburg_Master_Data"
# 保存为的文件名
OUTPUT_FILENAME = "00_Application_Rules.md"



def fetch_and_convert_rules(url: str) -> str:
    """
    获取规则页面，提取<main>内容并转换为Markdown，过滤图片。
    """
    print(f"正在获取规则页面: {url}")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"获取页面失败: {e}")
        return ""

    soup = BeautifulSoup(response.text, 'html.parser')

    # 定位<main>区域
    main = soup.find('main')
    if not main:
        print("未找到<main>，尝试使用body查询")
        body = soup.find('body')
        if body:
            # 移除导航、页脚等干扰区块
            for selector in ['header', 'footer', 'nav', 'aside', '.sidebar', '.nav', '.breadcrumb']:
                for elem in body.select(selector):
                    elem.decompose()
            main = body
        else:
            print("无法定位任何内容区域")
            return ""

    # 移除所有图片，避免Markdown中出现链接
    for img in main.find_all('img'):
        img.decompose()

    # 转换为Markdown并清理多余空行
    content_md = md(str(main))
    content_md = re.sub(r'\n{3,}', '\n\n', content_md)
    return content_md


def distribute_to_all_programs(content: str, main_dir: str, filename: str):
    """
    将内容写入main_dir下每一个子文件夹中的filename文件。
    """
    if not content:
        print("内容为空，无法分发")
        return

    # 获取所有专业子文件夹
    if not os.path.exists(main_dir):
        print(f"主文件夹 {main_dir} 不存在，请先运行主爬虫。")
        return

    subfolders = [f.path for f in os.scandir(main_dir) if f.is_dir()]
    if not subfolders:
        print(f"在主文件夹 {main_dir} 下未找到任何专业子文件夹。")
        return

    success_count = 0
    for folder_path in subfolders:
        target_path = os.path.join(folder_path, filename)
        try:
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"已写入: {target_path}")
            success_count += 1
        except Exception as e:
            print(f"写入失败 {target_path}: {e}")

    print(f"\n分发完成！共写入 {success_count} 个专业文件夹。")


if __name__ == "__main__":
    print("=== 通用申请规则分发脚本 ===\n")

    # 提取规则内容
    rules_md = fetch_and_convert_rules(RULES_URL)
    if not rules_md:
        print("未能获取规则内容，脚本终止。")
        exit(1)

    # 在内容前添加标题和来源信息
    header = f"# 通用申请与录取规则\n\n> 来源: {RULES_URL}\n\n---\n\n"
    full_content = header + rules_md

    # 分发到每个专业文件夹
    distribute_to_all_programs(full_content, MAIN_DATA_DIR, OUTPUT_FILENAME)

    print("\n脚本执行完毕。")