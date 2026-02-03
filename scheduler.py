# scheduler.py
"""
Scheduler for Campus Info Chatbot
- Periodically runs scrapers (PTU + GNDEC)
- Sends daily digest for alerts with frequency='daily'
- Use: python scheduler.py
"""
import pytz
import os
import traceback
from datetime import datetime, timedelta, date
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from modules.database import run_query
from modules import alerts as alerts_module

# import scraper run functions (these are the modules you replaced earlier)
from modules.scraper_ptu import run as run_scraper_ptu
from modules.scraper_gndec import run as run_scraper_gndec

# scheduler config
SCRAPE_INTERVAL_MINUTES = 30  # change if you want more/less frequent scraping
DAILY_DIGEST_HOUR = 18        # 24-hour clock (server/local time). Change to desired hour.
DAILY_DIGEST_MINUTE = 0

sched = BlockingScheduler(timezone=pytz.timezone("Asia/Kolkata"))

# ---- Job: run both scrapers ----
def run_all_scrapers():
    print(f"[{datetime.now()}] Scheduler: Running scrapers (PTU + GNDEC)...")
    try:
        try:
            run_scraper_ptu()
        except Exception as e:
            print("Error running PTU scraper:", e)
            traceback.print_exc()

        try:
            run_scraper_gndec()
        except Exception as e:
            print("Error running GNDEC scraper:", e)
            traceback.print_exc()

        print(f"[{datetime.now()}] Scheduler: Scrapers finished.")
    except Exception as e:
        print("Unexpected error in run_all_scrapers:", e)
        traceback.print_exc()

# ---- Helper: find notices since a date ----
def fetch_recent_notices(since_date):
    """
    since_date: datetime.date
    returns list of notices: dicts with keys id, title, link, date, source
    """
    try:
        rows = run_query(
            "SELECT id, title, link, date, source FROM notices WHERE date >= %s ORDER BY date DESC",
            (since_date,),
            fetch=True
        ) or []
        return rows
    except Exception as e:
        print("fetch_recent_notices error:", e)
        traceback.print_exc()
        return []

# ---- Job: daily digest ----
def daily_digest():
    """
    For each alert with frequency='daily' and active=1:
      - get notices from last 24 hours
      - filter by alert.source (if set) and alert.keyword (if set)
      - avoid notices already in alerts_sent
      - send a single digest message per alert (telegram or whatsapp)
      - mark_sent for those (so duplicates are avoided)
    """
    now = datetime.now()
    since = (now - timedelta(days=1)).date()
    print(f"[{now}] Running daily_digest for notices since {since}...")

    try:
        alerts = run_query("SELECT * FROM alerts WHERE frequency='daily' AND active=1", fetch=True) or []
        if not alerts:
            print("No daily alerts to process.")
            return

        recent_notices = fetch_recent_notices(since)
        if not recent_notices:
            print("No recent notices in last 24 hours.")
            return

        for a in alerts:
            try:
                aid = a.get("id")
                user_ident = a.get("user_identifier")
                channel = a.get("channel")
                kw = (a.get("keyword") or "").strip().lower()
                alert_source = (a.get("source") or "").upper()

                # match notices
                matched = []
                for n in recent_notices:
                    # source filter
                    n_source = (n.get("source") or "").upper()
                    if alert_source and alert_source != n_source:
                        continue
                    # keyword filter
                    if kw:
                        if kw not in (n.get("title") or "").lower():
                            continue
                    # already sent?
                    if alerts_module.already_sent(aid, n["id"]):
                        continue
                    matched.append(n)

                if not matched:
                    # nothing new for this alert
                    continue

                # build digest message
                text = "📬 Daily Digest — matching notices:\n\n"
                for m in matched:
                    text += f"- {m['title']}\n{m['link']}\n🗓 {m['date']}\n\n"

                sent = False
                if channel == "telegram":
                    sent = alerts_module.send_telegram(user_ident, text)
                elif channel == "whatsapp":
                    sent = alerts_module.send_whatsapp(user_ident, text)
                else:
                    print(f"Unknown channel for alert {aid}: {channel}")

                if sent:
                    for m in matched:
                        alerts_module.mark_sent(aid, m["id"])
                    print(f"Sent daily digest for alert {aid} ({len(matched)} items).")
                else:
                    print(f"Failed sending digest for alert {aid} via {channel}.")

            except Exception as inner:
                print("Error processing alert in daily_digest:", inner)
                traceback.print_exc()

    except Exception as e:
        print("daily_digest unexpected error:", e)
        traceback.print_exc()

# ---- Schedule jobs ----
# 1) scraper job every SCRAPE_INTERVAL_MINUTES
sched.add_job(run_all_scrapers, 'interval', minutes=SCRAPE_INTERVAL_MINUTES, id='scrapers_interval')

# 2) daily digest at specified hour minute (server local time)
# Using CronTrigger ensures it's run once a day at that time
sched.add_job(
    daily_digest,
    CronTrigger(hour=DAILY_DIGEST_HOUR, minute=DAILY_DIGEST_MINUTE,
                timezone=pytz.timezone("Asia/Kolkata")),
    id='daily_digest_job'
)


# ---- If run as main, start scheduler and run an immediate smoke test ----
if __name__ == "__main__":
    print("Scheduler starting. First, running scrapers once for immediate check...")
    run_all_scrapers()

    print(f"Starting APScheduler (scrape every {SCRAPE_INTERVAL_MINUTES} minutes, daily digest at {DAILY_DIGEST_HOUR:02d}:{DAILY_DIGEST_MINUTE:02d})")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler stopped by user.")
    except Exception as e:
        print("Scheduler error:", e)
        traceback.print_exc()
