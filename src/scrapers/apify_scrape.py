"""
Apify-based social media scraper — Twitter/X and Instagram.

Actors used (both tested working):
  Twitter  → gentle_cloud~twitter-tweets-scraper
  Instagram → apify~instagram-hashtag-scraper
"""

import os
import re
import time
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apify.com/v2"
TW_ACTOR = "gentle_cloud~twitter-tweets-scraper"
IG_ACTOR = "apify~instagram-hashtag-scraper"


def _token() -> str:
    t = os.getenv("APIFY_API_TOKEN", "")
    if not t:
        raise RuntimeError("APIFY_API_TOKEN not set in environment")
    return t


def _run_actor(actor_id: str, input_data: dict, timeout: int = 180) -> list:
    """Start an Apify actor run, poll until done, return dataset items."""
    token  = _token()
    params = {"token": token}

    resp = requests.post(
        f"{BASE_URL}/acts/{actor_id}/runs",
        json=input_data, params=params, timeout=30,
    )
    resp.raise_for_status()
    run_data   = resp.json()["data"]
    run_id     = run_data["id"]
    dataset_id = run_data["defaultDatasetId"]

    status_url = f"{BASE_URL}/actor-runs/{run_id}"
    deadline   = time.time() + timeout

    while time.time() < deadline:
        time.sleep(6)
        sr     = requests.get(status_url, params=params, timeout=15)
        sr.raise_for_status()
        status = sr.json()["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            logger.error("Apify run %s ended with status=%s", run_id, status)
            return []
    else:
        logger.warning("Apify run %s timed out after %ds", run_id, timeout)
        return []

    ir = requests.get(
        f"{BASE_URL}/datasets/{dataset_id}/items",
        params={**params, "clean": "true", "limit": 50},
        timeout=30,
    )
    ir.raise_for_status()
    return ir.json()


def _headline_to_hashtags(headline: str, max_tags: int = 3) -> list[str]:
    """Pick the most meaningful words from headline for hashtag search."""
    STOP = {
        'the','a','an','in','on','at','to','for','of','and','or','but','with',
        'from','by','is','are','was','were','be','been','have','has','had',
        'will','would','could','should','may','might','not','no','up','out',
        'if','as','it','its','we','he','she','they','what','which','who',
        'this','that','these','those','over','after','before','about','through',
        'when','while','than','such','say','says','said','also','more','new',
    }
    words = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', headline)
             if w.lower() not in STOP]
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            out.append(lw)
        if len(out) >= max_tags:
            break
    return out or ['news']


def scrape_twitter(query: str, max_items: int = 20) -> list:
    """
    Search Twitter/X for tweets about the news headline.
    Returns list of post dicts with url, text, author, stats.
    """
    try:
        raw = _run_actor(TW_ACTOR, {
            "searchTerms": [query],
            "maxTweets":   max_items,
        })

        posts = []
        for tw in raw[:max_items]:
            # Skip items that signal no results
            if "noResults" in tw or not tw.get("full_text"):
                continue

            user     = tw.get("user", {})
            legacy   = user.get("legacy", {}) if isinstance(user, dict) else {}
            username = legacy.get("screen_name") or tw.get("user_id_str") or "unknown"
            name     = legacy.get("name") or username

            posts.append({
                "platform":  "twitter",
                "post_url":  tw.get("url") or f"https://x.com/{username}/status/{tw.get('id_str','')}",
                "author":    name,
                "username":  username,
                "text":      tw.get("full_text") or "",
                "likes":     tw.get("favorite_count") or 0,
                "reposts":   tw.get("retweet_count") or 0,
                "replies":   tw.get("reply_count") or 0,
                "timestamp": tw.get("created_at") or "",
            })
        return posts
    except Exception as e:
        logger.error("Twitter scrape error: %s", e)
        return []


def scrape_instagram(query: str, max_items: int = 15) -> list:
    """
    Scrape Instagram posts by hashtag derived from the news headline.
    Returns list of post dicts with url, caption, comments, stats.
    """
    try:
        hashtags = _headline_to_hashtags(query, max_tags=2)
        raw = _run_actor(IG_ACTOR, {
            "hashtags":     hashtags,
            "resultsLimit": max_items,
        })

        posts = []
        for post in raw[:max_items]:
            if "error" in post or not post.get("url"):
                continue

            comments = [
                {
                    "author": c.get("ownerUsername") or "",
                    "text":   c.get("text") or "",
                    "likes":  c.get("likesCount") or 0,
                }
                for c in (post.get("latestComments") or [])[:5]
            ]

            posts.append({
                "platform":       "instagram",
                "post_url":       post.get("url") or "",
                "author":         post.get("ownerUsername") or "Unknown",
                "caption":        (post.get("caption") or "")[:300],
                "likes":          post.get("likesCount") or 0,
                "comments_count": post.get("commentsCount") or 0,
                "comments":       comments,
                "timestamp":      post.get("timestamp") or "",
                "image_url":      post.get("displayUrl") or "",
            })
        return posts
    except Exception as e:
        logger.error("Instagram scrape error: %s", e)
        return []
