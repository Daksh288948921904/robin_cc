"""
Deletes MongoDB articles older than 7 days based on their scraped_at field.
Run manually or via cron: 0 0 * * * python3 /path/to/cleanup_old_articles.py
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / '.env')

from loguru import logger


def cleanup_old_articles(days: int = 7, dry_run: bool = False) -> dict:
    from src.database.mongo_client import get_client

    db = get_client()
    if not db._ensure_connected():
        logger.error("Could not connect to MongoDB")
        return {"deleted": 0, "error": "Connection failed"}

    cutoff = datetime.utcnow() - timedelta(days=days)
    query = {"scraped_at": {"$lt": cutoff}}

    count = db.articles.count_documents(query)
    logger.info(f"Articles older than {days} days: {count} (cutoff: {cutoff.date()})")

    if count == 0:
        logger.info("Nothing to delete.")
        return {"deleted": 0}

    if dry_run:
        logger.info(f"[DRY RUN] Would delete {count} articles.")
        return {"deleted": 0, "would_delete": count}

    result = db.articles.delete_many(query)
    logger.success(f"Deleted {result.deleted_count} articles older than {days} days.")
    return {"deleted": result.deleted_count}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Delete MongoDB articles older than N days.")
    parser.add_argument("--days", type=int, default=7, help="Age threshold in days (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    args = parser.parse_args()

    logger.info(f"Starting cleanup: deleting articles older than {args.days} day(s)")
    result = cleanup_old_articles(days=args.days, dry_run=args.dry_run)

    if "error" in result:
        sys.exit(1)

    logger.info(f"Done. Result: {result}")
