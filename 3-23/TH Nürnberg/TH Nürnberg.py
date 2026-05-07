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
    "UNIVERSITY_NAME": "TH_Nuernberg",
    "ROOT_DOMAIN": "https://www.th-nuernberg.de",
    "LIST_URL": "https://www.th-nuernberg.de/studium-karriere/studien-und-bildungsangebot/masterstudiengaenge/",
    "OUTPUT_DIR": "TH_Nuernberg_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # PDF 匹配正则
    "PDF_PATTERN": r"Studien-.*Prüfungsordnung|Zulassungssatzung|Admission.*Regulations|Zulassung",
    # 穿透链接配置
    "PENETRATION_LINK_TEXT": "dieser Seite",
    "PENETRATION_XPATH": '//*[@id="c170979"]/div/div'
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
        noise = ['blockquote', 'img', 'video', 'picture', 'figure', 'svg', 'button', 'nav', 'script', 'style', 'iframe',
                 'header', 'footer']
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


class THNScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": CONFIG["USER_AGENT"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        })

    def run(self):
        tasks = self.fetch_major_list()
        if not tasks:
            print(" 无法锁定专业列表 请检查网络或是否被 WAF 拦截 ")
            return

        print(f"获取成功：共锁定 {len(tasks)} 个硕士专业 ")

        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(1.5, 3.0))

    def fetch_major_list(self):
        collected = []
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=30)
            if res.status_code != 200:
                print(f" 列表页请求失败，状态码: {res.status_code}")
                return []

            tree = etree.HTML(res.text)
            # 锁定 content-inner 容器下的所有包含 /studiengang/ 或 ohm-professional-school 的链接
            links = tree.xpath('//div[contains(@class, "content-inner")]//a')

            seen_urls = set()
            for a in links:
                href = a.get('href')
                if not href: continue
                full_url = urljoin(CONFIG["ROOT_DOMAIN"], href)

                # 筛选条件：链接包含特定路径 不是重复链接 文本不为空
                is_major_link = "/studiengang/" in full_url or "ohm-professional-school" in full_url
                if is_major_link and full_url not in seen_urls:
                    name = "".join(a.xpath('.//text()')).strip()
                    if name and len(name) > 3:  # 过滤掉掉头的小图标或短链接
                        collected.append({"name": name, "url": full_url})
                        seen_urls.add(full_url)

            return collected
        except Exception as e:
            print(f" 列表获取异常: {e}")
        return []

    def process_detail(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"    跳过, {safe_name}")
                return

            res = self.session.get(task["url"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)

            os.makedirs(major_dir, exist_ok=True)
            final_content_pieces = []

            # 探测 Type 1 (标准型：含有 breadcrumb 且内有 h1)
            is_t1 = tree.xpath('//div[contains(@class, "breadcrumb")]//h1')
            # 探测 Type 2 (学院型：含有 education-events--right)
            is_t2 = tree.xpath('//div[contains(@class, "education-events--right")]')

            if is_t1:
                print("      类型: 标准型详情页")
                # 顺序提取指定块
                selectors = [
                    '//div[contains(@class, "breadcrumb")]',
                    '//div[contains(@class, "course-header")]',
                    '//*[@id="tab-overview"]',
                    '//*[@id="tab-requirements"]'
                ]
                for xpath in selectors:
                    nodes = tree.xpath(xpath)
                    if nodes:
                        if "tab-requirements" in xpath:
                            # 在此区域执行 PDF 和 穿透审计
                            self.audit_special_content(nodes[0], task['url'], major_dir, final_content_pieces)

                        final_content_pieces.append(CrawlerUtils.clean_html_node(nodes[0], task['url']))

            elif is_t2:
                print("      类型: 专业学院型 (哨兵模式)")
                # 哨兵：从 .education-events--right 顶级块开始到 <h2>Referenzen</h2>
                start_node = is_t2[0]
                # 寻找该节点在 main 容器下的顶级父块
                main_child = start_node
                while main_child.getparent() is not None and main_child.getparent().tag != 'body' and 'main' not in (
                        main_child.getparent().get('class') or ''):
                    main_child = main_child.getparent()

                # 线性扫描
                curr = main_child
                while curr is not None:
                    # 检查熔断哨兵：h2 包含 Referenzen
                    h2_texts = curr.xpath('.//h2//text() | self::h2//text()')
                    if any("Referenzen" in t for t in h2_texts):
                        print("      命中哨兵 [Referenzen]")
                        break

                    final_content_pieces.append(CrawlerUtils.clean_html_node(curr, task['url']))
                    curr = curr.getnext()

            else:
                # 保底全量 main
                print("      类型: 未知异构，执行保底提取")
                main_box = tree.xpath('//main | //*[@id="main"] | //div[@role="main"]')
                if main_box:
                    final_content_pieces.append(CrawlerUtils.clean_html_node(main_box[0], task['url']))

            # --- 保存 ---
            full_md = CrawlerUtils.to_markdown("".join(final_content_pieces))
            with open(os.path.join(major_dir, f"{safe_name}.md"), "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n{full_md}")
            print(f"    成功，数据已归档 ")

        except Exception as e:
            print(f"    失败，{task['name']} 处理异常: {e}")

    def audit_special_content(self, node, base_url, major_dir, content_list):
        """针对 tab-requirements 区域 """
        links = node.xpath('.//a')
        # PDF 审计
        pdf_found = False
        for a in links:
            t = "".join(a.xpath('.//text()')).strip()
            h = a.get('href')
            if h and h.lower().endswith('.pdf') and re.search(CONFIG["PDF_PATTERN"], t, re.IGNORECASE):
                if not pdf_found:
                    self.download_pdf(urljoin(base_url, h), t, major_dir)
                    pdf_found = True

        # 穿透
        for a in links:
            t = "".join(a.xpath('.//text()')).strip()
            if CONFIG["PENETRATION_LINK_TEXT"] in t:
                jump_url = urljoin(base_url, a.get('href'))
                print(f"      穿透链接: {jump_url}")
                try:
                    r = self.session.get(jump_url, timeout=20)
                    jt = etree.HTML(r.text)
                    p_node = jt.xpath(CONFIG["PENETRATION_XPATH"])
                    if p_node:
                        content_list.append("\n\n---\n### Additional Requirements Appendix\n")
                        content_list.append(CrawlerUtils.clean_html_node(p_node[0], jump_url))
                except:
                    pass

    def download_pdf(self, url, label, folder):
        try:
            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                name = CrawlerUtils.sanitize_path(label, False) + ".pdf"
                with open(os.path.join(folder, name), 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF下载 {name}")
        except:
            pass


if __name__ == "__main__":
    THNScraper().run()