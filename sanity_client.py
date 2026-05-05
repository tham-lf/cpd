"""
Sanity write client for pushing CPD course data into the aithena-landing CMS.

The Next.js site at github.com/tham-lf/aithena-landing reads from Sanity. The
scraper writes here so the public /cpd pages stay in sync with each scrape.

Usage:
    from sanity_client import upsert_course, mark_archived

    upsert_course({
        "EventID": "12345",
        "Title": "Data Protection Essentials",
        "Organiser": "LawSoc",
        ...
    })

Environment variables:
    SANITY_PROJECT_ID    - default: 1wpyru2z (from aithena-landing/src/sanity/client.ts)
    SANITY_DATASET       - default: production
    SANITY_API_VERSION   - default: 2025-08-16
    SANITY_WRITE_TOKEN   - REQUIRED. Get from sanity.io/manage → API → Tokens
                           with at least 'Editor' permissions. Must be able to
                           create/update documents of type cpdCourse.
"""
import os
import re
import json
import httpx
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

SANITY_PROJECT_ID = os.environ.get("SANITY_PROJECT_ID", "1wpyru2z")
SANITY_DATASET = os.environ.get("SANITY_DATASET", "production")
SANITY_API_VERSION = os.environ.get("SANITY_API_VERSION", "2025-08-16")
SANITY_WRITE_TOKEN = os.environ.get("SANITY_WRITE_TOKEN")

MUTATE_URL = (
    f"https://{SANITY_PROJECT_ID}.api.sanity.io"
    f"/v{SANITY_API_VERSION}/data/mutate/{SANITY_DATASET}"
)


def _slugify(text):
    if not text:
        return ""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s[:80]


def _doc_id(event_id):
    return f"cpdCourse-{event_id}"


def _parse_dt(value):
    """Convert a SILE date string to ISO-8601 (Sanity datetime)."""
    if not value or value == "N/A":
        return None
    formats = [
        "%A %d %b %Y - %I:%M %p",
        "%d %b %Y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(str(value).strip(), fmt).isoformat()
        except (ValueError, TypeError):
            continue
    return None


def _safe_float(v):
    if v is None or v == "" or v == "N/A":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_str(v, default=""):
    if v is None or v == "N/A":
        return default
    return str(v).strip()


def build_document(row):
    """Translate a scraper row dict into a Sanity cpdCourse document."""
    event_id = _safe_str(row.get("EventID"))
    if not event_id:
        return None

    title = _safe_str(row.get("Title")) or f"CPD Course {event_id}"
    slug_text = f"{_slugify(title)}-{event_id}"

    return {
        "_id": _doc_id(event_id),
        "_type": "cpdCourse",
        "eventId": event_id,
        "title": title,
        "slug": {"_type": "slug", "current": slug_text},
        "organiser": _safe_str(row.get("Organiser")) or None,
        "dateText": _safe_str(row.get("Date")) or None,
        "fromAt": _parse_dt(row.get("From")),
        "toAt": _parse_dt(row.get("To")),
        "venue": _safe_str(row.get("Venue")) or None,
        "cpdPoints": _safe_float(row.get("Public_CPD_Points")),
        "mecSegment": _safe_str(row.get("MEC_Segment")) or None,
        "priceText": _safe_str(row.get("Price")) or None,
        "minPrice": _safe_float(row.get("Min_Price")),
        "isFree": bool(row.get("Is_Free", False)),
        "category": _safe_str(row.get("Category")) or None,
        "priceSource": _safe_str(row.get("Price_Source")) or None,
        "description": _safe_str(row.get("Description")) or None,
        "keyTopics": row.get("Key_Topics") if isinstance(row.get("Key_Topics"), list) else None,
        "targetAudience": _safe_str(row.get("Target_Audience")) or None,
        "externalLink": _safe_str(row.get("External_Link")) or None,
        "attachmentLink": _safe_str(row.get("Attachment_Link")) or None,
        "firstSeenAt": _parse_dt(row.get("first_seen_at"))
        or _parse_dt(row.get("Last_Scraped"))
        or datetime.now().isoformat(),
        "lastScrapedAt": _parse_dt(row.get("Last_Scraped")) or datetime.now().isoformat(),
        "archived": False,
    }


def _post_mutations(mutations):
    if not SANITY_WRITE_TOKEN:
        print("[sanity] SANITY_WRITE_TOKEN not set — skipping push.")
        return None
    headers = {
        "Authorization": f"Bearer {SANITY_WRITE_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"mutations": mutations}
    try:
        r = httpx.post(MUTATE_URL, headers=headers, json=payload, timeout=30)
        if r.status_code >= 400:
            print(f"[sanity] {r.status_code}: {r.text[:300]}")
            return None
        return r.json()
    except Exception as e:
        print(f"[sanity] mutation failed: {e}")
        return None


def upsert_course(row):
    """Create-or-replace a single course. Returns Sanity API response or None."""
    doc = build_document(row)
    if not doc:
        return None
    return _post_mutations([{"createOrReplace": doc}])


def upsert_courses_bulk(rows, batch_size=50):
    """Upsert many courses in batches. Returns count of successful batches."""
    docs = [build_document(r) for r in rows]
    docs = [d for d in docs if d is not None]
    if not docs:
        return 0
    success = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        muts = [{"createOrReplace": d} for d in batch]
        if _post_mutations(muts) is not None:
            success += 1
        else:
            print(f"[sanity] batch {i // batch_size} failed")
    return success


def mark_archived(event_ids):
    """Set archived=true on courses no longer present in the latest scrape."""
    if not event_ids:
        return None
    muts = [
        {
            "patch": {
                "id": _doc_id(eid),
                "set": {"archived": True},
            }
        }
        for eid in event_ids
    ]
    return _post_mutations(muts)


def patch_description(event_id, description, key_topics=None, target_audience=None):
    """Backfill or override the AI description fields without touching scrape data."""
    set_fields = {"description": description}
    if key_topics is not None:
        set_fields["keyTopics"] = key_topics
    if target_audience is not None:
        set_fields["targetAudience"] = target_audience
    return _post_mutations(
        [{"patch": {"id": _doc_id(event_id), "set": set_fields}}]
    )
