import os
import re
import time
import random
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "HTW_Dresden",
    "ROOT_DOMAIN": "https://www.htw-dresden.de",
    "START_URL": "https://www.htw-dresden.de/studium/vor-dem-studium/studienangebot",
    "OUTPUT_DIR": "HTW_Dresden_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "WAIT_TIME": 2.5,
    "TIMEOUT": 60000,
}

TARGET_KEYWORDS = ["Wichtigste", "essentials", "glance", "一览", "Profil", "Aufbau", "programme", "结构", "轮廓",
                   "Career", "prospects", "Promotion", "Doctoral", "Gut zu wissen", "apply?", "申请", "Dokumente",
                   "Downloads", "documents"]


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清理路径名：处理变音符号，移除非法字符，执行长度截断。"""
        if not name: return "Unknown_Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        return name[:60 if is_folder else 40]

    @staticmethod
    def purified_to_md(page, html_content):
        """
        核心净化函数：在浏览器沙箱内执行 DOM 物理去噪。
        1. 移除图片、视频、背景图、脚本、SVG、Blockquote
        2. 补全相对链接
        """
        if not html_content: return ""

        purified_html = page.evaluate("""(html) => {
            const div = document.createElement('div');
            div.innerHTML = html;

            const trash = div.querySelectorAll('img, picture, figure, video, blockquote, script, style, svg, button, nav, noscript, .slick-arrow');
            trash.forEach(n => n.remove());

            div.querySelectorAll('*').forEach(el => {
                if (el.style.backgroundImage) el.style.backgroundImage = 'none';
            });

            div.querySelectorAll('a').forEach(a => {
                let href = a.getAttribute('href');
                if(href && typeof href === 'string' && href.startsWith('/')) {
                    a.setAttribute('href', 'https://www.htw-dresden.de' + href);
                }
            });
            return div.innerHTML;
        }""", html_content)

        # 转化为 Markdown
        content = md(purified_html, heading_style="ATX")
        # 强制行首对齐并去除重复空格
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class HTWScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        with sync_playwright() as p:
            print("正在执行 HTW 德累斯顿采集")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 列表筛选
            print(f"访问：{CONFIG['START_URL']}")
            page.goto(CONFIG["START_URL"], wait_until="networkidle")
            self.nuke_cookie(page)

            print("正在操作 Select2 筛选硕士学位...")
            try:
                page.click('#select2-htw_field--filter-degree-container')
                time.sleep(1)
                page.locator(
                    'li.select2-results__option:has-text("掌握"), li.select2-results__option:has-text("Master")').click()
                time.sleep(1)
                page.click('button.htw_study-courses__submit')
                time.sleep(CONFIG["WAIT_TIME"])
            except:
                print("    筛选面板交互异常，跳过筛选动作。")

            # 抓取专业 URL 列表
            major_tasks = []
            items = page.locator('.htw_accordion__item').all()
            for item in items:
                try:
                    name = item.locator('.htw_accordion__button').evaluate("el => el.childNodes[0].textContent").strip()
                    detail_link = item.locator('a.btn-outline-light:has-text("Details")').first
                    href = detail_link.get_attribute("href")
                    if href:
                        major_tasks.append({"name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href)})
                except:
                    continue

            print(f"成功抓取，共锁定 {len(major_tasks)} 个硕士专业-开始采集")

            # 遍历详情
            for idx, task in enumerate(major_tasks):
                print(f"[{idx + 1}/{len(major_tasks)}] 正在解析：{task['name']}")
                self.process_detail(context, task)

            browser.close()
            print("\n任务结束")

    def nuke_cookie(self, page):
        """移除可能存在的遮挡物"""
        page.evaluate(
            "() => { ['#uc-center-container', '.cookie-notice', '.banner-wrapper'].forEach(s => { const e=document.querySelector(s); if(e) e.remove(); }); document.body.style.overflow='auto'; }")

    def process_detail(self, context, task):
        page = context.new_page()
        try:
            page.goto(task["url"], wait_until="domcontentloaded")
            time.sleep(CONFIG["WAIT_TIME"])
            self.nuke_cookie(page)

            url_slug = task["url"].strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_dir = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            md_content = f"URL: {task['url']}\n\n"
            md_content += f"# {task['name']}\n\n---\n\n"

            # 指定切片逻辑：从 H1 到 Social Bar 且排除 Blockquote
            print("    正在进行 DOM 线性切片...")
            extracted_html = page.evaluate("""() => {
                // 设定对应哨兵元素
                const startMarker = document.querySelector('h1.htw_headline');
                const endMarker = document.querySelector('.htw_social-bar__title');

                // 获取核心内容标签
                const container = document.querySelector('main, article, .content') || document.body;
                const children = Array.from(container.children);

                let result = "";
                let recording = false;

                for (const child of children) {
                    // 从遇到H1标签开始
                    if (child === startMarker || (startMarker && child.contains(startMarker))) {
                        recording = true;
                    }

                    if (recording) {
                        // 如果当前块包含终点，停止
                        if (child === endMarker || (endMarker && child.contains(endMarker))) {
                            break;
                        }

                        // 预先克隆并移除 blockquote，防止重复
                        const clone = child.cloneNode(true);
                        clone.querySelectorAll('blockquote').forEach(b => b.remove());
                        if (clone.tagName !== 'BLOCKQUOTE') {
                            result += clone.outerHTML;
                        }
                    }
                }
                return result;
            }""")

            if extracted_html:
                md_content += CrawlerUtils.purified_to_md(page, extracted_html)
            else:
                # 最后的保底全量抓取
                print("    切片为空，尝试全量保底提取...")
                backup = page.locator('main, article').first
                if backup.count() > 0:
                    md_content += CrawlerUtils.purified_to_md(page, backup.evaluate("el => el.innerHTML"))

            # 穿透逻辑-处理 Banner 跳转，保证内容获取完整
            banner = page.locator('a.htw_banner').first
            if banner.count() > 0:
                extra_url = urljoin(CONFIG["ROOT_DOMAIN"], banner.get_attribute("href"))
                print(f"    探测到双学位扩展页：{extra_url}")
                page.goto(extra_url, wait_until="networkidle")
                time.sleep(2)

                # 在子页面执行同样的线性扫描提取
                extra_html = page.evaluate("""() => {
                    const h1 = document.querySelector('h1');
                    const social = document.querySelector('.htw_social-bar__title');
                    const container = document.querySelector('main, article') || document.body;

                    let h = "";
                    let rec = false;
                    for(let child of container.children) {
                        if(h1 && (child === h1 || child.contains(h1))) rec = true;
                        if(!h1) rec = true; // 部分子页没有h1，默认全量开启

                        if(social && (child === social || child.contains(social))) break;

                        if(rec) {
                            const clone = child.cloneNode(true);
                            clone.querySelectorAll('blockquote').forEach(b => b.remove());
                            if(clone.tagName !== 'BLOCKQUOTE') h += clone.outerHTML;
                        }
                    }
                    return h;
                }""")
                if extra_html:
                    md_content += "\n\n---\n## 附加扩展信息 (Jump Page Info)\n\n"
                    md_content += f"Source: {extra_url}\n\n"
                    md_content += CrawlerUtils.purified_to_md(page, extra_html)

            # 保存 Markdown
            safe_file = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, safe_file), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    任务已全部完成并存入{safe_file}")

        except Exception as e:
            print(f"    解析故障：{e}")
        finally:
            page.close()


if __name__ == "__main__":
    HTWScraper().run()