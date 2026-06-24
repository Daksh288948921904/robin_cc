"""
Twitter/X scraper — FREE, no API tokens needed.

Uses DuckDuckGo search to find tweets + Twitter oEmbed API for full text.
"""

import os
import re
import logging
import requests

from src.content_generation.groq_pool import get_groq_client, rotate_groq_key

logger = logging.getLogger(__name__)


_STOP_WORDS = {
    'the','a','an','in','on','at','to','for','of','and','or','but','with',
    'from','by','is','are','was','were','be','been','have','has','had',
    'will','would','could','should','may','might','not','no','up','out',
    'if','as','it','its','we','he','she','they','what','which','who',
    'this','that','these','those','over','after','before','about','through',
    'when','while','than','such','say','says','said','also','more','new',
    'into','just','amid','under','most','many','some','being','make',
    'gets','news','report','reports','according','likely','every','much',
}


def _extract_keywords(headline: str, max_words: int = 5) -> list[str]:
    words = [w for w in re.findall(r'\b[a-zA-Z]{3,}\b', headline)
             if w.lower() not in _STOP_WORDS]
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            out.append(lw)
        if len(out) >= max_words:
            break
    return out or ['news']


def _llm_search_query(headline: str) -> str:
    client = get_groq_client()
    if not client:
        logger.warning("No Groq client, falling back to keyword extraction")
        return " ".join(_extract_keywords(headline, max_words=5))

    prompt = (
        "You are a Twitter/X search expert. Given a news headline, produce a single "
        "Twitter search query that will find tweets discussing this specific story.\n\n"
        "Rules:\n"
        "- Use 3-5 simple keywords that people would actually use when tweeting about this story\n"
        "- Do NOT use quotes around phrases — just use individual words\n"
        "- Do NOT use Twitter operators like OR, AND, from:, filter:, etc.\n"
        "- Do NOT add hashtags\n"
        "- Prefer proper nouns (names of people, countries, organizations) over generic words\n"
        "- Output ONLY the search query, nothing else — no explanation\n\n"
        f"Headline: {headline}\n\n"
        "Search query:"
    )

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=60,
            )
            query = resp.choices[0].message.content.strip().strip('"').strip("'")
            query = re.sub(r'["\']', '', query)
            if query:
                logger.info("LLM-generated Twitter query: %r", query)
                return query
        except Exception as e:
            logger.warning("Groq search-query attempt %d failed: %s", attempt + 1, e)
            if rotate_groq_key(reason=f"search query gen failed: {e}"):
                client = get_groq_client()
            else:
                break

    logger.warning("LLM query generation failed, falling back to keyword extraction")
    return " ".join(_extract_keywords(headline, max_words=5))


def _tweet_is_relevant(tweet_text: str, keywords: list[str], min_matches: int = 2) -> bool:
    text_lower = tweet_text.lower()
    matches = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matches >= min(min_matches, len(keywords))


def _relevance_score(tweet_text: str, keywords: list[str]) -> int:
    """Higher score = more keyword matches = more relevant."""
    text_lower = tweet_text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def _fetch_oembed(tweet_url: str) -> dict | None:
    """Fetch full tweet text via Twitter's free oEmbed API."""
    try:
        resp = requests.get(
            "https://publish.twitter.com/oembed",
            params={"url": tweet_url, "omit_script": "true", "hide_media": "true"},
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            html = data.get("html", "")
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            text = re.sub(r'&mdash;.*$', '', text).strip()
            text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            return {
                "text": text,
                "author": data.get("author_name", ""),
                "author_url": data.get("author_url", ""),
            }
    except Exception as e:
        logger.debug("oEmbed fetch failed for %s: %s", tweet_url, e)
    return None


def _search_ddg(query: str, max_results: int = 20) -> list[dict]:
    """Search DuckDuckGo for tweets. Returns list of {href, title, body}."""
    try:
        from ddgs import DDGS
        results = DDGS().text(
            f"site:x.com {query}",
            max_results=max_results,
        )
        logger.info("DuckDuckGo returned %d results for query: %r", len(results), query)
        return results
    except Exception as e:
        logger.error("DuckDuckGo search failed: %s", e)
        return []


def scrape_twitter(query: str, max_items: int = 10) -> list:
    """
    Search for tweets about a news headline using DuckDuckGo + Twitter oEmbed.
    Completely free — no API tokens required.
    """
    try:
        search_query = _llm_search_query(query)
        headline_kws = _extract_keywords(query, max_words=5)
        llm_kws = [w.strip('"\'') for w in re.findall(r'\b[a-zA-Z]{3,}\b', search_query)
                    if w.lower() not in _STOP_WORDS]
        all_keywords = list(dict.fromkeys(headline_kws + llm_kws))
        logger.info("Twitter search query: %r (keywords: %s)", search_query, all_keywords)

        raw_results = _search_ddg(search_query, max_results=max_items * 3)

        posts = []
        seen_ids = set()

        for r in raw_results:
            href = r.get("href", "") or r.get("link", "")
            m = re.match(r'https?://(?:twitter\.com|x\.com)/(\w+)/status/(\d+)', href)
            if not m:
                continue

            username = m.group(1)
            tweet_id = m.group(2)

            if tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)

            # Skip Twitter system accounts
            if username.lower() in ('i', 'search', 'explore', 'home', 'settings', 'x'):
                continue

            snippet = r.get("body", "") or r.get("title", "") or ""

            if not _tweet_is_relevant(snippet, all_keywords):
                continue

            tweet_url = f"https://x.com/{username}/status/{tweet_id}"

            # Try to get full text via oEmbed
            oembed = _fetch_oembed(tweet_url)
            if oembed and oembed.get("text"):
                text = oembed["text"]
                if oembed.get("author"):
                    username = oembed["author"]
            else:
                text = snippet

            posts.append({
                "platform":  "twitter",
                "post_url":  tweet_url,
                "author":    username,
                "username":  username,
                "text":      text,
                "likes":     0,
                "reposts":   0,
                "replies":   0,
                "timestamp": "",
            })

            if len(posts) >= max_items:
                break

        # Sort by relevance — tweets matching more keywords appear first
        posts.sort(key=lambda p: _relevance_score(p['text'], all_keywords), reverse=True)
        logger.info("Twitter: %d relevant tweets found via DuckDuckGo", len(posts))
        return posts

    except Exception as e:
        logger.error("Twitter scrape error: %s", e)
        return []
