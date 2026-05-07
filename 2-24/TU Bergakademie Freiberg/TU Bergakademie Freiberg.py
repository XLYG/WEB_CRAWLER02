import os
import re
import time
import requests
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "TU_Freiberg",
    "START_URL": "https://tu-freiberg.de/studienangebot",
    "OUTPUT_DIR": "TU_Freiberg_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "WAIT_TIME": 2,  # 每一个动作后的强制停滞时间
    "TIMEOUT": 60000,
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """处理德语字符，强制截断"""
        if not name: return "Unknown_Major"
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 40
        return name[:limit]

    @staticmethod
    def clean_and_convert(html_content):
        """清洗并转化"""
        if not html_content: return ""
        # 剔除 script, style, nav, svg, button 等干扰
        html_content = re.sub(r'<(script|style|nav|noscript|svg|button)[^>]*>.*?</\1>', '', html_content,
                              flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'</?font[^>]*>', '', html_content, flags=re.IGNORECASE)
        markdown_text = md(html_content, heading_style="ATX")
        return re.sub(r'\n{3,}', '\n\n', markdown_text).strip()

    @staticmethod
    def smart_download(url, save_path):
        """追踪重定向并根据 Content-Type 保存。"""
        try:
            headers = {"User-Agent": CONFIG["USER_AGENT"]}
            # 允许重定向，处理 /download 结尾的链接
            res = requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=30)
            if res.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                return True
        except Exception as e:
            print(f"       下载中断：{e}")
        return False


# ================= CORE SCRAPER =================
class FreibergScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        with sync_playwright() as p:
            print(" 正在开启弗莱贝格工业大学采集")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 1. 列表页筛选阶段
            print(f"访问：{CONFIG['START_URL']}")
            try:
                page.goto(CONFIG["START_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
                time.sleep(CONFIG["WAIT_TIME"])
            except Exception as e:
                print(f"入口页面访问失败：{e}")
                return

            # 筛选 Master 学位
            print("执行 'Master' 筛选")
            master_chk = page.locator('input[data-drupal-selector="edit-field-degree-value-master"]').filter(
                visible=True).first
            if master_chk.count() > 0:
                master_chk.evaluate("el => el.click()")
                print(f"已勾选，等待结果刷新 ({CONFIG['WAIT_TIME']}s)...")
                time.sleep(CONFIG["WAIT_TIME"])

            # 提取所有任务
            major_tasks = []
            cards = page.locator('.views-view-responsive-grid__item article a[rel="bookmark"]').all()
            for card in cards:
                href = card.get_attribute("href")
                title = card.locator('.card-title span').inner_text().strip()
                major_tasks.append({"name": title, "url": urljoin(CONFIG["START_URL"], href)})

            print(f"成功识别到 {len(major_tasks)} 个硕士专业。")

            # 2. 详情页采集阶段
            for idx, task in enumerate(major_tasks):
                print(f"\n[{idx + 1}/{len(major_tasks)}] 处理专业：{task['name']}")
                self.process_major(context, task)

            browser.close()
            print("\n弗莱贝格工业大学项目任务已全部完成。")

    def process_major(self, context, task):
        page = context.new_page()
        try:
            page.goto(task["url"], wait_until="domcontentloaded")
            time.sleep(CONFIG["WAIT_TIME"])

            # 文件夹准备
            node_id = task["url"].split("-")[-1] if "-" in task["url"] else "info"
            folder_name = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{node_id}"
            major_dir = os.path.join(self.output_dir, folder_name)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            md_content = f"# {task['name']}\n\n- **URL**: {task['url']}\n\n"

            # 提取核心区块 (前两个 div)
            print("    提取顶部核心文本...")
            top_xpath = '//*[@id="block-tubaf-barrio-content"]/div/article/div/div[1]/div/div'
            divs_html = page.evaluate(f"""() => {{
                const container = document.evaluate('{top_xpath}', document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                if (!container) return "";
                const children = Array.from(container.children).filter(n => n.tagName === 'DIV');
                return children.slice(0, 2).map(n => n.outerHTML).join('\\n');
            }}""")
            if divs_html:
                md_content += "## Overview\n" + CrawlerUtils.clean_and_convert(divs_html) + "\n\n"

            # 查找 'Erforderliche Vorkenntnisse'
            print("    -> 探测 '必要知识' 模块...")
            vorkenntnisse_html = page.evaluate("""() => {
                const h2 = Array.from(document.querySelectorAll('h2')).find(el => el.textContent.includes('Erforderliche Vorkenntnisse'));
                if (h2) {
                    let parent = h2.parentElement;
                    while (parent && parent.tagName !== 'DIV') { parent = parent.parentElement; }
                    return parent ? parent.innerHTML : "";
                }
                return "";
            }""")
            if vorkenntnisse_html:
                md_content += "## Prerequisites\n" + CrawlerUtils.clean_and_convert(vorkenntnisse_html) + "\n\n"

            # PDF 下载逻辑
            print("    正在检索规章制度文件...")
            # 寻找文本中包含 "PDF" 且 href 包含 "/download" 的链接来定位
            pdf_link = page.locator('a:has-text("(PDF)")').first

            if pdf_link.count() > 0:
                pdf_url = urljoin(task["url"], pdf_link.get_attribute("href"))
                pdf_display_text = pdf_link.inner_text().split('(PDF)')[0].strip()
                safe_pdf_name = f"{CrawlerUtils.sanitize_path(pdf_display_text, False)}.pdf"

                print(f"    锁定文件：{pdf_display_text}")
                if CrawlerUtils.smart_download(pdf_url, os.path.join(major_dir, safe_pdf_name)):
                    print(f"       PDF 已存入。")
                else:
                    print(f"       无法完成下载。")
            else:
                print("       未在页面找到有效的 PDF 文档。")

            # 保存 MD
            final_md_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, final_md_name), "w", encoding="utf-8") as f:
                f.write(md_content)

        except Exception as e:
            print(f"    专业采集过程出错：{e}")
        finally:
            page.close()


if __name__ == "__main__":
    FreibergScraper().run()