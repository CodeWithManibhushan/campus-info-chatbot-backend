# modules/scraper_gndec.py
"""
GNDEC notices scraper (drop-in replacement).
Saves notices into `notices` table using modules.database.run_query.

How to run:
    venv\Scripts\activate
    python -m modules.scraper_gndec
"""
from datetime import datetime
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from modules.database import run_query
import re
import time
from modules.alerts import notify_if_matches  # notify after insert

# --- CONFIG ---
GNDEC_URL = "https://erp.gndec.ac.in/notice"
CANDIDATE_PATHS = ["", ]  # currently only ERP notice page

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0 Safari/537.36"
}

# optional small delay between per-page fetches to be polite
PER_PAGE_DELAY = 0.4  # seconds

def fetch_page(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"❌ Error fetching {url}: {e}")
        return None

def try_extract_date_from_text(text):
    """Try to find a date-like pattern in a text string and return a date object or None."""
    if not text:
        return None
    # Common formats to match: 29 Nov 2025, 29-11-2025, 2025-11-29, 29/11/2025, Nov 29, 2025
    patterns = [
        r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",    # 29 November 2025
        r"(\d{1,2}-\d{1,2}-\d{4})",              # 29-11-2025
        r"(\d{4}-\d{1,2}-\d{1,2})",              # 2025-11-29
        r"(\d{1,2}/\d{1,2}/\d{4})",              # 29/11/2025
        r"([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})",   # November 29, 2025
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            s = m.group(1)
            for fmt in ("%d %B %Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y"):
                try:
                    return datetime.strptime(s, fmt).date()
                except Exception:
                    continue
    return None

def get_notice_date_from_page(url):
    """
    Fetch individual notice page and try to extract date from:
    - <time> tags
    - elements with class containing 'date' or 'posted'
    - meta tags (article:published_time or og:published_time)
    - plain text heuristics
    Returns date object or None.
    """
    html = fetch_page(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1) <time datetime="..."> or <time> text
    t = soup.find("time")
    if t:
        # try datetime attr first
        dt = t.get("datetime") or t.get_text(" ", strip=True)
        d = try_extract_date_from_text(dt)
        if d:
            return d

    # 2) meta tags
    meta_names = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"name": "publish-date"}),
        ("meta", {"name": "publish_date"}),
        ("meta", {"property": "og:published_time"}),
    ]
    for tag_name, attrs in meta_names:
        tag = soup.find(tag_name, attrs=attrs)
        if tag:
            val = tag.get("content") or tag.get("value") or tag.get_text(" ", strip=True)
            d = try_extract_date_from_text(val)
            if d:
                return d

    # 3) look for common date-like classes/spans near title
    candidates = soup.find_all(lambda el: el.name in ["span", "div", "p"] and el.get_text(strip=True))
    for el in candidates:
        cl = " ".join(el.get("class") or [])
        if any(k in cl.lower() for k in ["date", "posted", "publish", "time", "meta"]):
            d = try_extract_date_from_text(el.get_text(" ", strip=True))
            if d:
                return d

    # 4) fallback: search entire page text for first date-like pattern
    text = soup.get_text(" ", strip=True)
    d = try_extract_date_from_text(text)
    if d:
        return d

    return None

def extract_notices_from_soup(soup, base_url):
    """
    Extract candidate notices from a BeautifulSoup object.
    Returns list of (title, href, date_str_or_none).
    """
    notices = []

    selectors = [
        "table tbody tr td a",
        "div.noticeboard_list a",
        "div.notice a",
        "ul.notice-list a",
        "div.card a",
        "div.page a[href*='/notice/']",
        "a[href*='notice']",
        "a[href*='/notice/']",
        ".post .entry-title a",
        ".widget_recent_entries a",
    ]

    for sel in selectors:
        found = soup.select(sel)
        if found:
            for a in found:
                title = a.get_text(" ", strip=True)
                href = a.get("href")
                if not title or not href:
                    continue
                # skip non-http handlers
                if href.lower().startswith("javascript:") or href.lower().startswith("mailto:"):
                    continue
                full_href = urljoin(base_url, href)
                # attempt to find a date near link (parent/previous sibling)
                date_str = None
                try:
                    parent = a.parent
                    if parent:
                        # search within parent for date-like text
                        elems = parent.find_all(["span", "time", "small", "p"], limit=4)
                        for e in elems:
                            d = try_extract_date_from_text(e.get_text(" ", strip=True))
                            if d:
                                date_str = d
                                break
                except Exception:
                    date_str = None

                notices.append((title, full_href, date_str))
            if notices:
                print(f"🔎 Strategy matched selector: {sel}  (found {len(notices)})")
                return notices

    # fallback: scan all <a> tags and apply heuristics
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if not text:
            continue
        if len(text) > 30 or ("pdf" in href.lower()) or ("notice" in href.lower()) or ("circular" in href.lower()):
            if href.lower().startswith("javascript:") or href.lower().startswith("mailto:"):
                continue
            full_href = urljoin(base_url, href)
            notices.append((text, full_href, None))

    print(f"🔎 Fallback strategy used. Found {len(notices)} link candidates.")
    return notices

def save_notices(notice_items):
    saved = 0
    for title, href, date_hint in notice_items:
        try:
            # avoid duplicates
            existing = run_query("SELECT id FROM notices WHERE link=%s OR title=%s", (href, title), fetch=True)
            if existing:
                continue

            # convert date_hint (could already be date object) to date
            date_val = None
            if isinstance(date_hint, datetime):
                date_val = date_hint.date()
            elif hasattr(date_hint, "year"):  # date object
                date_val = date_hint
            elif isinstance(date_hint, str):
                # try parse common formats
                date_val = try_extract_date_from_text(date_hint)

            # If no date from list, attempt to open the notice page and extract date
            if not date_val:
                # politely fetch the notice page for a date
                page_date = get_notice_date_from_page(href)
                if page_date:
                    date_val = page_date
                else:
                    date_val = datetime.now().date()

                # small delay
                time.sleep(PER_PAGE_DELAY)

            # insert into DB with source tag and notify alerts
            try:
                run_query(
                    "INSERT INTO notices (title, link, date, source) VALUES (%s, %s, %s, %s)",
                    (title, href, date_val, "GNDEC")
                )
                # fetch inserted id
                res = run_query("SELECT id FROM notices WHERE link=%s", (href,), fetch=True)
                if res:
                    nid = res[0].get("id")
                    notice_row = {"id": nid, "title": title, "link": href, "date": date_val, "source": "GNDEC"}
                    try:
                        notify_if_matches(notice_row)
                    except Exception as e:
                        print("Notify error (GNDEC):", e)
                saved += 1
            except Exception as e:
                print("❌ Error saving notice (insert):", e)
        except Exception as e:
            print("❌ Error saving notice:", e)
    return saved

def find_valid_page():
    # try candidate paths (currently GNDEC_URL)
    for p in CANDIDATE_PATHS:
        url = GNDEC_URL if not p else GNDEC_URL.rstrip("/") + "/" + p.lstrip("/")
        print("Trying:", url)
        html = fetch_page(url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        text_lower = soup.get_text(" ", strip=True).lower()
        if "notice" in text_lower or len(soup.find_all("a")) > 8:
            return url, soup
    # fallback to GNDEC_URL homepage
    html = fetch_page(GNDEC_URL)
    if html:
        return GNDEC_URL, BeautifulSoup(html, "html.parser")
    return None, None

def run():
    print("🔔 GNDEC Scraper starting...")
    url, soup = find_valid_page()
    if not url or not soup:
        print("❌ Could not fetch GNDEC page. Adjust GNDEC_URL or candidate paths.")
        return

    print("✅ Page chosen:", url)
    items = extract_notices_from_soup(soup, url)
    if not items:
        print("⚠️ No candidate notices found. Inspect the page and update selectors.")
        return

    print(f"ℹ️ Candidates found: {len(items)}. Saving to DB (avoiding duplicates)...")
    saved = save_notices(items)
    print(f"✅ Scraped and stored {saved} new notices successfully.")

if __name__ == "__main__":
    run()
