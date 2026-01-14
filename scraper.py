import sys
import re
import json
import os
import time
import logging
import random
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from bs4 import BeautifulSoup

# --- Configuration ---
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
DEBUG_DIR = os.path.join(DATA_DIR, "debug_html")
os.makedirs(DEBUG_DIR, exist_ok=True)

MAX_PAGES = 3  # Number of pages to scrape per query
DELAY = 3      # Seconds to wait between actions
RETRIES = 3    # Number of retries per page if proxy fails

# Regex for phone numbers (Global format)
PHONE_REGEX = re.compile(r'(\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9})')

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# --- Load Proxies ---
PROXY_FILE = "proxies.txt"
if not os.path.exists(PROXY_FILE):
    logger.error(f"Proxy file not found: {PROXY_FILE}")
    sys.exit(1)

with open(PROXY_FILE, "r") as f:
    PROXIES = [line.strip() for line in f if line.strip()]

if not PROXIES:
    logger.error("No proxies loaded. Please add SOCKS5 proxies to proxies.txt")
    sys.exit(1)

def get_random_proxy():
    return random.choice(PROXIES)

# --- Helper Functions ---
def extract_coordinates(url):
    """Extracts Lat/Lon from a Google Maps URL."""
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match:
        return match.group(1), match.group(2)
    return None, None

# --- Main Scraper ---
def scrape_google_maps(query):
    results = []

    logger.info(f"Starting Google Maps scrape for: {query}")
    with sync_playwright() as p:
        for attempt in range(RETRIES):
            proxy_url = get_random_proxy()
            logger.info(f"Using proxy: {proxy_url} (Attempt {attempt + 1})")
            try:
                # Launch browser with proxy
                browser = p.chromium.launch(
                    headless=True,
                    proxy={"server": proxy_url}
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                page.set_default_timeout(60000)

                # Navigate to search
                url = f"https://www.google.com/maps/search/{query}"
                logger.info(f"Navigating to: {url}")
                page.goto(url, wait_until="domcontentloaded")

                # Wait for sidebar to load
                try:
                    page.wait_for_selector("div[role='feed']", timeout=15000)
                except PlaywrightTimeout:
                    logger.warning("Sidebar did not load in time. Retrying...")
                    browser.close()
                    continue  # retry with a new proxy

                # Pagination Loop
                for page_num in range(1, MAX_PAGES + 1):
                    logger.info(f"--- Processing Page {page_num} ---")

                    # Scroll to load lazy results
                    for _ in range(5):
                        page.mouse.wheel(0, 1000)
                        time.sleep(1)

                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")

                    items = soup.select("div[role='article']")
                    if not items:
                        items = soup.select("div.Nv2PK")  # fallback

                    logger.info(f"Found {len(items)} items on page.")

                    for item in items:
                        # Name
                        name_tag = item.select_one("div.fontHeadlineSmall") or item.select_one("div.qBF1Pd")
                        name = name_tag.get_text(strip=True) if name_tag else "Unknown"

                        # Phone
                        phone = None
                        text_content = item.get_text(" ", strip=True)
                        match = PHONE_REGEX.search(text_content)
                        if match:
                            phone = match.group(1).strip()

                        # Coordinates
                        link_tag = item.select_one("a.hfpxzc")
                        if link_tag:
                            href = link_tag.get("href", "")
                            lat, lon = extract_coordinates(href)
                        else:
                            lat, lon = None, None

                        # Image
                        img_tag = item.select_one("img")
                        image = img_tag.get("src") if img_tag else None

                        entry = {
                            "name": name,
                            "phone": phone,
                            "latitude": lat,
                            "longitude": lon,
                            "image": image,
                            "source_page": page_num
                        }

                        if not any(r['name'] == entry['name'] and r['phone'] == entry['phone'] for r in results):
                            results.append(entry)
                            logger.info(f"✅ {name} | {phone}")

                    # Click Next
                    if page_num < MAX_PAGES:
                        try:
                            next_button = page.get_by_role("button", name="Next page")
                            if next_button.is_visible():
                                logger.info("Clicking Next Page...")
                                next_button.click()
                                time.sleep(DELAY)
                            else:
                                logger.info("Next button not found. End of results.")
                                break
                        except Exception as e:
                            logger.warning(f"Could not click next: {e}")
                            break

                browser.close()
                break  # success, exit retry loop

            except Exception as e:
                logger.warning(f"Proxy {proxy_url} failed: {e}")
                time.sleep(2)  # small delay before next attempt
                continue

    # Save JSON
    safe_name = re.sub(r'[^a-z0-9\-]+', '-', query.lower())
    now = datetime.now().strftime("%Y-%m-%d-%H-%M")
    filename = f"{safe_name}-google-{now}.json"
    filepath = os.path.join(DATA_DIR, filename)

    logger.info(f"Saving {len(results)} results to {filepath}")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return results, filename

# --- Main ---
if __name__ == "__main__":
    query_arg = sys.argv[1] if len(sys.argv) > 1 else "restaurants in rabat"
    scrape_google_maps(query_arg)
    logger.info("Job finished.")
