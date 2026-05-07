import os
import re
import asyncio
import random
import requests
import unicodedata
from urllib.parse import urljoin
from lxml import etree
from playwright.async_api import async_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "WBH_Wilhelm_Buechner",
    "ROOT_DOMAIN": "https://www.wb-fernstudium.de",
    "LIST_URL": "https://www.wb-fernstudium.de/master/ueberblick.html",
    "OUTPUT_DIR": "WBH_Final_Data",
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "CONCURRENCY": 2,
    "AUTH_WAIT_TIME": 60,
    "PAGE_TIMEOUT": 60000,
    # PDF 下载更稳健：重试 + 超时
    "PDF_RETRY": 3,
    "PDF_TIMEOUT": 30000,
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
    def purified_to_md(html_content):
        if not html_content: return ""
        noise = [
            r'<script.*?>.*?</script>', r'<style.*?>.*?</style>',
            r'<nav.*?>.*?</nav>', r'<button.*?>.*?</button>',
            r'<svg.*?>.*?</svg>', r'<video.*?>.*?</video>',
            r'<blockquote.*?>.*?</blockquote>', r'<img.*?>'
        ]
        for p in noise:
            html_content = re.sub(p, '', html_content, flags=re.DOTALL | re.IGNORECASE)
        content = md(html_content, heading_style="ATX")
        lines = [line.strip() for line in content.split('\n')]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class WBHScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        os.makedirs(self.output_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CONFIG["USER_AGENT"]})
        self.global_footer_md = ""

    def fetch_list(self):
        print(f" 正在拉取专业网址列表")
        try:
            res = self.session.get(CONFIG["LIST_URL"], timeout=30)
            tree = etree.HTML(res.text)
            rows = tree.xpath('//table[@id="coursesList"]/tbody/tr')
            tasks = []
            for row in rows:
                link_node = row.xpath('.//a[contains(@class, "text-master")]')
                if not link_node: continue
                name = "".join(link_node[0].xpath('.//span[1]/text()')).strip()
                url = urljoin(CONFIG["ROOT_DOMAIN"], link_node[0].get('href'))
                badges = row.xpath('.//span[contains(@class, "badge")]/text()')
                meta = " | ".join([b.strip() for b in badges if b.strip()])
                tasks.append({"name": name, "url": url, "meta": meta})
            return tasks
        except Exception as e:
            print(f" 列表获取失败: {e}");
            return []

    async def run(self):
        tasks = self.fetch_list()
        if not tasks: return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(user_agent=CONFIG["USER_AGENT"])


            # 在首个专业填表鉴权
            first_task = tasks[0]
            main_page = await context.new_page()
            print(f" 首个专业进行鉴权写入: {first_task['name']}")
            await main_page.goto(first_task['url'], wait_until="domcontentloaded", timeout=CONFIG["PAGE_TIMEOUT"])
            await self.bypass_lead_gate(main_page)

            print(f" 鉴权已提交 进入 {CONFIG['AUTH_WAIT_TIME']} 秒等待期同步全局状态")
            await asyncio.sleep(CONFIG["AUTH_WAIT_TIME"])

            await self.process_major_logic(main_page, first_task)
            await main_page.close()

            # 并行采集
            if len(tasks) > 1:
                semaphore = asyncio.Semaphore(CONFIG["CONCURRENCY"])

                async def semaphore_wrapper(t):
                    async with semaphore:
                        p = await context.new_page()
                        await self.process_major_logic(p, t)
                        await p.close()

                # 防止单个专业异常导致其它任务被取消
                await asyncio.gather(*[semaphore_wrapper(task) for task in tasks[1:]], return_exceptions=True)
            await browser.close()

    async def bypass_lead_gate(self, page):
        """点击下拉框，确保通过表单校验"""
        try:
            await page.click('#selectSalutationInputformkndownajax')
            await asyncio.sleep(1)
            await page.click('li[data-value="Herr"]')
            #  参数填入
            await page.fill('#inputVorname', 'Max')
            await page.fill('#inputNachname', 'Mustermann')
            await page.fill('#route', 'Musterstrasse')
            await page.fill('#street_number', '1')
            await page.fill('#postal_code', '10115')
            await page.fill('#locality', 'Berlin')
            await page.fill('#email', f'wbh_user_{random.randint(1000, 9999)}@gmail.com')
            await page.fill('#telefonAnschlussPrivat', '017612345678')

            #  勾选选项并提交
            await page.check('#resultModalSelectDigital')
            await page.locator('label.newsletter').click()
            # 这个一定要等
            await asyncio.sleep(2)

            print("        正在执行静默提交...")
            # 这里就算是使用playwright的也不好使，只能说很奇妙
            await page.evaluate(
                '() => { const btn = document.querySelector("#formKnDownAjaxButton"); if(btn) btn.click(); }')

            print("      成功 提交指令已发出，请观察浏览器上传进度 ")
        except Exception as e:
            print(f"      失败 表单提交受阻: {e}")

    async def process_major_logic(self, page, task):
        try:
            print(f"    正在解析 {task['name']}")
            await page.goto(task['url'], wait_until="domcontentloaded", timeout=CONFIG["PAGE_TIMEOUT"])
            # 等待锚点
            await page.wait_for_selector('#uebersicht', state="attached", timeout=30000)

            safe_major_name = CrawlerUtils.sanitize_path(task['name'], True)
            major_dir = os.path.join(self.output_dir, safe_major_name)
            os.makedirs(major_dir, exist_ok=True)

            md_content = [f"URL: {task['url']}", f"**{task['name']} | {task['meta']}**\n", "---"]

            #  Inhalte 提取
            inhalte_html = await page.evaluate('''() => {
                const el = document.querySelector('#inhalte');
                return el ? (el.closest('div.container-md') || el.parentElement).innerHTML : "";
            }''')
            if inhalte_html:
                md_content.append("## 学习内容 (Inhalte)\n" + CrawlerUtils.purified_to_md(inhalte_html))
            # 课程解释内容
            radios = await page.locator('.course-tabs-variants input.form-check-input').all()
            if radios:
                md_content.append("## 课程版本详情 (Course Variants)")
                for radio in radios:
                    v_label = await page.evaluate('el => el.nextElementSibling.innerText', await radio.element_handle())
                    print(f"        切换并提取: {v_label.strip()}")
                    await radio.click()
                    await asyncio.sleep(3)

                    v_html = await page.evaluate('''() => {
                        const el = document.querySelector('#uebersicht');
                        const container = el ? (el.tagName === 'SECTION' ? el : el.closest('div.container-md')) : null;
                        return container ? container.innerHTML : "";
                    }''')
                    if v_html:
                        md_content.append(f"### 版本: {v_label.strip()}\n{CrawlerUtils.purified_to_md(v_html)}")
            else:
                kf_html = await page.evaluate('''() => {
                    const el = document.querySelector('#uebersicht');
                    const container = el ? (el.tagName === 'SECTION' ? el : el.closest('div.container-md')) : null;
                    return container ? container.innerHTML : "";
                }''')
                if kf_html:
                    md_content.append("## 专业关键数据 (Keyfacts)\n" + CrawlerUtils.purified_to_md(kf_html))

            #  PDF
            await self.audit_pdfs_strictly(page, major_dir)

            # 给页面完成二次渲染一点缓冲，不然有些专业没有多学期会出现下载失败
            await asyncio.sleep(1.5)

            #  保存文件
            final_md = "\n\n".join(md_content) + self.global_footer_md
            with open(os.path.join(major_dir, f"{safe_major_name}.md"), "w", encoding="utf-8") as f:
                f.write(final_md)
            print(f"    成功 数据已保存至文件夹 ")
        except Exception as e:
            print(f"    失败 {task['name']} 异常: {e}")

    async def audit_pdfs_strictly(self, page, major_dir):
        """仅在Regulation 找 priceArea，Plan 找 curriculum"""

        # 不同专业的下载按钮挂载时机可能不同，先给一点等待
        try:
            await page.wait_for_selector('#priceArea', state="attached", timeout=15000)
        except:
            pass
        try:
            await page.wait_for_selector('#curriculum', state="attached", timeout=15000)
        except:
            pass

        #  寻找 Regulation (Prüfungsordnung) - 仅限 priceArea
        reg_url = None
        try:
            reg_url = await page.evaluate('''() => {
                const area = document.querySelector('#priceArea');
                const root = area || document.body;
                if(!root) return null;

                const norm = s => (s || '').replace(/\\u00ad/g, '').replace(/\\s+/g, ' ').trim();
                const hasPruef = (s) => /pr[uü]fungsordnung/i.test(norm(s));

                const anchors = Array.from(root.querySelectorAll('a.btn-primary[href]'));

                // 强约束：只接受明确是 PDF 的链接，避免返回 Publitas/online-katalog viewer 页面
                const looksLikePdf = (href) => {
                    const h = norm(href);
                    return h && (/\\.pdf(\\?|#|$)/i.test(h) || h.startsWith('/fileadmin/pdf/'));
                };

                // 1) 优先：在包含“Prüfungsordnung”文本的卡片中找 btn-primary（下载按钮）
                const divs = Array.from(root.querySelectorAll('div'));
                const labelDiv = divs.find(d => hasPruef(d.textContent));
                if (labelDiv) {
                    const card = labelDiv.closest('div') || labelDiv.parentElement;
                    if (card) {
                        const btn = card.querySelector('a.btn-primary[href]');
                        if (btn) {
                            const href = btn.getAttribute('href');
                            if (looksLikePdf(href)) return btn.href || href;
                        }
                    }
                }

                // 2) 备选：直接从 btn-primary 列表里找 href 含 pruefungsordnung / pdf
                for (const a of anchors) {
                    const href = a.getAttribute('href');
                    if (!looksLikePdf(href)) continue;
                    if (hasPruef(href) || hasPruef(a.textContent)) {
                        return a.href || href;
                    }
                }

                // 3) 兜底：只要 btn-primary 存在任意 PDF，就取第一个（保证下载落在 pdf 文件）
                const pdfBtn = anchors.find(a => looksLikePdf(a.getAttribute('href')));
                return pdfBtn ? (pdfBtn.href || pdfBtn.getAttribute('href')) : null;
            }''')
        except Exception as e:
            print(f"       PDF定位异常， Regulation 定位失败: {e}")

        if reg_url:
            await self.download_binary_file(page, reg_url, major_dir, "Regulation.pdf")


    async def download_binary_file(self, page, url, folder, filename):
        """二进制下载，带 PDF 指纹检查"""
        full_url = urljoin(CONFIG["ROOT_DOMAIN"], url)
        last_err = None
        for attempt in range(1, CONFIG["PDF_RETRY"] + 1):
            try:
                response = await page.context.request.get(full_url, timeout=CONFIG["PDF_TIMEOUT"])
                if response.status != 200:
                    last_err = f"status={response.status}"
                    print(f"       PDF下载失败! {filename} attempt={attempt}/{CONFIG['PDF_RETRY']} {last_err} url={full_url}")
                    continue

                body = await response.body()
                if body.startswith(b'%PDF'):
                    out_path = os.path.join(folder, filename)
                    with open(out_path, "wb") as f:
                        f.write(body)
                    print(f"       PDF捕捉成功 {filename} ({len(body)} bytes)")
                    return

                head = body[:80]
                last_err = "not_pdf_fingerprint"
                print(f"       PDF跳过 {filename} attempt={attempt}/{CONFIG['PDF_RETRY']} 不是 PDF 指纹 head={head!r}")
            except Exception as e:
                last_err = str(e)
                print(f"       PDF下载异常 {filename} attempt={attempt}/{CONFIG['PDF_RETRY']}: {e}")

        print(f"       PDF最终失败 {filename} last_err={last_err} url={full_url}")


if __name__ == "__main__":
    scraper = WBHScraper()
    asyncio.run(scraper.run())