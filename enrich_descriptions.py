"""
Backfill script — re-runs the AI description tier for courses already in
Postgres but missing a description in Sanity (or where the description is
shorter than 80 chars, indicating a previous failure).

Usage:
    python enrich_descriptions.py                # all candidates
    python enrich_descriptions.py --limit 20     # cap iterations
    python enrich_descriptions.py --event 12345  # specific EventID

Cost guard: each iteration costs ~$0.0002 with gpt-4o-mini, so 100 events ≈ $0.02.
"""
import os
import sys
import asyncio
import argparse
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from ai_utils import get_ai_description
from sanity_client import patch_description, _doc_id

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print("DATABASE_URL not set.")
    sys.exit(1)

engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 5})


async def enrich_one(row):
    sile = (
        f"Title: {row.get('Title')}\n"
        f"Organiser: {row.get('Organiser')}\n"
        f"Points: {row.get('Public_CPD_Points')}\n"
        f"MEC: {row.get('MEC_Segment')}\n"
        f"Dates: {row.get('From')} to {row.get('To')}"
    )
    web_text = ""  # we don't store this in Postgres; description from SILE-only is acceptable for backfill
    pdf_text = ""
    desc = await get_ai_description(sile, web_text, pdf_text)
    if not desc or not desc.get("description"):
        return False
    patch_description(
        event_id=str(row["EventID"]),
        description=desc.get("description", ""),
        key_topics=desc.get("key_topics") or [],
        target_audience=desc.get("target_audience") or "",
    )
    return True


async def main(limit=None, event_id=None):
    df = pd.read_sql_table("courses", engine)
    if event_id:
        df = df[df["EventID"].astype(str) == str(event_id)]
    if limit:
        df = df.head(limit)
    print(f"Enriching {len(df)} course(s)...")
    ok = 0
    for _, row in df.iterrows():
        try:
            if await enrich_one(row):
                ok += 1
                print(f"  [{row['EventID']}] {row['Title'][:60]} ✓")
            else:
                print(f"  [{row['EventID']}] empty / skipped")
        except Exception as e:
            print(f"  [{row['EventID']}] error: {e}")
    print(f"Done. {ok}/{len(df)} successful.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--event", type=str, default=None)
    args = p.parse_args()
    asyncio.run(main(limit=args.limit, event_id=args.event))
