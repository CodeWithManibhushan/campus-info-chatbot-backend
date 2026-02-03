"""
PTU Noticeboard scraper (robust, date-detection + row heuristics).
- Scans the noticeboard table at https://ptu.ac.in/noticeboard-main/
- Picks title, best link (anchor in title cell or first valid anchor), and posted date
- Inserts into notices(title, link, date, source='PTU') only if within last 30 days
How to run:
    venv\Scripts\activate
    python -m modules.scraper_ptu
"""
from datetime import datetime, timedelta
from urllib.parse import urljoin
import requests, re, time
from bs4 import BeautifulSoup
from modules.database import run_query
from modules.alerts import notify_if_matches  # notify after insert

PTU_BASE = "https://ptu.ac.in"
PTU_NOTICE_PAGE = "https://ptu.ac.in/noticeboard-main/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MAX_AGE_DAYS = 30   # only keep notices posted within last 30 days
MAX_ROWS = 100      # top rows to scan (adjust for speed)
DEBUG = True

date_regexes = [
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"),         # 28/11/2025 or 28-11-2025
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b"),         # 28 November 2025
    re.compile(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b"),         # 2025-11-28 (ISO)
]

def debug(*a, **k):
    if DEBUG:
        print(*a, **k)

def fetch_html(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        return r.text
    except Exception as e:
        debug("❌ Fetch error:", e)
        return None

def try_parse_date(text):
    if not text:
        return None
    t = text.strip()
    # common formats
    # try dd/mm/yyyy or dd-mm-yyyy
    m = date_regexes[0].search(t)
    if m:
        d = f"{m.group(1).zfill(2)}-{m.group(2).zfill(2)}-{m.group(3)}"
        try:
            return datetime.strptime(d, "%d-%m-%Y").date()
        except:
            pass
    # try dd Month yyyy
    m = date_regexes[1].search(t)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d %B %Y").date()
        except:
            try:
                return datetime.strptime(m.group(0), "%d %b %Y").date()
            except:
                pass
    # try ISO yyyy-mm-dd
    m = date_regexes[2].search(t)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except:
            pass
    # fallback: try to parse common tokens
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except:
            pass
    return None

def best_title_and_link_from_row(tr):
    """
    Heuristic:
    - find the td that contains a date (by regex) -> mark it as date_td
    - among other tds, choose the td with max visible text length as title_td
    - title = text of title_td
    - link: prefer anchor within title_td (href). Otherwise use first anchor in the row
             that looks like a notice/pdf (href contains 'notice' or endswith .pdf)
    """
    tds = tr.find_all("td")
    if not tds:
        return None, None, None  # no useful data

    date_td = None
    for td in tds:
        if try_parse_date(td.get_text(" ", strip=True)):
            date_td = td
            break

    # select candidate title cell: max text length except date_td and small cells
    candidate = None
    max_len = 0
    for td in tds:
        if td is date_td:
            continue
        text = td.get_text(" ", strip=True)
        if len(text) > max_len:
            max_len = len(text)
            candidate = td

    title = candidate.get_text(" ", strip=True) if candidate else None

    # find link: prefer <a> in candidate, else first 'useful' anchor in the whole row
    link = None
    if candidate:
        a = candidate.find("a", href=True)
        if a:
            link = a["href"]

    if not link:
        # search for anchors in whole row for likely PDF or notice link
        anchors = tr.find_all("a", href=True)
        for a in anchors:
            href = a["href"]
            low = href.lower()
            if "notice" in low or low.endswith(".pdf") or href.startswith("http"):
                link = href
                break
        # if not found, take first anchor if any
        if not link and anchors:
            link = anchors[0]["href"]

    # try parse date from date_td or from any td containing date regex
    date_val = None
    if date_td:
        date_val = try_parse_date(date_td.get_text(" ", strip=True))
    if not date_val:
        # fallback: scan all tds for date-like text
        for td in tds:
            dv = try_parse_date(td.get_text(" ", strip=True))
            if dv:
                date_val = dv
                break

    return title, link, date_val

def run():
    debug("🔔 PTU Scraper starting...")
    html = fetch_html(PTU_NOTICE_PAGE)
    if not html:
        print("❌ Unable to fetch PTU noticeboard page.")
        return

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        debug("❌ No table found on page. Please verify the page structure.")
        debug("HTML snippet:", html[:1500])
        return

    rows = table.find_all("tr")
    if len(rows) <= 1:
        debug("⚠️ No data rows found in table.")
        debug("Table snippet:", str(table)[:1500])
        return

    # skip header row(s)
    data_rows = rows[1: MAX_ROWS + 1]
    debug(f"➡️ Scanning top {len(data_rows)} rows (MAX_ROWS={MAX_ROWS}).")

    saved = 0
    limit_date = datetime.now().date() - timedelta(days=MAX_AGE_DAYS)

    for idx, tr in enumerate(data_rows, start=1):
        title, link, date_val = best_title_and_link_from_row(tr)
        debug(f"\nRow #{idx}:")
        debug("  title:", repr(title))
        debug("  raw link:", repr(link))
        debug("  parsed date:", repr(date_val))

        if not title or title.strip() == "":
            debug("  ❌ Skipped: no title text.")
            continue

        if link:
            link = urljoin(PTU_BASE, link)
        else:
            debug("  ⚠️ No anchor link found in row — we will skip (to avoid useless entries).")
            continue

        if not date_val:
            debug("  ⚠️ Date not parsed — skipping to avoid bad dates. (If desired, enable fallback to today's date)")
            continue

        if date_val < limit_date:
            debug(f"  ⚠️ Skipped: date {date_val} older than {MAX_AGE_DAYS} days (limit {limit_date}).")
            continue

        # check duplicates
        try:
            existing = run_query("SELECT id FROM notices WHERE link=%s OR title=%s", (link, title), fetch=True)
            if existing:
                debug("  ↩️ Skipped: duplicate found in DB.")
                continue
        except Exception as e:
            debug("  ❌ DB check error:", e)
            continue

        # insert and notify alerts
        try:
            run_query("INSERT INTO notices (title, link, date, source) VALUES (%s, %s, %s, %s)",
                      (title, link, date_val, "PTU"))
            # fetch inserted id
            res = run_query("SELECT id FROM notices WHERE link=%s", (link,), fetch=True)
            if res:
                nid = res[0].get("id")
                notice_row = {"id": nid, "title": title, "link": link, "date": date_val, "source": "PTU"}
                try:
                    notify_if_matches(notice_row)
                except Exception as e:
                    debug("Notify error (PTU):", e)
            saved += 1
            debug("  ✅ Inserted.")
        except Exception as e:
            debug("  ❌ Insert failed:", e)

    print(f"\n✅ Finished. Saved {saved} new notices (source=PTU).")

if __name__ == "__main__":
    run()
