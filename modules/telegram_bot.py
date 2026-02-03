# modules/telegram_bot.py
"""
Telegram bot (python-telegram-bot v13 compatible)
- Keeps existing commands (start, notices, FAQ categories → questions → answers)
- Adds alert management:
    /alert_add <keyword> <channel> <source?>
    /myalerts
    /delalert <id>
"""
from telegram.ext import MessageHandler, Filters
import google.generativeai as genai

import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ParseMode,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    CallbackQueryHandler,
)
from modules.database import run_query

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
# -------- GEMINI SETUP --------
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    try:
        gemini_model = genai.GenerativeModel("models/gemini-2.5-flash")
    except:
        gemini_model = genai.GenerativeModel("gemini-pro")
else:
    gemini_model = None


# ---------------- START ----------------
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hello! I'm the Campus Info Chatbot.\n"
        "Commands:\n"
        "/notices - latest notices\n"
        "/notices ptu | gndec - filter\n"
        "/faq - browse FAQs\n\n"
        "Alert commands:\n"
        "/alert_add <keyword> <channel> <source?>\n"
        "/myalerts\n"
        "/delalert <id>"
    )


# ---------------- NOTICES ----------------
def notices(update: Update, context: CallbackContext):
    try:
        args = context.args or []
        source_arg = args[0].strip().upper() if args else None

        if source_arg:
            data = run_query(
                "SELECT title, link, date, source FROM notices WHERE UPPER(source)=%s ORDER BY date DESC LIMIT 5",
                (source_arg,), fetch=True
            )
            if not data:
                update.message.reply_text(f"No notices found for {source_arg}.")
                return

            msg = f"<b>📢 Latest {source_arg} Notices:</b>\n\n"
            for row in data:
                msg += f"🔹 <a href='{row['link']}'>{row['title']}</a>\n{row['date']}\n\n"
            update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            return

        # All sources
        sources = run_query("SELECT DISTINCT source FROM notices", fetch=True) or []
        if not sources:
            update.message.reply_text("No notices found.")
            return

        msg = "<b>📢 Latest Notices (5 per source)</b>\n\n"
        for s in sources:
            src = s["source"]
            rows = run_query(
                "SELECT title, link, date FROM notices WHERE source=%s ORDER BY date DESC LIMIT 5",
                (src,), fetch=True
            ) or []

            msg += f"<b>[{src}]</b>\n"
            for r in rows:
                msg += f"• <a href='{r['link']}'>{r['title']}</a>\n  {r['date']}\n"
            msg += "\n"

        update.message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    except Exception as e:
        update.message.reply_text(f"Error: {e}")


# ---------------- FAQ SYSTEM ----------------
def faq(update: Update, context: CallbackContext):
    cats = run_query("SELECT id, name FROM faq_categories ORDER BY id", fetch=True)
    if not cats:
        update.message.reply_text("No FAQ categories.")
        return

    buttons = [[InlineKeyboardButton(c["name"], callback_data=f"cat_{c['id']}")] for c in cats]
    update.message.reply_text("📚 Choose a category:", reply_markup=InlineKeyboardMarkup(buttons))


def faq_category(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    cat_id = int(query.data.split("_")[1])
    qs = run_query("SELECT id, question FROM faqs WHERE category_id=%s", (cat_id,), fetch=True)

    if not qs:
        query.edit_message_text("No questions.")
        return

    buttons = [[InlineKeyboardButton(q["question"][:40], callback_data=f"faq_{q['id']}")] for q in qs]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="faq_back")])
    query.edit_message_text("📝 Select a question:", reply_markup=InlineKeyboardMarkup(buttons))


def faq_answer(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    faq_id = int(query.data.split("_")[1])
    rows = run_query("SELECT question, answer, category_id FROM faqs WHERE id=%s", (faq_id,), fetch=True)

    if not rows:
        query.edit_message_text("Answer not found.")
        return

    q = rows[0]
    buttons = [[InlineKeyboardButton("🔙 Back", callback_data=f"cat_{q['category_id']}")]]

    query.edit_message_text(
        f"*{q['question']}*\n\n{q['answer']}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


def faq_back(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    faq(update, context)


# ---------------- ALERT COMMANDS ----------------
def alert_add(update: Update, context: CallbackContext):
    args = context.args or []
    if len(args) < 2:
        update.message.reply_text("Usage: /alert_add <keyword> <channel> <source?>")
        return

    keyword = args[0]
    channel = args[1].lower()
    source = args[2].upper() if len(args) >= 3 else None

    if channel not in ("telegram", "whatsapp"):
        update.message.reply_text("Channel must be telegram OR whatsapp.")
        return

    user_id = str(update.effective_chat.id)
    try:
        run_query(
            "INSERT INTO alerts (user_identifier, channel, keyword, source) VALUES (%s,%s,%s,%s)",
            (user_id, channel, keyword, source)
        )
        update.message.reply_text("✅ Alert created!")
    except Exception as e:
        update.message.reply_text(f"Error: {e}")

def myalerts(update: Update, context: CallbackContext):
    user_id = str(update.effective_chat.id)
    rows = run_query(
        "SELECT id, keyword, channel, source, frequency FROM alerts WHERE user_identifier=%s",
        (user_id,), fetch=True
    )

    if not rows:
        update.message.reply_text("No alerts yet.")
        return

    # Build a plain-text message (no markdown or html) to avoid parse errors
    text_lines = ["🔔 Your Alerts:\n"]
    keyboard = []

    for r in rows:
        rid = r["id"]
        kw = r.get("keyword") or "—"
        ch = r.get("channel") or "—"
        src = r.get("source") or "ANY"
        freq = r.get("frequency") or "immediate"
        text_lines.append(f"ID {rid} — [{ch}] '{kw}' from {src} ({freq})")
        keyboard.append([InlineKeyboardButton(f"Delete {rid}", callback_data=f"del_{rid}")])

    text = "\n".join(text_lines)

    # send as plain text (no parse_mode) to avoid "Can't parse entities"
    update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))



def delalert(update: Update, context: CallbackContext):
    args = context.args or []
    if not args:
        update.message.reply_text("Usage: /delalert <id>")
        return

    try:
        aid = int(args[0])
    except:
        update.message.reply_text("Invalid ID.")
        return

    user_id = str(update.effective_chat.id)
    run_query("DELETE FROM alerts WHERE id=%s AND user_identifier=%s", (aid, user_id))
    update.message.reply_text(f"Deleted alert {aid}.")


def alert_inline_delete(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    try:
        aid = int(query.data.split("_")[1])
    except:
        query.edit_message_text("Error.")
        return

    user_id = str(query.from_user.id)
    run_query("DELETE FROM alerts WHERE id=%s AND user_identifier=%s", (aid, user_id))

    query.edit_message_text(f"Deleted alert {aid}.")


def gemini_fallback(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    # ignore commands
    if text.startswith("/"):
        return

    try:
        if not gemini_model:
            update.message.reply_text("AI service not available.")
            return

        prompt = f"""
        You are a polite campus assistant.

        Rules:
        - Short and clear answers
        - English preferred, Hindi allowed
        - No markdown or emojis
        - Max 5 lines

        User question: "{text}"
        """

        response = gemini_model.generate_content(prompt)
        reply = response.text.strip()

        # safety cleanup
        reply = reply.replace("*", "").replace("_", "").replace("#", "")

        if len(reply) > 3500:
            reply = reply[:3500] + "..."

        update.message.reply_text(reply)

    except Exception as e:
        print("Gemini error:", e)
        update.message.reply_text("Sorry, I couldn't understand that right now.")


# ---------------- MAIN ----------------
def main():
    if not BOT_TOKEN:
        print("TELEGRAM_TOKEN missing in .env")
        return

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("notices", notices))
    dp.add_handler(CommandHandler("faq", faq))

    # alert commands
    dp.add_handler(CommandHandler("alert_add", alert_add))
    dp.add_handler(CommandHandler("myalerts", myalerts))
    dp.add_handler(CommandHandler("delalert", delalert))

    # callbacks
    dp.add_handler(CallbackQueryHandler(faq_category, pattern="^cat_"))
    dp.add_handler(CallbackQueryHandler(faq_answer, pattern="^faq_"))
    dp.add_handler(CallbackQueryHandler(faq_back, pattern="^faq_back$"))
    dp.add_handler(CallbackQueryHandler(alert_inline_delete, pattern="^del_"))
    # Gemini fallback for normal text (VERY IMPORTANT)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, gemini_fallback))

    print("🚀 Telegram bot running (PTB v13 mode)...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
