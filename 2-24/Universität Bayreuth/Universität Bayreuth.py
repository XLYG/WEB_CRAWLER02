import os
import re
import time
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "University_of_Bayreuth",
    "START_URL": "https://www.uni-bayreuth.de/studiengangsfinder#master",
    "ROOT_DOMAIN": "https://www.uni-bayreuth.de",
    "OUTPUT_DIR": "Uni_Bayreuth_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "WAIT_TIME": 2.5,
    "TIMEOUT": 60000,
}

# 目标关键词（用于匹配折叠页标题）
TARGET_KEYWORDS = [
    "Wichtigste", "essentials", "glance", "一览",
    "Profil", "Aufbau", "programme", "结构", "轮廓",
    "Career", "prospects", "前景",
    "Promotion", "Doctoral", "research", "博士",
    "Gut zu wissen", "Good to know", "须知",
    "Studienplatz", "How do I apply", "apply?", "申请",
    "Dokumente", "Downloads", "documents", "下载", "文件",
    "Supplementary", "Zusatzstudium", "in Kürze", "Contacts", "Related"
]


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清洗路径，处理德语变音，强制截断解决路径过长"""
        if not name: return "Unknown_Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        return name[:60 if is_folder else 50]

    @staticmethod
    def purified_to_md(page, element_handle):
        """移除图片、按钮、侧边栏，补全超链接。"""
        try:
            raw_html = element_handle.evaluate("""el => {
                const clone = el.cloneNode(true);
                // 移除所有视觉/脚本噪音
                const trash = clone.querySelectorAll('img, picture, figure, video, script, style, svg, button, noscript, .slick-arrow, .sr-only, aside, .sidebar');
                trash.forEach(n => n.remove());

                // 补全链接 (JS startsWith)
                const links = clone.querySelectorAll('a');
                links.forEach(a => {
                    let href = a.getAttribute('href');
                    if(href && typeof href === 'string' && href.startsWith('/')) {
                        a.setAttribute('href', 'https://www.uni-bayreuth.de' + href);
                    }
                });
                return clone.innerHTML;
            }""")
            # 转化为 MD 并排版
            content = md(raw_html, heading_style="ATX")
            lines = [line.strip() for line in content.split('\n')]
            return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        except:
            return ""


class BayreuthScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        with sync_playwright() as p:
            print("正在启动拜罗斯特大学的专业采集")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 列表页扫描
            print(f"访问：{CONFIG['START_URL']}")
            page.goto(CONFIG["START_URL"], wait_until="networkidle")
            self.nuke_cookie(page)
            time.sleep(CONFIG["WAIT_TIME"])

            # 提取列表
            all_links = page.locator('a[href*="/master/"]').all()
            tasks_dict = {}
            for link in all_links:
                href = link.get_attribute("href")
                if href and "/master/" in href:
                    full_url = urljoin(CONFIG["ROOT_DOMAIN"], href)
                    if full_url not in tasks_dict:
                        name = link.evaluate("el => el.innerText").strip()
                        if len(name) > 5:
                            tasks_dict[full_url] = {"name": name, "url": full_url}

            tasks = list(tasks_dict.values())
            print(f"发现 {len(tasks)} 个专业。开始执行多层穿透采集")

            # 详情遍历
            for idx, task in enumerate(tasks):
                print(f"\n[任务进展 {idx + 1}/{len(tasks)}] 正在解析：{task['name']}")
                self.process_major(context, task)

            browser.close()
            print("\n 全部任务执行完毕。")

    def nuke_cookie(self, page):
        """js删除每一步遇到的Cookie 弹窗"""
        page.evaluate("""() => {
            ['#usercentrics-root', '.cookie-notice', '.banner-wrapper', 'div[class*="cookie"]', 'div[id*="consent"]'].forEach(s => {
                const el = document.querySelector(s);
                if(el) el.remove();
            });
            document.body.style.overflow = 'auto';
        }""")

    def process_major(self, context, task):
        page = context.new_page()
        try:
            page.goto(task["url"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
            time.sleep(CONFIG["WAIT_TIME"])
            self.nuke_cookie(page)

            # 检查是否存在 OnePager 跳转链接
            one_pager = page.locator('a:has-text("OnePager"), td a:has-text("OnePager")').first
            if one_pager.count() > 0:
                target_url = urljoin(CONFIG["ROOT_DOMAIN"], one_pager.get_attribute("href"))
                print(f"    发现OnePager页，跳转中：{target_url}")
                page.goto(target_url, wait_until="networkidle")
                time.sleep(CONFIG["WAIT_TIME"])
                self.nuke_cookie(page)

            # 检查是否存在英语授课中转框
            eng_box = page.locator('.boxWidget--Df7uFRb9o9 a.btn-primary').first
            if eng_box.count() > 0:
                target_url = urljoin(CONFIG["ROOT_DOMAIN"], eng_box.get_attribute("href"))
                if target_url != page.url:
                    print(f"    -> 穿透宣传页跳转至数据详情：{target_url}")
                    page.goto(target_url, wait_until="networkidle")
                    time.sleep(CONFIG["WAIT_TIME"])
                    self.nuke_cookie(page)

            # 目录准备
            url_slug = page.url.strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_dir = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            md_content = f"# {task['name']}\n\n- **URL**: {page.url}\n\n"

            # 提取层：双版判定
            if page.locator('details, .accordion-item, .expanded--R6AcZQb0RS').count() > 0:
                print("    -> 识别为【现代新版结构】，执行切片提取...")
                md_content += self.extract_modern_content(page)
            else:
                print("    -> 识别为【旧版区块结构】，提取核心内容...")
                # 穿透层 C: 旧版页面的“Bewerbung”补充信息跳转
                md_content += self.extract_legacy_and_appendix(page, context)

            # 最终存储
            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, safe_file_name), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    存储成功")

        except Exception as e:
            print(f"    处理失败：{e}")
        finally:
            page.close()

    def extract_modern_content(self, page):
        """匹配逻辑:简介(h1之下,折叠页之上) + 匹配折叠项"""
        result = ""
        # 提取 Introduction
        intro_html = page.evaluate("""() => {
            const h1 = document.querySelector('h1');
            if(!h1) return "";
            let html = "";
            let curr = h1.nextElementSibling;
            while(curr) {
                // 停止标志：发现任何形式的折叠组件
                if(curr.querySelector('details, .accordion-item, [data-pi-widget-class="AccordionWidget"]') || curr.tagName === 'DETAILS') break;
                // 过滤图片
                if(!curr.querySelector('img')) html += curr.outerHTML;
                curr = curr.nextElementSibling;
            }
            return html;
        }""")
        if intro_html:
            result += "## Introduction\n\n" + md(intro_html) + "\n\n"

        # 提取折叠项
        blocks = page.locator('details, .accordion-item, [data-pi-element="AccordionWidgetElement"]').all()
        for b in blocks:
            try:
                title = b.evaluate("el => el.innerText.split('\\n')[0]").strip()
                if any(k.lower() in title.lower() for k in TARGET_KEYWORDS):
                    # 强行展开
                    b.evaluate("el => el.setAttribute('open', 'true')")
                    print(f"       发现折叠页：{title[:20]}...")
                    result += f"## {title}\n\n" + CrawlerUtils.purified_to_md(page, b) + "\n\n"
            except:
                continue
        return result

    def extract_legacy_and_appendix(self, page, context):
        """旧版处理逻辑：提取核心文字 + 处理‘Infos zur Bewerbung’二次跳转"""
        # 定位主体，排除侧边栏
        main_column = page.locator('section.text.full, article#spaltemitte, .editorcontent').first
        main_md = CrawlerUtils.purified_to_md(page, main_column)

        # 查找“Infos zur Bewerbung”链接
        appendix_md = ""
        try:
            apply_link = page.locator('a:has-text("Infos zur Bewerbung"), a:has-text("Bewerbung")').first
            if apply_link.count() > 0:
                sub_url = urljoin(CONFIG["ROOT_DOMAIN"], apply_link.get_attribute("href"))
                print(f"    -> 发现准入补充页面，正在穿透采集：{sub_url}")
                sub_page = context.new_page()
                sub_page.goto(sub_url, wait_until="domcontentloaded")
                time.sleep(2)
                # 提取补充页的指定结构内容
                sub_target = sub_page.locator('section.text.full, article, .editorcontent').first
                appendix_md = "\n\n---\n## 补充申请信息 (Application Appendix)\n\n" + CrawlerUtils.purified_to_md(
                    sub_page, sub_target)
                sub_page.close()
        except:
            pass

        return main_md + appendix_md


if __name__ == "__main__":
    BayreuthScraper().run()