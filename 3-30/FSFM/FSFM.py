import os
import re
import asyncio
import unicodedata
from playwright.async_api import async_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "Frankfurt_School",
    "ROOT_DOMAIN": "https://www.frankfurt-school.de",
    "LIST_URL": "https://www.frankfurt-school.de/de/study/master",
    "OUTPUT_DIR": "Frankfurt_School_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "CONCURRENCY": 2,
    "TARGET_IDS": ["keyfacts", "focus-and-format", "highlights", "requirements", "faq"],
    "KEYFACTS_FALLBACK": ".textFacts_root__OJVAE",
    "PAGE_TIMEOUT": 60000
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
    async def nuke_cookie_overlays(page):
        """清理 Cookie 和通用遮罩"""
        print("    正在物理清理 Cookie 遮罩")
        await page.evaluate("""() => {
            const trash = [
                '#cookiescript_injected_wrapper', 
                '#cookiescript_injected', 
                '.modal-backdrop',
                '[id*="cookiescript"]',
                '#cookiescript_badge'
            ];
            trash.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));
            document.body.style.setProperty('overflow', 'auto', 'important');
            document.documentElement.style.setProperty('overflow', 'auto', 'important');
        }""")

    @staticmethod
    async def purified_to_md(element_handle):
        raw_html = await element_handle.evaluate("""el => {
            const clone = el.cloneNode(true);
            const noise = clone.querySelectorAll('blockquote, img, picture, figure, video, script, style, svg, button, nav');
            noise.forEach(n => n.remove());
            return clone.innerHTML;
        }""")
        content = md(raw_html, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class FrankfurtScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        os.makedirs(self.output_dir, exist_ok=True)
        self.processed_names = set()

    async def run(self):
        async with async_playwright() as p:
            # 开全屏模式
            browser = await p.chromium.launch(headless=False, args=['--start-maximized'])
            context = await browser.new_context(no_viewport=True, user_agent=CONFIG["USER_AGENT"])

            init_page = await context.new_page()
            await init_page.goto(CONFIG["LIST_URL"], wait_until="domcontentloaded")
            await asyncio.sleep(4)
            await CrawlerUtils.nuke_cookie_overlays(init_page)

            print(" 正在执行列表滑动")
            await init_page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
            await asyncio.sleep(4)

            card_selector = 'div[class*="programmeCard_root"]'
            total_cards = await init_page.locator(card_selector).count()
            print(f" 捕捉到 {total_cards} 个专业卡")
            await init_page.close()

            # 分配并行任务
            indices = list(range(total_cards))
            mid = (len(indices) + 1) // 2
            await asyncio.gather(
                self.worker(context, indices[:mid]),
                self.worker(context, indices[mid:])
            )

            await browser.close()

    async def worker(self, context, indices):
        page = await context.new_page()
        for idx in indices:
            try:
                await page.goto(CONFIG["LIST_URL"], wait_until="domcontentloaded")
                await asyncio.sleep(4)
                await CrawlerUtils.nuke_cookie_overlays(page)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
                await asyncio.sleep(4)

                target_card = page.locator('div[class*="programmeCard_root"]').nth(idx)
                if await target_card.count() == 0: continue

                name = await target_card.locator('h5').inner_text()
                name = name.strip().split('\n')[0]

                if name in self.processed_names: continue

                print(f"    点击进入 {name}")
                await target_card.click()
                await page.wait_for_load_state("domcontentloaded")

                # 执行详情页逻辑
                await self.process_detail(page, name)
                self.processed_names.add(name)

            except Exception as e:
                print(f"    错误， 索引 {idx} 失败: {e}")
        await page.close()

    async def process_detail(self, page, major_name):
        try:
            await asyncio.sleep(4)

            # 可用的定位类
            modal_selector = '.languageAvailability_modal__PI4fB'
            redirect_btn = page.locator(
                f'button:has-text("Zur Englisch-Seite gehen"), a:has-text("Zur Englisch-Seite gehen"), a:has-text("Englisch")')

            if await redirect_btn.count() > 0:
                print(f"      发现语言跳转需求，正在点击跳转英文版")
                # 显式点击跳转
                await redirect_btn.first.click()
                # 等待导航完成
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(4)
            else:
                print(f"      无语言跳转需求，直接处理")

            # 尝试清理可能存在的屏蔽罩
            await CrawlerUtils.nuke_cookie_overlays(page)

            # 提取指定 ID 内容
            safe_major_name = CrawlerUtils.sanitize_path(major_name, True)
            major_dir = os.path.join(self.output_dir, safe_major_name)
            os.makedirs(major_dir, exist_ok=True)

            final_content = []
            for tid in CONFIG["TARGET_IDS"]:
                selector = f"#{tid}"
                try:
                    current_sel = f"{selector}, {CONFIG['KEYFACTS_FALLBACK']}" if tid == "keyfacts" else selector
                    # 守候组件挂载
                    handle = await page.wait_for_selector(current_sel, state='attached', timeout=8000)
                    if handle:
                        md_piece = await CrawlerUtils.purified_to_md(handle)
                        if md_piece:
                            final_content.append(f"## {tid.upper()}\n\n{md_piece}")
                except:
                    continue

            md_name = f"{CrawlerUtils.sanitize_path(major_name, False)}.md"
            with open(os.path.join(major_dir, md_name), "w", encoding="utf-8") as f:
                f.write(f"URL: {page.url}\n\n# {major_name}\n\n")
                f.write("\n\n".join(final_content))

            print(f"     数据已保存: {major_name}")

        except Exception as e:
            print(f"      {major_name} 详情提取异常: {e}")


if __name__ == "__main__":
    scraper = FrankfurtScraper()
    asyncio.run(scraper.run())