"""
Main Flask backend for Campus Info Chatbot
- WhatsApp webhook (Twilio)
- Gemini AI fallback
- Simple APIs: get_faqs, get_notices, add_faq
- Alerts management API: create/list/delete
Notes:
- This file expects modules.database.run_query to handle DB operations.
- WhatsApp user identifiers are 'whatsapp:+<countrycode><number>'.
"""

import os
from flask import Flask, jsonify, request
from dotenv import load_dotenv
from twilio.twiml.messaging_response import MessagingResponse
import requests





# Load environment
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Database helper
from modules.database import run_query

# Gemini AI (optional - fallback)
try:
    import google.generativeai as genai
    GEMINI_KEY = os.getenv("GEMINI_API_KEY")
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
        try:
            model = genai.GenerativeModel("models/gemini-2.5-flash")
        except Exception:
            # fallback to generic model name if needed
            model = genai.GenerativeModel("gemini-pro")
    else:
        model = None
except Exception:
    genai = None
    model = None

# Flask app
app = Flask(__name__)
# ---------------- Telegram helper ----------------
def send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_BOT_TOKEN missing")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }

    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

# ---------------- Telegram buttons helper ----------------
def send_telegram_buttons(chat_id, text, buttons):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": buttons
        }
    }
    requests.post(url, json=payload)


# ---------------- Basic routes ----------------

@app.route("/")
def home():
    return "🎓 Campus Info Chatbot + Gemini AI + WhatsApp Integration is Running!"

# ---------------- Public helper endpoints ----------------
@app.route('/get_faqs', methods=['GET'])
def get_faqs():
    data = run_query("SELECT * FROM faqs", fetch=True)
    if not data:
        return jsonify({"message": "No FAQs found."})
    return jsonify(data)

@app.route('/get_notices', methods=['GET'])
def get_notices():
    data = run_query("SELECT * FROM notices ORDER BY date DESC LIMIT 10", fetch=True)
    if not data:
        return jsonify({"message": "No notices available yet."})
    return jsonify(data)

@app.route('/add_faq', methods=['POST'])
def add_faq():
    content = request.json or {}
    question = content.get('question')
    answer = content.get('answer')
    if not question or not answer:
        return jsonify({"error": "Missing question or answer"}), 400

    run_query("INSERT INTO faqs (question, answer) VALUES (%s, %s)", (question, answer))
    return jsonify({"message": "FAQ added successfully!"})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"reply": "Please send a valid message."})

    try:
        # Gemini AI
        if model:
            prompt = f"""
            You are a helpful campus assistant for college students.
            Answer clearly and politely.
            Avoid markdown.
            Keep it short.

            Student question: "{user_message}"
            """
            response = model.generate_content(prompt)
            reply = response.text.strip() if response and response.text else "I could not generate a response."
        else:
            reply = "AI service is currently not configured."

    except Exception as e:
        print("Gemini error:", e)
        reply = "AI is temporarily unavailable. Please try again later."

    # ✅ CHAT HISTORY LOGGING (NEW)
    try:
        run_query(
            """
            INSERT INTO chat_logs (user_identifier, source, user_message, bot_reply)
            VALUES (%s, %s, %s, %s)
            """,
            ("mobile_app", "flutter", user_message, reply)
        )
    except Exception as e:
        print("Chat log insert error:", e)

    return jsonify({"reply": reply})



# ---------------- Alerts API ----------------
@app.route("/alerts", methods=["POST"])
def create_alert():
    data = request.json or {}
    user = data.get("user_identifier")
    channel = (data.get("channel") or "").lower()
    keyword = data.get("keyword")
    source = data.get("source")
    frequency = data.get("frequency", "immediate")

    if not user or channel not in ("telegram", "whatsapp"):
        return jsonify({"error": "Missing or invalid (user_identifier/channel)"}), 400

    try:
        run_query(
            "INSERT INTO alerts (user_identifier, channel, keyword, source, frequency) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user, channel, keyword, source, frequency)
        )
        return jsonify({"message": "Alert created"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/alerts", methods=["GET"])
def get_alerts():
    user = request.args.get("user")
    if not user:
        return jsonify({"error": "Missing ?user="}), 400
    rows = run_query("SELECT * FROM alerts WHERE user_identifier=%s", (user,), fetch=True)
    return jsonify(rows or [])

@app.route("/alerts/<int:alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    run_query("DELETE FROM alerts WHERE id=%s", (alert_id,))
    return jsonify({"message":"Deleted alert", "id": alert_id})



# ---------------- Helper functions for WhatsApp parsing ----------------
def normalize_whitespace(s: str) -> str:
    return " ".join(s.split())

def parse_command_parts(raw_text: str):
    """
    Returns tuple (cmd_lower, parts_list_raw, parts_list_lower)
    where:
      - cmd_lower: entire lowercased trimmed text
      - parts_list_raw: split raw tokens (preserves original casing for keywords)
      - parts_list_lower: split lowercased tokens (for parsing)
    """
    raw = normalize_whitespace(raw_text or "")
    lower = raw.lower()
    parts_raw = raw.split()
    parts_lower = lower.split()
    return lower, parts_raw, parts_lower


# ---------------- WhatsApp Webhook (Twilio) ----------------
@app.route('/webhook', methods=['POST'])
def whatsapp_webhook():
    # raw text (preserve original), and a lowercased version for parsing
    incoming_raw = (request.values.get('Body') or "").strip()
    incoming_msg_lower, parts_raw, parts_lower = parse_command_parts(incoming_raw)
    from_number = request.values.get('From')  # Twilio format: 'whatsapp:+91...'
    resp = MessagingResponse()
    msg = resp.message()

    # Simple logging (server console)
    print(f"[Webhook] from={from_number} msg='{incoming_raw}'")

    # --- greetings / help ---
    if incoming_msg_lower in ('hi', 'hello', 'hey', 'hii', 'hello!'):
        msg.body(
            "Hello! I’m your Campus Info Chatbot.\n\n"
            "Commands you can use:\n"
            "• notices [source] — show latest university updates (e.g. 'notices', 'notices ptu')\n"
            "• faq — list top FAQs\n"
            "• faq <n> — show FAQ answer (e.g. 'faq 1')\n"
            "• alert add <keyword> [whatsapp] [source] — create an alert (example: alert add admit_card whatsapp GNDEC)\n"
            "• myalerts — list your alerts\n"
            "• delalert <id> — delete an alert by id\n"
            "• help — show this message"
        )
        return str(resp)

    if incoming_msg_lower == 'help':
        msg.body(
            "Help — commands:\n"
            "notices [source]\nfaq\nfaq <n>\nalert add <keyword> [whatsapp] [source]\nmyalerts\ndelalert <id>\n"
        )
        return str(resp)



# ---------------- NOTICES COMMAND ----------------
#               Supports:
#   notices        -> 2 PTU + 2 GNDEC (grouped)
#   notices ptu    -> 5 PTU
#   notices gndec  -> 5 GNDEC

    if parts_lower and parts_lower[0] == "notices":

        try:
            # ---------- CASE 1: notices ptu / notices gndec ----------
            if len(parts_lower) >= 2:
                source = parts_lower[1].upper()   # PTU / GNDEC

                rows = run_query(
                    """
                    SELECT title, link, date
                    FROM notices
                    WHERE source = %s
                    ORDER BY date DESC
                    LIMIT 5
                    """,
                    (source,),
                    fetch=True
                )

                if not rows:
                    msg.body(f"No notices found for {source}.")
                    return str(resp)

                reply = f"📢 Latest {source} Notices\n\n"

                for r in rows:
                    reply += (
                        f"🔹 {r.get('title','Untitled')}\n"
                        f"🔗 {r.get('link','')}\n"
                        f"🗓 {r.get('date','')}\n\n"
                    )

                msg.body(reply)
                return str(resp)

            # ---------- CASE 2: notices (5 per source) ----------
            ptu_rows = run_query(
                """
                SELECT title, link, date
                FROM notices
                WHERE source = 'PTU'
                ORDER BY date DESC
                LIMIT 2
                """,
                fetch=True
            ) or []

            gndec_rows = run_query(
                """
                SELECT title, link, date
                FROM notices
                WHERE source = 'GNDEC'
                ORDER BY date DESC
                LIMIT 2
                """,
                fetch=True
            ) or []

            if not ptu_rows and not gndec_rows:
                msg.body("No recent notices found.")
                return str(resp)

            reply = "📢 Latest Notices (2 per source)\n\n"

            # PTU section
            if ptu_rows:
                reply += "📘 PTU\n\n"
                for r in ptu_rows:
                    reply += (
                        f"🔹 {r.get('title','Untitled')}\n"
                        f"🔗 {r.get('link','')}\n"
                        f"🗓 {r.get('date','')}\n\n"
                    )

            # GNDEC section
            if gndec_rows:
                reply += "📗 GNDEC\n\n"
                for r in gndec_rows:
                    reply += (
                        f"🔹 {r.get('title','Untitled')}\n"
                        f"🔗 {r.get('link','')}\n"
                        f"🗓 {r.get('date','')}\n\n"
                    )

            msg.body(reply)
            return str(resp)

        except Exception as e:
            print("❌ NOTICES ERROR:", e)
            msg.body("⚠️ Error fetching notices. Please try again later.")
            return str(resp)







    # --- faq list or specific faq ---
    if incoming_msg_lower == 'faq':
        data = run_query("SELECT question, answer FROM faqs LIMIT 5", fetch=True)
        if not data:
            msg.body("No FAQs available yet.")
            return str(resp)
        reply = "💬 Top FAQs:\n\n"
        for i, row in enumerate(data, 1):
            reply += f"{i}. {row['question']}\n"
        reply += "\nType 'faq 1' or 'faq 2' to see the answer."
        msg.body(reply)
        return str(resp)

    if incoming_msg_lower.startswith('faq '):
        try:
            parts = parts_lower
            if len(parts) == 2 and parts[1].isdigit():
                num = int(parts[1])
                data = run_query("SELECT question, answer FROM faqs LIMIT 5", fetch=True)
                if data and 1 <= num <= len(data):
                    q = data[num - 1]["question"]
                    a = data[num - 1]["answer"]
                    msg.body(f"❓ {q}\n\n✅ {a}")
                else:
                    msg.body("⚠️ Invalid FAQ number. Type 'faq' to see the list again.")
            else:
                msg.body("⚠️ Please type like 'faq 1' or 'faq 2'.")
        except Exception as e:
            print("Error in faq specific handler:", e)
            msg.body("⚠️ Error fetching FAQ. Try again later.")
        return str(resp)

    # --- alert add via WhatsApp ---
    # Support variants and flexible casing:
    # Examples:
    #   alert add admit_card whatsapp
    #   alert add admit card whatsapp GNDEC
    #   alert add admit_card (default channel=whatsapp)
    if incoming_msg_lower.startswith('alert add'):
        try:
            # use raw split to allow keyword with mixed case and spaces
            # parts_raw preserves the original tokens, parts_lower is for parsing control words
            partsR = parts_raw
            partsL = parts_lower

            # Need at least 3 tokens: ['alert', 'add', '<keyword>']
            if len(partsL) < 3:
                msg.body("Usage: alert add <keyword> [whatsapp] [source]\nExample: alert add admit_card whatsapp GNDEC")
                return str(resp)

            # keyword may be multiple tokens until we hit a known channel token (whatsapp) or end
            # find position of 'whatsapp' in parts_lower (if any)
            channel = "whatsapp"
            source = None

            if 'whatsapp' in partsL:
                idx = partsL.index('whatsapp')
                # keyword tokens are partsR[2:idx]
                keyword_tokens = partsR[2:idx]
                # if token after whatsapp exists, treat as source
                if len(partsR) > idx + 1:
                    source = partsR[idx + 1].strip()
            else:
                # no explicit channel token — assume whatsapp and everything after keyword is source if present
                # keyword = partsR[2]
                keyword_tokens = partsR[2:]
                # If more than 1 token, we may consider last token as source only if user provided 2+ tokens and wants source.
                # To keep it simple: if keyword_tokens length >=2, treat last token as source candidate only if it matches known source values (optional).
                # For now, if user provided >=2 tokens assume full tokens are keyword unless they explicitly wrote 'whatsapp' or provided a separate token for source.
                # So no source unless user included it explicitly after 'whatsapp'.
                pass

            keyword = " ".join(keyword_tokens).strip()
            if not keyword:
                msg.body("Invalid keyword. Usage: alert add <keyword> [whatsapp] [source]")
                return str(resp)

            user_ident = from_number  # Twilio gives 'whatsapp:+91...'
            # insert into DB
            run_query(
                "INSERT INTO alerts (user_identifier, channel, keyword, source) VALUES (%s,%s,%s,%s)",
                (user_ident, "whatsapp", keyword, source)
            )
            msg.body("✅ Alert saved. I'll notify you on this WhatsApp when relevant notices appear.")
        except Exception as e:
            print("Error inserting alert via WhatsApp:", e)
            msg.body(f"⚠️ Error saving alert: {e}")
        return str(resp)

    # --- myalerts (list alerts for this WhatsApp user) ---
    if incoming_msg_lower.startswith('myalerts'):
        try:
            rows = run_query(
                "SELECT id, keyword, channel, source, frequency, active FROM alerts WHERE user_identifier=%s",
                (from_number,), fetch=True
            ) or []
            if not rows:
                msg.body("You have no alerts set up. Use 'alert add <keyword> whatsapp' to create one.")
                return str(resp)

            reply = "🔔 Your Alerts:\n\n"
            for r in rows:
                aid = r.get("id")
                kw = r.get("keyword") or "—"
                ch = r.get("channel") or "—"
                src = r.get("source") or "ANY"
                freq = r.get("frequency") or "immediate"
                reply += f"ID {aid} — [{ch}] '{kw}' from {src} ({freq})\n"
            reply += "\nTo delete an alert, send: delalert <id>\nExample: delalert 3"
            msg.body(reply)
        except Exception as e:
            print("Error fetching myalerts:", e)
            msg.body("⚠️ Error fetching your alerts. Try again later.")
        return str(resp)

    # --- delalert (delete an alert by id) ---
    if incoming_msg_lower.startswith('delalert'):
        partsL = parts_lower
        if len(partsL) < 2:
            msg.body("Usage: delalert <id>\nExample: delalert 3")
            return str(resp)
        try:
            aid = int(partsL[1])
        except Exception:
            msg.body("Invalid id. It must be a number. Example: delalert 3")
            return str(resp)

        try:
            # verify ownership
            owner = run_query("SELECT id FROM alerts WHERE id=%s AND user_identifier=%s", (aid, from_number), fetch=True)
            if not owner:
                msg.body("Alert not found or you don't have permission to delete it.")
                return str(resp)

            run_query("DELETE FROM alerts WHERE id=%s", (aid,))
            msg.body(f"✅ Deleted alert {aid}.")
        except Exception as e:
            print("Error deleting alert via WhatsApp:", e)
            msg.body("⚠️ Error deleting alert. Try again later.")
        return str(resp)

    # --- Fallback -> Gemini AI ---
    # If user message didn't match any command above, we pass it to the AI fallback (if configured).
    try:
        if model:
            refined_prompt = f"""
            You are a smart and polite campus assistant for college students.
            The student is messaging you over WhatsApp.

            Guidelines:
            - Reply in short, natural sentences (English preferred, Hindi allowed if needed).
            - Do NOT use asterisks (*), hashtags (#), or markdown formatting.
            - If the message is about academics, college rules, events, or campus life — answer factually.
            - If unrelated, gently redirect to helpful topics.
            - Keep replies under 5 lines for WhatsApp readability.

            User message: "{incoming_raw}"
            """
            response = model.generate_content(refined_prompt)
            ai_reply = response.text.strip() if response and response.text else "I'm not sure, please try again."
        else:
            ai_reply = "AI not configured. Please try again later."

        # Clean formatting artifacts
        clean_reply = ai_reply.replace("*", "").replace("_", "").replace("#", "").strip()

        # Trim if message too long
        if len(clean_reply) > 1500:
            clean_reply = clean_reply[:1500] + "..."

        msg.body(clean_reply)
    except Exception as e:
        print("AI fallback error:", e)
        msg.body("⚠️ Sorry, AI seems busy right now. Please try again later.")
    return str(resp)

# ---------------- Telegram Webhook ----------------
@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json() or {}

    try:
        # --------------------------------
        # 1) FIRST HANDLE CALLBACK QUERY
        # --------------------------------
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            data = cq["data"]
# ---------------- TELEGRAM ALERT DELETE (INLINE) ----------------
            if data.startswith("delalert_"):
                alert_id = int(data.split("_")[1])

                try:
                    run_query(
                        """
                        DELETE FROM alerts
                        WHERE id=%s AND user_identifier=%s AND channel='telegram'
                        """,
                        (alert_id, str(chat_id))
                    )
                    send_telegram_message(chat_id, f"✅ Alert {alert_id} deleted.")
                except Exception as e:
                    print("Telegram alert delete error:", e)
                    send_telegram_message(chat_id, "⚠️ Failed to delete alert.")

                return "OK", 200
# ---------------- TELEGRAM FAQ HANDLER (INLINE) ----------------
            # Category selected
            if data.startswith("cat_"):
                cat_id = int(data.split("_")[1])
                qs = run_query(
                    "SELECT id, question FROM faqs WHERE category_id=%s",
                    (cat_id,),
                    fetch=True
                ) or []

                if not qs:
                    send_telegram_message(chat_id, "No questions in this category.")
                    return "OK", 200

                buttons = [
                    [{"text": q["question"][:40], "callback_data": f"faq_{q['id']}"}]
                    for q in qs
                ]
                buttons.append([{"text": "🔙 Back", "callback_data": "faq_back"}])

                send_telegram_buttons(chat_id, "📝 Select a question:", buttons)
                return "OK", 200

            # Question selected
            if data.startswith("faq_"):
                faq_id = int(data.split("_")[1])
                rows = run_query(
                    "SELECT question, answer, category_id FROM faqs WHERE id=%s",
                    (faq_id,),
                    fetch=True
                ) or []

                if not rows:
                    send_telegram_message(chat_id, "Answer not found.")
                    return "OK", 200

                q = rows[0]
                buttons = [[{"text": "🔙 Back", "callback_data": f"cat_{q['category_id']}"}]]

                send_telegram_buttons(
                    chat_id,
                    f"❓ {q['question']}\n\n✅ {q['answer']}",
                    buttons
                )
                return "OK", 200

            # Back
            if data == "faq_back":
                cats = run_query("SELECT id, name FROM faq_categories ORDER BY id", fetch=True) or []
                buttons = [
                    [{"text": c["name"], "callback_data": f"cat_{c['id']}"}]
                    for c in cats
                ]
                send_telegram_buttons(chat_id, "📚 Choose a category:", buttons)
                return "OK", 200

        # --------------------------------
        # 2) NOW HANDLE NORMAL TEXT MESSAGE
        # --------------------------------
        message = update.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip().lower()

        if not chat_id:
            return "OK", 200

        # Start
        if text in ("/start", "hi", "hello", "start"):
            reply = (
                "👋 Hello! I am the Campus Info Chatbot.\n\n"
                "Commands:\n"
                "• faq\n"
                "• notices\n"
                "• notices ptu\n"
                "• notices gndec\n"
                "• alert add <keyword> [source]\n"
                "• myalerts\n"
                "• delalert <id>\n"

            )
            send_telegram_message(chat_id, reply)
            return "OK", 200

        # FAQ
        if text == "faq":
            cats = run_query("SELECT id, name FROM faq_categories ORDER BY id", fetch=True) or []

            if not cats:
                send_telegram_message(chat_id, "No FAQ categories available.")
                return "OK", 200

            buttons = [
                [{"text": c["name"], "callback_data": f"cat_{c['id']}"}] for c in cats
            ]

            send_telegram_buttons(chat_id, "📚 Choose a category:", buttons)
            return "OK", 200

        # Notices
        text = text.strip()
        if text.lower().startswith("notices"):
            parts = text.split()
            source = None

            if len(parts) == 2:
                source = parts[1].upper()

            if source:
                rows = run_query(
                    "SELECT title, link, date FROM notices WHERE source=%s ORDER BY date DESC LIMIT 5",
                    (source,),
                    fetch=True
                )

                if not rows:
                    send_telegram_message(chat_id, f"No notices found for {source}.")
                    return "OK", 200

                reply = f"📢 Latest {source} Notices:\n\n"
                for r in rows:
                    reply += f"- {r['title']}\n{r['link']}\n{r['date']}\n\n"

                send_telegram_message(chat_id, reply)
                return "OK", 200

            # Default 2-2 notices
            ptu = run_query(
                "SELECT title, link, date FROM notices WHERE source='PTU' ORDER BY date DESC LIMIT 2",
                fetch=True
            ) or []
            gndec = run_query(
                "SELECT title, link, date FROM notices WHERE source='GNDEC' ORDER BY date DESC LIMIT 2",
                fetch=True
            ) or []

            reply = "📢 Latest Notices\n\n"

            if ptu:
                reply += "📘 PTU\n"
                for r in ptu:
                    reply += f"- {r['title']}\n{r['link']}\n{r['date']}\n\n"

            if gndec:
                reply += "📗 GNDEC\n"
                for r in gndec:
                    reply += f"- {r['title']}\n{r['link']}\n{r['date']}\n\n"

            send_telegram_message(chat_id, reply)
            return "OK", 200
        # ---------------- TELEGRAM ALERT ADD ----------------
        if text.startswith("alert add"):
            parts = text.split()

            if len(parts) < 3:
                send_telegram_message(
                    chat_id,
                    "Usage:\nalert add <keyword> [source]\nExample:\nalert add admit_card GNDEC"
                )
                return "OK", 200

            keyword = parts[2]
            source = parts[3].upper() if len(parts) >= 4 else None

            try:
                run_query(
                    """
                    INSERT INTO alerts (user_identifier, channel, keyword, source, frequency)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (str(chat_id), "telegram", keyword, source, "immediate")
                )
                send_telegram_message(
                    chat_id,
                    f"✅ Alert created!\nKeyword: {keyword}\nSource: {source or 'ANY'}"
                )
            except Exception as e:
                print("Telegram alert add error:", e)
                send_telegram_message(chat_id, "⚠️ Failed to create alert.")

            return "OK", 200


        # ---------------- TELEGRAM MY ALERTS ----------------
        if text == "myalerts":
            rows = run_query(
                """
                SELECT id, keyword, source
                FROM alerts
                WHERE user_identifier=%s AND channel='telegram'
                """,
                (str(chat_id),),
                fetch=True
            ) or []

            if not rows:
                send_telegram_message(chat_id, "You have no alerts.")
                return "OK", 200

            reply = "🔔 Your Alerts:\n\n"
            buttons = []

            for r in rows:
                reply += f"ID {r['id']} — '{r['keyword']}' ({r['source'] or 'ANY'})\n"
                buttons.append([
                    {"text": f"❌ Delete {r['id']}", "callback_data": f"delalert_{r['id']}"}
                ])

            send_telegram_buttons(chat_id, reply, buttons)
            return "OK", 200

        # fallback
        send_telegram_message(chat_id, "Please type: faq / notices")
        return "OK", 200

    except Exception as e:
        print("Telegram webhook error:", e)
        return "OK", 200




# ---------------- Run ----------------
if __name__ == '__main__':
    # Note: for production, use a proper WSGI server (gunicorn / waitress)
    app.run(host="0.0.0.0", port=5000, debug=True)
