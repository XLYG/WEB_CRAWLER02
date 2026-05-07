import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "HFT_Stuttgart",
    "ROOT_DOMAIN": "https://www.hft-stuttgart.de",
    "LIST_URL": "https://www.hft-stuttgart.de/#c6490",
    "OUTPUT_DIR": "HFT_Stuttgart_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "WAIT_TIME": 3,
    "TIMEOUT": 60000,
    "RESUME_MODE": True
}

# PDF 语义匹配词库
PDF_KEYWORDS = ["Zugang", "Zulassung", "Auswahl", "Regulation", "Admission", "SPO", "ASPO", "Prüfungsordnung",
                "Studienordnung"]


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        # 路径限制
        limit = 60 if is_folder else 40
        return name[:limit]

    @staticmethod
    def nuke_cookie_modal(page):
        """清除 Cookie """
        try:
            page.evaluate("""() => {
                const trash = ['#usercentrics-root', '.cookie-notice', '.banner-wrapper', 'div[class*="Consent"]', '#uc-center-container'];
                trash.forEach(s => { const el = document.querySelector(s); if(el) el.remove(); });
                // 递归清理影罩
                document.querySelectorAll('*').forEach(el => { if(el.shadowRoot) { el.shadowRoot.innerHTML = ''; el.remove(); } });
                // 强行恢复滚动
                document.body.style.setProperty('overflow', 'auto', 'important');
                document.documentElement.style.setProperty('overflow', 'auto', 'important');
            }""")
        except:
            pass

    @staticmethod
    def purified_to_md(page, element_handle):
        """执行彻底的 DOM 净化并转为 Markdown"""
        try:
            raw_html = element_handle.evaluate("""el => {
                const clone = el.cloneNode(true);
                // 移除：blockquote、图片、视频、按钮、导航
                const trash = clone.querySelectorAll('blockquote, img, picture, figure, video, script, style, svg, button, noscript, nav, .offcanvas, .headerDesktop, .fh-breadcrumb');
                trash.forEach(n => n.remove());
                // 补全超链接
                clone.querySelectorAll('a').forEach(a => {
                    let href = a.getAttribute('href');
                    if(href && typeof href === 'string' && href.startsWith('/')) {
                        a.setAttribute('href', 'https://www.hft-stuttgart.de' + href);
                    }
                });
                return clone.innerHTML;
            }""")
            content = md(raw_html, heading_style="ATX")
            lines = [line.strip() for line in content.split('\n')]
            return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        except:
            return ""


class HFTStuttgartScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})
        self.common_appendix_md = ""

    def run(self):
        print(f" 正在启动 HFT Stuttgart 采集程序")

        # 列表获取 (Requests)
        tasks = self.fetch_major_list()
        if not tasks: return

        # 详情解析 (Playwright 连续会话)
        with sync_playwright() as p:
            print(" 正在唤醒浏览器进程")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 预抓取通用附录
            self.fetch_common_info(page)

            for idx, task in enumerate(tasks):
                url_slug = task["url"].strip("/").split("/")[-1]
                safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
                safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
                check_path = os.path.join(self.output_dir, safe_folder, safe_file_name)

                # 断点续爬判定
                if CONFIG["RESUME_MODE"] and os.path.exists(check_path):
                    print(f"    跳过{task['name']} -档案已存在")
                    continue

                print(f"\n[任务阶段 {idx + 1}/{len(tasks)}] 正在解析：{task['name']}")
                self.process_major(page, task, safe_folder, safe_file_name)
                time.sleep(random.uniform(1.5, 3))

            browser.close()
        print(f"\n 所有专业采集任务已圆满结束")

    def fetch_major_list(self):
        print(f" 正在连接主页，拉取专业名录")
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=20)
            tree = etree.HTML(res.text)
            wrappers = tree.xpath('//div[contains(@class, "studycourses__wrapper--master")]')
            tasks = []
            for wrap in wrappers:
                for a in wrap.xpath('.//ul/li/a'):
                    name = "".join(a.xpath('.//text()')).strip()
                    href = a.get('href')
                    if href:
                        tasks.append({"name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href)})
            print(f" 捕捉完毕，共计 {len(tasks)} 个目标。")
            return tasks
        except Exception as e:
            print(f" 列表获取失败: {e}")
            return []

    def fetch_common_info(self, page):
        """获取通用注册说明"""
        try:
            admission_url = "https://www.hft-stuttgart.de/en/study/academic-life/getting-started/information-on-registration-contact-persons"
            page.goto(admission_url, wait_until="networkidle")
            CrawlerUtils.nuke_cookie_modal(page)
            node = page.locator('#main-content, main, article').first
            if node.count() > 0:
                self.common_appendix_md = CrawlerUtils.purified_to_md(page, node)
                print("    成功，通用注册信息已载入")
        except:
            pass

    def process_major(self, page, task, folder_name, file_name):
        try:
            # 进入详情页并删除弹窗
            page.goto(task["url"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
            CrawlerUtils.nuke_cookie_modal(page)
            time.sleep(2)

            # 逻辑为检索英语版描述页
            eng_data = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a'));
                const found = links.find(a => {
                    const t = a.innerText.toLowerCase();
                    return t.includes('english language website') || t.includes('website in english');
                });
                return found ? found.href : null;
            }""")

            if eng_data:
                print(f"    发现指定穿透点，正在切换至英语详情页：{eng_data}")
                page.goto(eng_data, wait_until="networkidle")
                CrawlerUtils.nuke_cookie_modal(page)
                time.sleep(2)

            # 准备文件夹
            major_dir = os.path.join(self.output_dir, folder_name)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            # 组装文档
            md_content = f"URL: {page.url}\n\n# {task['name']}\n\n---\n\n"

            # 提取核心正文
            print("    提取页面主体")
            main_block = page.locator('main#main, main, article').first
            # 等待内容注入
            try:
                page.wait_for_function("document.querySelector('main').innerText.trim().length > 100", timeout=8000)
            except:
                pass

            md_content += CrawlerUtils.purified_to_md(page, main_block)

            # PDF 语义匹配下载
            print("   正在扫描准入与考试规章 PDF")
            pdf_links = page.locator('a[href*=".pdf"]').all()
            for link in pdf_links:
                href = link.get_attribute("href")
                text = link.inner_text().strip()
                if any(re.search(kw, text + href, re.I) for kw in PDF_KEYWORDS):
                    pdf_url = urljoin(CONFIG["ROOT_DOMAIN"], href)
                    safe_pdf_name = f"Rules_{CrawlerUtils.sanitize_path(text if text else 'doc', False)}.pdf"
                    print(f"       找到 [{text[:20]}]，下载中")
                    self.download_pdf(pdf_url, os.path.join(major_dir, safe_pdf_name))

            # 外部附录穿透 (Konstanz 等)
            print("   检查是否有外部准入详情页")
            ext_url = page.evaluate("""() => {
                const link = Array.from(document.querySelectorAll('a')).find(a => a.href.includes('htwg-konstanz.de'));
                return link ? link.href : null;
            }""")

            if ext_url:
                print(f"    正在抓取外部附录：{ext_url}")
                page.goto(ext_url, wait_until="domcontentloaded")
                time.sleep(2)
                sub_main = page.locator('main, article, #main-content').first
                if sub_main.count() > 0:
                    md_content += "\n\n---\n## 外部准入说明 (Appendix)\n\n" + CrawlerUtils.purified_to_md(page,
                                                                                                          sub_main)

            # 追加通用的缓存信息
            if self.common_appendix_md:
                md_content += "\n\n---\n## 通用注册信息 (General Info)\n\n" + self.common_appendix_md

            # 保存
            with open(os.path.join(major_dir, file_name), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    档案已归档")

        except Exception as e:
            print(f"     详情解析失败：{e}")

    def download_pdf(self, url, path):
        try:
            r = self.session.get(url, stream=True, timeout=30)
            if r.status_code == 200:
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(8192): f.write(chunk)
        except:
            pass


if __name__ == "__main__":
    scraper = HFTStuttgartScraper()
    scraper.run()