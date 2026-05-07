import os
import requests
import re
import time
import random
import shutil
from lxml import etree
from markdownify import markdownify as md
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INFO_URL = "https://www.fau.eu/studying/international-students/application-and-enrollment-for-international-applicants/applying-for-a-masters-degree-program-as-an-international-student/"
TARGET_ROOT_DIR = "FAU_Erlangen_Data"
COMMON_FILE_NAME = "International_Master_Application_Guide.md"

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


def clean_html_to_md(html_node):
    """将HTML节点内容洗净并转为Markdown"""
    if html_node is None: return ""
    html_str = etree.tostring(html_node, encoding='unicode', method='html')
    # 彻底移除图片、脚本和翻译标签
    html_str = re.sub(r'<img.*?>', '', html_str, flags=re.DOTALL)
    html_str = re.sub(r'</?font.*?>', '', html_str, flags=re.IGNORECASE)
    return md(html_str, heading_style="ATX").strip()


def safe_get(url):
    time.sleep(random.uniform(1.0, 2.0))
    try:
        res = session.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        return res
    except Exception as e:
        print(f"无法获取页面内容: {e}")
        return None


def main_task():
    """执行同步抓取与分发"""
    # 抓取指南页面
    print(f"[1/2] 正在抓取国际生申请通用指南...")
    res = safe_get(INFO_URL)
    if not res: return

    tree = etree.HTML(res.text)
    # 锁定正文容器
    content_node = tree.xpath('//div[contains(@class, "entry-content")]')

    if not content_node:
        content_node = tree.xpath('//main')

    if content_node:
        # 准备 Markdown 内容
        full_md = f"# International Master Application Guide\n\nSource: {INFO_URL}\n\n---\n\n"
        full_md += clean_html_to_md(content_node[0])

        # 临时保存母本到当前运行目录
        temp_master_path = os.path.join(os.getcwd(), COMMON_FILE_NAME)
        with open(temp_master_path, 'w', encoding='utf-8') as f:
            f.write(full_md)
        print(f"通用指南母本已生成。")

        # 检查并分发
        if not os.path.exists(TARGET_ROOT_DIR):
            print(f"找不到专业根目录: {TARGET_ROOT_DIR}")
            print("请确保同名专业爬虫已经运行过，或者手动将代码中的 TARGET_ROOT_DIR 修改为实际的文件夹路径。")
            return

        print(f"[2/2] 正在将指南分发至各专业子文件夹...")
        dist_count = 0

        # 获取所有子文件夹
        for folder_name in os.listdir(TARGET_ROOT_DIR):
            sub_folder_path = os.path.join(TARGET_ROOT_DIR, folder_name)

            if os.path.isdir(sub_folder_path):
                dest_file_path = os.path.join(sub_folder_path, COMMON_FILE_NAME)
                # 使用 copy2
                shutil.copy2(temp_master_path, dest_file_path)
                dist_count += 1

        # 任务完成后清理临时文件
        if os.path.exists(temp_master_path):
            os.remove(temp_master_path)

        print(f"\n通用指南已分发至 {dist_count} 个专业文件夹")
    else:
        print("未能定位到网页正文，请检查路径。")


if __name__ == "__main__":
    main_task()