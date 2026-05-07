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
    "UNIVERSITY_NAME": "EBS_University",
    "ROOT_DOMAIN": "https://www.ebs.edu",
    "LIST_URL": "https://www.ebs.edu/en/education",
    "OUTPUT_DIR": "EBS_University_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # 语义匹配关键词
    "SEMANTIC_HEADLINES": [
        "Admission requirements and application",
        "An investment in your future"
    ],
    "TIMEOUT": 60000
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 50 if is_folder else 40
        return name[:limit]

    @staticmethod
    def nuke_cookie_overlays(page):
        page.evaluate("""() => {
            const trash = ['#usercentrics-root', '#uc-cmp-description', '.uc-container', '.modal-backdrop'];
            trash.forEach(s => document.querySelector(s)?.remove());
            document.querySelectorAll('*').forEach(el => {
                if(el.shadowRoot) { el.shadowRoot.innerHTML = ''; el.remove(); }
            });
            document.body.style.setProperty('overflow', 'auto', 'important');
            document.documentElement.style.setProperty('overflow', 'auto', 'important');
        }""")

    @staticmethod
    def clean_html_node(element, base_url):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)
        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and not href.startswith(('http', 'mailto', '#')):
                a.set('href', urljoin(base_url, href))
        raw_html = etree.tostring(el, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        content = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class EBSScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        # 获取列表
        tasks = self.fetch_list_via_playwright()
        if not tasks:
            print("采集失败：未能捕捉到专业链接")
            return

        print(f"成功：共锁定 {len(tasks)} 个专业，开始执行详情采集")

        # 详情处理
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_list_via_playwright(self):
        collected = []
        with sync_playwright() as p:
            print("启动浏览器执行全屏模拟与动态筛选")
            browser = p.chromium.launch(headless=False, args=['--start-maximized'])
            context = browser.new_context(no_viewport=True, user_agent=CONFIG["USER_AGENT"])
            page = context.new_page()

            try:
                page.goto(CONFIG["LIST_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT"])
                time.sleep(3)

                # 删除弹窗并解锁页面
                CrawlerUtils.nuke_cookie_overlays(page)

                # 下滑页面直到筛选器所在位置
                # 寻找包含筛选器的主要容器 id="bJS_main" 或者是 class="gridAnchor"
                print("    正在向下滚动至筛选区域")
                page.evaluate("""() => {
                    const anchor = document.querySelector('.gridAnchor') || document.querySelector('#bJS_main');
                    if (anchor) {
                        anchor.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                }""")
                time.sleep(2)

                # 点击“硕士（掌握）”按钮
                # 在 gridAnchor 内部寻找带有 active 文字或 Master/掌握文本的按钮
                master_selector = 'button.b_filter__flag:has-text("掌握"), button.b_filter__flag:has-text("Master")'

                try:
                    # 等待按钮在 DOM 中变为可用
                    master_btn = page.wait_for_selector(master_selector, state="attached", timeout=150000)
                    if master_btn:
                        print("   发现 Master 按钮，点击")
                        master_btn.scroll_into_view_if_needed()
                        master_btn.click(force=True)
                        time.sleep(4)  # 等待 Grid 根据筛选条件重新渲染
                except:
                    print("    语义定位失败，尝试使用类名顺序定位")
                    # 如果文本定位失败，尝试直接点击第二个 flag 按钮（通常是 Master）
                    flags = page.locator('.b_filter__flag').all()
                    if len(flags) >= 2:
                        flags[1].click(force=True)
                        time.sleep(4)

                # 提取卡片链接
                # 确保 article 已经加载
                page.wait_for_selector('article.b_lmgrid__item', timeout=10000)
                grid_container = page.locator('.b_lmgrid__content').first

                cards = grid_container.locator('article.b_lmgrid__item a.b_course__link').all()

                for card in cards:
                    href = card.get_attribute("href")
                    # 获取卡片内标题
                    header = card.locator('.b_course__header')
                    name = header.inner_text() if header.count() > 0 else "Major"
                    if href:
                        collected.append({
                            "name": name.strip(),
                            "url": urljoin(CONFIG["ROOT_DOMAIN"], href)
                        })

                print(f"    成功捕捉到 {len(collected)} 个专业")
            except Exception as e:
                print(f"    浏览器操作异常: {e}")

            browser.close()
        return collected

    def process_major(self, task):
        """密度比对 + 祖父级回溯"""
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"   跳过 {safe_name}");
                return

            res = self.session.get(task["url"], timeout=30)
            tree = etree.HTML(res.text)
            main_node = tree.xpath('//main')
            if not main_node: return
            main_node = main_node[0]

            os.makedirs(major_dir, exist_ok=True)
            final_content_html = []

            # 首部大块提取 (密度比对)
            # 获取 main 下的顶级 div
            top_divs = main_node.xpath('./div')
            if len(top_divs) >= 1:
                winner = top_divs[0]
                # 比对前两块的文本长度
                if len(top_divs) >= 2:
                    len1 = len("".join(top_divs[0].xpath('.//text()')))
                    len2 = len("".join(top_divs[1].xpath('.//text()')))
                    winner = top_divs[0] if len1 >= len2 else top_divs[1]
                final_content_html.append(CrawlerUtils.clean_html_node(winner, task['url']))
            else:
                # 保底
                fallback = tree.xpath('//*[contains(@class, "b_element")][1]')
                if fallback: final_content_html.append(CrawlerUtils.clean_html_node(fallback[0], task['url']))

            # 语义 2-step 回溯祖父标签
            for kw in CONFIG["SEMANTIC_HEADLINES"]:
                # 寻找 H2 标题内容
                h2s = tree.xpath(f'//h2[contains(normalize-space(.), "{kw}")]')
                if h2s:
                    try:
                        grandparent = h2s[0].getparent().getparent()
                        if grandparent is not None:
                            print(f"    提取块: {kw}")
                            final_content_html.append(CrawlerUtils.clean_html_node(grandparent, task['url']))
                    except:
                        pass

            final_md = CrawlerUtils.to_markdown("".join(final_content_html))
            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n# {task['name']}\n\n{final_md}")
            print(f"    成功 数据已保存。")

        except Exception as e:
            print(f"    失败 {task['name']} 异常: {e}")


if __name__ == "__main__":
    EBSScraper().run()