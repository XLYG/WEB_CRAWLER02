import asyncio
import httpx
from bs4 import BeautifulSoup
import os
import re
from urllib.parse import urljoin
import hashlib


class LUH_Master_Resilient_Scraper:
    def __init__(self):
        self.base_url = "https://www.uni-hannover.de"
        self.start_url = "https://www.uni-hannover.de/en/studium/studienangebot/gesamt?tx_luhsis_plugin%5baction%5d=prospectiveList&tx_luhsis_plugin%5bcontroller%5d=Course&tx_luhsis_plugin%5bFilterData%5d%5bstudyLevels%5d=3"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
        # 缩短根目录名称
        self.root_dir = os.path.abspath("LUH_Archive")
        if not os.path.exists(self.root_dir):
            os.makedirs(self.root_dir)
        self.semaphore = None

    def sanitize(self, text, max_len=60):
        """清洗并截断文件名，超路径字符上限"""
        if not text: return "Unknown"
        # 移除软连字符、特殊空白符、智能引号
        text = text.replace('\xad', '').replace('\xa0', ' ').replace('’', "'").replace('‘', "'")
        # 过滤非法字符
        clean = re.sub(r'[\\/*?:"<>|]', "", text)
        clean = " ".join(clean.split()).replace(" ", "_")
        # 强制截断
        if len(clean) > max_len:
            return clean[:max_len]
        return clean

    async def fetch_html(self, client, url):
        try:
            resp = await client.get(url, timeout=30.0)
            return resp.text if resp.status_code == 200 else None
        except Exception as e:
            return None

    async def download_pdf(self, client, url, folder_path):
        """物理下载德语版 PDF，带文件名长度控制"""
        exclude_keywords = ['_en_', 'english', 'translation', '-en.pdf', '_en.pdf']
        if any(key in url.lower() for key in exclude_keywords):
            return None

        # 提取原始文件名并截断
        raw_name = url.split('/')[-1]
        name_part, ext = os.path.splitext(raw_name)
        # 限制文件名在 50 字符以内 + 后缀
        safe_name = self.sanitize(name_part, max_len=50) + ext
        save_path = os.path.join(folder_path, safe_name)

        # Windows 长路径支持前缀 (针对绝对路径)
        if os.name == 'nt' and not save_path.startswith("\\\\?\\"):
            save_path = "\\\\?\\" + save_path

        try:
            resp = await client.get(url, timeout=60.0)
            if resp.status_code == 200:
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                return safe_name
        except Exception as e:
            print(f"  PDF 下载失败: {url[:50]}... | {e}")
        return None

    def extract_urls(self, html):
        soup = BeautifulSoup(html, 'lxml')
        links = []
        sections = soup.find_all('div', id=re.compile(r'^section-[A-Z]$'))
        for sec in sections:
            for a in sec.find_all('a', href=True):
                if '/detail/' in a['href']:
                    links.append(urljoin(self.base_url, a['href']))
        return list(dict.fromkeys(links))

    async def process_course(self, client, url):
        """处理单个专业，全流程包裹在 try-except 中防止 Client 崩溃"""
        async with self.semaphore:
            try:
                html = await self.fetch_html(client, url)
                if not html: return

                soup = BeautifulSoup(html, 'lxml')
                title_node = soup.select_one('h1.c-headline--h1')
                title_raw = title_node.get_text(strip=True) if title_node else "Unknown"

                # 文件夹名截断
                course_folder_name = self.sanitize(title_raw, max_len=80)
                course_folder = os.path.join(self.root_dir, course_folder_name)

                # 创建目录
                os.makedirs(course_folder, exist_ok=True)

                # Profile 提取
                profile_md = "## Profile\n\n| Feature | Detail |\n| :--- | :--- |\n"
                for p in soup.select('.c-stage-element__profile'):
                    k = p.select_one('.c-stage-element__profile--title')
                    v = p.select_one('.c-stage-element__profile--text')
                    if k and v:
                        profile_md += f"| **{k.get_text(strip=True)}** | {v.get_text(separator=' ', strip=True)} |\n"

                # Description & Course Content
                content_md = "## Program Content\n\n"
                for h2 in soup.find_all('h2', class_='c-headline--h2'):
                    h2_text = h2.get_text(strip=True)
                    if h2_text in ["Short Description", "Course Content"]:
                        content_md += f"### {h2_text}\n"
                        parent = h2.find_parent('div', class_='c-section__element') or h2.parent
                        for child in parent.find_all(['p', 'ul']):
                            if child.name == 'ul':
                                content_md += "\n".join(
                                    [f"- {li.get_text(strip=True)}" for li in child.find_all('li')]) + "\n"
                            else:
                                content_md += child.get_text(strip=True) + "\n\n"

                #  Accordion & PDF 下载
                accordion_md = "## Details & Admission\n\n"
                pdf_list = []
                for item in soup.select('.c-accordion__item'):
                    header = item.select_one('a.accordion-title')
                    content = item.select_one('.c-accordion__content')
                    if header:
                        accordion_md += f"### {header.get_text(strip=True)}\n\n"
                        if content:
                            accordion_md += content.get_text(separator="\n", strip=True) + "\n\n"
                            for a_pdf in content.find_all('a', href=True):
                                if a_pdf['href'].lower().endswith('.pdf'):
                                    p_url = urljoin(self.base_url, a_pdf['href'])
                                    p_name = await self.download_pdf(client, p_url, course_folder)
                                    if p_name:
                                        pdf_list.append(f"- [Local German PDF]({p_name})")

                # 写入 Markdown
                md_path = os.path.join(course_folder, "Overview.md")
                # Windows 长路径前缀
                if os.name == 'nt': md_path = "\\\\?\\" + md_path

                final_md = f"# {title_raw}\n\nURL: {url}\n\n{profile_md}\n{content_md}\n{accordion_md}"
                if pdf_list:
                    final_md += "\n## Documents\n\n" + "\n".join(pdf_list)

                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(final_md)

                print(f" 成功获取到:{title_raw[:40]}...")

            except Exception as e:
                print(f"无法处理专业 {url}: {e}")

    async def run(self):
        self.semaphore = asyncio.Semaphore(5)  # 降低并发以提高稳定性
        # 使用连接池管理
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        async with httpx.AsyncClient(headers=self.headers, limits=limits, follow_redirects=True) as client:
            print("正在拉取索引...")
            index_html = await self.fetch_html(client, self.start_url)
            if not index_html: return

            urls = self.extract_urls(index_html)
            print(f"发现 {len(urls)} 个专业，开始存档...")

            tasks = [self.process_course(client, u) for u in urls]
            await asyncio.gather(*tasks)
            print(f"保存完毕，请查看目录: {self.root_dir}")


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    scraper = LUH_Master_Resilient_Scraper()
    asyncio.run(scraper.run())