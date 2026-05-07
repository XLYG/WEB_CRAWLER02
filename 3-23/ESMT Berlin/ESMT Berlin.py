import os
import re
import json
import asyncio
import unicodedata
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "ESMT_Berlin",
    "START_URL": "https://esmt.berlin/degrees/master-programs",
    "BASE_DOMAIN": "https://esmt.berlin",
    "OUTPUT_DIR": "ESMT_Berlin_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # 详情页核心容器
    "DETAIL_CONTAINER": ".sidebar--wide",
    # 穿透页哨兵（语义匹配）
    "SENTINEL_START": "Admission requirements",
    "SENTINEL_END": "How do I apply?",
    # 穿透链接锚点 ID
    "JUMP_LINK_ID": "#tns2-item2"
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Major_Info"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 50 if is_folder else 40
        return name[:limit]

    @staticmethod
    async def nuke_overlays(page):
        await page.evaluate("""() => {
            const trash = ['#usercentrics-root', '.cookie-banner', '.modal-backdrop', '.sp-cookie-allow-all', '#sp-cookie-consent'];
            trash.forEach(s => document.querySelector(s)?.remove());
            document.body.style.setProperty('overflow', 'auto', 'important');
            document.documentElement.style.setProperty('overflow', 'auto', 'important');
        }""")

    @staticmethod
    async def purified_to_md(element_handle):
        raw_html = await element_handle.evaluate("""el => {
            const clone = el.cloneNode(true);
            const noise = clone.querySelectorAll('blockquote, img, picture, figure, video, script, style, svg, button, nav');
            noise.forEach(n => n.remove());
            clone.querySelectorAll('a').forEach(a => {
                let href = a.getAttribute('href');
                if(href && href.startsWith('/')) a.setAttribute('href', window.location.origin + href);
            });
            return clone.innerHTML;
        }""")
        content = md(raw_html, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class ESMTScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        os.makedirs(self.output_dir, exist_ok=True)

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, slow_mo=500)
            context = await browser.new_context(user_agent=CONFIG["USER_AGENT"])
            page = await context.new_page()

            self.semaphore = asyncio.Semaphore(1)

            print(f" 正在访问列表页: {CONFIG['START_URL']}")
            await page.goto(CONFIG["START_URL"], wait_until="networkidle")
            await asyncio.sleep(2)
            await CrawlerUtils.nuke_overlays(page)

            # 获取专业 (基于 tns3-item0/1/2)
            tasks = []
            for i in range(3):
                selector = f"#tns3-item{i}"
                item = page.locator(selector)
                if await item.count() > 0:
                    name = await item.locator(".highlights-slider-item__label").inner_text()
                    url = await item.locator("a.highlights-slider-item__link").get_attribute("href")
                    tasks.append({
                        "name": name.strip(),
                        "url": urljoin(CONFIG["BASE_DOMAIN"], url)
                    })

            print(f" 捕捉到 {len(tasks)} 个专业 开始顺序采集")

            for task in tasks:
                await self.process_major(context, task)

            print(f" 任务结束 ")
            await browser.close()

    async def process_major(self, context, task):
        async with self.semaphore:
            page = await context.new_page()
            try:
                print(f"\n[1/2] 正在处理详情: {task['name']}")
                await page.goto(task['url'], wait_until="networkidle", timeout=60000)
                await CrawlerUtils.nuke_overlays(page)

                # 详情页主体提取
                detail_md = ""
                main_container = await page.query_selector(CONFIG["DETAIL_CONTAINER"])
                if main_container:
                    detail_md = await CrawlerUtils.purified_to_md(main_container)

                # 穿透至 Admissions
                admission_md = ""
                jump_link = await page.query_selector(CONFIG["JUMP_LINK_ID"])
                if jump_link:
                    admission_url = urljoin(CONFIG["BASE_DOMAIN"], await jump_link.get_attribute("href"))
                    print(f"[2/2] 穿透中: {admission_url}")
                    admission_md = await self.fetch_admission_linear(context, admission_url)

                # 归档保存
                safe_folder = CrawlerUtils.sanitize_path(task['name'], True)
                major_path = os.path.join(self.output_dir, safe_folder)
                os.makedirs(major_path, exist_ok=True)

                md_file = os.path.join(major_path, f"{CrawlerUtils.sanitize_path(task['name'], False)}.md")
                with open(md_file, "w", encoding="utf-8") as f:
                    f.write(f"URL: {task['url']}\n\n")
                    f.write(f"# {task['name']}\n\n")
                    f.write(detail_md)
                    if admission_md:
                        f.write("\n\n---\n## Detailed Admission Requirements (Appendix)\n\n")
                        f.write(admission_md)

                print(f"    成功！已归档: {safe_folder}")

            except Exception as e:
                print(f"    错误： {task['name']} 失败: {e}")
            finally:
                await page.close()

    async def fetch_admission_linear(self, context, url):
        """用 Range 克隆 [startH2, endH2] 区间的 DOM，再在克隆结果上做一次降噪与链接补全"""
        page = await context.new_page()
        content = ""
        try:
            await page.goto(url, wait_until="networkidle")
            await CrawlerUtils.nuke_overlays(page)

            start_lit = json.dumps(CONFIG["SENTINEL_START"])
            end_lit = json.dumps(CONFIG["SENTINEL_END"])

            payload = await page.evaluate(f"""() => {{
                const main = document.querySelector('main');
                if (!main) return {{ html: "", startFound: false, endFound: false }};

                const startPhrase = {start_lit};
                const endPhrase = {end_lit};

                // 短语允许词间任意空白，忽略软连字符
                function phraseRe(phrase) {{
                    const clean = (phrase || '').replace(/\\u00ad/g, '').trim();
                    const parts = clean.split(/\\s+/).filter(Boolean)
                        .map(p => p.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'));
                    return new RegExp(parts.join('\\\\s+'), 'i');
                }}
                function normText(el) {{
                    return (el.textContent || '').replace(/\\u00ad/g, '').replace(/\\s+/g, ' ').trim();
                }}

                const startRe = phraseRe(startPhrase);
                const endRe = phraseRe(endPhrase);
                const h2s = Array.from(main.querySelectorAll('h2'));

                let si = -1, ei = -1;
                for (let i = 0; i < h2s.length; i++) {{
                    const t = normText(h2s[i]);
                    if (si < 0 && startRe.test(t)) si = i;
                }}
                if (si < 0) return {{ html: "", startFound: false, endFound: false }};

                for (let j = si + 1; j < h2s.length; j++) {{
                    if (endRe.test(normText(h2s[j]))) {{
                        ei = j;
                        break;
                    }}
                }}

                const startEl = h2s[si];
                const endEl = ei >= 0 ? h2s[ei] : null;

                const range = document.createRange();
                range.setStartBefore(startEl);
                if (endEl) range.setEndBefore(endEl);
                else range.setEndAfter(main);

                const doc = startEl.ownerDocument;
                const wdiv = doc.createElement('div');
                wdiv.appendChild(range.cloneContents());

                wdiv.querySelectorAll('blockquote, img, picture, figure, video, script, style, svg, button, nav').forEach(n => n.remove());
                wdiv.querySelectorAll('a').forEach(a => {{
                    let href = a.getAttribute('href');
                    if (href && href.startsWith('/')) a.setAttribute('href', window.location.origin + href);
                }});

                return {{ html: wdiv.innerHTML, startFound: true, endFound: endEl !== null }};
            }}""")

            html_fragment = (payload or {}).get("html") or ""
            if not (payload or {}).get("startFound"):
                print("    未在 main 内匹配到起始哨兵对应的 h2，Admission 附录为空")
            else:
                if not (payload or {}).get("endFound"):
                    print("    穿透问题： 未匹配到结束哨兵对应的 h2，已截取从起始 h2 至 main 末尾")
                content = md(html_fragment, heading_style="ATX")
                lines = [line.strip() for line in content.split("\n")]
                content = f"Source Appendix: {url}\n\n" + re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

        except Exception as e:
            print(f"    穿透错误 {e}")
        finally:
            await page.close()
            return content

if __name__ == "__main__":
    scraper = ESMTScraper()
    asyncio.run(scraper.run())