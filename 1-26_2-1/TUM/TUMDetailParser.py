# 文件名: tum_detail_scraper.py
import requests
from lxml import html
import re
import os
from urllib.parse import urljoin, urlparse, unquote
import logging

# 设置日志格式
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TUMDetailParser:
    """
    TUM 专业详情页解析器 (通用工具类)。
    """

    def __init__(self, output_dir="tum_results"):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        self.tree = None
        self.md_output = []
        self.current_url = ""
        self.program_name = "Unknown_Program"

        # 基础输出目录
        self.base_output_dir = output_dir
        if not os.path.exists(self.base_output_dir):
            os.makedirs(self.base_output_dir)

    def reset(self):
        self.tree = None
        self.md_output = []
        self.current_url = ""
        self.program_name = "Unknown_Program"

    def clean_filename(self, text):
        return re.sub(r'[\\/:*?"<>|]', '_', text.strip())

    def fetch_page(self, url):
        self.reset()
        self.current_url = url
        try:
            logging.info(f"正在抓取详情页: {url}")
            response = requests.get(url, headers=self.headers, timeout=20)
            response.raise_for_status()
            self.tree = html.fromstring(response.content)

            # 自动提取 H1 作为文件夹名称
            h1s = self.tree.xpath('//h1')
            if h1s:
                raw_name = h1s[0].text_content().strip()
                self.program_name = self.clean_filename(raw_name)[:80].strip()  # 截断防止文件名过长
            else:
                self.program_name = f"Program_{self.clean_filename(url.split('/')[-1])}"

            return True
        except Exception as e:
            logging.error(f"详情页抓取失败 {url}: {e}")
            return False

    def _get_program_dir(self):
        path = os.path.join(self.base_output_dir, self.program_name)
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    def download_file(self, file_url):
        try:
            parsed_url = urlparse(file_url)
            # 提取原始URL中的文件名
            original_filename = unquote(os.path.basename(parsed_url.path))
            # 分离原始文件名
            file_main_name, _ = os.path.splitext(original_filename)
            # 拼接 _aptitude_assessment，再直接指定 .pdf 后缀（满足格式要求）
            filename = f"{file_main_name}_aptitude_assessment.pdf"

            save_dir = self._get_program_dir()
            save_path = os.path.join(save_dir, filename)

            if os.path.exists(save_path):
                return filename

            logging.info(f"    正在下载附件: {filename}")
            with requests.get(file_url, headers=self.headers, stream=True) as r:
                r.raise_for_status()
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            return filename
        except Exception as e:
            logging.error(f"    附件下载失败: {e}")
            return None

    def clean_text(self, text):
        if not text: return ""
        return re.sub(r'\s+', ' ', text).strip()

    # 内容md格式转化
    def element_to_markdown(self, element, parse_pdfs=False):
        if element is None: return ""
        tag = element.tag
        text = self.clean_text(element.text)
        tail = self.clean_text(element.tail)
        content = ""
        for child in element:
            content += self.element_to_markdown(child, parse_pdfs=parse_pdfs)

        if tag == 'h1':
            result = f"# {text}{content}\n\n"
        elif tag == 'h2':
            result = f"## {text}{content}\n\n"
        elif tag == 'h3':
            result = f"### {text}{content}\n\n"
        elif tag == 'p':
            if 'roofline' in element.get('class', ''):
                result = f"_{text}{content}_\n"
            else:
                result = f"{text}{content}\n\n"
        elif tag in ['strong', 'b']:
            result = f"**{text}{content}**"
        elif tag == 'li':
            result = f"- {text}{content}\n"
        elif tag == 'a':
            href = element.get('href')
            if href:
                full_url = urljoin(self.current_url, href)
                if parse_pdfs and ('pdf' in full_url.lower()):
                    saved_name = self.download_file(full_url)
                    if saved_name:
                        result = f"[{text}{content}]({full_url}) *(附件: {saved_name})*"
                    else:
                        result = f"[{text}{content}]({full_url})"
                else:
                    result = f"[{text}{content}]({full_url})"
            else:
                result = f"{text}{content}"
        elif tag == 'div' and 'flex__md-6' in element.get('class', ''):
            result = f"{text}{content}\n"
        else:
            result = f"{text}{content}"

        return result + (tail if tail else " ")

    def extract_by_xpath(self, xpath_str):
        elements = self.tree.xpath(xpath_str)
        if elements:
            md = self.element_to_markdown(elements[0])
            self.md_output.append(re.sub(r'\n{3,}', '\n\n', md).strip())
            self.md_output.append("\n---\n")

    def extract_accordion(self, target_titles, download_pdfs=False):
        # 查找所有折叠容器
        containers = self.tree.xpath('//div[contains(@class, "accordion")]')
        if not containers: return

        # 遍历所有容器中的所有项目
        # 使用 .// 搜索当前容器下的所有层级
        for container in containers:
            items = container.xpath('.//div[contains(@class, "in2template-accordion")]')
            for item in items:
                title_node = item.xpath('.//span[contains(@class, "in2template-accordion__title")]')
                if not title_node: continue

                raw_title = title_node[0].text_content().strip()
                # 模糊匹配标题
                if any(t.lower() in raw_title.lower() for t in target_titles):
                    panel_nodes = item.xpath('.//div[contains(@class, "in2template-accordion__panel")]')
                    if panel_nodes:
                        panel = panel_nodes[0]
                        md = f"## {raw_title}\n\n" + self.element_to_markdown(panel, parse_pdfs=download_pdfs)
                        self.md_output.append(re.sub(r'\n{3,}', '\n\n', md).strip())
                        self.md_output.append("\n---\n")

    def save(self):
        save_dir = self._get_program_dir()
        filename = f"{self.program_name}.md"
        path = os.path.join(save_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write("\n".join(self.md_output))
        logging.info(f"    文档已保存: {filename}")

    def run_standard_extraction(self):
        if self.tree is None: return

        # 1. Header
        self.extract_by_xpath('//div[contains(@class, "c-header__content-headline")]/parent::div')
        # 2. Key Data
        self.extract_by_xpath('//h2[contains(text(), "Key Data")]/parent::div')

        # 3. Accordions (搜索全文折叠页内容，指定折叠页名称)
        self.extract_accordion(["Program profile", "Language of instruction"], download_pdfs=False)
        self.extract_accordion(["Application process", "Admission process"], download_pdfs=True)

        self.save()