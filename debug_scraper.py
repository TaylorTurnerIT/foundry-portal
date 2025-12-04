from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re
import time

def debug_scraper(url):
    print(f"Debugging scraper for: {url}")
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
        print(f"Page Title: {driver.title}")
        
        # Wait for body
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Wait a bit more for dynamic content
        time.sleep(5)
        
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print("\n--- Body Text Start ---")
        print(body_text[:1000])
        print("--- Body Text End ---\n")
        
        # Test Regex
        match = re.search(r"Current Players\s*(\d+)\s*/\s*(\d+)", body_text)
        if match:
            print(f"SUCCESS: Found {match.group(1)} / {match.group(2)}")
        else:
            print("FAIL: Regex did not match")
            
            # Try to find "Current Players" string location
            if "Current Players" in body_text:
                print("String 'Current Players' FOUND in text.")
                # Print context
                idx = body_text.find("Current Players")
                print(f"Context: {body_text[idx:idx+50]}")
            else:
                print("String 'Current Players' NOT FOUND in text.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    debug_scraper("https://foundry.instance1.astralkeep.com/join")
