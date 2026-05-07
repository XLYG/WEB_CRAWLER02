import asyncio
import os
import re
import random
import requests
from playwright.async_api import async_playwright
from markdownify import markdownify as md
from urllib.parse import urljoin


class ColognePathDefensiveScraper:
    def __init__(self):
        self.university_name = "Koeln_Data"
        self.possible_files = ["cologne_majors_precise.txt", "cologne_majors_final.txt", "cologne_links_final.txt"]
        self.failed_log = "failed_details_log.txt"
        self.base_url = "https://studieninteressierte.uni-koeln.de"

        # 附件下载配置
        self.session = requests.Session()
        self.session.trust_env = True

    def clean_and_truncate_path(self, text, max_len=70):
        """
        文件名长度控制核心：
        处理德语变音符号
        移除 Windows 非法字符
        强制限制长度防止路径过长 (MAX_PATH)
        """
        text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        # 移除 Windows 下非法的路径字符
        clean_text = re.sub(r'[\\/*?:"<>|]', "_", text).strip()
        # 强制截断长度
        if len(clean_text) > max_len:
            clean_text = clean_text[:max_len].strip()
        return clean_text

    async def scrape_single_major(self, name, url):
        major_id = re.search(r'id=(\d+)', url).group(1) if "id=" in url else "000"

        # 生成尽量不超长度的文件名
        short_name = self.clean_and_truncate_path(name)
        folder_name = f"{short_name}_{major_id}"
        major_folder = os.path.join(self.university_name, folder_name)

        # 生成 MD 文件名
        target_md = os.path.join(major_folder, f"{short_name}.md")

        # 断点续爬：如果已存在且不为空，直接跳过
        if os.path.exists(target_md) and os.path.getsize(target_md) > 0:
            print(f"   {name} (路径安全)")
            return "skipped"

        os.makedirs(major_folder, exist_ok=True)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                print(f"独立会话抓取: {name[:40]}...")
                await page.goto(url, wait_until="load", timeout=50000)
                await page.wait_for_selector("h1", timeout=20000)
                await asyncio.sleep(2)

                # 下载 PDF
                doc_area = page.locator("#documents_overview")
                if await doc_area.count() > 0:
                    links = await doc_area.locator("a.c-text-icon").all()
                    for link in links:
                        link_text = await link.inner_text()
                        if any(k in link_text for k in ["准入", "规定", "Zulassung", "Ordnung"]):
                            pdf_href = await link.get_attribute("href")
                            if pdf_href:
                                pdf_url = urljoin(self.base_url, pdf_href)
                                pdf_path = os.path.join(major_folder, "Admission_Regulations.pdf")
                                if not os.path.exists(pdf_path):
                                    r = self.session.get(pdf_url, timeout=30)
                                    with open(pdf_path, "wb") as f:
                                        f.write(r.content)
                            break

                # 抓取各内容区块
                sections_to_grab = ["#csteckbrief", "#cinhalt", "#cperspektive", "#ceignung", "#ckosten", "#cberatung",
                                    "#causwahlverfahren", "#cbewerbung", "#cfristen"]
                detail_html = ""
                for selector in sections_to_grab:
                    section_loc = page.locator(selector)
                    if await section_loc.count() > 0:
                        title = selector.replace("#c", "").capitalize()
                        detail_html += f"\n\n## {title}\n\n" + await section_loc.inner_html() + "\n\n---"

                # 深度清洗
                clean_md = md(detail_html, heading_style="ATX",
                              strip=['svg', 'font', 'script', 'style', 'button', 'img'])
                clean_md = clean_md.replace('(/', f'({self.base_url}/')
                clean_md = re.sub(r'\n\s*\n', '\n\n', clean_md)

                with open(target_md, "w", encoding="utf-8") as f:
                    f.write(f"# {name}\n\nURL: {url}\n\n{clean_md}")

                print(f"    抓取并清洗成功")
                return True

            except Exception as e:
                print(f"    失败: {e}")
                return False
            finally:
                await browser.close()

    async def run(self):
        actual_file = next((f for f in self.possible_files if os.path.exists(f)), None)
        if not actual_file:
            print("未找到列表文件。")
            return

        with open(actual_file, "r", encoding="utf-8") as f:
            tasks = [line.strip().split("||") for line in f if "||" in line]

        print(f"启动任务，当前列表共 {len(tasks)} 条。")

        for name, url in tasks:
            result = await self.scrape_single_major(name, url)

            if result == "skipped":
                continue

            if result is True:
                wait = random.uniform(8, 12)
                print(f"等待冷静期 {wait:.1f} 秒...")
                await asyncio.sleep(wait)
            else:
                print(" 触发异常，强制休眠 120 秒...")
                await asyncio.sleep(120)


if __name__ == "__main__":
    scraper = ColognePathDefensiveScraper()
    asyncio.run(scraper.run())