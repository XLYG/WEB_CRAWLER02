import asyncio
import os
from playwright.async_api import async_playwright
from urllib.parse import urljoin


class CologneWatermarkScraper:
    def __init__(self):
        self.start_url = "https://studieninteressierte.uni-koeln.de/studienangebot/index_ger.html?app=true&master=on&discipline=gesellschafts-und-sozialwissenschaften&discipline=kunst-musik&discipline=lehramt&discipline=mathematik-naturwissenschaften&discipline=medizin-gesundheitswissenschaften&discipline=rechtswissenschaften&discipline=sprach-und-kulturwissenschaften&discipline=wirtschaftswissenschaften"
        self.base_url = "https://studieninteressierte.uni-koeln.de"
        self.output_file = "cologne_majors_precise.txt"
        self.all_results = []

    async def scrape(self):
        async with async_playwright() as p:
            # 开启窗口模式方便观察
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = await context.new_page()

            print(f"正在初始化列表页...")
            await page.goto(self.start_url, wait_until="networkidle", timeout=60000)

            # 等待筛选结果文本出现 (例如 "205 从 370")，这标志着初始过滤完成
            await page.wait_for_selector("span[data-study-course-search-results]", timeout=20000)

            # 获取网页声称的总数
            total_expected_text = await page.locator("span[data-study-course-search-results]").first.inner_text()
            print(f"网页当前筛选出的硕士专业总数为: {total_expected_text}")

            page_num = 1
            while True:
                # 结构化锁定：抓取当前页可见卡片
                # 等待：确保该页至少有一个卡片是非隐藏状态
                card_selector = "div.c-cards--row article.c-card:not(.u-hide)"
                await page.wait_for_selector(card_selector, timeout=10000)

                cards = await page.locator(card_selector).all()
                page_data = []
                for card in cards:
                    # 仅锁定 h3 下的 a 链接
                    link_node = card.locator("h3.c-card__title a.c-card__link")
                    name = await link_node.inner_text()
                    href = await link_node.get_attribute("href")
                    if href:
                        page_data.append(f"{name.strip()}||{urljoin(self.base_url, href)}")

                self.all_results.extend(page_data)
                print(f"[第 {page_num} 页] 抓取到 {len(page_data)} 个专业。累计: {len(self.all_results)}")

                # 翻页判定逻辑
                # 获取当前分页组件的状态属性
                pagination = page.locator("study-course-pagination")
                current_val = await pagination.get_attribute("current-page")
                total_val = await pagination.get_attribute("total-pages")

                if current_val == total_val:
                    print("已到达分页组件指示的最后一页。")
                    break

                # 锁定“下一页”按钮 (根据 ID)
                next_btn = page.locator("#studycourse-pagination-beside-right button")

                if await next_btn.is_visible() and await next_btn.is_enabled():
                    # 记录点击前的页码
                    old_page = current_val
                    await next_btn.click()

                    # 关等待 current-page 属性发生物理改变
                    try:
                        await asyncio.wait_for(
                            self.wait_for_page_change(pagination, old_page),
                            timeout=10.0
                        )
                    except asyncio.TimeoutError:
                        print("翻页后页码未更新，可能已无更多数据。")
                        break

                    # 额外缓冲，等待 React 重新渲染卡片
                    await asyncio.sleep(1.5)
                    page_num += 1
                else:
                    print("找不到可点击的下一页按钮。")
                    break

            # 最终去重保存
            final_unique = list(set(self.all_results))
            with open(self.output_file, "w", encoding="utf-8") as f:
                for entry in sorted(final_unique):
                    f.write(entry + "\n")

            print(f"\n成功！共获取 {len(final_unique)} 个硕士专业链接")
            print(f"目标数据已对齐: {len(final_unique)} / {total_expected_text}")
            await browser.close()

    async def wait_for_page_change(self, pagination_locator, old_page_val):
        """循环检查属性值直到它改变"""
        while True:
            new_val = await pagination_locator.get_attribute("current-page")
            if new_val != old_page_val:
                return True
            await asyncio.sleep(0.2)


if __name__ == "__main__":
    scraper = CologneWatermarkScraper()
    asyncio.run(scraper.scrape())