import requests
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.poolmanager import PoolManager
from lxml import html
import os
import re
from urllib.parse import urljoin


# --- 自定义 SSL 适配器 ---
class LegacySSLAdapter(HTTPAdapter):
    """
    这个适配器强制 Python 使用较低的安全级别 (SECLEVEL=1)。
    这通常能解决 'EOF occurred in violation of protocol' 错误，
    因为它允许使用更多种类的加密套件与老旧服务器通信。
    """
    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.create_default_context()
        # 允许较低的安全等级，兼容更多服务器
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx
        )


class RWTHDownloader:
    def __init__(self, url):
        self.url = url
        self.download_dir = "rwth_documents"
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

        # 1. 创建一个 Session 对象（保持一个链接而不用req.get，会被反爬）
        self.session = requests.Session()

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
        })

        # 3. 挂载自定义的 SSL 修复器
        self.session.mount('https://', LegacySSLAdapter())

        # 4. 增加重试机制 (应对不稳定的网络)
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def clean_filename(self, text):
        if not text: return "unknown_file"
        clean_text = text.strip()
        clean_text = re.sub(r'[\\/:*?"<>|]', '_', clean_text)
        clean_text = re.sub(r'\s+', ' ', clean_text)
        return clean_text

    def fetch_and_download(self, limit=5):
        print(f"正在连接目标网页...")
        try:
            # 连接并且指定时间
            response = self.session.get(self.url, timeout=20)
            response.raise_for_status()
            tree = html.fromstring(response.content)
            print("网页加载成功，开始解析表格")
        except Exception as e:
            print(f"网页加载失败: {e}")
            return

        rows = tree.xpath('//*[@id="main"]//table/tbody/tr')
        if not rows:
            print("未找到表格行")
            return

        print(f"找到 {len(rows)} 行数据，准备下载前 {limit} 个...\n")

        for index, row in enumerate(rows[:limit]):
            current_num = index + 1
            title_parts = row.xpath('./td[1]/text()')
            raw_title = "".join(title_parts)
            file_name = self.clean_filename(raw_title)
            if not file_name: file_name = f"document_{current_num}"

            links = row.xpath('./td[2]/a/@href')
            if not links: continue

            full_url = urljoin(self.url, links[0])
            print(f"[{current_num}/{limit}] 正在下载: {file_name}.pdf")
            self.download_single_file(full_url, file_name)

    def download_single_file(self, file_url, file_name):
        save_path = os.path.join(self.download_dir, f"{file_name}.pdf")
        if os.path.exists(save_path):
            print(f"    文件已存在，跳过。")
            return

        try:
            with self.session.get(file_url, stream=True) as r:
                r.raise_for_status()
                with open(save_path, 'wb') as f:
                    # 缓速逐步下载
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            print(f"    下载完成")
        except Exception as e:
            print(f"    下载失败: {e}")


if __name__ == "__main__":
    target_url = "https://www.rwth-aachen.de/cms/root/wir/Aktuell/~xhf/Amtliche-Bekanntmachungen/?aaaaaaaaaaaaaqg=aaaaaaaaaaaaxoo&aaaaaaaaaaaaaqo=aaaaaaaaaaaaxqg&page=1&showall=1#search-results"

    # limit默认为5，可以指定数量，128可以保证当前下载已有的全部内容够用
    downloader = RWTHDownloader(target_url)
    downloader.fetch_and_download(limit=128)