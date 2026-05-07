import requests
from bs4 import BeautifulSoup
import os
import re
import time
from urllib.parse import urljoin
from markdownify import markdownify as md  # 用于 HTML 到 Markdown 的转换

# --- 核心配置 ---
LIST_URL = "https://www.uni-stuttgart.de/studium/master/"
DOMAIN = "https://www.uni-stuttgart.de"
TARGET_CLASS = "generic-list-item"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8"
}
SAVE_DIR = "Uni_Stuttgart_Master_Archive"

if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)


class StuttgartFinalScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def sanitize(self, name):
        # 移除常见后缀并替换非法字符
        clean_name = re.sub(r'\(Master\)|Master of Science|Master|M.Sc.|M.A.', '', name, flags=re.I)
        return re.sub(r'[\\/*?:"<>|]', "_", clean_name).strip()

    def get_program_links(self):
        """从列表页获取所有专业链接"""
        print(f"正在获取列表页: {LIST_URL}")
        try:
            res = self.session.get(LIST_URL, timeout=20)
            res.raise_for_status()
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'html.parser')

            links = set()  # 使用 set 避免重复
            items = soup.find_all("div", class_=lambda x: x and TARGET_CLASS in x)

            for item in items:
                a_tag = item.find("a", href=True)
                if a_tag:
                    full_url = urljoin(DOMAIN, a_tag['href'])
                    links.add(full_url)

            print(f"成功发现 {len(links)} 个专业")
            return list(links)
        except Exception as e:
            print(f"列表抓取失败: {e}")
            return []

    def download_pdf(self, soup, safe_title, folder):
        """下载 Zulassungsordnung 或 Admission Regulations PDF"""
        a_tags = soup.find_all("a", href=True)
        for a in a_tags:
            text = a.get_text().strip().lower()
            href = a['href']
            if ("zulassungsordnung" in text or "admission regulations" in text) and ".pdf" in href.lower():
                full_url = urljoin(DOMAIN, href)
                filename = f"{safe_title}_Zulassungsordnung.pdf"
                save_path = os.path.join(folder, filename)

                print(f"   正在下载: {filename}")
                try:
                    pdf_res = self.session.get(full_url, stream=True, timeout=30)
                    pdf_res.raise_for_status()
                    with open(save_path, 'wb') as f:
                        for chunk in pdf_res.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print(f"   下载完成: {save_path}")
                    return  # 只下载第一个匹配的 PDF
                except Exception as e:
                    print(f"   PDF 下载失败: {e}")
        print("   未找到匹配的 PDF")

    def process_program(self, url):
        """处理单个专业页面，包括英文重定向"""
        try:
            res = self.session.get(url, timeout=20)
            res.raise_for_status()
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'html.parser')

            # 检查英文重定向
            alert_div = soup.find("div", class_="alert alert-danger is-margin-bottom")
            if alert_div:
                en_link_tag = alert_div.find("a", href=True)
                if en_link_tag:
                    en_url = urljoin(DOMAIN, en_link_tag['href'])
                    print(f"   检测到英文重定向，跳转到: {en_url}")
                    res = self.session.get(en_url, timeout=20)
                    res.raise_for_status()
                    res.encoding = 'utf-8'
                    soup = BeautifulSoup(res.text, 'html.parser')
                    url = en_url

            # 提取标题
            h1 = soup.find("h1")
            if not h1:
                print(f"   未找到标题，跳过: {url}")
                return
            raw_title = h1.get_text(strip=True)
            safe_title = self.sanitize(raw_title)

            print(f"正在处理: {raw_title} ({url})")

            # 创建文件夹
            program_dir = os.path.join(SAVE_DIR, safe_title)
            os.makedirs(program_dir, exist_ok=True)

            # 提取内容并转换为 MD
            content_area = soup.find("main") or soup.find(id="content")
            if content_area:
                # 使用 markdownify 转换 HTML 到 MD
                md_text = md(str(content_area), heading_style="ATX", bullets="-*+", strip=['script', 'style', 'nav', 'footer', 'header', 'aside'])
                md_path = os.path.join(program_dir, f"{safe_title}.md")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(f"# {raw_title}\n\nURL: {url}\n\n{md_text}")
                print(f"   MD 保存完成: {md_path}")
            else:
                print("   未找到内容区域")

            # 下载 PDF
            self.download_pdf(soup, safe_title, program_dir)

        except Exception as e:
            print(f"详情页处理失败 {url}: {e}")


if __name__ == "__main__":
    bot = StuttgartFinalScraper()
    links = bot.get_program_links()

    if not links:
        print("未抓取到链接，请检查 Class 名称是否在当前页面存在。")
    else:
        for i, link in enumerate(links, 1):
            print(f"\n处理链接 {i}/{len(links)}")
            bot.process_program(link)
            time.sleep(1.5)  # 增加延迟避免反爬

    print("\n--- 任务执行完毕 ---")