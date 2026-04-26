import os
import pandas as pd
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("Missing Database URL! Check your .env file.")

app = Flask(__name__)
engine = create_engine(DATABASE_URL, connect_args={'connect_timeout': 5})


def fetch_data():
    try:
        return pd.read_sql_table('courses', engine)
    except Exception as e:
        print(f"DB Error: {e}")
        return pd.DataFrame()


def welcome_text():
    return (
        "*Welcome to the SILE CPD Course Finder Bot!*\n\n"
        "I am connected directly to your AWS Database to help you find Legal courses instantly.\n\n"
        "*Available Commands:*\n"
        "- /free - List all 100% Free courses\n"
        "- /search <keyword> - Find specific topics (e.g. /search ethics)\n"
        "- /stats - Get real-time dashboard analytics"
    )


def stats_text():
    df = fetch_data()
    if df.empty:
        return "Error connecting to AWS Database."

    total = len(df)
    free = len(df[df['Is_Free'] == True]) if 'Is_Free' in df.columns else 0
    mec = len(df[df["MEC_Segment"].astype(str).str.contains("Yes", na=False, case=False)]) if "MEC_Segment" in df.columns else 0
    last = df['Last_Scraped'].iloc[0] if 'Last_Scraped' in df.columns else 'Unknown'

    return (
        "*Live Course Analytics*\n\n"
        f"Total Courses Built: {total}\n"
        f"Totally Free Courses: {free}\n"
        f"MEC/Ethics Courses: {mec}\n\n"
        f"_Last Updated: {last}_"
    )


def free_text():
    df = fetch_data()
    if df.empty:
        return "Error connecting to AWS Database."

    free_df = df[df['Is_Free'] == True] if 'Is_Free' in df.columns else pd.DataFrame()
    if free_df.empty:
        return "No Free courses currently available on SILE."

    free_df = free_df.head(5)
    response = "*Top 5 Upcoming FREE Courses*\n\n"
    for _, row in free_df.iterrows():
        title = str(row['Title']).strip()
        points = str(row['Public_CPD_Points'])
        dates = str(row['Date'])
        link = str(row['External_Link']) if row['External_Link'] != 'N/A' else 'Check SILE directly'
        response += f"*{title}*\nDate: {dates}\nPoints: {points}\nLink: {link}\n------------------------\n"
    return response


def search_text(keyword):
    if not keyword:
        return "Please provide a keyword. Example: /search ethics"

    df = fetch_data()
    if df.empty:
        return "Error connecting to AWS."

    results = df[df['Title'].astype(str).str.contains(keyword, case=False, na=False)]
    if results.empty:
        return f"No courses found matching '{keyword}'."

    results = results.head(5)
    response = f"*Top 5 Results for '{keyword}'*\n\n"
    for _, row in results.iterrows():
        title = str(row['Title']).strip()
        price = str(row['Price'])
        link = str(row.get('External_Link', 'N/A'))
        response += f"*{title}*\nPrice: {price}\n"
        if link != 'N/A':
            response += f"Link: {link}\n"
        response += "------------------------\n"
    return response


def route_message(body):
    text = (body or "").strip()
    lower = text.lower()

    if lower in ("/start", "/help", "hi", "hello", "start", "help"):
        return welcome_text()
    if lower == "/stats":
        return stats_text()
    if lower == "/free":
        return free_text()
    if lower.startswith("/search"):
        keyword = text[len("/search"):].strip()
        return search_text(keyword)
    return "Unknown command. Send /help to see what I can do."


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    incoming = request.values.get("Body", "")
    reply = route_message(incoming)

    twiml = MessagingResponse()
    twiml.message(reply)
    return str(twiml)


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("WHATSAPP_PORT", 5000))
    print(f"SILE WhatsApp Bot listening on :{port}/whatsapp")
    app.run(host="0.0.0.0", port=port)
