#!/usr/bin/env python3
"""
MongoDB cleanup — deletes all documents older than RETENTION_DAYS from every collection.
Run via cron every 2 days:
  0 2 */2 * *  cd /path/to/osi-news-automation && python3 scripts/cleanup_mongo.py >> logs/mongo_cleanup.log 2>&1
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

RETENTION_DAYS = int(os.getenv("MONGO_RETENTION_DAYS", "2"))

def run():
    from pymongo import MongoClient

    uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_ATLAS_URI") or os.getenv("MONGODB_LOCAL_URI")
    db_name = os.getenv("MONGO_DB_NAME") or os.getenv("MONGODB_DATABASE", "osi_news_automation")

    if not uri:
        print("ERROR: No MongoDB URI found in environment.", flush=True)
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    print(f"[{datetime.now().isoformat()}] Connecting to MongoDB…", flush=True)

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]

    collections = {
        os.getenv("MONGODB_COLLECTION_ARTICLES", "articles"):         "scraped_at",
        os.getenv("MONGODB_COLLECTION_TRENDS",   "trends"):           "created_at",
        os.getenv("MONGODB_COLLECTION_SESSIONS", "scraping_sessions"): "started_at",
    }

    total_deleted = 0
    for coll_name, date_field in collections.items():
        coll = db[coll_name]
        result = coll.delete_many({date_field: {"$lt": cutoff}})
        print(f"  {coll_name}: deleted {result.deleted_count} docs older than {RETENTION_DAYS}d", flush=True)
        total_deleted += result.deleted_count

    client.close()
    print(f"[{datetime.now().isoformat()}] Done — {total_deleted} total documents removed.", flush=True)

if __name__ == "__main__":
    run()
