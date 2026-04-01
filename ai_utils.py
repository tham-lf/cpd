import os
import json
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if OPENAI_API_KEY:
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
else:
    client = None

# Simple rate limiting for free tier
last_call_time = 0

async def get_ai_metadata(sile_text, web_text, pdf_text):
    """
    Use OpenAI to extract structured metadata from tiered sources.
    sile_text: Text from SILE details page
    web_text: Text from external provider website
    pdf_text: Text extracted from PDF brochure
    """
    if not client:
        return None

    # Use a fast and cost-effective model like gpt-4o-mini
    model_name = "gpt-4o-mini"
    
    # Rate limiting if necessary
    await asyncio.sleep(1) 
    
    prompt = f"""
    You are an expert legal education data extractor for Singapore lawyers. 
    Your goal is to extract accurate CPD course metadata from three tiered sources of information.

    PROMPT INSTRUCTIONS:
    - PRICES: The PDF Brochure (if provided) is the ABSOLUTE source of truth for pricing. If not available, use the Provider Website.
    - POINTS: Use the SILE Details for the official Public CPD Points and MEC (Ethics) status. 
    - STRICT FREE DEFINITION: DO NOT assume an event is free just because a price isn't explicitly listed. Only set "Is_Free" to true if the text EXPLICITLY states it is "Free", "Complimentary", "No charge", or "S$0".
    - MISSING PRICES: If pricing information is completely missing, and it does not explicitly say "free", set "Prices" to "Unknown" and "Is_Free" to false. It is better to flag a course for manual review than to falsely label a paid course as free.
    - RECONCILIATION: Compare SILE dates/points with brochure.

    SOURCES:
    --- SILE DETAILS ---
    {sile_text}
    
    --- EXTERNAL PROVIDER WEBSITE ---
    {web_text}
    
    --- PDF BROCHURE TEXT ---
    {pdf_text}

    OUTPUT FORMAT (JSON ONLY):
    {{
        "Title": "Full course title",
        "Organiser": "Organiser name",
        "Public_CPD_Points": "Number (e.g. 1.5)",
        "MEC_Points": "Number of Ethics points (if any, else 0)",
        "Dates": "Formatted date string",
        "Venue": "Physical location or 'Webinar'",
        "Prices": "Comma-separated list of ALL unique price points found (e.g. 'S$100, S$150'). Ignore numbers that are clearly not prices (like 'Module 1' or 'April 1').",
        "Min_Price": "The lowest non-zero price numerical value (e.g. 100.0)",
        "Is_Free": true/false (true if price is $0 or explicitly 'Complimentary' or 'Free'),
        "Reasoning": "Brief explanation of price detection"
    }}
    """

    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"AI Extraction Error: {e}")
        return None
