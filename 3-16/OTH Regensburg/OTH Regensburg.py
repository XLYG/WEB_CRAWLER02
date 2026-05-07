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
    "UNIVERSITY_NAME": "OTH_Regensburg",
    "ROOT_DOMAIN": "https://www.oth-regensburg.de",
    "START_URL": "https://www.oth-regensburg.de/studieren/studienganguebersicht",
    "OUTPUT_DIR": "OTH_Regensburg_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "WAIT_TIME": 2.5,
    "TIMEOUT": 60000,
    "PDF_SIZE_LIMIT": 500 * 1024  # PDF下载-500KB 限制
}

# 语义匹配关键字库 (针对正文 div 区块)
DIV_KEYWORDS = ["Lerninhalte", "Stärken des Studiengangs", "Vertiefung", "Aufbau und Module", "Berufschancen"]
# 语义匹配关键字库 (针对折叠项 li h3 区块)
LI_H3_KEYWORDS = [
    "Standort", "Vorlesungszeiten", "Akkreditierung und Rankings",
    "Zulassungsvoraussetzungen", "Regelungen zu Studienablauf und Prüfungen",
    "Finanzierung, Förderung und Stipendien", "Internationalität und Auslandsaufenthalte",
    "Inhalt", "Chancen und Möglichkeiten", "Unser Anspruch"
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
        # 移除噪音标签
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style',
                 'noscript']
        for tag in noise:
            for node in element.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        # 补全相对链接为绝对链接
        for a in element.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        raw_html = etree.tostring(element, encoding='unicode', method='html')
        # 移除翻译标签残留
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_md(html):
        """将 HTML 转为 Markdown 并执行对齐修复"""
        if not html: return ""
        markdown_text = md(html, heading_style="ATX")
        # 移除行首空格防止触发代码块模式，压缩空行
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class RegensburgScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        # Playwright 获取列表
        tasks = self.fetch_list_via_playwright()
        if not tasks:
            print(" 无法获取专业列表，请检查网络环境或站点状态")
            return

        print(f" 共锁定 {len(tasks)} 个硕士专业")

        # Requests 详情解析
        for idx, task in enumerate(tasks):
            print(f"\n[{idx + 1}/{len(tasks)}] 正在挖掘专业：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(1, 2))

        print(f"\n 任务结束, 所有文件存放在：{self.output_dir}")

    def fetch_list_via_playwright(self):
        collected = []
        with sync_playwright() as p:
            print(" 启动浏览器引擎执行动态筛选与“Mehr laden”穿透")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            page.goto(CONFIG["START_URL"], wait_until="networkidle", timeout=45000)

            print(" 处理 cookie 同意弹窗")
            try:
                page.wait_for_selector('.in2-modal', timeout=15000)
                page.click('button[data-in2-modal-accept-button], button:has-text("Alle akzeptieren")', timeout=8000)
                print("    已接受所有 cookie")
                time.sleep(1.8)
            except:
                print("    cookie 弹窗未出现或已处理")
                # 尝试备用清除方式
                page.evaluate("document.querySelector('.in2-modal__blackbox')?.remove()")
                page.evaluate("document.body.style.overflow = 'auto'")

            # 勾选 Master 筛选
            print(" 正在设置 'Master' 筛选器...")
            # 锁定包含 Master 文字的 label 并点击其关联的 input
            try:
                page.locator('label:has-text("Master")').first.click()
                time.sleep(3)  # 等待第一次数据水合
            except:
                print("     无法点击筛选器，尝试直接加载")

            # 循环点击 "Mehr laden" 按钮直到全部展开
            print(" 正在执行 'Mehr laden' 循环加载...")
            while True:
                # 统计当前页面已存在的卡片数量
                current_count = page.locator('li a.c-teaserbox-studycourse').count()

                # 寻找“更多加载”按钮
                load_more_btn = page.locator('button:has-text("Mehr laden"), button:has-text("Load more")').filter(
                    visible=True)

                if load_more_btn.count() > 0:
                    print(f"    当前显示 {current_count} 个专业，正在加载更多...")
                    # 使用 JS 点击以防遮罩层干扰
                    load_more_btn.evaluate("el => el.click()")

                    # 等待卡片数量增加
                    try:
                        page.wait_for_function(
                            f"document.querySelectorAll('li a.c-teaserbox-studycourse').length > {current_count}",
                            timeout=10000)
                        time.sleep(1.5)
                    except:
                        print("    加载动作未引起数量变化，可能已到底部 ")
                        break
                else:
                    print("     所有内容已展开 ")
                    break

            # 提取所有卡片的信息
            items = page.locator('li a.c-teaserbox-studycourse').all()
            for item in items:
                try:
                    href = item.get_attribute("href")
                    # 提取标题
                    name_el = item.locator('.c-teaserbox-studycourse__headline')
                    name = name_el.inner_text().strip() if name_el.count() > 0 else "Major"
                    # 提取事实数据 (学期, 模式等)
                    facts_el = item.locator('.c-teaserbox-studycourse__facts')
                    facts = facts_el.inner_text().replace('\n', ' | ') if facts_el.count() > 0 else ""

                    if href:
                        collected.append({
                            "name": name,
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                            "meta": facts
                        })
                except:
                    continue

            browser.close()
        return collected

    def process_detail(self, task):
        """详情页解析：执行 Aside 审计、正文语义块提取及准入页穿透"""
        try:
            res = self.session.get(task["url"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            url_slug = task["url"].strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_dir = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            # 初始化 MD 内容 (URL 第一行, 元数据第二行)
            md_content = f"URL: {task['url']}\n\n# {task['name']}\n\n**{task['meta']}**\n\n---\n\n"

            # Aside 侧边栏提取与 PDF 大小审计
            aside = tree.xpath('//aside')
            if aside:
                print("    提取边栏文本并审计 PDF 文件大小 ")
                # 扫描 PDF 链接
                pdf_links = aside[0].xpath('.//a[contains(@href, ".pdf")]')
                for pl in pdf_links:
                    p_url = urljoin(CONFIG["ROOT_DOMAIN"], pl.get('href'))
                    p_label = "".join(pl.xpath('.//text()')).strip()
                    # 仅针对模块手册或概览进行匹配
                    if any(k.lower() in p_label.lower() for k in ["modul", "handbuch", "übersicht"]):
                        self.smart_download_pdf(p_url, p_label, major_dir)

                md_content += "## Sidebar Information\n\n" + CrawlerUtils.to_md(
                    CrawlerUtils.clean_node(aside[0])) + "\n\n"

            # 正文语义匹配 (追溯父级 div 模式)
            print("    正在执行正文块关键词匹配 ")
            for kw in DIV_KEYWORDS:
                # 寻找包含关键字的文本节点，并向上追溯第一个 DIV 容器
                nodes = tree.xpath(f'//*[contains(text(), "{kw}")]/ancestor::div[1]')
                if nodes:
                    md_content += f"## {kw}\n\n" + CrawlerUtils.to_md(CrawlerUtils.clean_node(nodes[0])) + "\n\n"

            # 折叠页匹配 (LI + H3 模式)
            print("    正在匹配折叠项列表 ")
            for kw in LI_H3_KEYWORDS:
                li_nodes = tree.xpath(f'//li[.//h3[contains(text(), "{kw}")]]')
                if li_nodes:
                    md_content += f"## {kw}\n\n" + CrawlerUtils.to_md(CrawlerUtils.clean_node(li_nodes[0])) + "\n\n"

            # 准入要求页面穿透
            qual_link = tree.xpath(
                '//a[contains(text(), "Qualifikationsvoraussetzungen") or @title="Qualifikationsvoraussetzungen"]/@href')
            if qual_link:
                sub_url = urljoin(CONFIG["ROOT_DOMAIN"], qual_link[0])
                print(f"    正在跳转获取详细入学要求：{sub_url}")
                try:
                    sub_res = self.session.get(sub_url, timeout=20)
                    sub_tree = etree.HTML(sub_res.text)
                    sub_main = sub_tree.xpath('//main')
                    if sub_main:
                        md_content += "\n\n---\n## Specific Admission Requirements (Appendix)\n\n"
                        md_content += f"**Source Appendix: {sub_url}**\n\n"
                        md_content += CrawlerUtils.to_md(CrawlerUtils.clean_node(sub_main[0]))
                except:
                    pass

            # 保存文件
            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, safe_file_name), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    档案已归档至文件夹")

        except Exception as e:
            print(f"     处理失败：{e}")

    def smart_download_pdf(self, url, label, save_dir):
        """流式下载：首先执行 Head 请求审计大小，符合条件(<500KB)则下载"""
        try:
            # 使用 GET 并 stream=True 来手动检查内容大小，以防 HEAD 请求被服务器拦截
            with self.session.get(url, stream=True, timeout=20) as r:
                content_len = r.headers.get('Content-Length')
                size = int(content_len) if content_len else 0

                if 0 < size < CONFIG["PDF_SIZE_LIMIT"]:
                    filename = f"Info_{CrawlerUtils.sanitize_path(label, False)}.pdf"
                    with open(os.path.join(save_dir, filename), 'wb') as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    print(f"       PDF下载成功 {label} ({size // 1024}KB)")
                elif size >= CONFIG["PDF_SIZE_LIMIT"]:
                    print(f"       PDF跳过 {label} (大小: {size // 1024}KB, 超过 500KB)")
        except:
            pass


if __name__ == "__main__":
    RegensburgScraper().run()
