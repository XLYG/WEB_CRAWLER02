import os
import re
import time
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "Uni_Giessen",
    "START_URL": "https://www.uni-giessen.de/de/studium/studienangebot/finder?form.widgets.q=&form.widgets.graduation%3Alist=master&form.widgets.graduation-empty-marker=1&form.widgets.begin-empty-marker=1&form.widgets.numerus_clausus-empty-marker=1&form.widgets.language-empty-marker=1&form.widgets.area%3Alist=--NOVALUE--&form.widgets.area-empty-marker=1&form.widgets.faculty%3Alist=--NOVALUE--&form.widgets.faculty-empty-marker=1&form.widgets.condition-empty-marker=1&protected_1=",
    "OUTPUT_DIR": "Uni_Giessen_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "WAIT_TIME": 2,
    "TIMEOUT": 60000,
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清洗路径名，处理德语变音，解决长路径问题。"""
        if not name: return "Unknown_Major"
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 50
        return name[:limit]

    @staticmethod
    def process_content_logic(html):
        """格式转化与修复"""
        if not html: return ""
        html = re.sub(r'<(script|style|nav|noscript|svg|button)[^>]*>.*?</\1>', '', html,
                      flags=re.DOTALL | re.IGNORECASE)
        content = md(html, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        content = '\n'.join(lines)

        # 遇到冒号则换行,但排除掉 URL 协议头 (https://)
        content = re.sub(r'(?<!http)(?<!https):\s*', ':\n\n', content)

        # 标题规范化：确保 ## 出现在行首
        lines = content.split('\n')
        fixed_lines = []
        for line in lines:
            line = line.strip()
            if '##' in line:
                line = re.sub(r'^.*?(##+.*)', r'\1', line)
            fixed_lines.append(line)
        content = '\n'.join(fixed_lines)

        # 表格修复:循环多次合并，确保中间有多个空行的表格行也能接上
        for _ in range(3):
            content = re.sub(r'(\|\s*)\n+(\s*\|)', r'\1\n\2', content)
            content = re.sub(r'(\|\s*)\n+(\s*\| ---)', r'\1\n\2', content)

        # 最终的合并脱水
        content = re.sub(r'\n{3,}', '\n\n', content)

        return content.strip()


class GiessenScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        with sync_playwright() as p:
            print("正在获取吉森大学专业详情列表")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 列表页抓取
            print(f"访问入口：{CONFIG['START_URL']}")
            try:
                page.goto(CONFIG["START_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
                time.sleep(CONFIG["WAIT_TIME"])
            except Exception as e:
                print(f"访问失败：{e}")
                return

            tasks = []
            items = page.locator('li.courses__search-results-item').all()
            for item in items:
                try:
                    anchor = item.locator('h2.multi a.link').first
                    name = anchor.inner_text().strip()
                    href = anchor.get_attribute("href")
                    if href:
                        uid = re.search(r'resolveuid/([a-z0-9]+)', href).group(1) if "resolveuid" in href else "info"
                        tasks.append({"name": name, "url": urljoin(CONFIG["START_URL"], href), "uid": uid})
                except:
                    continue

            print(f"发现了 {len(tasks)} 个硕士专业。执行采集")

            # 详情页处理
            for idx, task in enumerate(tasks):
                print(f"\n[{idx + 1}/{len(tasks)}] 正在解析：{task['name']}")
                self.process_detail(context, task)

            browser.close()
            print("\n 任务全部圆满完成。")

    def process_detail(self, context, task):
        page = context.new_page()
        try:
            page.goto(task["url"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
            time.sleep(CONFIG["WAIT_TIME"])

            # 创建文件夹
            folder_name = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{task['uid']}"
            major_dir = os.path.join(self.output_dir, folder_name)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            # 提取前四个 tabbertab
            print("    -> 提取内容切片并进行格式修复...")
            container_xpath = '//*[@id="content-core"]/div/div[1]'

            tabs_html = page.evaluate(f"""() => {{
                const container = document.evaluate('{container_xpath}', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (!container) return "";
                const tabs = Array.from(container.querySelectorAll('div.tabbertab'));
                return tabs.slice(0, 4).map(t => t.outerHTML).join('\\n');
            }}""")

            if not tabs_html:
                tabs_html = page.locator('#content-core').inner_html()

            final_md = f"# {task['name']}\n\n- **Source**: {task['url']}\n\n"
            final_md += CrawlerUtils.process_content_logic(tabs_html)

            # 写入文件
            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, safe_file_name), "w", encoding="utf-8") as f:
                f.write(final_md)

            print(f"    存储成功 {safe_file_name}")

        except Exception as e:
            print(f"    详情页处理错误：{e}")
        finally:
            page.close()


if __name__ == "__main__":
    scraper = GiessenScraper()
    scraper.run()