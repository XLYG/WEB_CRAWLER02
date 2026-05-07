import os
import re
import time
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "Uni_Mainz",
    "ROOT_DOMAIN": "https://www.studium.uni-mainz.de",
    "START_URL": "https://www.studium.uni-mainz.de/studienwahl/studienangebot/",
    "OUTPUT_DIR": "Uni_Mainz_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "WAIT_TIME": 2,
    "TIMEOUT": 60000,
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清洗路径：处理德语字符，强制截断"""
        if not name: return "Unknown_Major"
        replacements = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in replacements.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 50
        return name[:limit]

    @staticmethod
    def clean_and_complete_html(element_html):
        """清洗 HTML，补全超链接，并移除干扰元素"""
        if not element_html: return ""
        # 补齐可能缺失的根标签
        if not element_html.startswith('<div'):
            element_html = f"<div>{element_html}</div>"

        tree = etree.HTML(element_html)

        # 补全超链接
        for a in tree.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        # 移除干扰标签 (script, style, svg, button, img等)
        for tag in ['script', 'style', 'nav', 'noscript', 'svg', 'button', 'img']:
            for node in tree.xpath(f'.//{tag}'):
                parent = node.getparent()
                if parent is not None: parent.remove(node)

        raw_html = etree.tostring(tree, encoding='unicode', method='html')
        # 移除翻译标签残留
        raw_html = re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)
        return raw_html

    @staticmethod
    def to_markdown(html):
        """转化为 MD 并执行排版修复"""
        if not html: return ""
        markdown_text = md(html, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        content = '\n'.join(lines)
        content = re.sub(r'(?<!http)(?<!https):\s*', ':\n\n', content)
        return re.sub(r'\n{3,}', '\n\n', content).strip()


class MainzScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        with sync_playwright() as p:
            print("正在启动准备美因茨大学采集")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 列表页筛选
            print(f"正在访问列表页：{CONFIG['START_URL']}")
            page.goto(CONFIG["START_URL"], wait_until="networkidle")
            time.sleep(CONFIG["WAIT_TIME"])

            try:
                # 激活筛选
                page.click('input[name="degree-search"]')
                time.sleep(1)
                options = page.locator('.options-dropdown ul.options li a').all()
                for opt in options:
                    text = opt.evaluate("el => el.textContent")
                    if "Master" in text and "Meisterschülerbrief" not in text:
                        opt.click()
                        time.sleep(0.2)
                print(f"筛选已完成，正在等待列表同步")
                time.sleep(CONFIG["WAIT_TIME"])
            except Exception as e:
                print(f"筛选操作失败：{e}")

            # 提取任务
            major_tasks = []
            items = page.locator('.sgf-result-view--list .items ul li a').all()
            for item in items:
                href = item.get_attribute("href")
                title = item.locator('span.title').evaluate("el => el.textContent").strip()
                if href:
                    major_tasks.append({"name": title, "url": urljoin(CONFIG["START_URL"], href)})

            print(f"成功锁定 {len(major_tasks)} 个硕士专业, 现在开始执行解析")

            # 遍历详情
            for idx, task in enumerate(major_tasks):
                print(f"\n[{idx + 1}/{len(major_tasks)}] 正在解析专业：{task['name']}")
                self.process_detail(context, task)

            browser.close()
            print("\n美因茨大学的所有专业数据已全部采集完毕")

    def process_detail(self, context, task):
        page = context.new_page()
        try:
            page.goto(task["url"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
            time.sleep(CONFIG["WAIT_TIME"])

            # 文件夹准备
            major_id = task["url"].strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{major_id}"
            major_path = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_path): os.makedirs(major_path)

            md_content = f"# {task['name']}\n\n- **Source URL**: {task['url']}\n\n"

            # -主体内容提取
            main_el = page.locator('main.main')
            if main_el.count() > 0:
                print("    -> 提取主页面核心区块...")
                extracted_main_html = page.evaluate("""() => {
                    const main = document.querySelector('main.main');
                    let combined = "";
                    for (let child of main.children) {
                        if (child.querySelector('jgu-base-slider') || child.tagName === 'JGU-BASE-SLIDER') break;
                        combined += child.outerHTML;
                    }
                    return combined;
                }""")
                md_content += CrawlerUtils.to_markdown(CrawlerUtils.clean_and_complete_html(extracted_main_html))
            else:
                article_el = page.locator('article#spaltemitte, article.editorcontent').first
                if article_el.count() > 0:
                    md_content += CrawlerUtils.to_markdown(
                        CrawlerUtils.clean_and_complete_html(article_el.evaluate("el => el.innerHTML")))

            # 弹出模态框内容提取
            modal_blocks = page.locator('.jgu-modal-block').all()
            if modal_blocks:
                print(f"    -> 发现 {len(modal_blocks)} 个潜在弹出信息框，正在逐一穿透提取...")
                md_content += "\n\n---\n## 详细申请与程序说明 (Modal Contents)\n\n"

                for idx, modal in enumerate(modal_blocks):
                    try:
                        btn = modal.locator('button.jgu_button').first
                        btn_text = btn.evaluate("el => el.textContent").strip()
                        print(f"      正在点击并提取：{btn_text}")

                        # 强制 JS 点击触发弹出
                        btn.evaluate("el => el.click()")
                        # 强制等待内容渲染
                        time.sleep(CONFIG["WAIT_TIME"])

                        # 定位对应的 dialog 容器
                        dialog = modal.locator('dialog.content').first
                        if dialog.count() > 0:
                            # 提取 dialog 内部的所有 HTML (排除关闭按钮)
                            dialog_html = dialog.evaluate("""el => {
                                const clone = el.cloneNode(true);
                                const closeBtn = clone.querySelector('.dialog-close-btn');
                                if(closeBtn) closeBtn.remove();
                                return clone.innerHTML;
                            }""")

                            md_content += f"### {btn_text}\n\n"
                            md_content += CrawlerUtils.to_markdown(
                                CrawlerUtils.clean_and_complete_html(dialog_html)) + "\n\n"
                        page.keyboard.press("Escape")
                    except Exception as modal_e:
                        print(f"       提取弹出框 '{btn_text}' 失败: {modal_e}")

            # 保存
            safe_filename = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_path, safe_filename), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    存储完毕 {safe_filename}")

        except Exception as e:
            print(f"   详情页解析发生错误：{e}")
        finally:
            page.close()


if __name__ == "__main__":
    MainzScraper().run()