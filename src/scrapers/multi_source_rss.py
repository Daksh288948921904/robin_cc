"""
OSI News — Multi-Source RSS Fetcher
=====================================
Fetches headlines + summaries from 20+ major international news sources
in parallel using ThreadPoolExecutor. Returns lightweight article dicts
(no full-page scraping) for speed — suitable for coverage comparison.
"""

import feedparser
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


def _translate_heading(title: str) -> tuple[str, str]:
    """
    Detect and translate a non-English title to English.
    Returns (translated_title, language_code).
    Only called when the title contains non-ASCII characters.

    Uses source_lang='auto' so mixed-language titles (e.g. Japanese text with
    embedded English words) are still translated — langdetect can misclassify
    these as 'en' and the old guard would skip translation entirely.
    """
    lang_code = "en"
    try:
        from langdetect import detect
        lang_code = detect(title) or "en"
    except Exception:
        pass

    try:
        from src.translation.translator import translate_text
        # Use 'auto' so Google Translate handles mixed-language titles correctly.
        # If langdetect says 'en' but non-ASCII chars are present, auto-detect
        # will still pick up the dominant non-English language.
        translated = translate_text(title, source_lang="auto", target_lang="en")
        if translated and translated.strip() and translated.strip() != title:
            # If langdetect said "en" but we successfully translated a different
            # result, the title was actually non-English — keep the detected code
            # or fall back to "xx" so the language badge still appears.
            if lang_code == "en":
                lang_code = "xx"
            return translated.strip(), lang_code
    except Exception:
        pass

    return title, lang_code

# ── Source registry ────────────────────────────────────────────
MAJOR_SOURCES: Dict[str, Dict] = {
    # Global Wire Services
    "Reuters":          {"url": "https://feeds.reuters.com/reuters/topNews",                      "region": "Global"},
    "Associated Press": {"url": "https://feeds.apnews.com/rss/apf-topnews",                       "region": "Global"},

    # UK / European Broadcasters
    "BBC News":         {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",                    "region": "UK"},
    "The Guardian":     {"url": "https://www.theguardian.com/world/rss",                           "region": "UK"},
    "Sky News":         {"url": "http://feeds.skynews.com/feeds/rss/world.xml",                    "region": "UK"},
    "The Telegraph":    {"url": "https://www.telegraph.co.uk/rss.xml",                             "region": "UK"},
    "France 24":        {"url": "https://www.france24.com/en/rss",                                 "region": "France"},
    "Deutsche Welle":   {"url": "https://rss.dw.com/xml/rss-en-all",                              "region": "Germany"},

    # US Majors
    "CNN":              {"url": "http://rss.cnn.com/rss/cnn_topstories.rss",                      "region": "USA"},
    "Fox News":         {"url": "http://feeds.foxnews.com/foxnews/latest",                         "region": "USA"},
    "NPR":              {"url": "https://feeds.npr.org/1001/rss.xml",                              "region": "USA"},
    "USA Today":        {"url": "http://rssfeeds.usatoday.com/usatoday-NewsTopStories",            "region": "USA"},
    "NY Times":         {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",          "region": "USA"},
    "Washington Post":  {"url": "https://feeds.washingtonpost.com/rss/world",                      "region": "USA"},
    "CNBC":             {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",           "region": "USA"},

    # Financial / Business
    "Bloomberg":        {"url": "https://feeds.bloomberg.com/markets/news.rss",                    "region": "USA"},
    "Wall Street Journal": {"url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",                 "region": "USA"},
    "Financial Times":  {"url": "https://www.ft.com/rss/home/uk",                                  "region": "UK"},

    # Middle East / International
    "Al Jazeera":       {"url": "https://www.aljazeera.com/xml/rss/all.xml",                      "region": "Middle East"},

    # Asia
    "NHK World":        {"url": "https://www3.nhk.or.jp/rss/news/cat0.xml",                       "region": "Japan"},
    "SCMP":             {"url": "https://www.scmp.com/rss/91/feed",                                "region": "Hong Kong"},
    "The Hindu":        {"url": "https://www.thehindu.com/news/international/feeder/default.rss",  "region": "India"},
    "Times of India":   {"url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",      "region": "India"},
    "Economic Times":   {"url": "https://economictimes.indiatimes.com/rssfeedstopstories.cms",     "region": "India"},
    "NDTV":             {"url": "https://feeds.feedburner.com/ndtvnews-top-stories",               "region": "India"},
}

_HEADERS = {
    "User-Agent": "OSI-NewsBot/2.0 (RSS aggregator; research purposes)",
    "Accept":     "application/rss+xml, application/xml, text/xml, */*",
}


import re as _re

def _extract_image(entry) -> str:
    """
    Try every known RSS/Atom image slot and return the first usable URL.
    Priority: media:thumbnail → media:content → enclosure → <img> in HTML body.
    """
    # 1. media:thumbnail  (most common: BBC, Reuters, AP, Al Jazeera …)
    thumbs = entry.get("media_thumbnail") or []
    if thumbs:
        url = thumbs[0].get("url", "")
        if url:
            return url

    # 2. media:content with image medium or image-like URL
    for mc in (entry.get("media_content") or []):
        url   = mc.get("url", "")
        mtype = mc.get("type", "") or mc.get("medium", "")
        if url and ("image" in mtype or _re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, _re.I)):
            return url
        if url:          # accept any media:content URL as a last-resort
            return url

    # 3. Enclosures  (e.g. NPR, USA Today)
    for enc in (entry.get("enclosures") or []):
        url = enc.get("href") or enc.get("url", "")
        if url and "image" in enc.get("type", ""):
            return url

    # 4. links with image type
    for lnk in (entry.get("links") or []):
        if "image" in lnk.get("type", ""):
            url = lnk.get("href", "")
            if url:
                return url

    # 5. First <img src="…"> inside the HTML summary / description / content
    for field in ("summary", "description"):
        html = entry.get(field, "") or ""
        m = _re.search(r'<img\b[^>]+\bsrc=["\']([^"\']{12,})["\']', html, _re.I)
        if m:
            return m.group(1)

    for block in (entry.get("content") or []):
        html = block.get("value", "") or ""
        m = _re.search(r'<img\b[^>]+\bsrc=["\']([^"\']{12,})["\']', html, _re.I)
        if m:
            return m.group(1)

    # 6. Fetch the article page and read og:image / twitter:image meta tag
    link = (entry.get("link") or "").strip()
    if link:
        try:
            with httpx.Client(timeout=6, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (compatible; OSI-NewsBot/2.0)",
                "Accept": "text/html,*/*",
            }) as client:
                r = client.get(link)
            for pattern in (
                r'<meta\b[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']{12,})["\']',
                r'<meta\b[^>]+content=["\']([^"\']{12,})["\'][^>]+property=["\']og:image["\']',
                r'<meta\b[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']{12,})["\']',
                r'<meta\b[^>]+content=["\']([^"\']{12,})["\'][^>]+name=["\']twitter:image["\']',
            ):
                m = _re.search(pattern, r.text, _re.I)
                if m:
                    return m.group(1)
        except Exception:
            pass

    return ""


def _fetch_one(name: str, cfg: Dict, limit: int = 8) -> List[Dict]:
    """Fetch and parse one RSS feed. Returns list of lightweight article dicts."""
    url    = cfg["url"]
    region = cfg.get("region", "Unknown")
    try:
        with httpx.Client(timeout=12, follow_redirects=True, headers=_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw = resp.text

        feed = feedparser.parse(raw)
        entries = []
        for entry in feed.entries[:limit]:
            title     = (entry.get("title") or "").strip()
            link      = (entry.get("link")  or "").strip()
            raw_sum   = (entry.get("summary") or entry.get("description") or "").strip()
            summary   = _re.sub(r"<[^>]+>", " ", raw_sum).strip()
            published = entry.get("published") or entry.get("updated") or ""
            top_image = _extract_image(entry)

            if title and link:
                original_heading = title
                lang_code = "en"
                if any(ord(c) > 127 for c in title):
                    title, lang_code = _translate_heading(title)

                entries.append({
                    "heading":          title,
                    "original_heading": original_heading if lang_code != "en" else "",
                    "story":            summary,
                    "source_url":       link,
                    "source_name":      name,
                    "region":           region,
                    "publish_date":     published,
                    "scraped_at":       datetime.utcnow().isoformat(),
                    "word_count":       len(summary.split()),
                    "authors":          [],
                    "top_image":        top_image,
                    "language":         lang_code,
                })

        logger.info(f"[RSS] {name}: {len(entries)} entries  "
                    f"({sum(1 for e in entries if e['top_image'])} with images)")
        return entries

    except Exception as exc:
        logger.warning(f"[RSS] {name} failed: {exc}")
        return []


def fetch_all_sources(
    sources: Optional[List[str]] = None,
    max_per_source: int = 6,
    max_workers: int = 12,
) -> List[Dict]:
    """
    Fetch from all (or a subset of) major sources in parallel.

    Args:
        sources:        List of source names to fetch. None = all.
        max_per_source: Maximum articles per source.
        max_workers:    Thread-pool concurrency.

    Returns:
        Flat list of article dicts, sorted newest-first by scraped_at.
    """
    targets = {
        k: v for k, v in MAJOR_SOURCES.items()
        if sources is None or k in sources
    }

    all_articles: List[Dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, name, cfg, max_per_source): name
                   for name, cfg in targets.items()}
        for fut in as_completed(futures):
            all_articles.extend(fut.result())

    logger.info(f"[RSS] Total fetched: {len(all_articles)} articles from {len(targets)} sources")
    return all_articles
