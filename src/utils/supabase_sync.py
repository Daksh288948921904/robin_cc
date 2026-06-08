"""
Supabase sync — fire-and-forget persistence layer.

All public functions are safe to call even when Supabase is not configured
or unreachable; errors are logged but never propagate to the caller.
"""

import os, logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        log.info("Supabase client initialised (%s)", url)
    except Exception as e:
        log.warning("Supabase init failed: %s", e)
    return _client


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Articles ─────────────────────────────────────────────────────────────────

def upsert_articles(articles: list):
    """Upsert the full scraped article list. Called after every scrape/load."""
    client = _get_client()
    if not client:
        return
    try:
        rows = [
            {
                "server_idx":  idx,
                "heading":     a.get("heading", ""),
                "sub_heading": a.get("sub_heading", ""),
                "source_name": a.get("source_name", ""),
                "source_url":  a.get("source_url", ""),
                "scraped_at":  a.get("scraped_at") or _now(),
                "payload":     a,
                "updated_at":  _now(),
            }
            for idx, a in enumerate(articles)
        ]
        # Batch in 100s to stay inside Supabase request limits
        for i in range(0, len(rows), 100):
            client.table("articles").upsert(
                rows[i : i + 100], on_conflict="server_idx"
            ).execute()
        log.info("Supabase: upserted %d articles", len(articles))
    except Exception as e:
        log.warning("Supabase upsert_articles failed: %s", e)


def patch_article(idx: int, article: dict):
    """Update a single article after a heading/sub_heading edit."""
    client = _get_client()
    if not client:
        return
    try:
        client.table("articles").upsert(
            {
                "server_idx":  idx,
                "heading":     article.get("heading", ""),
                "sub_heading": article.get("sub_heading", ""),
                "source_name": article.get("source_name", ""),
                "source_url":  article.get("source_url", ""),
                "scraped_at":  article.get("scraped_at") or _now(),
                "payload":     article,
                "updated_at":  _now(),
            },
            on_conflict="server_idx",
        ).execute()
    except Exception as e:
        log.warning("Supabase patch_article(%d) failed: %s", idx, e)


def load_articles() -> Optional[list]:
    """
    Load articles from Supabase, ordered by server_idx.
    Returns list of article dicts, or None if unavailable.
    """
    client = _get_client()
    if not client:
        return None
    try:
        res = (
            client.table("articles")
            .select("server_idx, payload")
            .order("server_idx")
            .execute()
        )
        if res.data:
            articles = [row["payload"] for row in res.data]
            log.info("Supabase: loaded %d articles", len(articles))
            return articles
    except Exception as e:
        log.warning("Supabase load_articles failed: %s", e)
    return None


# ── Published articles ────────────────────────────────────────────────────────

def insert_published(
    idx: int,
    heading: str,
    sub_heading: str,
    hocalwire_id: str = "",
    reporter: str = "",
    category: str = "",
    image_url: str = "",
    body_html: str = "",
):
    """Insert a record into published_articles when an article goes live."""
    client = _get_client()
    if not client:
        return
    try:
        # Resolve the articles.id FK by server_idx
        res = (
            client.table("articles")
            .select("id")
            .eq("server_idx", idx)
            .maybe_single()
            .execute()
        )
        article_uuid = res.data["id"] if res.data else None

        client.table("published_articles").insert(
            {
                "article_id":   article_uuid,
                "heading":      heading,
                "sub_heading":  sub_heading,
                "hocalwire_id": hocalwire_id,
                "reporter":     reporter,
                "category":     category,
                "image_url":    image_url,
                "body_html":    body_html,
                "published_at": _now(),
            }
        ).execute()
        log.info("Supabase: recorded publish for article idx=%d", idx)
    except Exception as e:
        log.warning("Supabase insert_published(%d) failed: %s", idx, e)
