import asyncio
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def test_date_extraction():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        event_id = 7432
        url = f"https://www.silecpdcentre.sg/EventDetails/?EventID={event_id}"
        print(f"Navigating to {url}...")
        try:
            await page.goto(url)
            await page.wait_for_selector("#ContentPlaceHolder1_lblEventTitle")
            
            html = await page.content()
            soup = BeautifulSoup(html, 'html.parser')
            
            all_labels = ["Event Title", "Organiser", "From", "To", "Venue", "Practice Area", "MEC segment", "Training Level", "Public CPD Points", "Event Outline"]

            def find_data(suffix, label_text=None):
                el = soup.find(id=re.compile(f".*{suffix}$"))
                if el and el.get_text(strip=True):
                    cleaned = el.get_text(strip=True)
                    if cleaned.lower() != "n/a":
                        return cleaned
                    
                if label_text:
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

            date_from = find_data("lblEventFrom", "From")
            date_to = find_data("lblEventTo", "To")
            print(f"Extracted Date (From): {date_from}")
            print(f"Extracted Date (To): {date_to}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(test_date_extraction())
