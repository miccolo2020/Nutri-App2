import requests
import pandas as pd
import logging
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter import scrolledtext
import os
import time
import re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from bs4 import BeautifulSoup
import webbrowser
from PIL import Image, ImageTk
from io import BytesIO
import schedule
import threading
import datetime
import json

# --- Globals ---
SEEN_URLS_FILE = "seen_urls.txt"
JSON_RESULTS_FILE = "marketplace_results.json"
VALID_CITY_SLUGS = {
    "miami": "miami",
    "boca": "bocaraton",
    "homestead": "homestead",
    "newyork": "newyork",
    "losangeles": "losangeles",
    "chicago": "chicago",
}

# --- Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
image_refs = []
seen_urls = set()
scheduled_thread = None
stop_scheduled = threading.Event()

# --- Functions ---
def setup_browser():
    try:
        options = webdriver.ChromeOptions() if browser_choice.get() == "Chrome" else webdriver.EdgeOptions()
        user_data_path = os.path.join(os.getcwd(), "chrome-profile")
        options.add_argument(f"--user-data-dir={user_data_path}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        if browser_choice.get() == "Chrome":
            return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        else:
            return webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=options)
    except Exception as e:
        logger.error(f"Browser setup failed: {e}")
        messagebox.showerror("WebDriver Error", str(e))
        return None

def scroll_to_load(browser):
    last_height = browser.execute_script("return document.body.scrollHeight")
    while True:
        browser.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        new_height = browser.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def load_seen_urls():
    if os.path.exists(SEEN_URLS_FILE):
        with open(SEEN_URLS_FILE, 'r') as f:
            for line in f:
                seen_urls.add(line.strip())

def save_seen_url(url):
    seen_urls.add(url)
    with open(SEEN_URLS_FILE, 'a') as f:
        f.write(url + '\n')

def save_results_to_json(results):
    existing = []
    if os.path.exists(JSON_RESULTS_FILE):
        with open(JSON_RESULTS_FILE, 'r') as f:
            existing = json.load(f)

    urls = {item['url'] for item in existing}
    unique = [item for item in results if item['url'] not in urls]
    all_results = existing + unique

    with open(JSON_RESULTS_FILE, 'w') as f:
        json.dump(all_results, f, indent=2)

    logger.info(f"Saved {len(unique)} new results. Total: {len(all_results)}")

def seller_joined_in_2025(browser):
    try:
        joined_text = browser.find_element(By.XPATH, "//*[contains(text(),'Joined')]").text
        return '2025' in joined_text
    except:
        return False

def scrape_facebook_marketplace(city, product, min_price, max_price, days_listed, radius, condition, seller_type):
    city_slug = VALID_CITY_SLUGS.get(city.strip().lower())
    if not city_slug:
        logger.warning(f"Unknown city: {city}")
        return []

    browser = setup_browser()
    if not browser:
        return []

    radius_km = int(radius) * 1.60934
    url = f"https://www.facebook.com/marketplace/{city_slug}/search?query={product}&minPrice={min_price}&maxPrice={max_price}&daysSinceListed={days_listed}&radiusKM={radius_km}&exact=false"
    browser.get(url)
    time.sleep(3)

    try:
        browser.find_element(By.XPATH, '//div[@aria-label="Close" and @role="button"]').click()
    except:
        pass

    scroll_to_load(browser)
    soup = BeautifulSoup(browser.page_source, 'html.parser')
    links = soup.find_all('a')
    listings = [l for l in links if product.lower() in l.text.lower()]

    results = []
    for link in listings:
        item_url = 'https://facebook.com' + link.get('href').split('?')[0]
        if item_url in seen_urls:
            continue

        img_tag = link.find('img')
        image_url = img_tag['src'] if img_tag else None
        text = "\n".join(link.stripped_strings)

        if condition and condition.lower() not in text.lower():
            continue
        if seller_type and seller_type.lower() not in text.lower():
            continue

        browser.get(item_url)
        time.sleep(2)
        if seller_joined_in_2025(browser):
            continue

        try:
            price = re.search(r'\$\d[\d,.]*', text).group()
            price_val = float(price.replace('$', '').replace(',', ''))
        except:
            continue

        lines = text.split('\n')
        title = lines[0] if lines else 'N/A'
        location = lines[-1] if len(lines) > 1 else 'N/A'

        result = {'title': title, 'price': price_val, 'location': location, 'url': item_url, 'image': image_url}
        results.append(result)
        save_seen_url(item_url)

    browser.quit()
    save_results_to_json(results)
    return results

def send_to_discord(results, webhook):
    for item in results:
        embed = {
            "title": item['title'],
            "url": item['url'],
            "fields": [
                {"name": "Price", "value": f"${item['price']}", "inline": True},
                {"name": "Location", "value": item['location'], "inline": True}
            ],
            "image": {"url": item['image']} if item['image'] else {},
        }
        payload = {"username": "FB Bot", "embeds": [embed]}
        requests.post(webhook, json=payload)

def display_results_gui(data):
    results_box.delete(1.0, tk.END)
    image_refs.clear()
    for item in data:
        results_box.insert(tk.END, f"{item['title']} - ${item['price']} - {item['location']}\n")
        results_box.insert(tk.END, f"{item['url']}\n\n")
        if item['image']:
            try:
                resp = requests.get(item['image'])
                img = Image.open(BytesIO(resp.content)).resize((100, 100))
                photo = ImageTk.PhotoImage(img)
                image_refs.append(photo)
                results_box.image_create(tk.END, image=photo)
                results_box.insert(tk.END, '\n\n')
            except:
                continue

def export_to_csv(data):
    if not data:
        messagebox.showwarning("No Data", "Nothing to export")
        return
    df = pd.DataFrame(data)
    fname = f"results_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(fname, index=False)
    messagebox.showinfo("Exported", f"Saved to {fname}")

def start_scraping():
    global result_data
    cities = city_var.get().split(',')
    products = product_entry.get().split(',')
    condition = condition_entry.get()
    seller_type = seller_entry.get()
    webhook_url = webhook_entry.get()
    result_data.clear()
    for city in cities:
        for product in products:
            result_data += scrape_facebook_marketplace(
                city.strip(), product.strip(), min_price_entry.get(),
                max_price_entry.get(), days_entry.get(),
                radius_entry.get(), condition, seller_type)
    display_results_gui(result_data)
    if webhook_url:
        send_to_discord(result_data, webhook_url)
    last_scrape_label.config(text=f"Last Scrape: {datetime.datetime.now().strftime('%H:%M:%S')}")

def run_schedule():
    while not stop_scheduled.is_set():
        schedule.run_pending()
        time.sleep(1)

def schedule_scraping():
    stop_scheduled.clear()
    choice = scraping_choice.get()
    if choice == "Run Now":
        threading.Thread(target=start_scraping).start()
    elif choice == "Every 2 minutes":
        schedule.clear()
        schedule.every(2).minutes.do(lambda: threading.Thread(target=start_scraping).start())
        global scheduled_thread
        scheduled_thread = threading.Thread(target=run_schedule)
        scheduled_thread.start()

def stop_scraping():
    stop_scheduled.set()
    schedule.clear()
    logger.info("Scheduled scraping stopped")
    messagebox.showinfo("Stopped", "Scraping schedule stopped.")

def reset_seen():
    seen_urls.clear()
    if os.path.exists(SEEN_URLS_FILE):
        os.remove(SEEN_URLS_FILE)
        messagebox.showinfo("Reset", "Seen URL history cleared.")

# --- GUI ---
root = tk.Tk()
root.title("Facebook Marketplace Scraper")

city_var = tk.StringVar()
result_data = []

inputs = [
    ("Cities (comma-separated):", city_var),
    ("Products (comma-separated):", tk.StringVar()),
    ("Min Price:", tk.StringVar()),
    ("Max Price:", tk.StringVar()),
    ("Days Listed:", tk.StringVar()),
    ("Radius (miles):", tk.StringVar()),
    ("Condition:", tk.StringVar()),
    ("Seller Type:", tk.StringVar()),
    ("Discord Webhook:", tk.StringVar()),
]

entries = []
for i, (label, var) in enumerate(inputs):
    tk.Label(root, text=label).grid(row=i, column=0)
    entry = tk.Entry(root, textvariable=var, width=50)
    entry.grid(row=i, column=1)
    entries.append(var)

product_entry, min_price_entry, max_price_entry, days_entry, radius_entry, condition_entry, seller_entry, webhook_entry = entries[1:]

# Browser selection dropdown
browser_choice = tk.StringVar(value="Edge")
tk.Label(root, text="Browser:").grid(row=9, column=0)
browser_dropdown = ttk.Combobox(root, textvariable=browser_choice, values=["Edge", "Chrome"])
browser_dropdown.grid(row=9, column=1)

scraping_choice = ttk.Combobox(root, values=["Run Now", "Every 2 minutes"])
scraping_choice.set("Run Now")
scraping_choice.grid(row=10, column=1)

last_scrape_label = tk.Label(root, text="Last Scrape: Never")
last_scrape_label.grid(row=11, column=1)

results_box = scrolledtext.ScrolledText(root, width=80, height=20)
results_box.grid(row=12, column=0, columnspan=2)

buttons = [
    ("Start Scraping", schedule_scraping),
    ("Stop Scraping", stop_scraping),
    ("Export to CSV", lambda: export_to_csv(result_data)),
    ("Reset Seen History", reset_seen),
]

for i, (label, command) in enumerate(buttons):
    tk.Button(root, text=label, command=command).grid(row=13+i, column=1)

load_seen_urls()
root.mainloop()
