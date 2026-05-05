import asyncio
import json
import pandas as pd
import re
import os
from datetime import datetime
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from pdf_utils import download_pdf_content, extract_text_from_pdf
from ai_utils import (
    get_ai_metadata,
    get_ai_metadata_from_pdf,
    get_ai_metadata_from_screenshot,
    get_ai_description,
)

# Configuration
BASE_URL = "https://www.silecpdcentre.sg"
CALAS_URL = f"{BASE_URL}/calas/"
KEYWORDS_FREE = ["free", "complimentary", "no charge", "s$0", "0.00", "nil"]

# Cap on vision-model escalations per scrape run (cost guard).
# Each escalation calls gpt-4o with PDF/image input (~$0.005-0.02). 30 ≈ <$0.60/run.
MAX_VISION_ESCALATIONS = int(os.getenv("MAX_VISION_ESCALATIONS_PER_RUN", "30"))
_escalation_count = 0


def _needs_escalation(ai_data):
    """Heuristic: should we re-call with PDF/screenshot input?"""
    if not ai_data:
        return True
    prices = ai_data.get("Prices")
    if prices in (None, "", "Unknown", "unknown"):
        return True
    if ai_data.get("Min_Price") in (None, "", "null") and not ai_data.get("Is_Free"):
        return True
    return False


def _can_escalate():
    return _escalation_count < MAX_VISION_ESCALATIONS

async def get_event_ids(page):
    """Extract all event IDs from the main CALAS table."""
    print(f"Navigating to {CALAS_URL}...")
    # Use load instead of networkidle as it's more reliable for some sites
    await page.goto(CALAS_URL, wait_until="load", timeout=60000)
    
    # Wait specifically for the table container or ANY table
    print("Waiting for content to load...")
    await page.wait_for_timeout(10000) # Give it 10 seconds to render everything
    
    html = await page.content()
    soup = BeautifulSoup(html, 'html.parser')
    
    # Debug: List all table IDs
    tables = soup.find_all('table')
    print(f"Found {len(tables)} tables on the page.")
    for i, t in enumerate(tables):
        print(f"Table {i} ID: {t.get('id', 'No ID')}, Class: {t.get('class', 'No Class')}")
    
    # Search for EventDetails links globally in the HTML first
    links = soup.find_all('a', href=re.compile(r"EventDetails"))
    event_ids = []
    for link in links:
        match = re.search(r"EventID=(\d+)", link['href'])
        if match:
            event_ids.append(match.group(1))
    
    event_ids = list(set(event_ids))
    
    if not event_ids:
        print("No event IDs found via <a> tags. Checking for event IDs in the entire HTML text...")
        # Emergency backup: regex the whole HTML
        found = re.findall(r"EventID=(\d+)", html)
        event_ids = list(set(found))
                
    return event_ids

def calculate_duration(from_str, to_str):
    """Calculate duration in hours between two time strings."""
    try:
        # Example format: 'Wednesday 13 May 2026 - 04:30 PM'
        # Or sometimes: '25 Mar 2024 09:30 AM' (old format)
        fmt1 = "%A %d %b %Y - %I:%M %p"
        fmt2 = "%d %b %Y %I:%M %p"
        
        start = None
        for fmt in [fmt1, fmt2]:
            try:
                start = datetime.strptime(from_str.strip(), fmt)
                end = datetime.strptime(to_str.strip(), fmt)
                break
            except ValueError:
                continue
        
        if not start:
            return "N/A"
            
        duration = (end - start).total_seconds() / 3600
        return round(duration, 2)
    except Exception:
        return "N/A"

async def scrape_event_details(page, event_id):
    """Scrape details for a specific event with AI/PDF integration."""
    url = f"{BASE_URL}/EventDetails/?EventID={event_id}"
    await page.goto(url)
    
    # Wait for the basics to load
    try:
        await page.wait_for_selector("#ContentPlaceHolder1_lblEventTitle", timeout=20000)
    except:
        pass
    
    html = await page.content()
    soup = BeautifulSoup(html, 'html.parser')
    
    def find_data(suffix, label_text=None):
        el = soup.find(id=re.compile(f".*{suffix}$"))
        if el and el.get_text(strip=True):
            cleaned = el.get_text(strip=True)
            if cleaned.lower() != "n/a":
                return cleaned
        if label_text:
            all_labels = ["Event Title", "Organiser", "From", "To", "Venue", "Practice Area", "MEC segment", "Training Level", "Public CPD Points", "Event Outline"]
            label_node = soup.find(["em", "span", "strong"], string=re.compile(f"^{label_text}", re.I))
            if not label_node:
                label_node = soup.find(string=re.compile(f"^{label_text}", re.I))
            if label_node:
                next_el = label_node.find_next_sibling(["span", "div"])
                if next_el and next_el.get_text(strip=True):
                    return next_el.get_text(strip=True)
                parent = label_node.find_parent()
                if parent:
                    text = parent.get_text(" ", strip=True)
                    pattern = f"{label_text}\\s*[:\\-]?\\s*(.*?)(?:\\s*(?:{'|'.join(all_labels)})|$)"
                    match = re.search(pattern, text, re.I | re.DOTALL)
                    if match:
                        return match.group(1).strip(": ").strip()
        return "N/A"

    # Base SILE metadata
    data = {
        "EventID": event_id,
        "Title": find_data("lblEventTitle", "Event Title"),
        "Organiser": find_data("lblOrganiser", "Organiser"),
        "Public_CPD_Points": find_data("lblPublicCPDPoints", "Public CPD Points"),
        "MEC_Segment": find_data("lblMECSegment", "MEC segment"),
        "Date": find_data("lblEventFrom", "From"),
        "From": find_data("lblEventFrom", "From"),
        "To": find_data("lblEventTo", "To"),
        "External_Link": soup.find(id=re.compile(r".*hypEventLink$")).get('href', 'N/A') if soup.find(id=re.compile(r".*hypEventLink$")) else "N/A",
        "Attachment_Link": soup.find(id=re.compile(r".*hypEventAttachment$")).get('href', 'N/A') if soup.find(id=re.compile(r".*hypEventAttachment$")) else "N/A"
    }
    
    # Overwrite Organiser if it's "See Event Outline"
    if "see event outline" in data["Organiser"].lower() and data["External_Link"] != "N/A":
        try:
            domain = urlparse(data["External_Link"]).netloc
            if domain.startswith("www."):
                domain = domain[4:]
            elif domain.startswith("www2."):
                domain = domain[5:]
            if domain:
                data["Organiser"] = domain.capitalize()
        except Exception:
            pass
    
    # 2. Extract Data from Tiers (Web & PDF)
    ext_text = ""
    pdf_text = ""
    pdf_bytes_for_escalation = None  # First downloaded PDF — used if AI text result is uncertain
    ext_screenshot_bytes = None      # Full-page screenshot of provider site — fallback escalation

    # Handle Attachment
    if data["Attachment_Link"] != "N/A":
        # Handle relative links
        pdf_url = data["Attachment_Link"]
        if not pdf_url.startswith("http"):
            pdf_url = f"{BASE_URL}/{pdf_url.lstrip('/')}"

        content = await download_pdf_content(pdf_url)
        pdf_text = extract_text_from_pdf(content)
        if content and pdf_bytes_for_escalation is None:
            pdf_bytes_for_escalation = content
        print(f"[{event_id}] Extracted {len(pdf_text)} chars from attachment PDF.")

    # Handle External Link
    ext_link = data["External_Link"]
    if ext_link != "N/A":
        if ext_link.lower().endswith(".pdf"):
            content = await download_pdf_content(ext_link)
            ext_pdf_text = extract_text_from_pdf(content)
            pdf_text += "\n" + ext_pdf_text
            if content and pdf_bytes_for_escalation is None:
                pdf_bytes_for_escalation = content
            print(f"[{event_id}] Extracted {len(ext_pdf_text)} chars from direct PDF link.")
        else:
            try:
                # Scrape text from provider website
                await page.goto(ext_link, wait_until="domcontentloaded", timeout=15000)
                ext_text = await page.inner_text("body")
                print(f"[{event_id}] Extracted {len(ext_text)} chars from external website.")
                # Capture a full-page screenshot for the vision-tier escalation path.
                try:
                    ext_screenshot_bytes = await page.screenshot(full_page=True, timeout=10000)
                except Exception as se:
                    print(f"[{event_id}] Screenshot capture failed (escalation will skip): {se}")
                    ext_screenshot_bytes = None
                
                # Scan for nested brochures and custom provider logic
                from urllib.parse import urljoin
                links_html = await page.locator("a").element_handles()
                
                reg_url_to_visit = None
                
                for link_element in links_html:
                    href = await link_element.get_attribute("href")
                    if href:
                        text = await link_element.inner_text()
                        
                        if "legalbusinessonline" in ext_link.lower() and "register" in text.lower():
                            reg_url_to_visit = urljoin(ext_link, href)
                            
                        if "brochure" in text.lower() or "download" in text.lower() or href.lower().endswith(".pdf"):
                            full_url = urljoin(ext_link, href)
                            try:
                                content = await download_pdf_content(full_url)
                                ext_pdf_text = extract_text_from_pdf(content)
                                pdf_text += "\n" + ext_pdf_text
                                if content and pdf_bytes_for_escalation is None:
                                    pdf_bytes_for_escalation = content
                                print(f"[{event_id}] Extracted {len(ext_pdf_text)} chars from nested brochure PDF: {full_url}")
                                break # Stop after finding the first valid brochure to save time/bandwidth
                            except Exception:
                                pass
                                
                if reg_url_to_visit:
                    try:
                        await page.goto(reg_url_to_visit, wait_until="domcontentloaded", timeout=15000)
                        reg_text = await page.inner_text("body")
                        ext_text += "\n--- REGISTRATION PAGE ---\n" + reg_text
                        ext_text += "\n*** SPECIAL OVERRIDE FOR LegalBusinessOnline: If the registration page text above doesn't explicitly mention any fees or prices, you MUST classify this event as FREE (Is_Free: true, Prices: '$0'). Do NOT use the strict unknown fallback! ***"
                        print(f"[{event_id}] LegalBusinessOnline: Extracted registration page: {reg_url_to_visit}")
                    except Exception as e:
                        print(f"[{event_id}] LegalBusinessOnline: Failed to load register page: {e}")
                        
            except Exception as e:
                print(f"[{event_id}] Warning: External Link fetch failed: {e}")

    # 3. Tiered AI extraction:
    #    Tier 1 (cheap)   — text-only, gpt-4o-mini.
    #    Tier 2 (vision)  — escalate to gpt-4o with the raw PDF, if available.
    #    Tier 3 (vision)  — escalate to gpt-4o with a full-page screenshot of the provider site.
    #    The vision tier is gated by MAX_VISION_ESCALATIONS_PER_RUN (cost guard).
    global _escalation_count
    if ext_text or pdf_text:
        sile_summary = f"Title: {data['Title']}\nOrganiser: {data['Organiser']}\nPoints: {data['Public_CPD_Points']}\nMEC: {data['MEC_Segment']}\nDates: {data['From']} to {data['To']}"
        ai_data = await get_ai_metadata(sile_summary, ext_text, pdf_text)
        price_source = "text"

        if _needs_escalation(ai_data) and _can_escalate():
            if pdf_bytes_for_escalation:
                print(f"[{event_id}] Escalating to PDF-document tier (gpt-4o).")
                escalated = await get_ai_metadata_from_pdf(sile_summary, pdf_bytes_for_escalation)
                if escalated:
                    ai_data = escalated
                    price_source = "pdf-document"
                    _escalation_count += 1
            elif ext_screenshot_bytes:
                print(f"[{event_id}] Escalating to screenshot tier (gpt-4o).")
                escalated = await get_ai_metadata_from_screenshot(sile_summary, ext_screenshot_bytes)
                if escalated:
                    ai_data = escalated
                    price_source = "screenshot"
                    _escalation_count += 1

        if ai_data:
            min_price = ai_data.get("Min_Price")
            try:
                min_price_num = float(min_price) if min_price not in (None, "", "null") else None
            except (TypeError, ValueError):
                min_price_num = None

            data["Price"] = ai_data.get("Prices", "Unknown")
            data["Min_Price"] = min_price_num
            data["Is_Free"] = bool(ai_data.get("Is_Free", False))
            data["Price_Source"] = price_source
            data["Price_Reasoning"] = ai_data.get("Reasoning", "") or ""

            if data["Is_Free"]:
                data["Category"] = "Free"
            elif min_price_num is None:
                data["Category"] = "Paid (Check Link)"
            elif min_price_num < 100:
                data["Category"] = "Paid Under $100"
            else:
                data["Category"] = "Paid Over $100"

            if ai_data.get("Dates") and ai_data.get("Dates") != "N/A":
                data["Date"] = ai_data["Dates"]

            print(f"[{event_id}] AI({price_source}): Price='{data['Price']}', Min={min_price_num}, Free={data['Is_Free']}")

            # Rich description for the public landing page (cheap, gpt-4o-mini).
            try:
                desc_data = await get_ai_description(sile_summary, ext_text, pdf_text)
                if desc_data:
                    data["Description"] = desc_data.get("description", "") or ""
                    data["Key_Topics"] = desc_data.get("key_topics", []) or []
                    data["Target_Audience"] = desc_data.get("target_audience", "") or ""
            except Exception as de:
                print(f"[{event_id}] description generation failed: {de}")
        else:
            print(f"[{event_id}] AI fallback: all tiers failed.")
            data["Price"] = "Unknown"
            data["Min_Price"] = None
            data["Is_Free"] = False
            data["Price_Source"] = "ai-failed"
            data["Price_Reasoning"] = ""
            data["Category"] = "Paid (Check Link)"
    else:
        # Fallback when there is neither website text nor PDF text.
        if any(keyword in data["Title"].lower() for keyword in ["free", "complimentary"]):
            data["Is_Free"] = True
            data["Price"] = 0
            data["Min_Price"] = 0.0
            data["Price_Source"] = "title-fallback"
            data["Price_Reasoning"] = "Title contains 'free' or 'complimentary'"
            data["Category"] = "Free"
        else:
            data["Price"] = "Unknown"
            data["Min_Price"] = None
            data["Is_Free"] = False
            data["Price_Source"] = "no-source"
            data["Price_Reasoning"] = ""
            data["Category"] = "Paid (Check Link)"
            
    # Duration Calculation
    data["Duration_Hours"] = calculate_duration(data["From"], data["To"])
    
    return data

def _persist_with_diff(df, engine):
    """Upsert courses table preserving first_seen_at and manual description overrides.

    Returns (new_ids, updated_ids, removed_ids).
    """
    try:
        existing = pd.read_sql_table('courses', engine)
    except Exception:
        existing = pd.DataFrame(columns=df.columns)

    existing_ids = set(existing['EventID'].astype(str)) if 'EventID' in existing.columns else set()
    scraped_ids = set(df['EventID'].astype(str))
    new_ids = sorted(scraped_ids - existing_ids)
    updated_ids = sorted(scraped_ids & existing_ids)
    removed_ids = sorted(existing_ids - scraped_ids)

    if 'first_seen_at' in existing.columns:
        first_seen_map = dict(zip(existing['EventID'].astype(str), existing['first_seen_at']))
    else:
        first_seen_map = {}

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df['first_seen_at'] = df['EventID'].astype(str).map(lambda x: first_seen_map.get(x, now_str))

    # Preserve manual description overrides set via the admin console.
    # If description_overridden=True for an existing row, keep its existing description fields.
    if 'description_overridden' in existing.columns:
        override_rows = existing[existing['description_overridden'] == True]
        for _, ex_row in override_rows.iterrows():
            eid = str(ex_row.get('EventID'))
            mask = df['EventID'].astype(str) == eid
            if not mask.any():
                continue
            for col in ('Description', 'Key_Topics', 'Target_Audience'):
                if col in existing.columns:
                    df.loc[mask, col] = ex_row.get(col)
        df['description_overridden'] = df['EventID'].astype(str).isin(
            override_rows['EventID'].astype(str).tolist()
        )
    else:
        df['description_overridden'] = False

    # Make sure description fields are present even if AI returned nothing — keeps API shape stable.
    for col, default in (('Description', ''), ('Key_Topics', None), ('Target_Audience', '')):
        if col not in df.columns:
            df[col] = default

    df.to_sql('courses', engine, if_exists='replace', index=False)
    return new_ids, updated_ids, removed_ids


def _record_run(engine, started_at, status, error, new_ids, updated_ids, removed_ids):
    row = pd.DataFrame([{
        "started_at": started_at,
        "finished_at": datetime.now(),
        "status": status,
        "error": error,
        "new_ids": json.dumps(new_ids),
        "updated_ids": json.dumps(updated_ids),
        "removed_ids": json.dumps(removed_ids),
        "new_count": len(new_ids),
        "updated_count": len(updated_ids),
        "removed_count": len(removed_ids),
    }])
    row.to_sql('scrape_runs', engine, if_exists='append', index=False)


async def run_scraper(progress_callback=None, limit=None):
    # Reset the per-run vision escalation counter so the cap applies per scrape, not per process.
    global _escalation_count
    _escalation_count = 0

    # Ensure browser binaries exist (crucial for Streamlit Cloud environments)
    import subprocess
    import sys
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
    
    started_at = datetime.now()
    run_status = "success"
    run_error = None
    run_new_ids = run_updated_ids = run_removed_ids = []
    df = pd.DataFrame()

    from dotenv import load_dotenv
    from sqlalchemy import create_engine
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    audit_engine = create_engine(db_url) if db_url else None

    async with async_playwright() as p:
        # Use more realistic headers
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
        except Exception as e:
            try:
                import streamlit as st
                st.error(f"🔥 **RAW CHROMIUM CRASH LOG:**\n\n`{str(e)}`")
            except Exception:
                pass
            if audit_engine:
                _record_run(audit_engine, started_at, "failed", f"chromium launch: {e}", [], [], [])
            raise e
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        
        if progress_callback:
            progress_callback(0, 1, "Fetching event list from SILE CALAS...")
        else:
            print("Fetching event list from SILE CALAS...")
            
        try:
            event_ids = await get_event_ids(page)
        except Exception as e:
            msg = f"Failed to fetch event list: {e}"
            print(msg)
            await browser.close()
            return pd.DataFrame()

        if limit:
            event_ids = event_ids[:limit]

        total = len(event_ids)
        print(f"Found {total} events.")
        
        results = []
        for i, event_id in enumerate(event_ids):
            status = f"Scraping event {event_id} ({i+1}/{total})..."
            if progress_callback:
                progress_callback(i + 1, total, status)
            else:
                print(f"[{i+1}/{total}] {status}")
                
            try:
                details = await scrape_event_details(page, event_id)
                results.append(details)
            except Exception as e:
                print(f"Error scraping event {event_id}: {e}")
            
            # Politeness delay
            await asyncio.sleep(1)
        
        if not results:
            print("No results collected.")
            await browser.close()
            if audit_engine:
                _record_run(audit_engine, started_at, "failed", "No results collected", [], [], [])
            return pd.DataFrame()

        df = pd.DataFrame(results)
        df["Last_Scraped"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            if audit_engine:
                run_new_ids, run_updated_ids, run_removed_ids = _persist_with_diff(df, audit_engine)
                output_file = "PostgreSQL Database"
            else:
                output_file = "cpd_courses.csv"
                df.to_csv(output_file, index=False)
        except Exception as e:
            run_status = "failed"
            run_error = f"persist: {e}"
            print(f"Persist error: {e}")
            if audit_engine:
                _record_run(audit_engine, started_at, run_status, run_error, [], [], [])
            await browser.close()
            raise

        if audit_engine:
            _record_run(audit_engine, started_at, run_status, run_error, run_new_ids, run_updated_ids, run_removed_ids)

        # The public Next.js site reads directly from this Postgres via /api/courses
        # exposed by whatsapp_bot.py — no separate push step needed.

        print(
            f"\nScraping complete! Saved to {output_file}. "
            f"New: {len(run_new_ids)}, updated: {len(run_updated_ids)}, removed: {len(run_removed_ids)}"
        )

        await browser.close()
        return df

import sys
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit the number of events to scrape for testing")
    args = parser.parse_args()
    asyncio.run(run_scraper(limit=args.limit))
