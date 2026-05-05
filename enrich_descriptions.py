"""
Backfill script — re-runs the AI description tier for courses already in
Postgres but missing a description (or where it's shorter than 80 chars,
indicating a previous failure).

Updates the courses table directly. Does NOT set description_overridden=True
because these are AI-generated, not human-edited — the next scrape can refresh.

Usage:
    python enrich_descriptions.py                # all candidates
    python enrich_descriptions.py --limit 20     # cap iterations
    python enrich_descriptions.py --event 12345  # specific EventID
    python enrich_descriptions.py --force        # re-run even if description exists

Cost guard: each iteration costs ~$0.0002 with gpt-4o-mini (~$0.02 per 100 events).
"""
import os
import sys
import json
import asyncio
import argparse
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from ai_utils import get_ai_description

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
    # We don't store provider HTML / PDF text in Postgres, so backfill works
    # from the SILE summary alone. Quality will be lower than in-scrape descriptions.
    desc = await get_ai_description(sile, "", "")
    if not desc or not desc.get("description"):
        return False

    description = desc.get("description", "") or ""
    key_topics = json.dumps(desc.get("key_topics") or [])
    target_audience = desc.get("target_audience", "") or ""

    with engine.begin() as conn:
        conn.execute(
            text(
                '''
                UPDATE courses
                SET "Description" = :desc,
                    "Key_Topics" = :topics,
                    "Target_Audience" = :audience
                WHERE "EventID" = :eid
                '''
            ),
            {
                "desc": description,
                "topics": key_topics,
                "audience": target_audience,
                "eid": str(row["EventID"]),
            },
        )
    return True


async def main(limit=None, event_id=None, force=False):
    df = pd.read_sql_table("courses", engine)
    if event_id:
        df = df[df["EventID"].astype(str) == str(event_id)]
    elif not force and "Description" in df.columns:
        # Only enrich rows missing or with very short descriptions.
        df = df[df["Description"].fillna("").astype(str).str.len() < 80]
    if limit:
        df = df.head(limit)
    print(f"Enriching {len(df)} course(s)...")
    ok = 0
    for _, row in df.iterrows():
        try:
            if await enrich_one(row):
                ok += 1
                print(f"  [{row['EventID']}] {str(row['Title'])[:60]} OK")
            else:
                print(f"  [{row['EventID']}] empty / skipped")
        except Exception as e:
            print(f"  [{row['EventID']}] error: {e}")
    print(f"Done. {ok}/{len(df)} successful.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--event", type=str, default=None)
    p.add_argument("--force", action="store_true", help="Re-enrich even if description exists.")
    args = p.parse_args()
    asyncio.run(main(limit=args.limit, event_id=args.event, force=args.force))
