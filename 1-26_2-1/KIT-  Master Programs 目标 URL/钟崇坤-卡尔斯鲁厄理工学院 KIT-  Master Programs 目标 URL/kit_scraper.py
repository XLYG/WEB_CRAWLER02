import requests
from bs4 import BeautifulSoup
import csv

# 目标URL
base_url = "https://www.sle.kit.edu/english/vorstudium/study-programs.php"

# 保存结果的列表
results = []
target_count = 60  # 设定目标数量（前3页，每页20条）

print("开始抓取...")

try:
    response = requests.get(base_url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')

    # 找到表格
    table = soup.find('table', {'id': 'example'})
    if table:
        tbody = table.find('tbody')
        if tbody:
            # 获取所有行
            rows = tbody.find_all('tr')

            # 指定想数量
            current_rows = rows[:target_count]
            for row in current_rows:
                # 提取项目名称和url
                name_cell = row.find('td')
                if not name_cell:
                    continue

                name_link = name_cell.find('a')
                if not name_link:
                    continue

                degree_program = name_link.text.strip()
                url = name_link.get('href')

                # 确保URL是完整的
                if url and not url.startswith('http'):
                    if url.startswith('/'):
                        url = f"https://www.sle.kit.edu{url}"
                    else:
                        url = f"https://www.sle.kit.edu/english/vorstudium/{url}"

                # 提取学位类型（第3个td）
                tds = row.find_all('td')
                if len(tds) < 3:
                    continue

                degree = tds[2].text.strip()

                # 添加到结果列表
                results.append({
                    'Degree Program': degree_program,
                    'Degree': degree,
                    'URL': url
                })

except Exception as e:
    print(f"获取数据时出错: {str(e)}")

# 保存为CSV文件
csv_file = 'results.csv'
if results:
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['Degree Program', 'Degree', 'URL']
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        writer.writeheader()
        for result in results:
            writer.writerow(result)
    print(f"\n任务完成，共 {len(results)} 条数据")
else:
    print("\n未获取到任何数据。")
