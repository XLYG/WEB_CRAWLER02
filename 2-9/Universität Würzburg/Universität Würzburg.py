import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
from typing import List, Dict, Optional
from markdownify import markdownify as md

class WuerzburgMasterScraper:
    """
    维尔茨堡大学硕士专业爬虫（过滤图片与侧边栏，仅保存文本Markdown）
    """
    def __init__(self, list_url: str):
        self.list_url = list_url
        self.base_domain = "uni-wuerzburg.de"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.data_dir = "Wuerzburg_Master_Data"
        os.makedirs(self.data_dir, exist_ok=True)

    def audit_url(self, url: str, context: str = ""):
        parsed = urlparse(url)
        print(f"{context}: {url}")
        if self.base_domain not in parsed.netloc:
            print(f"警告！跳转到外部域名: {parsed.netloc}")
        if parsed.query:
            print(f"参数: {parsed.query}")
            for param in parsed.query.split('&'):
                if '=' in param:
                    k, v = param.split('=', 1)
                    if len(v) > 50 or any(x in k.lower() for x in ['token', 'hash', 'sig']):
                        print(f"注意: 参数 '{k}' 可能为动态令牌")

    # 获取专业链接
    def get_program_links_from_main(self) -> List[Dict[str, str]]:
        print(f"正在获取主列表页: {self.list_url}")
        self.audit_url(self.list_url, "主列表页")
        response = self.session.get(self.list_url)
        soup = BeautifulSoup(response.text, 'html.parser')
        programs = []

        table = soup.find('table')
        if not table:
            print("未找到表格，尝试直接查找指向院系子域名的链接")
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'phil.uni-wuerzburg.de' in href or 'uni-wuerzburg.de' in href:
                    if 'bewerbung' not in href and 'studienangelegenheiten' not in href:
                        name = a.get_text(strip=True)
                        if name and len(name) > 3:
                            full_url = urljoin(self.list_url, href)
                            programs.append({'name': name, 'url': full_url})
            return programs

        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 1:
                continue
            first_cell = cells[0]
            a_tag = first_cell.find('a', href=True)
            if a_tag:
                prog_url = a_tag['href']
                prog_name = a_tag.get_text(strip=True)
                prog_name = re.sub(r'\s+', ' ', prog_name).strip()
                if prog_name and prog_url:
                    full_url = urljoin(self.list_url, prog_url)
                    if not any(p['url'] == full_url for p in programs):
                        programs.append({'name': prog_name, 'url': full_url})
                        print(f"发现专业: {prog_name} -> {full_url}")

        print(f"共找到 {len(programs)} 个专业")
        return programs

    # 解析详情页（过滤图片与侧边栏）
    def parse_program_detail(self, program: Dict[str, str]) -> Dict:
        url = program['url']
        name = program['name']
        print(f"正在解析: {name} - {url}")
        self.audit_url(url, "详情页")

        try:
            response = self.session.get(url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            print(f"请求失败: {e}")
            return {'name': name, 'content_md': '', 'error': str(e)}

        # 定位主要内容区域
        main_content = None
        possible_selectors = [
            'main', 'div#content', 'div.content', 'div#main-content',
            'div.field-items', 'article', 'div.region-content'
        ]
        for selector in possible_selectors:
            main_content = soup.select_one(selector)
            if main_content:
                print(f"使用选择器 '{selector}' 定位到内容")
                break

        if not main_content:
            print("未找到特定内容区域，使用body（排除页眉页脚）")
            body = soup.find('body')
            if body:
                for elem in body.find_all(['header', 'footer', 'nav']):
                    elem.decompose()
                main_content = body

        if main_content:
            # 过滤图片
            for img in main_content.find_all('img'):
                img.decompose()

            # 过滤侧边栏（基于常见类名/标签）
            sidebar_selectors = [
                'aside',
                'div.sidebar',
                'div.aside',
                'div.secondary',
                'div[class*="sidebar"]',
                'div[class*="col-md-3"]',
                'div[class*="col-sm-3"]',
                'div[class*="col-lg-3"]',
                'div[class*="col-xs-3"]',
                'div[class*="col-3"]'
            ]
            for selector in sidebar_selectors:
                for elem in main_content.select(selector):
                    elem.decompose()

            # 转换为Markdown
            content_md = md(str(main_content))
            content_md = re.sub(r'\n{3,}', '\n\n', content_md)
        else:
            content_md = ""

        return {'name': name, 'url': url, 'content_md': content_md}

    # 保存Markdown
    def save_program_data(self, program_data: Dict):
        folder_name = re.sub(r'[\\/*?:"<>|]', '_', program_data['name'])
        folder_name = folder_name[:100]
        program_folder = os.path.join(self.data_dir, folder_name)
        os.makedirs(program_folder, exist_ok=True)

        md_filename = f"{folder_name}.md"
        md_path = os.path.join(program_folder, md_filename)
        with open(md_path, 'w', encoding='utf-8') as f:
            header = f"# {program_data['name']}\n\n> 原始URL: {program_data['url']}\n\n"
            f.write(header + program_data['content_md'])
        print(f"已保存Markdown: {md_path}")

    # 主流程
    def run(self, max_programs: Optional[int] = None):
        all_programs = self.get_program_links_from_main()
        if not all_programs:
            print("未找到任何专业链接，请检查页面结构")
            return

        # 按专业名称去重
        unique = {}
        for p in all_programs:
            if p['name'] not in unique:
                unique[p['name']] = p
        all_programs = list(unique.values())
        print(f"去重后剩余 {len(all_programs)} 个专业")

        if max_programs:
            all_programs = all_programs[:max_programs]

        for idx, prog in enumerate(all_programs, 1):
            print(f"\n--- 正在处理 {idx}/{len(all_programs)}: {prog['name']} ---")
            try:
                detail = self.parse_program_detail(prog)
                if detail.get('error'):
                    print(f"跳过 {prog['name']} 因错误: {detail['error']}")
                    continue
                self.save_program_data(detail)
                time.sleep(2)
            except Exception as e:
                print(f"处理 {prog['name']} 时出现未预期错误: {e}")
                continue

        print("\n所有专业处理完成！")

if __name__ == "__main__":
    MAIN_LIST_URL = "https://www.uni-wuerzburg.de/studium/studienangelegenheiten/bewerbung-und-einschreibung/masterstudiengaenge/"
    scraper = WuerzburgMasterScraper(MAIN_LIST_URL)
    scraper.run()