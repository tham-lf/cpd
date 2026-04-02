import os
import telebot
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Load Secrets
load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

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
        link = str(row['External_Link']) if row['External_Link'] != 'N/A' else 'Check SILE directly'
        
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
        link = str(row.get('External_Link', 'N/A'))
        
        response += f"🔹 *{title}*\n"
        response += f"💰 Price: {price}\n"
        if link != 'N/A':
            response += f"🔗 Link: [View Details]({link})\n"
        response += "------------------------\n"
        
    bot.reply_to(message, response, parse_mode="Markdown", disable_web_page_preview=True)

print("⚖️ SILE Telegram Bot is running! Waiting for messages...")
bot.infinity_polling()
