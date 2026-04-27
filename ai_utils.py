import os
import json
import base64
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if OPENAI_API_KEY:
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
else:
    client = None

CHEAP_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")

OUTPUT_SCHEMA_BLOCK = """
OUTPUT FORMAT (JSON ONLY):
{
    "Title": "Full course title",
    "Organiser": "Organiser name",
    "Public_CPD_Points": "Number (e.g. 1.5)",
    "MEC_Points": "Number of Ethics points (if any, else 0)",
    "Dates": "Formatted date string",
    "Venue": "Physical location or 'Webinar'",
    "Prices": "Comma-separated list of ALL unique price points found (e.g. 'S$100, S$150'). Ignore numbers that are clearly not prices (like 'Module 1' or 'April 1'). Use 'Unknown' if no price is present.",
    "Min_Price": "The lowest non-zero numerical price (e.g. 100.0). Use null when no price was found — never guess.",
    "Is_Free": true/false (true ONLY if price is $0 or text explicitly says 'Free' / 'Complimentary' / 'No charge'),
    "Reasoning": "Brief explanation of the price detection: which source, which exact substring, why this number"
}
"""

PROMPT_RULES = """
You are an expert legal-education data extractor for Singapore lawyers.
Your goal: extract accurate CPD course pricing.

RULES:
- The PDF brochure (when provided) is the ABSOLUTE source of truth for pricing.
  If absent, fall back to the provider website.
- STRICT FREE DEFINITION: only set Is_Free=true when the text EXPLICITLY says
  "Free", "Complimentary", "No charge", or "S$0".
- MISSING PRICES: if no price is present, set Prices="Unknown", Min_Price=null,
  Is_Free=false. NEVER guess. It is better to flag a course for manual review
  than to mislabel a paid course as free.
- IGNORE non-price numbers (module numbers, dates, page numbers, addresses).
"""


def _build_text_prompt(sile_text, web_text, pdf_text):
    return f"""{PROMPT_RULES}

SOURCES:
--- SILE DETAILS ---
{sile_text}

--- EXTERNAL PROVIDER WEBSITE ---
{web_text}

--- PDF BROCHURE TEXT ---
{pdf_text}

{OUTPUT_SCHEMA_BLOCK}
"""


def _build_attachment_prompt(sile_text, source_label):
    return f"""{PROMPT_RULES}

A {source_label} is attached to this message — read it directly to extract the price.
The text-only extraction step was inconclusive, so this attachment is the primary source.

SILE METADATA (for cross-reference only):
{sile_text}

{OUTPUT_SCHEMA_BLOCK}
"""


async def _call_openai_json(model, content_blocks):
    if not client:
        return None
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content_blocks}],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"AI call failed (model={model}): {e}")
        return None


async def get_ai_metadata(sile_text, web_text, pdf_text):
    """Cheap text-only extraction. Returns the JSON schema dict or None on failure."""
    if not client:
        return None
    await asyncio.sleep(1)  # gentle rate limit
    prompt = _build_text_prompt(sile_text, web_text, pdf_text)
    return await _call_openai_json(CHEAP_MODEL, [{"type": "text", "text": prompt}])


async def get_ai_metadata_from_pdf(sile_text, pdf_bytes, filename="brochure.pdf"):
    """Vision-tier escalation: send the actual PDF bytes to gpt-4o for native parsing."""
    if not client or not pdf_bytes:
        return None
    await asyncio.sleep(1)
    b64 = base64.b64encode(pdf_bytes).decode("ascii")
    prompt = _build_attachment_prompt(sile_text, "PDF brochure")
    blocks = [
        {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": f"data:application/pdf;base64,{b64}",
            },
        },
        {"type": "text", "text": prompt},
    ]
    return await _call_openai_json(VISION_MODEL, blocks)


async def get_ai_metadata_from_screenshot(sile_text, png_bytes):
    """Vision-tier escalation: send a full-page screenshot of the provider site to gpt-4o."""
    if not client or not png_bytes:
        return None
    await asyncio.sleep(1)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    prompt = _build_attachment_prompt(sile_text, "full-page screenshot of the provider's course page")
    blocks = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text": prompt},
    ]
    return await _call_openai_json(VISION_MODEL, blocks)
