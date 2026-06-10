"""
OSI News Automation – Article Assembler
=========================================
Stitches the generated lede and sections into a complete article body,
then runs one final Groq call to generate the metadata header:
TITLE, SUBTITLE, BYLINE, LOCATION, CATEGORY.

Also runs basic validation (word count, quote presence, no generic headers)
and returns the final article dict in the same shape as the old pipeline
so the rest of the system (MongoDB storage, Hocalwire uploader, dashboard)
works without any changes.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from src.content_generation.models import FlowPlan, GeneratedSection


# ─────────────────────────────────────────────────────────────────
# BANNED PHRASES  (post-generation substitution)
# ─────────────────────────────────────────────────────────────────

_PHRASE_SUBS = [
    (" sparking ",      " prompting "),
    (" sparking\n",     " prompting\n"),
    ("sparking ",       "prompting "),
    (" amid ",          " as "),
    (" historic ",      " notable "),
    (" historic\n",     " notable\n"),
    ("historic ",       "notable "),
    (" unprecedented ", " unparalleled "),
    ("unprecedented ",  "unparalleled "),
    (" crucial ",       " essential "),
    ("crucial ",        "essential "),
    ("highlighted the need", "pointed to the need"),
    ("raises questions about", "draws attention to"),
]


# ─────────────────────────────────────────────────────────────────
# METADATA GENERATION PROMPT
# ─────────────────────────────────────────────────────────────────

_META_SYSTEM = """You are a headline writer at robin cc.
Given an assembled article body, return ONLY valid JSON with these exact keys:
{
  "title":    "<original headline — 5-7 words, strong verb, specific — NOT copied from sources>",
  "subtitle": "<complete sentence 120-180 chars naming key actor + specific consequence>",
  "location": "<most specific city or country dateline from the article — uppercase>",
  "category": "<exactly one of: World / Technology / Politics / Sports / Business / Entertainment / Science / Health / Environment / Crime / Society>"
}
Rules:
- title must be original — do not repeat any headline from the sources.
- subtitle must be a full sentence (subject + verb + consequence), 120-180 characters.
- No markdown, no preamble."""


def _build_meta_prompt(body: str, topic: str, sources: List[str]) -> str:
    banned_block = "\n".join(f"  - {s}" for s in sources[:6]) if sources else "  (none)"
    return (
        f"Topic: {topic}\n\n"
        f"Banned source headlines (do not copy or paraphrase):\n{banned_block}\n\n"
        f"Article body (first 1200 chars):\n{body[:1200]}\n\n"
        f"Generate the JSON metadata now:"
    )


# ─────────────────────────────────────────────────────────────────
# ASSEMBLE BODY
# ─────────────────────────────────────────────────────────────────

def _assemble_body(lede: str, sections: List[GeneratedSection]) -> str:
    parts = []
    if lede.strip():
        parts.append(lede.strip())
    for sec in sections:
        parts.append(f"\n## {sec.heading}\n\n{sec.body.strip()}")
    return "\n\n".join(parts)


def _apply_phrase_subs(text: str) -> str:
    for old, new in _PHRASE_SUBS:
        text = text.replace(old, new)
    return text


# ─────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────

_GENERIC_HEADERS = {
    "the escalating conflict", "the human cost", "the humanitarian crisis",
    "the response", "the diplomatic efforts", "the broader context",
    "the economic cost", "the implications", "looking ahead",
    "background", "what happened", "voices", "key facts", "conclusion",
    "what comes next", "the road ahead",
}

def _validate_article(body: str, word_ceiling: int) -> List[str]:
    warnings = []
    words = len(body.split())
    if words < 200:
        warnings.append(f"Very short article: {words} words")
    if words > word_ceiling + 100:
        warnings.append(f"Exceeds ceiling: {words}/{word_ceiling} words")

    headers = re.findall(r'^##\s+(.+)$', body, re.MULTILINE)
    for h in headers:
        if h.strip().lower() in _GENERIC_HEADERS:
            warnings.append(f"Generic section header: '## {h}'")

    if '"' not in body and '“' not in body:
        warnings.append("No verbatim quotes found in body")

    return warnings


# ─────────────────────────────────────────────────────────────────
# MAIN ASSEMBLER
# ─────────────────────────────────────────────────────────────────

def assemble_article(
    lede: str,
    sections: List[GeneratedSection],
    flow: FlowPlan,
    trend: Dict,
    audit_result,
    groq_client,
    today: str = None,
) -> Optional[Dict]:
    """
    Assemble the full article and generate title/subtitle/location/category.

    Args:
        lede:         Lede paragraph text.
        sections:     List of GeneratedSection objects.
        flow:         FlowPlan used for generation.
        trend:        Original trend dict (topic, articles, keywords).
        audit_result: AuditResult from the audit stage (for word ceiling).
        groq_client:  Active Groq client.
        today:        Date string for byline (defaults to today's date).

    Returns:
        Article dict in the standard pipeline shape, or None on critical failure.
    """
    if not sections and not lede.strip():
        logger.error("No sections and no lede — cannot assemble article")
        return None

    today_str = today or datetime.utcnow().strftime("%B %d, %Y")
    topic     = trend.get("topic", "News Update")
    source_articles = trend.get("articles", [])
    source_headlines = [a.get("heading", "") for a in source_articles[:10]]

    # ── Assemble body ───────────────────────────────────────────────
    body = _assemble_body(lede, sections)
    body = _apply_phrase_subs(body)

    # ── Generate metadata via Groq ──────────────────────────────────
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    title    = topic
    subtitle = ""
    location = audit_result.primary_location or "INTERNATIONAL"
    category = "World"

    try:
        meta_prompt = _build_meta_prompt(body, topic, source_headlines)
        resp = groq_client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=250,
            messages=[
                {"role": "system", "content": _META_SYSTEM},
                {"role": "user",   "content": meta_prompt},
            ],
        )
        meta = json.loads(resp.choices[0].message.content)
        title    = meta.get("title", topic).strip()
        subtitle = meta.get("subtitle", "").strip()
        location = meta.get("location", location).strip().upper()
        category = meta.get("category", "World").strip()
    except Exception as e:
        logger.warning(f"Metadata generation failed ({e}), using defaults")

    # Sanitise title: strip leading #, trim whitespace
    title = re.sub(r'^#+\s*', '', title).strip() or topic

    # ── Build formatted article string (matches old parse_generated_article output) ──
    byline_line  = f"BYLINE: robin cc | {today_str}"
    location_line = f"LOCATION: {location}"
    category_line = f"CATEGORY: {category}"

    formatted_story = (
        f"# {title}\n\n"
        f"### {subtitle}\n\n"
        f"{byline_line}\n"
        f"{location_line}\n"
        f"{category_line}\n"
        f"---\n\n"
        f"{body}"
    )

    # ── Validation warnings ─────────────────────────────────────────
    ceiling  = audit_result.honest_word_ceiling if audit_result else 900
    warnings = _validate_article(body, ceiling)
    for w in warnings:
        logger.warning(f"Assembly validation: {w}")

    # ── Keyword extraction (top-10 from body) ──────────────────────
    stopwords = {
        "the","a","an","and","or","but","in","on","at","to","for","of","with",
        "by","from","as","is","was","are","were","been","be","have","has","had",
        "it","its","this","that","these","those","they","their","there","then",
        "said","says","also","after","before","when","where","who","which","what",
        "will","would","could","should","may","might","can","new","one","two",
    }
    words = re.findall(r'\b[a-zA-Z]{4,}\b', body.lower())
    keyword_counts = Counter(w for w in words if w not in stopwords)
    keywords = [w for w, _ in keyword_counts.most_common(10)]

    # ── Collect all verbatim quotes used across sections ───────────
    all_quotes: List[str] = []
    seen_quotes: set = set()
    for sec in sections:
        for q in sec.quotes_embedded:
            if q not in seen_quotes:
                seen_quotes.add(q)
                all_quotes.append(q)

    # ── Final article dict ──────────────────────────────────────────
    article = {
        # Core content (matches old pipeline schema)
        "heading":     title,
        "sub_heading": subtitle,
        "story":       formatted_story,
        "dateline":    f"{location}, {today_str}",
        "topic":       topic,
        "timestamp":   _format_timestamp(),
        "sources_used": list({a.get("source_name", "Unknown") for a in source_articles}),
        "source_count": len(source_articles),
        "word_count":  len(body.split()),
        "keywords":    keywords,
        "generated_at": datetime.utcnow().isoformat(),
        "model_used":  model,

        # Quality metadata
        "source_quality":       audit_result.source_quality if audit_result else "adequate",
        "honest_word_ceiling":  ceiling,
        "audit_sections":       audit_result.available_sections if audit_result else [],
        "validation_warnings":  warnings,

        # New pipeline metadata
        "story_type":           flow.story_type,
        "section_headings":     [s.heading for s in sections],
        "quotes_used":          all_quotes[:8],
        "generation_pipeline":  "aspect_rag_v1",
    }

    logger.info(
        f"Article assembled: '{title[:60]}' | "
        f"{article['word_count']} words | "
        f"{len(sections)} sections | "
        f"story_type={flow.story_type}"
    )
    return article


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _format_timestamp(timezone: str = "Asia/Kolkata") -> str:
    try:
        import pytz
        tz  = pytz.timezone(timezone)
        now = datetime.now(tz)
        return now.strftime("%A, %B %d, %Y, %I:%M %p IST")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
