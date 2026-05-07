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
    "UNIVERSITY_NAME": "HHL_Leipzig",
    "ROOT_DOMAIN": "https://www.hhl.de",
    "LIST_URL": "https://www.hhl.de/programs/master-program/",
    "OUTPUT_DIR": "HHL_Leipzig_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # id锚点
    "ID_ANCHORS": ["Ataglance", "Application", "Financing"],
    # h2补充关键词
    "SEMANTIC_H2_KEYWORDS": [
        "Essentials", "Double degree option", "Tuition fees",
        "Scholarships", "Payment policy"
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


class HHLScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        tasks = self.fetch_major_list()
        if not tasks:
            print(" 无法锁定专业列表，请检查容器 ")
            return

        print(f"共锁定 {len(tasks)} 个专业 开始执行采集")

        # 详情采集
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_major(task)
            time.sleep(random.uniform(1.2, 2.5))

    def fetch_major_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=30)
            tree = etree.HTML(res.text)
            list_containers = tree.xpath('//*[@is="flynt-grid-teasers"]')
            if not list_containers: return []
            columns = [col for container in list_containers for col in
                       container.xpath('.//div[contains(@class, "gridTeasers-column")]')]
            # 元数据获取和专业网址获取
            for col in columns:
                name = "".join(col.xpath('.//h3//text()')).strip()
                desc_nodes = col.xpath('.//div[contains(@class, "gridTeasers-contentWrapper")]/p[not(a)]//text()')
                meta = " | ".join([d.strip() for d in desc_nodes if d.strip()])
                link_node = col.xpath('.//a[contains(., "Learn") or contains(., "Details") or contains(., "了解")]')
                if not link_node: link_node = col.xpath('.//a')

                if name and link_node:
                    collected.append({
                        "name": name,
                        "url": urljoin(CONFIG["ROOT_DOMAIN"], link_node[0].get('href')),
                        "meta": meta
                    })
            return collected
        except:
            return []

    def process_major(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过{safe_name}");
                return

            res = self.session.get(task["url"], timeout=30)
            tree = etree.HTML(res.text)
            main_node = tree.xpath('//main')
            if not main_node: return
            main_node = main_node[0]

            final_html_chunks = []

            # 首个有效 div 提取
            first_div = main_node.xpath('./div[1]')
            if first_div:
                final_html_chunks.append(CrawlerUtils.clean_html_node(first_div[0], task['url']))

            # id锚点获取到下一个h2再熔断
            for tid in CONFIG["ID_ANCHORS"]:
                id_el = tree.xpath(f'//*[@id="{tid}"]')
                if id_el:
                    print(f"      执行 ID 获取: {tid}")
                    chunk = []
                    curr = id_el[0]
                    is_start = True
                    while curr is not None:
                        # 非起点且遇到 H2
                        if not is_start and (curr.tag == 'h2' or curr.xpath('.//h2')):
                            break
                        chunk.append(CrawlerUtils.clean_html_node(curr, task['url']))
                        curr = curr.getnext()
                        is_start = False
                    final_html_chunks.append("".join(chunk))

            # 语义 H2 匹配
            # 建立已处理内容的指纹，防止与 ID 阶段重复抓取
            processed_html_fingerprints = "".join(final_html_chunks)

            all_h2s = tree.xpath('//h2')
            for h2 in all_h2s:
                h2_text = "".join(h2.xpath('.//text()')).strip()
                # 语义匹配关键词
                if any(kw.lower() in h2_text.lower() for kw in CONFIG["SEMANTIC_H2_KEYWORDS"]):
                    try:
                        # 向上找三层父类标签
                        target_container = h2.getparent().getparent().getparent()
                        if target_container is not None:
                            html_snippet = CrawlerUtils.clean_html_node(target_container, task['url'])
                            # 如果这个大块还没被抓过，则添加
                            if html_snippet[:100] not in processed_html_fingerprints:
                                print(f"      命中语义回溯块: {h2_text[:20]}")
                                final_html_chunks.append(html_snippet)
                    except:
                        pass

            os.makedirs(major_dir, exist_ok=True)
            final_md_body = CrawlerUtils.to_markdown("".join(final_html_chunks))

            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n# {task['name']}\n\n")
                f.write(f"**{task['meta']}**\n\n---\n\n")
                f.write(final_md_body)
            print(f"    数据已归档 ")

        except Exception as e:
            print(f"    {task['name']} 异常: {e}")


if __name__ == "__main__":
    scraper = HHLScraper()
    scraper.run()