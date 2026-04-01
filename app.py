import streamlit as st
import pandas as pd
import asyncio
import os
import sys
from cpd_scraper import run_scraper

# Fix for Windows asyncio NotImplementedError
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Set page config for wide layout and custom theme
st.set_page_config(
    page_title="Singapore CPD Tracker (AI Powered)",
    page_icon="⚖️",
    layout="wide"
)

# Load feedback data if exists
FEEDBACK_FILE = "cpd_feedback.csv"
def load_feedback():
    if os.path.exists(FEEDBACK_FILE):
        return pd.read_csv(FEEDBACK_FILE)
    return pd.DataFrame(columns=["EventID", "Status", "ManualPrice", "Notes"])

def save_feedback(event_id, status, manual_price=None, notes=""):
    df = load_feedback()
    # Remove existing feedback for this event if any
    df = df[df["EventID"] != event_id]
    new_row = pd.DataFrame([{"EventID": event_id, "Status": status, "ManualPrice": manual_price, "Notes": notes}])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(FEEDBACK_FILE, index=False)

st.title("⚖️ SILE CPD Course Finder")
st.markdown("""
Find free and low-cost CPD courses for Singapore Lawyers. 
This tool scrapes the [SILE CALAS](https://www.silecpdcentre.sg/calas/) list and categorizes courses by price.
""")

# Load existing data
CSV_FILE = "cpd_courses.csv"

def load_data():
    if os.path.exists(CSV_FILE):
        return pd.read_csv(CSV_FILE)
    return pd.DataFrame()

df = load_data()

# Scrape Button Logic
if st.button("🔄 Refresh / Start Scrape"):
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    def update_progress(current, total, text):
        progress_bar.progress(current / total)
        status_text.text(text)
    
    # Run the scraper
    with st.spinner("Scraping in progress... this may take several minutes."):
        df = asyncio.run(run_scraper(progress_callback=update_progress))
        st.success("Scraping complete!")
        st.rerun()

if os.path.exists("cpd_courses.csv"):
    df = pd.read_csv("cpd_courses.csv")
    feedback_df = load_feedback()
    
    # Merge with feedback
    df = df.merge(feedback_df, on="EventID", how="left")
    
    # --- VISUALLY PLEASING OVERVIEW STATS ---
    st.markdown("---")
    st.subheader("📊 Course Overview")
    
    total_courses = len(df)
    free_count = len(df[df["Is_Free"] == True]) if "Is_Free" in df.columns else 0
    mec_count = len(df[df["MEC_Segment"].astype(str).str.contains("Yes", na=False, case=False)]) if "MEC_Segment" in df.columns else 0
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Courses Analyzed", total_courses, delta=f"{len(df)} New" if total_courses > 0 else "")
    m2.metric("Free Courses Available", free_count, delta="Zero Cost" if free_count > 0 else "")
    m3.metric("Courses with Ethics Points", mec_count)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # --- FILTER SECTION ---
    with st.expander("🔍 Filter & Search Courses", expanded=True):
        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        with f_col1:
            categories = ["All"] + sorted(df["Category"].dropna().unique().tolist()) if "Category" in df.columns else ["All"]
            selected_cat = st.selectbox("Category / Price Tier", categories)
        with f_col2:
            organisers = ["All"] + sorted(df["Organiser"].dropna().unique().tolist()) if "Organiser" in df.columns else ["All"]
            selected_org = st.selectbox("Organiser", organisers)
        with f_col3:
            date_filter = st.text_input("Filter by Date", placeholder="e.g. Apr 2026")
        with f_col4:
            st.markdown("Toggle Options")
            show_only_free = st.checkbox("🟢 Show Only Free Courses")
            show_only_mec = st.checkbox("⚖️ Show Only MEC (Ethics)")

    # --- APPLY FILTERS ---
    filtered_df = df.copy()
    if selected_cat != "All":
        filtered_df = filtered_df[filtered_df["Category"] == selected_cat]
    if selected_org != "All":
        filtered_df = filtered_df[filtered_df["Organiser"] == selected_org]
    if show_only_free and "Is_Free" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Is_Free"] == True]
    if show_only_mec and "MEC_Segment" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["MEC_Segment"].astype(str).str.contains("Yes", na=False, case=False)]
    if date_filter and "Date" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Date"].astype(str).str.contains(date_filter, case=False, na=False) | 
                                  filtered_df["From"].astype(str).str.contains(date_filter, case=False, na=False)]
        
    st.subheader(f"📋 Available Courses ({len(filtered_df)})")
    
    # Manual Feedback Section
    with st.expander("📝 Provide Feedback / Manual Correction"):
        event_to_fix = st.selectbox("Select Event ID to correct", filtered_df["EventID"].tolist())
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            status = st.radio("Status", ["Verified Correct", "Report Wrong Price", "Needs Review"])
        with f_col2:
            new_price = st.text_input("Correct Price (if known)")
        with f_col3:
            notes = st.text_area("Notes")
        if st.button("Save Feedback"):
            save_feedback(event_to_fix, status, new_price, notes)
            st.success(f"Feedback saved for Event {event_to_fix}!")
            st.rerun()

    # Config table display
    column_config = {
        "External_Link": st.column_config.LinkColumn("Registration Link"),
        "Attachment_Link": st.column_config.LinkColumn("Brochure (PDF)"),
        "Price": st.column_config.TextColumn("Detected Prices"),
        "Status": st.column_config.TextColumn("Status (User Feedback)"),
        "ManualPrice": st.column_config.TextColumn("Manual Price Override")
    }
    
    st.dataframe(
        filtered_df,
        hide_index=True,
        column_config=column_config,
        use_container_width=True
    )
else:
    st.info("No data available. Click 'Start AI-Powered Scrape' to fetch current events.")

# Footer
st.divider()
st.caption("Note: Prices are estimated based on external landing pages. Always verify on the provider's official site.")
