import os
import re
import time
import requests
import unicodedata
from lxml import etree
from urllib.parse import urljoin
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "TH_Deggendorf",
    "ROOT_DOMAIN": "https://www.th-deg.de",
    "START_URL": "https://www.th-deg.de/bewerbung#sprachvoraussetzungen",
    "OUTPUT_DIR": "TH_Deggendorf_Data",
    "HEADERS": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    "TIMEOUT": 30,
    "DELAY": 1.2
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
        limit = 50 if is_folder else 35
        return name[:limit]

    @staticmethod
    def clean_html(element):
        if element is None: return ""
        for a in element.xpath('.//a'):
            href = a.get('href')
            if href and href.startswith('/'):
                a.set('href', urljoin(CONFIG["ROOT_DOMAIN"], href))

        noise_tags = [
            '//div[@id="CybotCookiebotDialog"]',  # 移除指定的弹窗
            './/script', './/style', './/font', './/img', './/picture',
            './/video', './/svg', './/button', './/nav', './/blockquote'
        ]
        for tag in noise_tags:
            targets = element.xpath(tag) if tag.startswith('/') else element.xpath(tag)
            for node in targets:
                p = node.getparent()
                if p is not None: p.remove(node)

        raw_html = etree.tostring(element, encoding='unicode', method='html')
        return re.sub(r'</?font[^>]*>', '', raw_html, flags=re.IGNORECASE)

    @staticmethod
    def to_markdown(html_content):
        if not html_content: return ""
        markdown_text = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in markdown_text.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class DeggendorfScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(CONFIG["HEADERS"])
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        print(f" 正在启动德根多夫应用技术大学采集")

        # 列表页提取
        print(f" 正在访问列表页：{CONFIG['START_URL']}")
        try:
            res = self.session.get(CONFIG["START_URL"], timeout=CONFIG["TIMEOUT"])
            res.encoding = 'utf-8'
            tree = etree.HTML(res.text)
        except Exception as e:
            print(f"访问列表页失败：{e}")
            return

        # 语义化定位硕士列表容器
        # 找文字包含 Masterstudiengänge 的 h4 -> 其所属的 heading -> 它的下一个兄弟节点内容区
        master_panel_xpath = '//h4[contains(., "Masterstudiengänge")]/ancestor::div[contains(@class, "panel-heading")]/following-sibling::div[contains(@class, "panel-collapse")]'
        container = tree.xpath(master_panel_xpath)

        if not container:
            print("[定位失败：未能通过文字内容找到硕士列表容器。正在尝试备选")
            container = tree.xpath('//div[contains(@id, "accordion")]//div[contains(@class, "panel-collapse")]')

        if not container:
            print("严重错误：无法在页面上找到硕士课程区域，任务终止")
            return

        # 锁定面板内的所有表格
        tables = container[0].xpath('.//table')
        if len(tables) < 2:
            print(f" 警告：在该区域仅找到 {len(tables)} 个表格，预期应有两个（全/非全日制）。")

        tasks = []
        # 第一个表：全日制
        if len(tables) >= 1:
            print("全日制硕士专业列表")
            tasks += self.parse_rows(tables[0], is_full_time=True)
        # 第二个表：非全日制
        if len(tables) >= 2:
            print("非全日制硕士专业列表")
            tasks += self.parse_rows(tables[1], is_full_time=False)

        print(f" "
              f"获取成功,锁定了 {len(tasks)} 个有效硕士专业。开始执行详情切片采集")

        # 遍历详情
        for idx, task in enumerate(tasks):
            time_label = "全日制" if task["is_full_time"] else "非全日制"
            print(f"\n[{idx + 1}/{len(tasks)}] ({time_label}) 正在挖掘：{task['name']}")
            self.process_major_detail(task)
            time.sleep(CONFIG["DELAY"])

        print(f"\n 采集任务圆满完成-存放在：{self.output_dir}")

    def parse_rows(self, table_node, is_full_time):
        """解析表格行，提取元数据及内嵌 PDF 链接"""
        results = []
        rows = table_node.xpath('.//tbody/tr')
        for row in rows:
            try:
                tds = row.xpath('./td')
                if len(tds) < 6: continue

                # 专业名及 URL
                name_cell = tds[0]
                major_name = "".join(name_cell.xpath('.//text()')).strip()
                major_url_node = name_cell.xpath('.//a/@href')
                major_url = urljoin(CONFIG["ROOT_DOMAIN"], major_url_node[0]) if major_url_node else None

                # 其他元数据列
                start = tds[1].xpath('string(.)').strip()
                period = tds[2].xpath('string(.)').strip()
                location = tds[4].xpath('string(.)').strip()
                lang = tds[5].xpath('string(.)').strip()

                # 准入要求文本 + PDF 下载链接
                req_cell = tds[3]
                req_text = " ".join([t.strip() for t in req_cell.xpath('.//text()') if t.strip()])
                pdf_urls = [urljoin(CONFIG["ROOT_DOMAIN"], h) for h in
                            req_cell.xpath('.//a[contains(@href, ".pdf")]/@href')]

                results.append({
                    "name": major_name, "url": major_url, "start": start, "period": period,
                    "req_text": req_text, "pdfs": pdf_urls, "location": location,
                    "lang": lang, "is_full_time": is_full_time
                })
            except:
                continue
        return results

    def process_major_detail(self, task):
        """详情页解析：执行精准切片并将 PDF 保存至对应文件夹"""
        try:
            # 建立文件夹
            url_slug = task["url"].strip("/").split("/")[-1] if task["url"] else "no_link"
            safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
            major_path = os.path.join(self.output_dir, safe_folder)
            if not os.path.exists(major_path): os.makedirs(major_path)

            # 下载表格内探测到的 PDF
            for p_url in task["pdfs"]:
                p_filename = f"Req_{CrawlerUtils.sanitize_path(p_url.split('/')[-1], False)}"
                if not p_filename.lower().endswith('.pdf'): p_filename += ".pdf"
                self.download_file(p_url, os.path.join(major_path, p_filename))

            # 如果存在详情链接，执行切片抓取
            detail_md = ""
            if task["url"]:
                detail_md = self.fetch_detail_slice(task)

            # 组装 MD 内容
            # URL
            final_md = f"URL: {task['url'] if task['url'] else 'None'}\n\n"
            # 元数据
            meta_line = f"**{task['name']} | {task['start']} | {task['period']} | {task['req_text']} | {task['location']} | {task['lang']}**\n\n---\n\n"
            final_md += meta_line + (detail_md if detail_md else "[无法通过 URL 获取更多详情，请参考上述列表信息]")

            # 保存 MD 文件
            md_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
            with open(os.path.join(major_path, md_name), "w", encoding="utf-8") as f:
                f.write(final_md)
            print(f"    -存储成功{md_name}")

        except Exception as e:
            print(f"    专业采集过程出错：{e}")

    def fetch_detail_slice(self, task):
        """根据全日制/非全日制逻辑执行详情页切片"""
        try:
            res = self.session.get(task["url"], timeout=20)
            tree = etree.HTML(res.text)

            # 第一个 textarea
            start_node = tree.xpath('//div[contains(@class, "pimcore_area_textarea")]')
            if not start_node: return ""

            # 哨兵设置
            if task["is_full_time"]:
                sentinel = "studieninhalte"  # 学习内容
            else:
                sentinel = "kontakt & beratung für interessierte"  # 联系与咨询

            extracted_html = []
            recording = False
            # 找到起点后，线性扫描其父容器下的兄弟节点
            parent = start_node[0].getparent()
            for child in parent.xpath('./*'):
                if child == start_node[0]:
                    recording = True

                if recording:
                    # 检查该节点内是否包含哨兵标题 (H2)
                    headers = child.xpath('.//h2')
                    h2_hit = False
                    for h in headers:
                        h_text = "".join(h.xpath('.//text()')).lower()
                        if sentinel in h_text:
                            h2_hit = True
                            break
                    if h2_hit:
                        print(f"    遇到哨兵位：[{sentinel}]-切片结束")
                        break

                    extracted_html.append(CrawlerUtils.clean_html(child))

            return CrawlerUtils.to_markdown("".join(extracted_html))
        except:
            return ""

    def download_file(self, url, path):
        try:
            with self.session.get(url, stream=True, timeout=30) as r:
                if r.status_code == 200:
                    with open(path, 'wb') as f:
                        for chunk in r.iter_content(8192): f.write(chunk)
                    print(f"    -PDF 已保存：{os.path.basename(path)}")
        except:
            pass


if __name__ == "__main__":
    DeggendorfScraper().run()