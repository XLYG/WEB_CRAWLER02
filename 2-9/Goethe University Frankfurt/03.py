import os
import asyncio
import re
import random
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from markdownify import markdownify as md

START_URL = "https://www.uni-frankfurt.de/de/studium/studiengaenge?page=1&degree=master"
ROOT_DIR = "Uni_Frankfurt_Data"


def sanitize(name):
    if not name: return "Unknown_Major"
    name = re.sub(r'<[^>]+>', '', str(name))
    name = re.sub(r'\s+', ' ', name)
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return name[:120]


def clean_html_to_md(html_str):
    if not html_str: return ""
    html_str = re.sub(r'<(script|style|img).*?>.*?</\1>|<img.*?>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
    html_str = re.sub(r'</?font.*?>', '', html_str, flags=re.IGNORECASE)
    return md(html_str, heading_style="ATX").strip()


async def process_admission_requirements(page):
    """
    逐一点击展开并提取面板内容（处理懒加载和排他性展开）
    """
    combined_admission_md = ""

    # 仅锁定当前“可见”的折叠项容器
    items_locator = page.locator('div[data-testid="accordion-item"]').filter(visible=True)
    count = await items_locator.count()
    print(f"      发现 {count} 个可见折叠面板")

    for i in range(count):
        try:
            # 重新获取当前索引的项，防止 DOM 刷新
            item = items_locator.nth(i)
            button = item.locator('button[data-radix-collection-item]')

            # 提取标题
            headline = await button.locator('[data-testid="headline"]').inner_text()
            print(f"       正在点击并提取: {headline.strip()}")

            # 滚动并展开
            await button.scroll_into_view_if_needed()
            state = await button.get_attribute('data-state')
            if state == 'closed':
                await button.click()
                # 等待对应的 region 渲染并可见
                await item.locator('div[role="region"]').wait_for(state="visible", timeout=8000)
                await asyncio.sleep(0.8)

            # 及时提取当前的 HTML
            item_html = await item.inner_html()
            combined_admission_md += f"\n\n{clean_html_to_md(item_html)}\n"

        except Exception as e:
            print(f"       某个面板提取失败: {e}")

    return combined_admission_md


async def process_major_detail(context, url, folder, major_name):
    md_file_path = os.path.join(folder, f"{sanitize(major_name)}.md")
    if os.path.exists(md_file_path):
        print(f"     {major_name} 已存在")
        return

    page = await context.new_page()
    try:
        print(f"    正在加载: {url}")
        await page.goto(url, wait_until="networkidle", timeout=60000)

        # 识别侧边栏是否存在跳转链接
        admission_url = None
        sidebar_links = page.locator('//*[@id="main"]/div/div/div/div[3]/div[1]/div//nav//a').filter(visible=True)
        link_count = await sidebar_links.count()
        for i in range(link_count):
            link = sidebar_links.nth(i)
            href = await link.get_attribute('href')
            text = (await link.inner_text()).lower()
            if any(kw in text for kw in ["zugang", "bewerbung", "admission", "application"]):
                admission_url = urljoin("https://www.uni-frankfurt.de", href)
                break

        md_output = [f"# {major_name}\n"]

        # 提取概览：获取指定路径下的前三个子标签
        overview_container = page.locator('//*[@id="main"]/div/div/div/div[3]/div[2]/div/div').filter(
            visible=True).first
        if await overview_container.count() > 0:
            overview_html = await overview_container.evaluate("""(el) => {
                const tags = Array.from(el.children).slice(0, 3);
                return tags.map(t => t.outerHTML).join('');
            }""")
            if overview_html:
                md_output.append("## Overview\n")
                md_output.append(clean_html_to_md(overview_html))

        # 提取申请详情
        if admission_url:
            print(f"    正在解析申请页: {admission_url}")
            await page.goto(admission_url, wait_until="networkidle", timeout=60000)

            admission_md = await process_admission_requirements(page)
            if admission_md:
                md_output.append("\n---\n## Admission Requirements and Application\n")
                md_output.append(admission_md)

        # 统一整合写入
        if len(md_output) > 1:
            with open(md_file_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(md_output))
            print(f"    原生态内容已保存")

    except Exception as e:
        print(f"    处理 {major_name} 失败: {e}")
    finally:
        await page.close()


async def main():
    if not os.path.exists(ROOT_DIR): os.makedirs(ROOT_DIR)

    async with async_playwright() as p:
        # headless=False 方便观察
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1440, 'height': 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )

        page = await context.new_page()
        print(f"正在访问列表页...")
        await page.goto(START_URL, wait_until="networkidle")

        total_processed = 0
        current_page_num = 1
        all_processed_urls = set()

        while True:
            print(f"\n===== 正在处理第 {current_page_num} 页 =====")
            # 等待专业卡片渲染（排除加载占位符）
            await page.wait_for_selector('article:not(.animate-pulse)', timeout=30000)

            # 定位可见卡片
            cards_locator = page.locator('//*[@id="main"]/div/div/div/div[3]/div[2]//article').filter(visible=True)
            count = await cards_locator.count()

            visible_majors = []
            for i in range(count):
                card = cards_locator.nth(i)
                try:
                    name = await card.locator('h4').inner_text()
                    link_el = card.locator('a[class*="bg-blue-500"]')
                    href = await link_el.get_attribute('href')
                    if name and href:
                        full_url = urljoin("https://www.uni-frankfurt.de", href)
                        if full_url not in all_processed_urls:
                            visible_majors.append((name.strip(), full_url))
                            all_processed_urls.add(full_url)
                except:
                    continue

            print(f"  本页识别到 {len(visible_majors)} 个可见专业")

            for m_name, m_url in visible_majors:
                total_count = len(all_processed_urls)
                major_folder = os.path.join(ROOT_DIR, sanitize(m_name))
                os.makedirs(major_folder, exist_ok=True)

                print(f"\n>>> [{total_processed + 1}/{total_count}] 正在抓取专业: {m_name}")
                await process_major_detail(context, m_url, major_folder, m_name)
                total_processed += 1

            # 基于指示器对比的稳健跳转，不然会出现重复第一页或者到最后一页
            print(f"\n  寻找下一页按钮...")

            pagination_nav = page.locator('nav[aria-label="pagination"]')
            indicator = pagination_nav.locator('button[aria-current="page"]')

            if await indicator.count() > 0:
                old_page_text = await indicator.inner_text()
                # 寻找包含“forward”标题的按钮
                next_btn = page.get_by_role("button", name="Pagination forward icon button")

                if await next_btn.is_visible() and await next_btn.is_enabled():
                    print(f"  已点击下一页，等待数字从 {old_page_text} 变化...")
                    await next_btn.click(force=True)

                    try:
                        # 校验监控指示器数字跳变
                        await page.wait_for_function(
                            f"old => {{ const el = document.querySelector('nav[aria-label=\"pagination\"] button[aria-current=\"page\"]'); return el && el.innerText !== '{old_page_text}'; }}",
                            timeout=15000
                        )
                        await page.wait_for_load_state("networkidle")
                        current_page_num += 1
                        await asyncio.sleep(2)
                    except:
                        print("  页码指示器未在规定时间内跳变。")
                        break
                else:
                    print("  “下一页”按钮不可用，任务完成。")
                    break
            else:
                print("   未找到分页导航。")
                break

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())