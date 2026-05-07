import os
import re
import time
import requests
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "大学名称": "University_of_Gottingen",
    "根链接": "https://www.uni-goettingen.de/de/3811.html?degree=3&admission=0&faculty=0&begin=0&language=0",
    "输出目录": "University_of_Gottingen_Data",
    "用户头": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "等待时间": 2,
    "超时时间": 60000,
}

# 详情页 Tab 标签的备选 ID
TAB_MAPPING = {
    "Steckbrief": ["#steckbrief", "#features", "#profile"],
    "Studienaufbau": ["#studienaufbau", "#structure", "#curriculum"],
    "Bewerbung": ["#bewerbung", "#application", "#admission"]
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name):
        """清洗文件路径名：处理德语变音符号，移除非法字符，确保不含中文以防路径报错"""
        if not name: return "Untitled"
        # 转换德语特有字符
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        # 强制转 ASCII（忽略中文等非拉丁字符，用于文件夹名和文件名）
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        # 强制截断长度,将文件夹/文件名限制在 80 字符以内
        return re.sub(r'\s+', "_", name).strip("._")[:80]

    @staticmethod
    def clean_html(html):
        """深度清洗 HTML：移除翻译插件标签、脚本、导航栏等干扰"""
        if not html: return ""
        html = re.sub(r'</?font[^>]*>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<(script|style|nav|noscript|header|footer|iframe)[^>]*>.*?</\1>', '', html,
                      flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<img[^>]*>', '', html)
        return html

    @staticmethod
    def to_markdown(html):
        """将清洗后的 HTML 转化为 Markdown 格式"""
        cleaned_html = CrawlerUtils.clean_html(html)
        markdown_text = md(cleaned_html, heading_style="ATX")
        # 压缩多余的空行
        return re.sub(r'\n{3,}', '\n\n', markdown_text).strip()

    @staticmethod
    def download_pdf(url, filepath):
        """自动处理 HTML 重定向并保存 PDF"""
        try:
            headers = {"User-Agent": CONFIG["用户代理"]}
            # 允许重定向，处理从 HTML 跳转到 PDF 文件的路径
            res = requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=30)
            content_type = res.headers.get('Content-Type', '').lower()

            # 如果直接是 PDF
            if 'pdf' in content_type or res.url.lower().endswith('.pdf'):
                with open(filepath, 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                return True
            # 如果是 HTML 页面，尝试搜索第一个出现的 PDF 链接作为保底
            elif 'html' in content_type:
                match = re.search(r'href=["\'](.*?\.pdf)["\']', res.text, re.IGNORECASE)
                if match:
                    new_url = urljoin(res.url, match.group(1))
                    return CrawlerUtils.download_pdf(new_url, filepath)
            return False
        except Exception as e:
            print(f"       下载过程中出现异常: {e}")
            return False


class GottingenScraper:
    def __init__(self):
        if not os.path.exists(CONFIG["输出目录"]):
            os.makedirs(CONFIG["输出目录"])

    def run(self):
        with sync_playwright() as p:
            print("正在启动浏览器...")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["用户代理"], locale="de-DE")
            page = context.new_page()

            # 列表页提取
            print(f"正在访问专业列表页: {CONFIG['入口链接']}")
            try:
                page.goto(CONFIG['入口链接'], wait_until="domcontentloaded", timeout=CONFIG["超时时间"])
                time.sleep(CONFIG["等待时间"])
            except Exception as e:
                print(f"访问列表页失败: {e}")
                return

            # 清除遮挡元素（Cookie 栏和导航栏）
            page.evaluate("""() => {
                ['cookie_banner_footer','navigation-container','navigation-container-mobil'].forEach(id => {
                    const el = document.getElementById(id);
                    if(el) el.remove();
                });
            }""")

            # 使用 JS 展开所有折叠面板，解决 nth(3)或者(4)导致的超时错误
            print("[*] 正在展开所有折叠面板...")
            page.evaluate("""() => {
                const buttons = document.querySelectorAll('section#stg-liste a.list-button.collapsed');
                buttons.forEach(btn => btn.click());
            }""")
            time.sleep(CONFIG["等待时间"])

            # 提取所有专业的链接和基本信息
            tasks = []
            major_items = page.locator("li.stg").all()
            for item in major_items:
                try:
                    anchor = item.locator(".stg-header a").first
                    url = urljoin("https://www.uni-goettingen.de", anchor.get_attribute("href"))
                    name = anchor.evaluate("el => el.textContent").strip()
                    degree = item.locator(".stg-footer").evaluate("el => el.textContent").split('\n')[0].strip()
                    tasks.append({"name": name, "degree": degree, "url": url})
                except:
                    continue

            print(f"成功提取到 {len(tasks)} 个专业。开始进行深度采集...")

            # 详情页处理循环
            for idx, major in enumerate(tasks):
                print(f"\n[{idx + 1}/{len(tasks)}] 正在采集专业: {major['name']}")
                self.process_major_detail(context, major)

            browser.close()
            print("\n所有采集任务已完成。")

    def process_major_detail(self, context, major):
        """处理每一个专业的详细页面内容"""
        page = context.new_page()
        try:
            page.goto(major["url"], wait_until="domcontentloaded", timeout=CONFIG["超时时间"])
            time.sleep(CONFIG["等待时间"])

            # 创建专业独立的文件,提取链接末尾的 ID 防止重名冲突
            url_id = re.search(r'/(\d+)\.html', major["url"]).group(1) if re.search(r'/(\d+)\.html',
                                                                                    major["url"]) else "000"
            folder_name = f"{CrawlerUtils.sanitize_path(major['name'])}_{CrawlerUtils.sanitize_path(major['degree'])}_{url_id}"
            major_dir = os.path.join(CONFIG["输出目录"], folder_name)
            if not os.path.exists(major_dir):
                os.makedirs(major_dir)

            md_content = f"# {major['name']}\n\nDegree: {major['degree']}\nURL: {major['url']}\n\n"

            # 详情内容提取
            # 检查页面是否包含 Tab 菜单
            if page.locator("#zsb-menu").count() > 0:
                for label, selectors in TAB_MAPPING.items():
                    target_id = None
                    # 匹配当前页面存在的 Tab
                    for s in selectors:
                        if page.locator(f"a[href='{s}']").count() > 0:
                            target_id = s
                            break

                    if target_id:
                        # 点击切换 Tab
                        print(f"     切换至选项卡: {label} ({target_id})")
                        page.evaluate(f"document.querySelector(\"a[href='{target_id}']\").click()")
                        time.sleep(CONFIG["等待时间"])

                        # 获取该 Tab 内部的 HTML，并剔除不需要的部分（nav, panel）
                        raw_html = page.evaluate(f"""() => {{
                            const el = document.querySelector('{target_id}');
                            if(!el) return "";
                            const clone = el.cloneNode(true);
                            clone.querySelectorAll('nav, .panel-zsb-list').forEach(n => n.remove());
                            return clone.innerHTML;
                        }}""")

                        md_content += f"## {label}\n\n{CrawlerUtils.to_markdown(raw_html)}\n\n"

                        # 如果是学习结构（Studienaufbau）Tab，处理 PDF 下载逻辑
                        if label == "Studienaufbau":
                            self.handle_pdf_process(page, target_id, major_dir)
            else:
                # 处理没有 Tab 选项卡的普通页面
                print("    普通页面模式提取")
                article = page.locator("article").first
                if article.count() > 0:
                    raw_html = article.inner_html()
                    md_content += CrawlerUtils.to_markdown(raw_html)

            # 保存 Markdown 文件
            md_filename = f"{CrawlerUtils.sanitize_path(major['name'])}.md"
            with open(os.path.join(major_dir, md_filename), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    MD内容已成功保存。")

        except Exception as e:
            print(f"   处理详情页时出错: {e}")
        finally:
            page.close()

    def handle_pdf_process(self, page, parent_selector, save_dir):
        """处理模态框中的第一个 PDF 文件下载"""
        # 定位触发模态框的按钮
        trigger = page.locator(f"{parent_selector} .panel-zsb-list a[href='#modal']").first
        if trigger.is_visible():
            print("    正在打开规章制度模态框...")
            trigger.click()
            time.sleep(CONFIG["等待时间"])

            try:
                # 锁定模态框内第一个文件
                # 排除 .separator 分隔条
                first_link = page.locator("#gcms_page li:not(.separator) > a").first
                if first_link.is_visible():
                    pdf_page_url = urljoin("https://www.uni-goettingen.de", first_link.get_attribute("href"))
                    pdf_display_name = first_link.evaluate("el => el.textContent").strip()
                    print(f"    锁定目标文件: {pdf_display_name}")

                    # 生成清洗后的文件名
                    safe_pdf_name = f"{CrawlerUtils.sanitize_path(pdf_display_name)}.pdf"
                    save_path = os.path.join(save_dir, safe_pdf_name)

                    if not os.path.exists(save_path):
                        success = CrawlerUtils.download_pdf(pdf_page_url, save_path)
                        if success:
                            print(f"       pdf下载成功: {safe_pdf_name}")
                    else:
                        print(f"       文件已存在，跳过下载。")
            except Exception as e:
                print(f"    目标 PDF 处理失败: {e}")


if __name__ == "__main__":
    GottingenScraper().run()