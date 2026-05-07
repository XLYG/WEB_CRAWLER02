import os
import re
import time
import random
import base64
import unicodedata
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from markdownify import markdownify as md

CONFIG = {
    "UNIVERSITY_NAME": "FH_Aachen",
    "ROOT_DOMAIN": "https://www.fh-aachen.de",
    "TARGET_DOMAIN": "fh-aachen.de",
    "OUTPUT_DIR": "FH_Aachen_Data",
    "CDP_URL": "http://localhost:9222",  # 远程调试端口
    "WAIT_TIME": 2.5,
    "RESUME_MODE": True
}


class CrawlerUtils:
    @staticmethod
    def sanitize_path(name, is_folder=True):
        if not name: return "Unknown_Major"
        repls = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
        for k, v in repls.items(): name = name.replace(k, v)
        name = re.sub(r'[\\/*?:"<>|]', " ", name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        name = re.sub(r'\s+', "_", name).strip("._")
        limit = 60 if is_folder else 40
        return name[:limit]

    @staticmethod
    def purified_to_md(page, element_handle):
        try:
            raw_html = element_handle.evaluate("""el => {
                const clone = el.cloneNode(true);
                // 移除噪音：图片、脚本、侧边栏等
                const trash = clone.querySelectorAll('img, picture, figure, video, script, style, svg, button, noscript, nav, .offcanvas, .headerDesktop, .fh-breadcrumb, .footer-content');
                trash.forEach(n => n.remove());

                const links = clone.querySelectorAll('a');
                links.forEach(a => {
                    let href = a.getAttribute('href');
                    if(href && typeof href === 'string' && href.startsWith('/')) {
                        a.setAttribute('href', 'https://www.fh-aachen.de' + href);
                    }
                });
                return clone.innerHTML;
            }""")
            content = md(raw_html, heading_style="ATX")
            lines = [line.strip() for line in content.split('\n')]
            return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
        except:
            return ""


class AachenScraper:
    def __init__(self):
        self.output_dir = CONFIG["OUTPUT_DIR"]
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

    def run(self):
        with sync_playwright() as p:
            print(f"正在尝试连接至已打开的 Chrome (端口 9222)")
            try:
                browser = p.chromium.connect_over_cdp(CONFIG["CDP_URL"])
                context = browser.contexts[0]

                target_page = None
                for p_item in context.pages:
                    if CONFIG["TARGET_DOMAIN"] in p_item.url:
                        target_page = p_item
                        break

                if not target_page:
                    print(f"找不到目标标签页,请在浏览器中打开：{CONFIG['TARGET_DOMAIN']}")
                    return

                target_page.bring_to_front()
                print(f"成功找到：{target_page.title()}")

                # 列表解析
                major_rows = target_page.locator(
                    'table tr:has(a[href*="/studies/degree-programmes/"]), table tr:has(a[href*="/studiengaenge/"])').all()

                tasks = []
                for row in major_rows:
                    try:
                        tds = row.locator('td').all()
                        if len(tds) < 2: continue
                        title_link = tds[0].locator('a').first
                        name = title_link.evaluate("el => el.innerText").strip()
                        url = urljoin(CONFIG["ROOT_DOMAIN"], title_link.get_attribute("href"))
                        tasks.append({
                            "name": name, "url": url,
                            "degree": tds[1].inner_text().strip(),
                            "type": tds[2].inner_text().strip() if len(tds) > 2 else "Full-time",
                            "location": tds[3].inner_text().strip() if len(tds) > 3 else "Aachen"
                        })
                    except:
                        continue

                print(f" 成功：共找到 {len(tasks)} 个专业。开始执行下载逻辑")

                # 遍历采集
                for idx, task in enumerate(tasks):
                    url_slug = task["url"].strip("/").split("/")[-1]
                    safe_folder = f"{CrawlerUtils.sanitize_path(task['name'], True)}_{url_slug}"
                    safe_file_name = f"{CrawlerUtils.sanitize_path(task['name'], False)}.md"
                    check_path = os.path.join(self.output_dir, safe_folder, safe_file_name)

                    if CONFIG["RESUME_MODE"] and os.path.exists(check_path):
                        print(f"    跳过 {task['name']}")
                        continue

                    print(f"\n[{idx + 1}/{len(tasks)}] 正在解析：{task['name']}")
                    self.process_major(target_page, task, safe_folder, safe_file_name)
                    time.sleep(random.uniform(2, 4))

                print("\n 全部任务完成")
            except Exception as e:
                print(f" 发生故障：{e}")

    def process_major(self, page, task, folder_name, file_name):
        """执行专业页与申请子页的合并解析，含特定 PDF 匹配"""
        try:
            # 进入专业主详情页
            page.goto(task["url"], wait_until="domcontentloaded")
            time.sleep(CONFIG["WAIT_TIME"])

            # 清理弹窗
            page.evaluate(
                "() => { const b=document.querySelector('#usercentrics-root') || document.querySelector('.in2-modal'); if(b) b.remove(); }")

            major_dir = os.path.join(self.output_dir, folder_name)
            if not os.path.exists(major_dir): os.makedirs(major_dir)

            md_content = f"URL: {task['url']}\n\n"
            md_content += f"**{task['name']} | {task['degree']} | {task['type']} | {task['location']}**\n\n---\n\n"

            # 提取主详情内容切片
            print("    提取主页详情文字")
            html_slice = page.evaluate("""() => {
                const main = document.querySelector('#fh_main');
                if (!main) return "";
                let combined = "";
                for (let child of main.children) {
                    combined += child.outerHTML;
                    if (child.getAttribute('fhscrolltarget') === '0' || child.querySelector('[fhscrolltarget="0"]')) break;
                }
                return combined;
            }""")
            if html_slice:
                handle = page.evaluate_handle(
                    f"() => {{ const d=document.createElement('div'); d.innerHTML=`{html_slice}`; return d; }}")
                md_content += CrawlerUtils.purified_to_md(page, handle)

            # 穿透至申请子页：提取文字并匹配最新的 Zugangsordnung
            print("    正在获取申请与录取细则")
            admission_info = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a'));
                const keywords = ['application and admission', 'bewerbung und zulassung', 'admission', 'bewerbung'];
                const found = links.find(a => {
                    const t = a.innerText.replace(/\\n/g, ' ').trim().toLowerCase();
                    const h = (a.getAttribute('href') || "").toLowerCase();
                    return (keywords.some(k => t.includes(k)) && (h.includes('bewerbung') || h.includes('admission')));
                });
                return found ? { href: found.href, label: found.innerText.trim() } : null;
            }""")

            if admission_info:
                sub_url = admission_info['href']
                print(f"    穿透至子页：{sub_url}")
                page.goto(sub_url, wait_until="domcontentloaded")
                time.sleep(2)

                # 提取子页文字内容
                sub_main = page.locator('#fh_main').first
                if sub_main.count() > 0:
                    md_content += f"\n\n---\n## 申请与录取详细信息 (Admission Details)\n\n"
                    md_content += f"**Source (Admission): {sub_url}**\n\n"
                    md_content += CrawlerUtils.purified_to_md(page, sub_main)

                # 匹配 Zugangsordnung PDF
                print("    -> 正在匹配最新版 [Zugangsordnung] 文件...")
                pdf_target_url = page.evaluate("""() => {
                    // 寻找包含特定文本的链接
                    const links = Array.from(document.querySelectorAll('a'));
                    // 只要找到第一个包含 Zugangsordnung 的链接即返回
                    const target = links.find(a => a.innerText.includes('Zugangsordnung'));
                    return target ? target.href : null;
                }""")

                if pdf_target_url:
                    # 使用内存拉取技术下载，保证绕过验证
                    pdf_filename = f"Regulation_{CrawlerUtils.sanitize_path(task['name'], False)}.pdf"
                    print(f"       发现目标 PDF，启动内存流拉取")
                    self.download_pdf_via_fetch(page, pdf_target_url, os.path.join(major_dir, pdf_filename))
            else:
                print("    未发现申请链接，跳过子页抓取。")

            # 写入 Markdown 结果
            with open(os.path.join(major_dir, file_name), "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"    任务结束，档案已成功存入")

        except Exception as e:
            print(f"    详情解析出错：{e}")

    def download_pdf_via_fetch(self, page, url, save_path):
        """利用浏览器内核 fetch 绕过 JWT 和 Referer 限制"""
        try:
            b64_data = page.evaluate("""async (url) => {
                const response = await fetch(url);
                const blob = await response.blob();
                return new Promise((resolve) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result.split(',')[1]);
                    reader.readAsDataURL(blob);
                });
            }""", url)

            if b64_data:
                with open(save_path, 'wb') as f:
                    f.write(base64.b64decode(b64_data))
                print("       准入条例已通过内存通道下载")
            else:
                print("       PDF 转换失败")
        except Exception as e:
            print(f"       错误-内存拉取中断：{e}")


if __name__ == "__main__":
    AachenScraper().run()