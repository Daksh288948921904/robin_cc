"""
OSI News Automation – Aspect Chunker
======================================
Splits source articles into paragraph-level chunks, classifies each
chunk's thematic aspect (using the NLI intent classifier), extracts
verbatim quotes, and persists the chunks in Qdrant.

Works for any number of source articles — even a single source.

Main entry point:
    chunk_and_store_cluster(articles, cluster_id, story_type, session_id)
        → Dict[str, int]  {aspect: chunk_count}
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Tuple

from loguru import logger

from src.content_generation.intent_classifier import classify_chunk_aspect

# Reuse the quote regex already defined in prompt_builder
# (captures 20-200 char quoted strings).
_QUOTE_RE = re.compile(
    r'[“”""]'
    r'([^"“”„]{20,200})'
    r'[“”""]',
)

_MIN_CHUNK_CHARS = 60       # discard single-line fragments
_MAX_CHUNKS_PER_ARTICLE = 30


# ─────────────────────────────────────────────────────────────────
# PARAGRAPH SPLITTER
# ─────────────────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> List[str]:
    """
    Split article text into non-empty paragraphs of at least
    _MIN_CHUNK_CHARS characters.

    Splitting on double newlines preserves the article's natural
    paragraph boundaries; discarding short fragments removes
    navigation artefacts captured by scrapers.
    """
    raw = re.split(r'\n{2,}', text.strip())
    chunks = []
    for para in raw:
        para = para.strip()
        if len(para) >= _MIN_CHUNK_CHARS:
            chunks.append(para)
    return chunks[:_MAX_CHUNKS_PER_ARTICLE]


# ─────────────────────────────────────────────────────────────────
# QUOTE EXTRACTOR
# ─────────────────────────────────────────────────────────────────

def _extract_quotes(text: str) -> List[str]:
    """Return verbatim quoted strings found in *text*."""
    return [m.group(1).strip() for m in _QUOTE_RE.finditer(text)]


# ─────────────────────────────────────────────────────────────────
# SINGLE-ARTICLE CHUNKER
# ─────────────────────────────────────────────────────────────────

def chunk_article(
    article: Dict,
    cluster_id: str,
    story_type: str,
    session_id: str = "",
) -> List[Dict]:
    """
    Chunk one article into classified paragraph dicts.

    Args:
        article:    Source article dict (heading, story, source_name, …)
        cluster_id: The trend cluster this article belongs to.
        story_type: Pre-classified story type string.
        session_id: Scraping session id for housekeeping.

    Returns:
        List of chunk dicts ready for Qdrant upsert.
    """
    story_text = article.get("story", "")
    heading    = article.get("heading", "")
    source_name       = article.get("source_name", "Unknown")
    source_article_id = str(article.get("_id", article.get("source_url", heading)))
    scraped_at        = article.get("scraped_at", datetime.utcnow())

    # Prepend the headline as the first paragraph so it gets chunked + classified
    paragraphs = _split_paragraphs(heading + "\n\n" + story_text)

    chunks: List[Dict] = []
    for idx, para in enumerate(paragraphs):
        aspect, confidence = classify_chunk_aspect(para)
        quotes = _extract_quotes(para)

        chunks.append({
            "cluster_id":        cluster_id,
            "source_article_id": source_article_id,
            "source_name":       source_name,
            "chunk_text":        para,
            "chunk_index":       idx,
            "aspect":            aspect,
            "aspect_confidence": confidence,
            "quotes":            quotes,
            "story_type":        story_type,
            "session_id":        session_id,
            "scraped_at":        scraped_at,
        })

    return chunks


# ─────────────────────────────────────────────────────────────────
# CLUSTER CHUNKER — main entry point
# ─────────────────────────────────────────────────────────────────

def chunk_and_store_cluster(
    articles: List[Dict],
    cluster_id: str,
    story_type: str,
    session_id: str = "",
) -> Dict[str, int]:
    """
    Chunk all articles in a trend cluster, classify aspects, extract quotes,
    and persist them in Qdrant.

    Args:
        articles:   List of source article dicts.
        cluster_id: Unique identifier for this trend cluster.
        story_type: Pre-classified story type (from intent_classifier).
        session_id: Optional scraping session id.

    Returns:
        {aspect: chunk_count} inventory — used by the flow planner.
    """
    from src.database.qdrant_store import save_chunks

    all_chunks: List[Dict] = []
    for art in articles:
        art_chunks = chunk_article(art, cluster_id, story_type, session_id)
        all_chunks.extend(art_chunks)
        logger.debug(
            f"  {art.get('source_name', '?')}: {len(art_chunks)} chunks"
        )

    if not all_chunks:
        logger.warning(f"No chunks produced for cluster {cluster_id!r}")
        return {}

    saved = save_chunks(all_chunks)
    logger.info(f"Stored {saved}/{len(all_chunks)} chunks for cluster {cluster_id!r}")

    # Build aspect inventory: {aspect: count}
    inventory: Dict[str, int] = {}
    for chunk in all_chunks:
        asp = chunk["aspect"]
        inventory[asp] = inventory.get(asp, 0) + 1

    return inventory


# ─────────────────────────────────────────────────────────────────
# QUOTE AGGREGATOR  (used by section_generator)
# ─────────────────────────────────────────────────────────────────

def collect_quotes_from_chunks(chunks: List[Dict]) -> List[str]:
    """
    Deduplicate and flatten verbatim quotes from a list of chunk payloads.
    Returns up to 8 unique quotes, longest first.
    """
    seen = set()
    quotes = []
    for chunk in chunks:
        for q in chunk.get("quotes", []):
            normalised = q.strip()
            if normalised and normalised not in seen:
                seen.add(normalised)
                quotes.append(normalised)

    # Sort longest first so the most substantial quotes appear first
    quotes.sort(key=len, reverse=True)
    return quotes[:8]
