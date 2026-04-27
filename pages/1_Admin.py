import os
import sys
import json
import asyncio
import subprocess
import pandas as pd
import streamlit as st
from datetime import datetime
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Inject Streamlit Cloud secrets into the OS env
try:
    for key, value in st.secrets.items():
        if isinstance(value, str):
            os.environ[key] = value
except Exception:
    pass

load_dotenv()

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

st.set_page_config(page_title="CPD Admin", page_icon="🛠️", layout="wide")
st.title("🛠️ CPD Admin Console")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    st.error("ADMIN_PASSWORD not set. Configure it in `.env` or Streamlit secrets.")
    st.stop()

if "admin_authed" not in st.session_state:
    st.session_state["admin_authed"] = False

if not st.session_state["admin_authed"]:
    pwd = st.text_input("Admin password", type="password")
    if st.button("Sign in"):
        if pwd == ADMIN_PASSWORD:
            st.session_state["admin_authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    st.error("DATABASE_URL not configured.")
    st.stop()
engine = create_engine(db_url, connect_args={'connect_timeout': 5})


def read_runs(limit=30):
    try:
        return pd.read_sql(
            f"SELECT * FROM scrape_runs ORDER BY finished_at DESC LIMIT {int(limit)}",
            engine,
        )
    except Exception as e:
        st.info(f"No scrape_runs table yet ({e}).")
        return pd.DataFrame()


def read_courses():
    try:
        return pd.read_sql_table("courses", engine)
    except Exception:
        return pd.DataFrame()


tab_run, tab_history, tab_new, tab_broadcast, tab_schedule = st.tabs(
    ["▶️ Run scrape", "📜 Run history", "🆕 New entries", "📢 Manual broadcast", "⏰ Schedule"]
)

with tab_run:
    st.subheader("Run scrape now")
    limit = st.number_input("Limit (events to scrape; 0 = all)", min_value=0, value=0, step=1)
    if st.button("🚀 Run scrape", type="primary"):
        from cpd_scraper import run_scraper
        progress = st.progress(0.0)
        status_box = st.empty()

        def cb(done, total, status):
            progress.progress(min(done / max(total, 1), 1.0))
            status_box.info(status)

        with st.spinner("Scraping..."):
            try:
                df = asyncio.run(run_scraper(progress_callback=cb, limit=limit or None))
                st.success(f"Done. {len(df)} events scraped.")
            except Exception as e:
                st.error(f"Scrape failed: {e}")

with tab_history:
    st.subheader("Last 30 scrape runs")
    runs = read_runs(30)
    if runs.empty:
        st.info("No runs recorded yet.")
    else:
        display = runs[["started_at", "finished_at", "status", "new_count", "updated_count", "removed_count", "error"]].copy()
        st.dataframe(display, use_container_width=True)

        st.metric("Last run new entries", int(runs.iloc[0]["new_count"]))
        with st.expander("Raw new_ids / updated_ids / removed_ids (latest run)"):
            st.json({
                "new_ids": json.loads(runs.iloc[0]["new_ids"] or "[]"),
                "updated_ids": json.loads(runs.iloc[0]["updated_ids"] or "[]"),
                "removed_ids": json.loads(runs.iloc[0]["removed_ids"] or "[]"),
            })

with tab_new:
    st.subheader("New entries from latest successful scrape")
    runs = read_runs(1)
    courses = read_courses()
    if runs.empty or courses.empty:
        st.info("No data to display yet.")
    else:
        new_ids = set(str(x) for x in json.loads(runs.iloc[0]["new_ids"] or "[]"))
        if not new_ids:
            st.success("No new entries in the most recent scrape.")
        else:
            new_df = courses[courses["EventID"].astype(str).isin(new_ids)]
            cols = [c for c in ["EventID", "Title", "Organiser", "Date", "Public_CPD_Points", "Price", "Is_Free", "External_Link"] if c in new_df.columns]
            st.dataframe(new_df[cols] if cols else new_df, use_container_width=True)

with tab_broadcast:
    st.subheader("Manual broadcast")
    st.caption("Force = post even when there are no new entries since the last scrape.")
    force = st.checkbox("Force post (ignore 'no new entries' guard)", value=False)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📣 Post to Telegram channel"):
            try:
                from bot import post_to_channel as tg_post
                result = tg_post(force=force)
                if result is None:
                    st.info("Skipped: no new entries since last scrape (untick Force to override).")
                else:
                    st.success("Posted to Telegram channel.")
            except Exception as e:
                st.error(f"Telegram post failed: {e}")

    with col2:
        if st.button("📣 Post to WhatsApp channel"):
            try:
                from whatsapp_bot import post_to_channel as wa_post
                result = wa_post(force=force)
                if result is None:
                    st.info("Skipped: no new entries since last scrape (untick Force to override).")
                else:
                    st.success("Posted to WhatsApp channel.")
            except Exception as e:
                st.error(f"WhatsApp post failed: {e}")

with tab_schedule:
    st.subheader("Schedule (staggered every 12h)")
    st.markdown(
        "Scraping is mirrored across **two CI providers** so SILE is polled twice/day. "
        "The diff-aware digest prevents double-broadcasts — the second runner sees the first "
        "runner's results and finds 0 new `EventID`s.\n\n"
        "| Provider | Cron (UTC) | Local (SGT) | Config |\n"
        "|---|---|---|---|\n"
        "| GitHub Actions | `0 18 * * *` | 02:00 | [`.github/workflows/scrape.yml`](.github/workflows/scrape.yml) |\n"
        "| GitLab CI/CD | `0 6 * * *` | 14:00 | [`.gitlab-ci.yml`](.gitlab-ci.yml) + Project → CI/CD → Schedules |\n\n"
        "**To change a schedule:** edit the workflow YAML (GitHub) or update the GitLab Schedule in the UI. "
        "To run off-schedule, use the *Run workflow* / *Play* button on the provider, or the **▶️ Run scrape** tab here."
    )
