import requests
from bs4 import BeautifulSoup, NavigableString
import os
import re
from urllib.parse import urljoin
import time

# --- 核心配置 ---
INDEX_URL = "https://www.tu-darmstadt.de/studieren/studieninteressierte/studienangebot_studiengaenge/master_studiengaenge/index.en.jsp"
DOMAIN = "https://www.tu-darmstadt.de"
SAVE_DIR = "TU_Darmstadt_Master_Vault"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9"
}

# 初始化环境
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)


class TUDarmstadtFinalScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def clean_text(self, text):
        """清洗文本中的冗余图标字符和多余空格"""
        if not text: return ""
        # 移除常见的图标文本及其变体
        forbidden = [
            r'Arrow\s+Right', r'fass', r'fa-long-arrow-right',
            r'fa-chevron-down', r'fa-fw', r'icon'
        ]
        for pattern in forbidden:
            text = re.sub(pattern, '', text, flags=re.I)
        return re.sub(r'\s+', ' ', text).strip()

    def element_to_markdown(self, element):
        """递归转换：保留链接、格式化加粗、处理换行"""
        if element is None: return ""
        if isinstance(element, NavigableString):
            return self.clean_text(str(element))

        tag = element.name
        content = ""
        for child in element.children:
            content += self.element_to_markdown(child)

        # md转化
        if tag == 'a':
            href = element.get('href')
            if href:
                # 再次清洗链接内的显示文字
                link_text = self.clean_text(element.get_text())
                return f" [{link_text}]({urljoin(DOMAIN, href)}) "
            return content
        elif tag in ['strong', 'b']:
            return f"**{content.strip()}**"
        elif tag == 'p':
            return f"\n{content.strip()}\n"
        elif tag == 'li':
            return f"\n- {content.strip()}"
        elif tag == 'br':
            return "\n"
        elif tag in ['table', 'tbody']:
            return f"\n{content}\n"
        elif tag == 'tr':
            return f"{content}\n"
        elif tag == 'td':
            # 表格处理简化
            return f" {content.strip()} |"
        else:
            return content

    def find_content_by_semantic_title(self, soup, keywords):
        # 根据标题内容锁定其所属的折叠内容区
        headers = soup.find_all(['h2', 'h3'])
        for h in headers:
            text = h.get_text().lower()
            if any(kw.lower() in text for kw in keywords):
                # 寻找包含该标题的最近折叠页
                container = h.find_parent("div", class_="toggle-section-content")
                if container:
                    # 提取该容器内实际折叠的部分
                    content_box = container.find("div", class_="collapse")
                    return content_box if content_box else container
                # 兜底：返回下一个 div
                return h.find_next("div")
        return None

    def get_all_links(self):
        """获取列表页所有专业的绝对路径链接"""
        print(f">>> 正在获取列表页链接: {INDEX_URL}")
        try:
            res = self.session.get(INDEX_URL)
            soup = BeautifulSoup(res.text, 'html.parser')
            # 指定锁定 A-Z 列表中的 a 标签
            items = soup.select("ul.sublist li h4 a.link")
            links = [urljoin(INDEX_URL, item.get('href')) for item in items if item.get('href')]
            print(f"--- 成功发现 {len(links)} 个专业链接 ---")
            return list(dict.fromkeys(links))  # 去重
        except Exception as e:
            print(f"!!! 列表获取失败: {e}")
            return []

    def scrape_detail(self, url):
        """解析单页面详情"""
        try:
            res = self.session.get(url, timeout=20)
            soup = BeautifulSoup(res.text, 'html.parser')

            # 提取标题与页眉
            header = soup.select_one("#artikel__details_ header")
            if not header: return
            h1 = header.find("h1")
            h2 = header.find("h2")
            title = h1.get_text(strip=True) if h1 else "Unknown"
            subtitle = h2.get_text(strip=True) if h2 else ""

            safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title).strip()
            md_body = f"# {title}\n\n## {subtitle}\n\n"

            # 介绍
            md_body += "### 1. Description\n"
            intro = soup.find(id="absatz_1")
            if intro:
                md_body += intro.get_text(separator="\n", strip=True) + "\n\n"
            else:
                md_body += "Description\n\n"

            # 信息和要求部分
            # 关键词：Admission, General, Requirements
            admission_area = self.find_content_by_semantic_title(soup,
                                                                 ["Admission", "General Information", "Requirements"])
            if admission_area:
                md_body += "### 2. General Information & Admission\n"
                # 如果是表格，直接处理表格
                table = admission_area.find("table")
                if table:
                    for tr in table.find_all("tr"):
                        tds = tr.find_all("td")
                        if len(tds) >= 2:
                            key = tds[0].get_text(strip=True)
                            val = self.element_to_markdown(tds[1])
                            md_body += f"- **{key}**: {val}\n"
                else:
                    md_body += self.element_to_markdown(admission_area)
                md_body += "\n"

            # Career Perspectives & Service
            # 关键词：Career, Perspectives, Service
            career_area = self.find_content_by_semantic_title(soup, ["Career Perspectives", "Career Service"])
            if career_area:
                raw_career = self.element_to_markdown(career_area)
                if raw_career.strip():
                    md_body += "### 3. Career Perspectives\n"
                    # 清除可能重复出现的标题文字
                    clean_career = re.sub(r'Career\s+Perspectives\s+&\s+Service', '', raw_career, flags=re.I)
                    md_body += f"{clean_career.strip()}\n"

            # 保存文件
            with open(os.path.join(SAVE_DIR, f"{safe_filename}.md"), "w", encoding="utf-8") as f:
                f.write(md_body)
            print(f"  [SUCCESS] {title}")

        except Exception as e:
            print(f"  [FAILED] {url}: {e}")


if __name__ == "__main__":
    crawler = TUDarmstadtFinalScraper()

    target_links = crawler.get_all_links()

    for i, link in enumerate(target_links):
        crawler.scrape_detail(link)
        if i % 5 == 0:
            time.sleep(1.5)
        else:
            time.sleep(0.5)

    print(f"\n任务全部完成。结果存储在: {SAVE_DIR} 文件夹中。")