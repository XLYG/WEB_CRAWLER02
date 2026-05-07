import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "OTH_AW",
    "ROOT_DOMAIN": "https://www.oth-aw.de",
    "LIST_URL": "https://www.oth-aw.de/studium/studienangebote/studiengaenge/#graduation=2",
    "OUTPUT_DIR": "OTH_AW_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "WAIT_TIME": 3,
}

# 语义匹配关键词池
TARGET_KEYWORDS = [
    "Alles auf einen Blick", "At a glance", "一览",
    "HIGHLIGHTS DES STUDIENGANGS", "Course highlights", "亮点",
    "Jetzt bewerben", "Apply now", "立即申请",
    "Zugangs- und Zulassungsvoraussetzungen", "Admission requirements", "准入要求",
    "Berufschancen", "Career opportunities", "就业前景",
    "Studienmodelle", "Study models", "学习模式",
    "Bewerbung", "Application", "申请",
    "Studiengebühren", "Tuition fees", "学费",
    "Zertifizierungskurse", "Certification courses", "证书课程"
]


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_node(element):
        if element is None: return ""

        for a in element.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        noise_tags = ['blockquote', 'img', 'picture', 'figure', 'video', 'svg', 'button', 'nav', 'script', 'style',
                      'noscript']
        for tag in noise_tags:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        raw_html = etree.tostring(element, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        markdown_text = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class OTHAWScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        print(f" 启动 {CONFIG['UNIVERSITY_NAME']} 模式采集")

        # Playwright 提取专业列表
        major_tasks = self.fetch_list_via_playwright()

        if not major_tasks:
            print("未能成功获取列表")
            return

        print(f" 成功！共锁定 {len(major_tasks)} 个硕士专业")

        #  Requests 解析详情页
        for idx, task in enumerate(major_tasks):
            print(f"\n[{idx + 1}/{len(major_tasks)}] 正在解析：{task['name']}")
            self.process_detail_with_requests(task)
            time.sleep(1)

        print(f"\n 所有任务已执行完毕")

    def fetch_list_via_playwright(self):
        """Playwright 负责处理动态锚点筛选后的专业列表"""
        tasks = []
        with sync_playwright() as p:
            print(f"  正在通过浏览器访问：{CONFIG['LIST_URL']}")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            try:
                page.goto(CONFIG["LIST_URL"], wait_until="networkidle")
                # 删除遮挡
                page.evaluate(
                    "() => { const b=document.querySelector('.consent-modal') || document.querySelector('#usercentrics-root'); if(b) b.remove(); }")

                # 等待特定于 Master 的列表项出现
                page.wait_for_selector('li.in2studyfinder__item', timeout=20000)
                time.sleep(CONFIG["WAIT_TIME"])

                items = page.locator('li.in2studyfinder__item').all()
                for item in items:
                    if not item.is_visible(): continue
                    anchor = item.locator('a.in2studyfinder__item-link').first
                    name = anchor.inner_text().strip()
                    href = anchor.get_attribute("href")
                    degree = item.locator('span.degree').inner_text().strip()

                    if href:
                        tasks.append({
                            "name": name,
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "degree": degree
                        })
            except Exception as e:
                print(f"列表获取失败：{e}")

            browser.close()
        return tasks

    def process_detail_with_requests(self, task):
        """Requests 负责：语义块提取与物理去重"""
        try:
            res = self.session.get(task["url"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            # 目录准备
            url_slug = task["url"].strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_path = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_path): os.makedirs(major_path)

            # Markdown 初始化
            md_content = f"URL: {task['url']}\n\n"
            md_content += f"**{task['name']} | {task['degree']}**\n\n---\n\n"

            # 核心提取逻辑：物理防重语义扫描
            # 找到 main 容器
            main_node = tree.xpath('//main')[0] if tree.xpath('//main') else None
            if main_node is None: return

            print("    执行语义块提取 ")
            processed_frame_ids = set()  # 用于记录已处理的，杜绝重复写入

            # 寻找所有的 h2 标题
            h2_headers = main_node.xpath('.//h2')

            for h2 in h2_headers:
                h2_text = "".join(h2.xpath('.//text()')).strip()

                # 检查标题是否命中关键词
                if any(kw.lower() in h2_text.lower() for kw in TARGET_KEYWORDS):
                    # 寻找该标题所属的最近的一个 class 包含 frame 的 div 祖先
                    parent_frame = h2.xpath('./ancestor::div[contains(@class, "frame")][1]')

                    if parent_frame:
                        # 使用元素的唯一内存 ID 或特定属性作为指纹，防止重复抓取
                        frame_obj = parent_frame[0]
                        frame_fingerprint = frame_obj.get('id') or str(hash(etree.tostring(frame_obj)))

                        if frame_fingerprint not in processed_frame_ids:
                            print(f"        匹配到板块：{h2_text}")
                            # 净化并转化
                            cleaned_html = CrawlerUtils.clean_node(frame_obj)
                            md_content += CrawlerUtils.to_markdown(cleaned_html) + "\n\n"
                            processed_frame_ids.add(frame_fingerprint)

            # 如果一个都没匹配到，执行全量保底提取
            if not processed_frame_ids:
                print("     语义匹配落空，执行全量内容提取 ")
                md_content += CrawlerUtils.to_markdown(CrawlerUtils.clean_node(main_node))

            # 保存
            safe_file = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_path, safe_file), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    存储成功 {safe_file}")

        except Exception as e:
            print(f"     详情解析故障：{e}")


if __name__ == "__main__":
    scraper = OTHAWScraper()
    scraper.run()