from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import time
import logging

from TUMDetailParser import TUMDetailParser
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TUMProgramLister:
    def __init__(self):
        # 采用无头浏览器，驱动采用在线驱动
        options = webdriver.ChromeOptions()
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')

        # 直接初始化，无需 Service 路径，Selenium Manager 会接管
        self.driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self.driver, 15)

        # 初始入口
        self.base_url = "https://www.tum.de/en/studies/degree-programs#graduation=2"
        self.program_links = set()

    def close_cookie_banner(self):
        """处理 Cookie 弹窗"""
        try:
            time.sleep(3)
            # 寻找 "Accept all" 或类似按钮
            # TUM 现在的弹窗结构比较复杂，尝试寻找 Shadow DOM 或者直接点击
            buttons = self.driver.find_elements(By.XPATH,
                                                "//button[contains(text(), 'Accept') or contains(text(), 'akzeptieren') or contains(text(), 'Alle')]")
            if buttons:
                for btn in buttons:
                    if btn.is_displayed():
                        btn.click()
                        logging.info("Cookie 弹窗已尝试关闭")
                        time.sleep(1)
                        return
        except Exception as e:
            logging.warning(f"Cookie 弹窗处理跳过: {e}")

    def apply_filter_master(self):
        logging.info("正在加载列表页...")
        self.driver.get(self.base_url)
        self.close_cookie_banner()


    def extract_links_from_current_page(self):
        """提取当前页所有链接"""
        # TUM 的链接通常在 h3.h5 > a 中，且 href 包含 'detail'
        links = self.driver.find_elements(By.XPATH, "//a[contains(@href, '/degree-programs/detail/')]")
        count = 0
        for link in links:
            try:
                href = link.get_attribute('href')
                if href and href not in self.program_links:
                    self.program_links.add(href)
                    count += 1
            except StaleElementReferenceException:
                continue
        logging.info(f"    当前页提取到 {count} 个新链接")

    def run_pagination_loop(self):
        """执行翻页循环"""
        self.apply_filter_master()

        page = 1
        while True:
            logging.info(f"正在扫描第 {page} 页...")
            self.extract_links_from_current_page()

            # 寻找下一页按钮
            # 通常是一个 li.next > a
            try:
                next_buttons = self.driver.find_elements(By.XPATH, "//li[contains(@class, 'next')]/a")

                # 如果没有按钮，或者按钮不可见，说明到了最后一页
                if not next_buttons or not next_buttons[0].is_displayed():
                    logging.info("没有下一页了，抓取结束。")
                    break

                # 点击下一页
                self.driver.execute_script("arguments[0].click();", next_buttons[0])
                logging.info("翻页中...")
                page += 1

                # 等待加载
                time.sleep(4)

            except Exception as e:
                logging.error(f"翻页出错: {e}")
                break

        logging.info(f"总共收集到 {len(self.program_links)} 个专业链接。")
        self.driver.quit()
        return list(self.program_links)

# --- 主程序执行 ---
if __name__ == "__main__":
    # 1. 使用 Selenium 获取所有 Master 链接
    lister = TUMProgramLister()
    all_urls = lister.run_pagination_loop()

    TEST_LIMIT = 116
    # 可指定获取数量
    target_urls = all_urls[:]

    print("\n" + "=" * 50)
    print(f"处理前 {len(target_urls)} 个专业 (共发现 {len(all_urls)} 个)")
    print("=" * 50 + "\n")

    # 2. 使用 Requests 批量下载详情
    detail_scraper = TUMDetailParser(output_dir="tum_master_degrees_test")

    # 注意：这里遍历的是 target_urls，不再是 all_urls
    for i, url in enumerate(target_urls):
        print(f"[{i + 1}/{len(target_urls)}] 处理: {url}")

        success = detail_scraper.fetch_page(url)
        if success:
            detail_scraper.run_standard_extraction()
            time.sleep(1)
        else:
            print(f"跳过: {url}")

    print(f"\n请检查 tum_master_degrees_test 文件夹内的前 {TEST_LIMIT} 个结果。")