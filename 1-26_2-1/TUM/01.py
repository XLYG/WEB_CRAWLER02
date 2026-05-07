from selenium import webdriver

# 不要设置 executable_path，直接初始化
# Selenium Manager 会自动接管一切
driver = webdriver.Chrome()

driver.get("https://www.google.com")
print("自动驱动配置成功！")
driver.quit()