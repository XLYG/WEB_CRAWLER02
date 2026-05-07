import os
import re
import time
import random
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "WHU_Management",
    "ROOT_DOMAIN": "https://www.whu.edu",
    "START_URL": "https://www.whu.edu/en/programs/?tx_fprogram_pi1%5Baction%5D=list&tx_fprogram_pi1%5Bcontroller%5D=Program&cHash=e25f7273304e59fbd0e27c76905147ec&tx_fprogram_pi1[categories]=2&tx_fprogram_pi1[searchword]=",
    "OUTPUT_DIR": "WHU_Management_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "DETAIL_ROOT_XPATH": '//*[@id="main"]/section[1]',
    "DETAIL_SLIDER_CLASS": "icon-slider-wrapper",
    "SENTINEL_START_TEXTS": ["Your application at a glance", "Admission requirements"],
    "SENTINEL_END_TEXTS": [
        "Your admissions journey",
        "Our 3-step application process explained",
        "Your admissions journey: Step by step"
    ]
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
    def clean_html_node(element, base_url):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)
        # 物理移除干扰项 (V7.0 标准)
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
        raw_html = raw_html.replace('\xad', '').replace('&shy;', '')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        content = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class WHUScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        # 获取专业列表
        tasks = self.fetch_major_list()
        if not tasks:
            print("列表抓取失败，请检查 URL")
            return

        print(f"捕捉成功：共锁定 {len(tasks)} 个专业，开始采集")

        # 详情处理
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在处理：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_major_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["START_URL"], timeout=30)
            tree = etree.HTML(res.text)
            items = tree.xpath('//div[contains(@class, "program-item")]')
            for item in items:
                link_node = item.xpath('.//div[contains(@class, "headline")]//a')[0]
                name = "".join(link_node.xpath('.//text()')).strip()
                href = link_node.get('href')
                if name and href:
                    collected.append({
                        "name": name,
                        "url": urljoin(CONFIG["ROOT_DOMAIN"], href)
                    })
            return collected
        except:
            return []

    def find_admission_link(self, tree, base_url):
        # A: 在指定的 Section 1 中寻找
        target_section = tree.xpath(CONFIG["DETAIL_ROOT_XPATH"])
        search_roots = target_section if target_section else []
        # B: 添加 Main 容器作为保底搜索域
        main_box = tree.xpath('//main')
        if main_box: search_roots.append(main_box[0])

        for root in search_roots:
            a_tags = root.xpath('.//a')
            for a in a_tags:
                text = "".join(a.xpath('.//text()'))
                title = a.get('title') or ""
                # 包含 Application 且包含 Admissions
                if ("Application" in text and "Admissions" in text) or \
                        ("Application" in title and "Admissions" in title):
                    return urljoin(base_url, a.get('href'))
        return None

    def process_major(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过: {safe_name}")
                return

            res = self.session.get(task["url"], timeout=30)
            tree = etree.HTML(res.text)

            final_content_html = []

            section_1 = tree.xpath(CONFIG["DETAIL_ROOT_XPATH"])
            if section_1:
                final_content_html.append(CrawlerUtils.clean_html_node(section_1[0], task['url']))

            slider = tree.xpath(f'//div[contains(@class, "{CONFIG["DETAIL_SLIDER_CLASS"]}")]')
            if slider:
                final_content_html.append(CrawlerUtils.clean_html_node(slider[0], task['url']))

            # 入学要求页
            admission_link = self.find_admission_link(tree, task['url'])
            admission_md = ""
            if admission_link:
                print(f"     穿透准入页面: {admission_link}")
                admission_md = self.fetch_admission_sentinel(admission_link)

            os.makedirs(major_dir, exist_ok=True)
            main_md = CrawlerUtils.to_markdown("".join(final_content_html))

            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n")
                f.write(f"# {task['name']}\n\n")
                f.write(main_md)
                if admission_md:
                    f.write("\n\n---\n## Detailed Admissions Requirements (Appendix)\n\n")
                    f.write(admission_md)
            print(f"    数据已保存")

        except Exception as e:
            print(f"    {task['name']} 处理异常: {e}")

    def fetch_admission_sentinel(self, url):
        try:
            res = self.session.get(url, timeout=20)
            tree = etree.HTML(res.text)
            main_content = tree.xpath('//main | //*[@id="main"]')[0]

            # 寻找起点 H2/H3
            start_header = None
            all_headers = main_content.xpath('.//h2 | .//h3')
            for h in all_headers:
                t = "".join(h.xpath('.//text()')).strip().lower()
                if any(kw.lower() in t for kw in CONFIG["SENTINEL_START_TEXTS"]):
                    start_header = h
                    break

            if start_header is None: return ""

            # 回溯到 main 下的直接子元素
            temp = start_header
            start_block = None
            while temp is not None:
                if temp.getparent() == main_content:
                    start_block = temp
                    break
                temp = temp.getparent()

            if start_block is None: start_block = start_header

            # 线性获取后续内容
            collected_html = []
            curr = start_block
            found_end = False

            while curr is not None:
                # 检查当前大积木内部是否有结束词
                inner_headers = curr.xpath('.//h2 | .//h3 | self::h2 | self::h3')
                for ih in inner_headers:
                    it = "".join(ih.xpath('.//text()')).strip().lower()
                    if any(kw.lower() in it for kw in CONFIG["SENTINEL_END_TEXTS"]):
                        print(f"        命中熔断点: [{it[:25]}...]")
                        found_end = True
                        break

                if found_end: break

                collected_html.append(CrawlerUtils.clean_html_node(curr, url))
                curr = curr.getnext()

            if collected_html:
                # 在附录开头加入穿透页网址
                appendix_md = f"Source URL: {url}\n\n"
                appendix_md += CrawlerUtils.to_markdown("".join(collected_html))
                return appendix_md
        except:
            pass
        return ""


if __name__ == "__main__":
    WHUScraper().run()