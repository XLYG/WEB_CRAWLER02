import os
import re
import time
import requests
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

# ================= 全局配置 (CONFIG) =================
CONFIG = {
    "大学名称": "University_of_Mannheim",
    "入口链接": "https://www.uni-mannheim.de/studium/vor-dem-studium/studienangebot/",
    "输出目录": "University_of_Mannheim_Data",
    "用户头": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "等待时间": 2,  # 每一个步骤后的强制停滞时间
    "超时时间": 60000,
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name):
        """清洗文件路径：转义德语变音符号，移除非法字符，不含中文"""
        if not name: return "Untitled"
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        return re.sub(r'\s+', "_", name).strip("._")[:100]

    @staticmethod
    def clean_html_and_convert(page, selector):
        """执行浏览器侧清洗：剔除 img, script, style, font 标签并转为 MD"""
        html = page.evaluate(f"""() => {{
            const el = document.querySelector('{selector}');
            if (!el) return "";
            const clone = el.cloneNode(true)
            const noise = clone.querySelectorAll('img, script, style, font, noscript, svg');
            noise.forEach(n => n.remove());
            return clone.innerHTML;
        }}""")
        if not html: return ""
        # 转化为 Markdown
        content = md(html, heading_style="ATX")
        # 压缩空行
        return re.sub(r'\n{{3,}}', '\n\n', content).strip()


class MannheimScraper:
    def __init__(self):
        if not os.path.exists(CONFIG["输出目录"]):
            os.makedirs(CONFIG["输出目录"])

    def run(self):
        with sync_playwright() as p:
            print("正在启动曼海姆大学采集项目...")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["用户代理"], locale="de-DE")
            page = context.new_page()

            # 获取专业列表
            major_list = self.get_major_list(page)
            print(f"准备采集 {len(major_list)} 个专业的详细内容...")

            # 遍历详情页
            for idx, major in enumerate(major_list):
                print(f"\n[{idx + 1}/{len(major_list)}] 正在处理: {major['name']}")
                self.process_detail(context, major)

            browser.close()
            print("\n所有曼海姆大学专业数据采集完成。")

    def get_major_list(self, page):
        """设置 Master 过滤并获取所有链接"""
        page.goto(CONFIG["入口链接"], wait_until="domcontentloaded")
        time.sleep(CONFIG["等待时间"])

        # 移除 Cookie Banner
        page.evaluate("() => { const b = document.querySelector('.cookie-notice'); if(b) b.remove(); }")

        # 选定 Master (value=3)
        page.select_option('select[name="tx_umaprogramsearch_search[search][searchDegreeFilter]"]', value="3")
        time.sleep(1)
        # 点击按钮
        page.locator('input.uma-ps-form-button').first.click()
        page.wait_for_selector('.uma-ps-results', state="visible")
        time.sleep(CONFIG["等待时间"])

        # 提取链接
        majors = []
        items = page.locator('.uma-ps-results a.tracking-initialized').all()
        for item in items:
            try:
                name = item.locator('.uma-ps-result-item-title').evaluate("el => el.textContent").strip()
                href = item.get_attribute("href")
                majors.append({"name": name, "url": urljoin(CONFIG["入口链接"], href)})
            except:
                continue
        return majors

    def process_detail(self, context, major):
        """解析详情页内容"""
        page = context.new_page()
        try:
            page.goto(major["url"], wait_until="domcontentloaded", timeout=CONFIG["超时时间"])
            time.sleep(CONFIG["等待时间"])

            # 提取专业 ID (从 URL 获取)
            major_id = major["url"].strip("/").split("/")[-1]
            safe_name = CrawlerUtils.sanitize_path(major["name"])
            folder_name = f"{safe_name}_{major_id}"[:80]
            major_dir = os.path.join(CONFIG["输出目录"], folder_name)

            if not os.path.exists(major_dir):
                os.makedirs(major_dir)

            # 内容组装
            md_content = f"# {major['name']}\n\nURL: {major['url']}\n\n"

            # 处理折叠面板
            print("    正在展开并提取 Accordion 内容...")
            accordion_items = page.locator("ul.accordion li.accordion-item").all()

            for i, item in enumerate(accordion_items):
                try:
                    # 获取标题
                    title_el = item.locator(".accordion-title").first
                    title_text = title_el.evaluate("el => el.textContent").strip()

                    # 模拟点击展开
                    title_el.evaluate("el => el.click()")
                    time.sleep(CONFIG["等待时间"])  # 强制停滞等待内容水合

                    # 提取内容
                    content_md = CrawlerUtils.clean_html_and_convert(page,
                                                                     f"ul.accordion li.accordion-item:nth-child({i + 1}) .accordion-content")
                    md_content += f"## {title_text}\n\n{content_md}\n\n"
                except:
                    continue

            # 处理额外的快捷按钮区域 (如 c370389, 包含申请提醒和资料获取)
            print("    正在提取底部 Button 区域...")
            # 使用模糊匹配类名
            shortcut_section = page.locator(".shortcut div.content-type-contentelements_buttons").all()
            if shortcut_section:
                md_content += "## Additional Information & Links\n\n"
                for sec in shortcut_section:
                    sec_html = sec.evaluate("el => el.innerHTML")
                    md_content += md(sec_html, heading_style="ATX").strip() + "\n\n"

            # 保存 Markdown 文件
            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    内容已保存至: {folder_name}")

        except Exception as e:
            print(f"     详情页处理失败: {e}")
        finally:
            page.close()


if __name__ == "__main__":
    MannheimScraper().run()