from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import re

def test_scraper(url):
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('window-size=1920x1080')
    options.add_argument('--ignore-certificate-errors')

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print("Body Text Sample:")
        print(body_text[:500])  # Print first 500 chars
        
        # Test Regex
        match = re.search(r"Current Players\s*(\d+)\s*/\s*(\d+)", body_text)
        if match:
            print(f"Match found: {match.group(1)} / {match.group(2)}")
        else:
            # Try with newlines
            match = re.search(r"Current Players\s*\n\s*(\d+)\s*/\s*(\d+)", body_text)
            if match:
                print(f"Match found with newline: {match.group(1)} / {match.group(2)}")
            else:
                print("No match found")
                
    finally:
        driver.quit()

if __name__ == "__main__":
    test_scraper("https://foundry.instance1.astralkeep.com/join")
