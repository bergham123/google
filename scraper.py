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

MAX_PAGES = 3  # Number of pages to scrape per query (20 results per page)
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
    """
    Extracts Lat/Lon from a Google Maps URL inside a search result.
    Format usually: .../maps/place/.../@34.020,-6.83,17z
    """
    # Look for pattern @lat,long,z
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match:
        return match.group(1), match.group(2)
    return None, None

# --- Main Scraper ---
def scrape_google_search(query):
    results = []

    logger.info(f"Starting Google Search scrape for: {query}")
    
    # Loop over proxies if needed (from your original code structure)
    # We keep the retry loop wrapper
    
    for attempt in range(RETRIES):
        proxy_url = get_random_proxy()
        logger.info(f"Using proxy: {proxy_url} (Attempt {attempt + 1})")
        
        try:
            with sync_playwright() as p:
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

                # Pagination Loop (Using URL parameter 'start')
                # Google Search standard is usually 20 results per page
                for page_num in range(0, MAX_PAGES):
                    start_val = page_num * 20
                    
                    # Construct URL exactly as requested
                    # Format: search?q=phone+number+for+restaurant+in+rabat&udm=1&start=20
                    safe_query = query.replace(" ", "+")
                    url = f"https://www.google.com/search?q={safe_query}&udm=1&start={start_val}"
                    
                    logger.info(f"--- Processing Page {page_num + 1} -> {url} ---")

                    page.goto(url, wait_until="domcontentloaded")
                    
                    # Wait for results to load
                    time.sleep(2) # Wait for JS to settle

                    # --- PARSING ---
                    html = page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    items = []

                    # Strategy 1: Try to find the Local Results Map Pack (div with role='list')
                    # This is where coordinates are usually accessible via links
                    local_pack = soup.select("div[role='list'] div[role='listitem']")
                    if local_pack:
                        items = local_pack
                        logger.info("Found Local Pack results.")
                    
                    # Strategy 2: Fallback to Standard Organic Results (div class='g')
                    if not items:
                        items = soup.select("div.g")
                        logger.info("Found Standard Organic results (Local Pack not detected).")

                    logger.info(f"Found {len(items)} items on page.")

                    for item in items:
                        # Name: Try H3 (Standard) or span inside local list item
                        name_tag = item.select_one("h3") or item.select_one("span")
                        if not name_tag:
                            continue
                        
                        # Clean name (remove "· Rating" etc if caught)
                        name = name_tag.get_text(strip=True)
                        if len(name) > 50: # Likely a description, not a name
                            name = name[:50] + "..."

                        # Phone: Search via Regex
                        phone = None
                        text_content = item.get_text(" ", strip=True)
                        match = PHONE_REGEX.search(text_content)
                        if match:
                            phone = match.group(1).strip()

                        # Coordinates: Look for a link to "maps.google.com/place"
                        lat, lon = None, None
                        link_tag = item.select_one("a[href*='maps.google.com']")
                        if link_tag:
                            href = link_tag.get("href", "")
                            lat, lon = extract_coordinates(href)
                        
                        # Image: Try to find an image tag
                        img_tag = item.select_one("img")
                        image = img_tag.get("src") if img_tag else None

                        entry = {
                            "name": name,
                            "phone": phone,
                            "latitude": lat,
                            "longitude": lon,
                            "image": image,
                            "source_page": page_num + 1
                        }

                        # Avoid duplicates
                        if not any(r['name'] == entry['name'] and r['phone'] == entry['phone'] for r in results):
                            if name:
                                results.append(entry)
                                logger.info(f"✅ {name} | {phone}")

                # Success: Save and exit retry loop
                browser.close()
                
                # Save JSON
                safe_name = re.sub(r'[^a-z0-9\-]+', '-', query.lower())
                now = datetime.now().strftime("%Y-%m-%d-%H-%M")
                filename = f"{safe_name}-search-{now}.json"
                filepath = os.path.join(DATA_DIR, filename)

                logger.info(f"Saving {len(results)} results to {filepath}")
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)

                return results, filename

        except Exception as e:
            logger.warning(f"Proxy {proxy_url} failed: {e}")
            time.sleep(2)  # small delay before next attempt
            continue

    return [], "failed"

# --- Main ---
if __name__ == "__main__":
    # The input from GitHub Actions workflow comes here
    query_arg = sys.argv[1] if len(sys.argv) > 1 else "phone number for restaurant in rabat"
    scrape_google_search(query_arg)
    logger.info("Job finished.")
