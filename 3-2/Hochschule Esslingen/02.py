import os
import re
import time
import random
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "HS_Esslingen",
    "ROOT_DOMAIN": "https://www.hs-esslingen.de",
    "LIST_URL": "https://www.hs-esslingen.de/en/studium/studienangebot/lebenslanges-lernen/berufsbegleitende-master-studiengaenge",
    "ADMISSION_URL": "https://www.hs-esslingen.de/en/study/application-and-enrolment/applications-for-a-masters-degree-course",
    "OUTPUT_DIR": "University_of_Esslingen_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "WAIT_TIME": 2.5,
    "TIMEOUT": 60000,
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        """清洗路径：处理德语、物理剥离非法字符并执行强制截断。"""
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        # 强制截断
        limit = 50 if is_folder else 40
        return name[:limit]

    @staticmethod
    def purified_to_md(page, element_handle):
        """物理级 DOM 净化：移除图片、脚本、侧边栏、Cookie 弹窗残留。"""
        try:
            raw_html = element_handle.evaluate("""el => {
                const clone = el.cloneNode(true);
                const trash = clone.querySelectorAll('img, picture, figure, video, script, style, svg, button, noscript, nav, .fh-breadcrumb, .headerDesktop, .in2-modal');
                trash.forEach(n => n.remove());

                const links = clone.querySelectorAll('a');
                links.forEach(a => {
                    let href = a.getAttribute('href');
                    if(href && typeof href === 'string' && href.startsWith('/')) {
                        a.setAttribute('href', 'https://www.hs-esslingen.de' + href);
                    }
                });
                return clone.innerHTML;
            }""")
            content = md(raw_html, heading_style="ATX")
            lines = [line.strip() for line in content.split('\n')]
            return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        except:
            return ""


class EsslingenScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.common_admission_md = ""

    def run(self):
        with sync_playwright() as p:
            print("正在开始采集")
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["USER_AGENT"], locale="de-DE")
            page = context.new_page()

            # 加载通用申请要求
            self.fetch_common_info(page)

            # 列表页提取
            print(f"访问：{CONFIG['LIST_URL']}")
            page.goto(CONFIG["LIST_URL"], wait_until="domcontentloaded")
            self.nuke_cookie_modal(page)
            time.sleep(CONFIG["WAIT_TIME"])

            major_links = page.locator('#c2224 a.boxlink').all()
            tasks = []
            for link in major_links:
                try:
                    name = link.locator('.link-box__label').evaluate("el => el.innerText").strip()
                    href = link.get_attribute("href")
                    if href:
                        tasks.append({"name": name, "url": urljoin(CONFIG["ROOT_DOMAIN"], href)})
                except:
                    continue

            print(f"捕捉成功：发现 {len(tasks)} 个非全日制专业-开始采集")

            # 详情遍历
            for idx, task in enumerate(tasks):
                print(f"[{idx + 1}/{len(tasks)}] 处理：{task['name']}")
                self.process_detail(context, task)

            browser.close()
            print("\n 任务全部完成。")

    def nuke_cookie_modal(self, page):
        """直接删除所有的 Cookie 弹窗"""
        page.evaluate("""() => {
            const selectors = ['.in2-modal', '#usercentrics-root', '.cookie-notice', '.banner-wrapper'];
            selectors.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));

            const nukeShadow = (root) => {
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        el.shadowRoot.innerHTML = '';
                        el.remove();
                    }
                });
            };
            nukeShadow(document);
            document.body.style.setProperty('overflow', 'auto', 'important');
            document.documentElement.style.setProperty('overflow', 'auto', 'important');
        }""")

    def fetch_common_info(self, page):
        try:
            page.goto(CONFIG["ADMISSION_URL"], wait_until="domcontentloaded")
            self.nuke_cookie_modal(page)
            node = page.locator('#c17657').first
            if node.count() > 0:
                self.common_admission_md = CrawlerUtils.purified_to_md(page, node)
                print("通用申请信息已缓存。")
        except:
            pass

    def process_detail(self, context, task):
        page = context.new_page()
        try:
            page.goto(task["url"], wait_until="domcontentloaded")
            self.nuke_cookie_modal(page)

            # 等待内容加载
            try:
                page.wait_for_selector('main', timeout=10000)
                page.wait_for_function("document.querySelector('main').innerText.trim().length > 150", timeout=10000)
            except:
                pass

            time.sleep(CONFIG["WAIT_TIME"])

            url_slug = task["url"].strip("/").split("/")[-1]
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_dir = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            md_content = f"URL: {task['url']}\n\n"
            md_content += f"**{task['name']} | Part-time Study**\n\n---\n\n"

            # 进行指定范围切片提取
            print("    正在执行切片提取 ")
            html_slice = page.evaluate("""() => {
                const main = document.querySelector('main');
                if (!main) return "";
                let combined = "";
                for (let child of main.children) {
                    const moreInfos = (child.id === 'more-infos') ? child : child.querySelector('#more-infos');
                    if (moreInfos) {
                        const clone = child.cloneNode(true);
                        const targetInClone = (clone.id === 'more-infos') ? clone : clone.querySelector('#more-infos');
                        const sections = Array.from(targetInClone.querySelectorAll('section'));
                        if (sections.length > 1) {
                            // 遇到第二个 section 即停止并在其之后执行删除
                            let startDeleting = false;
                            Array.from(targetInClone.children).forEach(node => {
                                if (node === sections[1]) startDeleting = true;
                                if (startDeleting) node.remove();
                            });
                        }
                        combined += clone.outerHTML;
                        break; 
                    }
                    combined += child.outerHTML;
                }
                return combined;
            }""")

            if html_slice:
                # 通过参数传递 html 字符串，避免 f-string 报错
                temp_handle = page.evaluate_handle("(html_data) => { \
                    const d = document.createElement('div'); \
                    d.innerHTML = html_data; \
                    return d; \
                }", html_slice)
                md_content += CrawlerUtils.purified_to_md(page, temp_handle)
            else:
                md_content += CrawlerUtils.purified_to_md(page, page.locator('main').first)

            if self.common_admission_md:
                md_content += "\n\n---\n## General Admission Requirements\n\n" + self.common_admission_md

            # 保存文件
            safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_dir, safe_file_name), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    存储成功 {safe_file_name}")

        except Exception as e:
            print(f"    详情解析异常：{e}")
        finally:
            page.close()


if __name__ == "__main__":
    EsslingenScraper().run()
