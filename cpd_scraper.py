import asyncio
import pandas as pd
import re
import os
from datetime import datetime
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from pdf_utils import download_pdf_content, extract_text_from_pdf
from ai_utils import get_ai_metadata

# Configuration
BASE_URL = "https://www.silecpdcentre.sg"
CALAS_URL = f"{BASE_URL}/calas/"
KEYWORDS_FREE = ["free", "complimentary", "no charge", "s$0", "0.00", "nil"]

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

    
    # Handle Attachment
    if data["Attachment_Link"] != "N/A":
        # Handle relative links
        pdf_url = data["Attachment_Link"]
        if not pdf_url.startswith("http"):
            pdf_url = f"{BASE_URL}/{pdf_url.lstrip('/')}"
        
        content = await download_pdf_content(pdf_url)
        pdf_text = extract_text_from_pdf(content)
        print(f"[{event_id}] Extracted {len(pdf_text)} chars from attachment PDF.")

    # Handle External Link
    ext_link = data["External_Link"]
    if ext_link != "N/A":
        if ext_link.lower().endswith(".pdf"):
            content = await download_pdf_content(ext_link)
            ext_pdf_text = extract_text_from_pdf(content)
            pdf_text += "\n" + ext_pdf_text
            print(f"[{event_id}] Extracted {len(ext_pdf_text)} chars from direct PDF link.")
        else:
            try:
                # Scrape text from provider website
                await page.goto(ext_link, wait_until="domcontentloaded", timeout=15000)
                ext_text = await page.inner_text("body")
                print(f"[{event_id}] Extracted {len(ext_text)} chars from external website.")
                
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

    # 3. Use AI to Process and Reconcile
    if ext_text or pdf_text:
        sile_summary = f"Title: {data['Title']}\nOrganiser: {data['Organiser']}\nPoints: {data['Public_CPD_Points']}\nMEC: {data['MEC_Segment']}\nDates: {data['From']} to {data['To']}"
        ai_data = await get_ai_metadata(sile_summary, ext_text, pdf_text)
        
        if ai_data:
            # Reconcile AI results with SILE data
            data["Price"] = ai_data.get("Prices", "Unknown")
            data["Is_Free"] = ai_data.get("Is_Free", False)
            data["Category"] = "Free" if data["Is_Free"] else ("Paid Under $100" if ai_data.get("Min_Price", 999) < 100 else "Paid Over $100")
            
            # Use AI for Date/Venue if found
            if ai_data.get("Dates") and ai_data.get("Dates") != "N/A":
                data["Date"] = ai_data["Dates"]
            if ai_data.get("Venue") and ai_data.get("Venue") != "N/A":
                # We can add a Venue column later if needed
                pass
            
            print(f"[{event_id}] AI: Price='{data['Price']}', Free={data['Is_Free']}")
        else:
            print(f"[{event_id}] AI fallback: Price extraction failed.")
            data["Price"] = "Unknown"
            data["Category"] = "Paid (Check Link)"
    else:
        # Fallback for no external data
        if any(keyword in data["Title"].lower() for keyword in ["free", "complimentary"]):
            data["Is_Free"] = True
            data["Price"] = 0
            data["Category"] = "Free"
        else:
            data["Price"] = "Unknown"
            data["Category"] = "Paid (Check Link)"
            
    # Duration Calculation
    data["Duration_Hours"] = calculate_duration(data["From"], data["To"])
    
    return data

async def run_scraper(progress_callback=None, limit=None):
    async with async_playwright() as p:
        # Use more realistic headers
        browser = await p.chromium.launch(headless=True)
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
            return pd.DataFrame()

        df = pd.DataFrame(results)
        df["Last_Scraped"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        from dotenv import load_dotenv
        from sqlalchemy import create_engine
        import os
        
        load_dotenv()
        db_url = os.getenv("DATABASE_URL")
        
        if db_url:
            engine = create_engine(db_url)
            df.to_sql('courses', engine, if_exists='replace', index=False)
            output_file = "PostgreSQL Database"
        else:
            # Save to CSV
            output_file = "cpd_courses.csv"
            df.to_csv(output_file, index=False)
            
        print(f"\nScraping complete! Results saved to {output_file}")
        
        await browser.close()
        return df

import sys
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Limit the number of events to scrape for testing")
    args = parser.parse_args()
    asyncio.run(run_scraper(limit=args.limit))
