# modules/alerts.py
import os
from dotenv import load_dotenv
from modules.database import run_query
from typing import Dict, Any

load_dotenv()

# Twilio
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Lazy imports to avoid import-time errors if not configured
_twilio_client = None
_telegram_bot = None

def _get_twilio_client():
    global _twilio_client
    if _twilio_client is None:
        try:
            if TWILIO_SID and TWILIO_TOKEN:
                from twilio.rest import Client
                _twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
        except Exception as e:
            print("Twilio init error:", e)
    return _twilio_client

def _get_telegram_bot():
    global _telegram_bot
    if _telegram_bot is None:
        try:
            if TELEGRAM_TOKEN:
                from telegram import Bot
                _telegram_bot = Bot(token=TELEGRAM_TOKEN)
        except Exception as e:
            print("Telegram init error:", e)
    return _telegram_bot

# ---------------- Helpers ----------------
def normalize(s: str):
    return s.lower().strip() if s else None

def get_active_alerts():
    """Return list of active alerts (dicts)."""
    rows = run_query("SELECT * FROM alerts WHERE active=1", fetch=True)
    return rows or []

def already_sent(alert_id: int, notice_id: int) -> bool:
    res = run_query(
        "SELECT id FROM alerts_sent WHERE alert_id=%s AND notice_id=%s",
        (alert_id, notice_id),
        fetch=True,
    )
    return bool(res)

def mark_sent(alert_id: int, notice_id: int):
    try:
        run_query(
            "INSERT INTO alerts_sent (alert_id, notice_id) VALUES (%s, %s)",
            (alert_id, notice_id),
        )
    except Exception:
        # ignore duplicate / race
        pass

# ---------------- Delivery functions ----------------
def send_whatsapp(to_number: str, text: str) -> bool:
    """
    to_number must be in Twilio WhatsApp format: whatsapp:+91XXXXXXXXXX
    """
    client = _get_twilio_client()
    if not client or not TWILIO_WHATSAPP_NUMBER:
        print("Twilio not configured properly.")
        return False
    try:
        client.messages.create(
            body=text,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=to_number
        )
        return True
    except Exception as e:
        print("WhatsApp send failed:", e)
        return False

def send_telegram(chat_id: str, text: str) -> bool:
    bot = _get_telegram_bot()
    if not bot:
        print("Telegram bot not configured.")
        return False
    try:
        bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        return True
    except Exception as e:
        print("Telegram send failed:", e)
        return False

# ---------------- Core: matching and notify ----------------
def notify_if_matches(notice_row: Dict[str, Any]):
    """
    Called after inserting a new notice.

    notice_row must include keys: id, title, link, date, source
    """
    try:
        notice_id = int(notice_row.get("id"))
    except Exception:
        print("notify_if_matches: invalid notice id")
        return

    title = notice_row.get("title", "") or ""
    link = notice_row.get("link", "") or ""
    source = (notice_row.get("source") or "").upper()
    date = notice_row.get("date", "")

    message = f"📢 New [{source}] Notice:\n{title}\n{link}\n🗓 {date}"

    active_alerts = get_active_alerts()
    t_title = title.lower()

    for a in active_alerts:
        try:
            # a is a dict from DB, keys: id, user_identifier, channel, keyword, source, frequency, active
            # Apply source filter (if set on alert)
            alert_source = (a.get("source") or "").upper()
            if alert_source:
                if alert_source != source:
                    continue

            # Apply keyword filter (if set)
            kw = (a.get("keyword") or "").strip()
            if kw:
                if kw.lower() not in t_title:
                    continue

            # frequency: only immediate alerts handled here
            freq = (a.get("frequency") or "immediate")
            if freq != "immediate":
                # skip here; daily digest job will handle
                continue

            alert_id = int(a["id"])
            if already_sent(alert_id, notice_id):
                continue

            user_ident = a.get("user_identifier")
            channel = a.get("channel")

            sent = False
            if channel == "whatsapp":
                # Expecting user_ident like 'whatsapp:+91...'
                sent = send_whatsapp(user_ident, message)
            elif channel == "telegram":
                # Telegram chat_id (string or int)
                sent = send_telegram(user_ident, message)
            else:
                print("Unknown channel for alert:", channel)

            if sent:
                mark_sent(alert_id, notice_id)

        except Exception as inner_e:
            print("Error processing alert:", inner_e)
            continue
