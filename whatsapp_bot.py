import os
import sys
import json
import httpx
import pandas as pd
from flask import Flask, request, jsonify
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Load Secrets
load_dotenv()
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")  # Meta Cloud API permanent token
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID")  # sender phone number id
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN")  # webhook verify string
WHATSAPP_CHANNEL_ID = os.environ.get("WHATSAPP_CHANNEL_ID")  # WhatsApp Channel id OR broadcast destination
DATABASE_URL = os.environ.get("DATABASE_URL")
GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_VERSION", "v21.0")

if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID or not DATABASE_URL:
    raise ValueError("Missing WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, or DATABASE_URL! Check your .env file.")

engine = create_engine(DATABASE_URL, connect_args={'connect_timeout': 5})
app = Flask(__name__)

GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


def fetch_data():
    try:
        return pd.read_sql_table('courses', engine)
    except Exception as e:
        print(f"DB Error: {e}")
        return pd.DataFrame()


def send_text(to, body):
    """Send a plain WhatsApp text message via Cloud API."""
    url = f"{GRAPH_BASE}/{WHATSAPP_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body, "preview_url": True},
    }
    r = httpx.post(url, headers=headers, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()


def format_welcome():
    return (
        "⚖️ *SILE CPD Course Finder* ⚖️\n\n"
        "I'm connected to the live AWS course database.\n\n"
        "*Commands:*\n"
        "🔹 free — list FREE courses\n"
        "🔹 search <keyword> — find topics (e.g. search ethics)\n"
        "🔹 stats — live analytics"
    )


def format_stats(df):
    if df.empty:
        return "⚠️ Error connecting to AWS Database."
    total = len(df)
    free = len(df[df['Is_Free'] == True]) if 'Is_Free' in df.columns else 0
    mec = len(df[df["MEC_Segment"].astype(str).str.contains("Yes", na=False, case=False)]) if "MEC_Segment" in df.columns else 0
    last = df['Last_Scraped'].iloc[0] if 'Last_Scraped' in df.columns else 'Unknown'
    return (
        "📊 *Live Course Analytics*\n\n"
        f"📚 Total Courses: {total}\n"
        f"🆓 Free Courses: {free}\n"
        f"🧠 MEC/Ethics: {mec}\n\n"
        f"🕒 Last Updated: {last}"
    )


def format_free(df):
    if df.empty:
        return "⚠️ Error connecting to AWS Database."
    free_df = df[df['Is_Free'] == True] if 'Is_Free' in df.columns else pd.DataFrame()
    if free_df.empty:
        return "No FREE courses currently available on SILE."
    out = "🎉 *Top 5 FREE Courses* 🎉\n\n"
    for _, row in free_df.head(5).iterrows():
        title = str(row['Title']).strip()
        points = str(row.get('Public_CPD_Points', 'N/A'))
        dates = str(row.get('Date', 'TBA'))
        link = str(row.get('External_Link', 'N/A'))
        out += f"🔹 *{title}*\n📅 {dates}  •  🎖 {points} pts\n"
        if link and link != 'N/A':
            out += f"🔗 {link}\n"
        out += "------------------------\n"
    return out


def format_search(df, keyword):
    if df.empty:
        return "⚠️ Error connecting to AWS Database."
    results = df[df['Title'].astype(str).str.contains(keyword, case=False, na=False)]
    if results.empty:
        return f"❌ No courses found matching '{keyword}'."
    out = f"🔍 *Top 5 Results for '{keyword}'* 🔍\n\n"
    for _, row in results.head(5).iterrows():
        title = str(row['Title']).strip()
        price = str(row.get('Price', 'N/A'))
        link = str(row.get('External_Link', 'N/A'))
        out += f"🔹 *{title}*\n💰 {price}\n"
        if link and link != 'N/A':
            out += f"🔗 {link}\n"
        out += "------------------------\n"
    return out


def handle_command(text):
    """Map an incoming WhatsApp text to a reply string."""
    t = (text or "").strip()
    low = t.lower()
    if low in ("start", "help", "hi", "hello", "/start", "/help"):
        return format_welcome()
    if low in ("stats", "/stats"):
        return format_stats(fetch_data())
    if low in ("free", "/free"):
        return format_free(fetch_data())
    if low.startswith("search") or low.startswith("/search"):
        keyword = t.split(maxsplit=1)[1].strip() if len(t.split(maxsplit=1)) > 1 else ""
        if not keyword:
            return "⚠️ Please provide a keyword. Example: search ethics"
        return format_search(fetch_data(), keyword)
    return "🤖 Unknown command. Send *help* to see options."


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta calls this once when you register the webhook URL."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return challenge, 200
    return "forbidden", 403


@app.route("/webhook", methods=["POST"])
def receive_webhook():
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []) or []:
                    if msg.get("type") != "text":
                        continue
                    sender = msg["from"]
                    body = msg.get("text", {}).get("body", "")
                    reply = handle_command(body)
                    send_text(sender, reply)
    except Exception as e:
        print(f"Webhook handling error: {e}")
    return jsonify({"status": "ok"}), 200


def latest_new_ids():
    """EventIDs added in the most recent scrape run, per scrape_runs audit table."""
    try:
        runs = pd.read_sql(
            "SELECT new_ids FROM scrape_runs WHERE status = 'success' ORDER BY finished_at DESC LIMIT 1",
            engine,
        )
        if runs.empty:
            return []
        return json.loads(runs.iloc[0]['new_ids'] or "[]")
    except Exception as e:
        print(f"latest_new_ids error: {e}")
        return []


def build_channel_digest(only_if_new=True):
    df = fetch_data()
    if df.empty:
        return None
    new_ids = set(str(x) for x in latest_new_ids())
    if only_if_new and not new_ids:
        return None
    total = len(df)
    free_df = df[df['Is_Free'] == True] if 'Is_Free' in df.columns else pd.DataFrame()
    last = df['Last_Scraped'].iloc[0] if 'Last_Scraped' in df.columns else 'Unknown'
    msg = (
        "📣 *SILE CPD Channel — Update*\n\n"
        f"📚 Total courses tracked: *{total}*\n"
        f"🆓 Free courses available: *{len(free_df)}*\n"
        f"🕒 Last updated: _{last}_\n\n"
    )
    if new_ids and 'EventID' in df.columns:
        new_df = df[df['EventID'].astype(str).isin(new_ids)]
        if not new_df.empty:
            msg += f"🆕 *{len(new_df)} New Course(s) Since Last Scrape:*\n"
            for _, row in new_df.head(8).iterrows():
                title = str(row['Title']).strip()
                dates = str(row.get('Date', 'TBA'))
                points = str(row.get('Public_CPD_Points', 'N/A'))
                price = str(row.get('Price', 'N/A'))
                link = str(row.get('External_Link', 'N/A'))
                msg += f"🔹 *{title}*\n   📅 {dates}  •  🎖 {points} pts  •  💰 {price}\n"
                if link and link != 'N/A':
                    msg += f"   🔗 {link}\n"
            msg += "\n"
    if not free_df.empty:
        msg += "🎉 *Top FREE Picks:*\n"
        for _, row in free_df.head(5).iterrows():
            title = str(row['Title']).strip()
            dates = str(row.get('Date', 'TBA'))
            points = str(row.get('Public_CPD_Points', 'N/A'))
            link = str(row.get('External_Link', 'N/A'))
            msg += f"🔹 *{title}*\n   📅 {dates}  •  🎖 {points} pts\n"
            if link and link != 'N/A':
                msg += f"   🔗 {link}\n"
        msg += "\n"
    msg += "💬 Reply *help* to this number for search and stats."
    return msg


def post_to_channel(text=None, force=False):
    """Post the digest to the configured WhatsApp Channel.

    Uses the Meta Cloud API. WHATSAPP_CHANNEL_ID is the channel/broadcast destination id.
    Note: programmatic posting to WhatsApp Channels requires Meta-approved access; otherwise
    point WHATSAPP_CHANNEL_ID at a broadcast list / single recipient as a fallback.
    Returns the API response on success, or None when skipped due to no new entries.
    """
    if not WHATSAPP_CHANNEL_ID:
        raise RuntimeError("WHATSAPP_CHANNEL_ID not set in environment.")
    if text is None:
        text = build_channel_digest(only_if_new=not force)
    if not text:
        print("WhatsApp channel post skipped: no new entries since last scrape.")
        return None
    return send_text(WHATSAPP_CHANNEL_ID, text)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "broadcast":
        force = "--force" in sys.argv
        result = post_to_channel(force=force)
        if result is None:
            print("ℹ️ Skipped: no new entries since last scrape.")
        else:
            print(f"✅ Digest posted to WhatsApp channel {WHATSAPP_CHANNEL_ID}")
    else:
        port = int(os.environ.get("PORT", 5000))
        print(f"⚖️ SILE WhatsApp Bot webhook listening on :{port}/webhook")
        app.run(host="0.0.0.0", port=port)
