import os
import re
import json
import telebot
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Load Secrets
load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # e.g. @sile_cpd or -100123456789
TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID")  # numeric user id allowed to /broadcast
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://aithena-landing.vercel.app")
CLICK_TRACKER_URL = os.environ.get("CLICK_TRACKER_URL")  # e.g. https://api.aithena.sg/api/r — falls back to PUBLIC_BASE_URL/api/r


def _slugify(text):
    if not text:
        return ""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:80]


def link_for(row, source="telegram"):
    """Build a public landing-page URL for a course row.

    Points at the Next.js /cpd/{slug}-{event_id} description page (Aithena's domain).
    The user lands there, reads the AI-summarised description, and clicks the
    "Register" CTA — which routes through CLICK_TRACKER_URL/api/r/{event_id}
    for click-tracking before 302-ing to the provider.
    """
    slug = f"{_slugify(row.get('Title'))}-{row['EventID']}"
    return f"{PUBLIC_BASE_URL}/cpd/{slug}?utm_source={source}"

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise ValueError("Missing Telegram Token or Database URL! Check your .env file.")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
engine = create_engine(DATABASE_URL, connect_args={'connect_timeout': 5})

def fetch_data():
    """Pulls live course data securely from AWS"""
    try:
        return pd.read_sql_table('courses', engine)
    except Exception as e:
        print(f"DB Error: {e}")
        return pd.DataFrame()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "⚖️ *Welcome to the SILE CPD Course Finder Bot!* ⚖️\n\n"
        "I am connected directly to your AWS Database to help you find Legal courses instantly.\n\n"
        "👇 *Available Commands:*\n"
        "🔹 /free - List all 100% Free courses\n"
        "🔹 /search <keyword> - Find specific topics (e.g. /search ethics)\n"
        "🔹 /stats - Get real-time dashboard analytics"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")

@bot.message_handler(commands=['stats'])
def get_stats(message):
    df = fetch_data()
    if df.empty:
        bot.reply_to(message, "⚠️ Error connecting to AWS Database.")
        return
        
    total = len(df)
    free = len(df[df['Is_Free'] == True]) if 'Is_Free' in df.columns else 0
    mec = len(df[df["MEC_Segment"].astype(str).str.contains("Yes", na=False, case=False)]) if "MEC_Segment" in df.columns else 0
    
    stats_text = (
        "📊 *Live Course Analytics*\n\n"
        f"📚 *Total Courses Built:* {total}\n"
        f"🆓 *Totally Free Courses:* {free}\n"
        f"🧠 *MEC/Ethics Courses:* {mec}\n\n"
        f"🕒 _Last Updated: {df['Last_Scraped'].iloc[0] if 'Last_Scraped' in df.columns else 'Unknown'}_"
    )
    bot.reply_to(message, stats_text, parse_mode="Markdown")

@bot.message_handler(commands=['free'])
def list_free(message):
    df = fetch_data()
    if df.empty:
        bot.reply_to(message, "⚠️ Error connecting to AWS Database.")
        return
        
    free_df = df[df['Is_Free'] == True] if 'Is_Free' in df.columns else pd.DataFrame()
    
    if free_df.empty:
        bot.reply_to(message, "No Free courses currently available on SILE.")
        return
        
    free_df = free_df.head(5)
    
    response = "🎉 *Top 5 Upcoming FREE Courses* 🎉\n\n"
    for _, row in free_df.iterrows():
        title = str(row['Title']).strip()
        points = str(row['Public_CPD_Points'])
        dates = str(row['Date'])
        link = link_for(row, source="telegram_dm")

        response += f"🔹 *{title}*\n"
        response += f"📅 Date: {dates}\n"
        response += f"🎖 Points: {points}\n"
        response += f"🔗 Link: [Register Here]({link})\n"
        response += "------------------------\n"
        
    bot.reply_to(message, response, parse_mode="Markdown", disable_web_page_preview=True)

@bot.message_handler(commands=['search'])
def search_courses(message):
    command_text = message.text.replace("/search", "").strip()
    
    if not command_text:
        bot.reply_to(message, "⚠️ Please provide a keyword. Example: `/search ethics`", parse_mode="Markdown")
        return
        
    df = fetch_data()
    if df.empty:
        bot.reply_to(message, "⚠️ Error connecting to AWS.")
        return
        
    search_results = df[df['Title'].astype(str).str.contains(command_text, case=False, na=False)]
    
    if search_results.empty:
        bot.reply_to(message, f"❌ No courses found matching '{command_text}'.")
        return
        
    search_results = search_results.head(5)
    
    response = f"🔍 *Top 5 Results for '{command_text}'* 🔍\n\n"
    for _, row in search_results.iterrows():
        title = str(row['Title']).strip()
        price = str(row['Price'])
        link = link_for(row, source="telegram_dm")

        response += f"🔹 *{title}*\n"
        response += f"💰 Price: {price}\n"
        response += f"🔗 Link: [View Details]({link})\n"
        response += "------------------------\n"
        
    bot.reply_to(message, response, parse_mode="Markdown", disable_web_page_preview=True)

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
    """Build the broadcast message. If only_if_new is True and no new entries, return None (skip)."""
    df = fetch_data()
    if df.empty:
        return None

    new_ids = set(str(x) for x in latest_new_ids())
    if only_if_new and not new_ids:
        return None

    total = len(df)
    free_df = df[df['Is_Free'] == True] if 'Is_Free' in df.columns else pd.DataFrame()
    free_count = len(free_df)
    last_updated = df['Last_Scraped'].iloc[0] if 'Last_Scraped' in df.columns else 'Unknown'

    msg = (
        "📣 *SILE CPD Channel — Update*\n\n"
        f"📚 Total courses tracked: *{total}*\n"
        f"🆓 Free courses available: *{free_count}*\n"
        f"🕒 Last updated: _{last_updated}_\n\n"
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
                link = link_for(row, source="telegram_channel")
                msg += f"🔹 *{title}*\n"
                msg += f"   📅 {dates}  •  🎖 {points} pts  •  💰 {price}\n"
                msg += f"   🔗 [Details + Register]({link})\n"
            msg += "\n"

    if not free_df.empty:
        msg += "🎉 *Top FREE Picks:*\n"
        for _, row in free_df.head(5).iterrows():
            title = str(row['Title']).strip()
            dates = str(row.get('Date', 'TBA'))
            points = str(row.get('Public_CPD_Points', 'N/A'))
            link = link_for(row, source="telegram_channel")
            msg += f"🔹 *{title}*\n"
            msg += f"   📅 {dates}  •  🎖 {points} pts\n"
            msg += f"   🔗 [Details + Register]({link})\n"
        msg += "\n"

    msg += "💬 DM @" + (bot.get_me().username or "the bot") + " for /search and /stats."
    return msg

def post_to_channel(text=None, force=False):
    """Post a message to the configured Telegram channel.

    If text is None and force is False, posts only when there are new entries since the last scrape.
    Returns the API response on success, or None when skipped due to no new entries.
    """
    if not TELEGRAM_CHANNEL_ID:
        raise RuntimeError("TELEGRAM_CHANNEL_ID not set in environment.")
    if text is None:
        text = build_channel_digest(only_if_new=not force)
    if not text:
        print("Telegram channel post skipped: no new entries since last scrape.")
        return None
    return bot.send_message(
        TELEGRAM_CHANNEL_ID,
        text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

@bot.message_handler(commands=['broadcast'])
def broadcast_to_channel(message):
    """Admin-only: push the latest digest to the public channel."""
    if not TELEGRAM_ADMIN_ID or str(message.from_user.id) != str(TELEGRAM_ADMIN_ID):
        bot.reply_to(message, "🚫 Not authorized.")
        return
    if not TELEGRAM_CHANNEL_ID:
        bot.reply_to(message, "⚠️ TELEGRAM_CHANNEL_ID not configured.")
        return
    try:
        result = post_to_channel(force=True)
        if result is None:
            bot.reply_to(message, "ℹ️ No content to post (empty digest).")
        else:
            bot.reply_to(message, f"✅ Digest posted to {TELEGRAM_CHANNEL_ID}.")
    except Exception as e:
        bot.reply_to(message, f"❌ Failed to post: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "broadcast":
        # Headless mode: `python bot.py broadcast [--force]` — used by GitHub Actions after a scrape.
        force = "--force" in sys.argv
        result = post_to_channel(force=force)
        if result is None:
            print("ℹ️ Skipped: no new entries since last scrape.")
        else:
            print(f"✅ Digest posted to channel {TELEGRAM_CHANNEL_ID}")
    else:
        print("⚖️ SILE Telegram Bot is running! Waiting for messages...")
        bot.infinity_polling()
