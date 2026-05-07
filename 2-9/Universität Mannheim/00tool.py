import os
import re
import time
import requests
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

CONFIG = {
    "目标文件夹路径": "University_of_Mannheim_Data",
    "准入条例链接": "https://www.uni-mannheim.de/studium/vor-dem-studium/bewerbung/bewerbung-a-bis-z/auswahlsatzungen/#c17206",
    "用户头": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "等待时间": 2,
}

# 干扰词库：在比对时忽略这些词，只对比核心学科名称
STOP_WORDS = {
    'master', 'mannheim', 'studiengang', 'englischsprachiger', 'mit', 'und',
    'schwerpunkt', 'of', 'in', 'science', 'arts', 'education', 'track', 'part-time'
}

# 手动补丁，处理名称完全不相关的特殊情况
MANUAL_PATCH = {
    "igs": "intercultural_german_studies",
    "mkw": "medien_und_kommunikationswissenschaft",
    "igs": "intercultural_german_studies",
    "mmds": "data_science",
    "mmsds": "social_data_science",
    "mmm": "management",
    "vwl": "volkswirtschaftslehre",
    "wifo": "wirtschaftsinformatik",
    "wima": "wirtschaftsmathematik",
    "wipaed": "wirtschaftspaedagogik",
    "limeku": "literatur_medien_und_kultur",
    "igs": "intercultural_german_studies"
}


class Utils:
    @staticmethod
    def sanitize_filename(name):
        if not name: return "Document"
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        return re.sub(r'\s+', "_", name).strip("._")

    @staticmethod
    def get_core_tokens(text):
        text = text.lower().replace('_', ' ').replace('-', ' ').replace('/', ' ')
        text = re.sub(r'\(.*?\)', '', text)
        # 提取单词并过滤干扰词
        tokens = {word for word in re.findall(r'[a-z]{2,}', text) if word not in STOP_WORDS}
        return tokens

    @staticmethod
    def download_pdf(url, save_path):
        try:
            headers = {"User-Agent": CONFIG["用户代理"]}
            res = requests.get(url, headers=headers, stream=True, allow_redirects=True, timeout=30)
            if res.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in res.iter_content(8192):
                        f.write(chunk)
                return True
        except Exception as e:
            print(f"      下载失败: {e}")
        return False


class MannheimRegulationDistributor:
    def __init__(self):
        self.base_dir = CONFIG["目标文件夹路径"]
        if not os.path.exists(self.base_dir):
            print(f"找不到路径 '{self.base_dir}'")
            exit()

        # 建立本地索引
        self.folder_index = {}
        for f in os.listdir(self.base_dir):
            if os.path.isdir(os.path.join(self.base_dir, f)):
                self.folder_index[f] = Utils.get_core_tokens(f)

        print(f"已扫描本地路径，建立索引完毕。")

    def run(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(user_agent=CONFIG["用户代理"], locale="de-DE")
            page = context.new_page()

            print(f"访问条例页面: {CONFIG['准入条例链接']}")
            page.goto(CONFIG["准入条例链接"])
            time.sleep(CONFIG["等待时间"])

            # 展开硕士面板
            page.evaluate("document.querySelector(\"a[href='#c17206']\").click()")
            time.sleep(CONFIG["等待时间"])

            links = page.locator("#c17206 a[href$='.pdf']").all()
            print(f"发现 {len(links)} 个条例文件，启动深度比对算法...")

            unmatched = []
            matched_count = 0

            for link in links:
                raw_text = link.inner_text().strip()
                pdf_url = urljoin(CONFIG["准入条例链接"], link.get_attribute("href"))

                # 匹配文件夹
                target_folder = self.find_best_match(raw_text)

                if target_folder:
                    dest_dir = os.path.join(self.base_dir, target_folder)
                    clean_pdf_name = Utils.sanitize_filename(raw_text.split('(')[0])
                    save_path = os.path.join(dest_dir, f"Admission_Reg_{clean_pdf_name}.pdf")

                    print(f"    匹配成功: [{raw_text[:30]}...] -> {target_folder}")
                    if not os.path.exists(save_path):
                        if Utils.download_pdf(pdf_url, save_path):
                            print(f"      下载成功")
                        matched_count += 1
                    else:
                        print(f"       文件已存在")
                        matched_count += 1
                else:
                    unmatched.append(raw_text)

            self.report(len(links), matched_count, unmatched)
            browser.close()

    def find_best_match(self, raw_text):
        """深度匹配算法"""
        # 手动补丁匹配
        text_lower = raw_text.lower()
        for patch_key, target_key in MANUAL_PATCH.items():
            if patch_key in text_lower:
                for f_name, f_tokens in self.folder_index.items():
                    if target_key in f_name.lower():
                        return f_name

        # 词交集匹配
        pdf_tokens = Utils.get_core_tokens(raw_text)
        if not pdf_tokens: return None

        best_folder = None
        max_overlap = 0

        for f_name, f_tokens in self.folder_index.items():
            # 计算两个集合的交集
            intersection = pdf_tokens.intersection(f_tokens)
            overlap_count = len(intersection)

            # 如果交集完全覆盖了PDF的核心词，或者交集很大
            if overlap_count > max_overlap:
                max_overlap = overlap_count
                best_folder = f_name

        # 设定阈值：核心词至少要重合1个（如果是复合词则需更多）
        return best_folder if max_overlap >= 1 else None

    def report(self, total, matched, unmatched_list):
        print("\n" + "=" * 40)
        print(f"匹配报告:")
        print(f"  - 总数: {total} | 成功: {matched} | 失败: {len(unmatched_list)}")
        if unmatched_list:
            print("\n  [失败列表]:")
            for item in unmatched_list: print(f"    - {item}")
        print("=" * 40)


if __name__ == "__main__":
    MannheimRegulationDistributor().run()