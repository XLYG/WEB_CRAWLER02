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
    "UNIVERSITY_NAME": "TH_Luebeck",
    "ROOT_DOMAIN": "https://www.th-luebeck.de",
    "LIST_URL": "https://www.th-luebeck.de/studium/studienangebot/studiengaenge/",
    "OUTPUT_DIR": "TH_Luebeck_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # PDF 优先级匹配正则
    "PDF_PRI_1": r"application information",
    "PDF_PRI_2": r"Prüfungsordnung",
    # 哨兵配置
    "SENTINEL_END_TEXT": "Kontakt"
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
    def clean_element(element):
        if element is None: return ""
        import copy
        el = copy.deepcopy(element)
        noise = ['blockquote', 'img', 'video', 'picture', 'svg', 'button', 'nav', 'script', 'style', 'iframe']
        for tag in noise:
            for node in el.xpath(f'.//{tag}'):
                p = node.getparent()
                if p is not None: p.remove(node)

        for a in el.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        raw_html = etree.tostring(el, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_list):
        full_html = "".join(html_list)
        if not full_html: return ""
        content = md(full_html, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class THLuebeckScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})

    def run(self):
        # 获取硕士列表
        tasks = self.fetch_master_list()
        if not tasks:
            print("  无法在页面中找到专业列表表格 ")
            return

        print(f" 共锁定 {len(tasks)} 个硕士专业 开始执行详情采集")

        # 详情处理
        for idx, task in enumerate(tasks):
            print(f"[{idx + 1}/{len(tasks)}] 正在挖掘：{task['name']}")
            self.process_detail(task)
            time.sleep(random.uniform(1.5, 3.0))

    def fetch_master_list(self):
        """采用语义化探测 """
        collected = []
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=30)
            res.encoding = res.apparent_encoding
            tree = etree.HTML(res.text)

            # 寻找包含 "Studiengang" 表头的 table
            tables = tree.xpath('//table[.//th[contains(., "Studiengang")]]')
            if not tables:
                # 保底策略：寻找所有 ce-table
                tables = tree.xpath('//table[contains(@class, "ce-table")]')

            if not tables: return []

            # 遍历表格行
            rows = tables[0].xpath('.//tr[td]')
            for row in rows:
                cells = row.xpath('./td')
                if len(cells) < 4: continue

                # 语义探测学位列：遍历所有单元格，寻找包含 M.Sc, M.Eng, M.A. 的专业
                row_text = "".join(row.xpath('.//text()'))
                degree_match = re.search(r'(M\.Sc\.|M\.Eng\.|M\.A\.)', row_text)

                if not degree_match:
                    continue  # 跳过非硕士专业

                # 可以直接通过这个参数调用学位类型
                degree = degree_match.group(1)

                # 提取第一个单元格的链接和名称
                link_node = cells[0].xpath('.//a')
                if not link_node: continue

                name = "".join(link_node[0].xpath('.//text()')).strip()
                href = link_node[0].get('href')

                # 抓取元数据列
                meta = " | ".join(["".join(c.xpath('.//text()')).strip() for c in cells])

                collected.append({
                    "name": name,
                    "url": urljoin(CONFIG["ROOT_DOMAIN"], href),
                    "meta": meta
                })
        except Exception as e:
            print(f"      列表抓取失败: {e}")
        return collected

    def process_detail(self, task):
        try:
            safe_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_name)
            if os.path.exists(major_dir):
                print(f"   跳过 {safe_name}");
                return

            res = self.session.get(task["url"], timeout=30)
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)
            main_node = tree.xpath('//main')
            if not main_node:
                # 保底：若无 main，寻找 content 区域
                main_node = tree.xpath('//*[@id="content"] | //article')

            if not main_node:
                print(f"     无法锁定内容区域：{task['url']}")
                return
            main_node = main_node[0]

            # 通用 PDF 审计
            pdf_url = None
            all_links = main_node.xpath('.//a')

            # 第一优先：application information
            for a in all_links:
                atxt = "".join(a.xpath('.//text()')).strip()
                if re.search(CONFIG["PDF_PRI_1"], atxt, re.IGNORECASE):
                    pdf_url = urljoin(task['url'], a.get('href'))
                    break

            # 第二优先：Prüfungsordnung (若 1 没中)
            if not pdf_url:
                for a in all_links:
                    atxt = "".join(a.xpath('.//text()')).strip()
                    if re.search(CONFIG["PDF_PRI_2"], atxt, re.IGNORECASE):
                        pdf_url = urljoin(task['url'], a.get('href'))
                        break

            # 哨兵切片提取
            start_el = main_node.xpath('.//h1')[0] if main_node.xpath('.//h1') else None

            collected_snippets = []
            if start_el is not None:
                # 获取 main 下的直接子元素
                top_blocks = main_node.xpath('./*')

                # 回溯定位起点所在的顶级块
                start_idx = 0
                temp = start_el
                found_top = None
                while temp is not None:
                    if temp.getparent() == main_node:
                        found_top = temp;
                        break
                    temp = temp.getparent()

                if found_top is not None:
                    try:
                        start_idx = top_blocks.index(found_top)
                    except:
                        pass

                found_stop = False
                for i in range(start_idx, len(top_blocks)):
                    block = top_blocks[i]

                    # 检查熔断哨兵：该块内是否有 h2 包含 "Kontakt"
                    h2_list = block.xpath('.//h2')
                    if block.tag == 'h2': h2_list.append(block)

                    for h2 in h2_list:
                        if CONFIG["SENTINEL_END_TEXT"].lower() in "".join(h2.xpath('.//text()')).lower():
                            print(f"     命中哨兵 [{CONFIG['SENTINEL_END_TEXT']}]")
                            found_stop = True
                            break

                    if found_stop: break

                    collected_snippets.append(CrawlerUtils.clean_element(block))

            # 异常保底：如果切片结果太少或失败，全量获取 main
            if len(collected_snippets) < 1:
                print(f"    特殊情况 哨兵提取异常，全量保底获取 main 区域 ")
                collected_snippets = [CrawlerUtils.clean_element(main_node)]

            # 保存
            os.makedirs(major_dir, exist_ok=True)
            if pdf_url:
                url_slug = pdf_url.split('/')[-1].split('.')[0][-8:]
                p_name = f"Reg_Info_{url_slug}.pdf"
                self.download_pdf(pdf_url, major_dir, p_name)

            final_md = CrawlerUtils.to_markdown(collected_snippets)
            md_path = os.path.join(major_dir, f"{safe_name}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"URL: {task['url']}\n\n")
                f.write(f"**{task['name']} | 元数据: {task['meta']}**\n\n")
                f.write("---\n\n")
                f.write(final_md)
            print(f"    数据已归档 ")

        except Exception as e:
            print(f"   失败 {task['name']} 处理异常: {e}")

    def download_pdf(self, url, folder, filename):
        try:
            res = self.session.get(url, timeout=25, stream=True)
            if res.status_code == 200:
                with open(os.path.join(folder, filename), 'wb') as f:
                    for chunk in res.iter_content(8192): f.write(chunk)
                print(f"       PDF获取成功 {filename}")
        except:
            pass


if __name__ == "__main__":
    THLuebeckScraper().run()