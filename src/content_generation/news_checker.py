"""
News Checker — Three-level verification for scraped articles.

Level 1 — Content Credibility:  concrete / speculative / misleading / false
Level 2 — Fake News Check:      credible / unverified / potentially_misleading / likely_false
Level 3 — Trending:             trending / not_trending  (coverage-count based)
"""

import os
import re
import json
from typing import Dict, List

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq():
    from groq import Groq
    key = os.getenv("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY not set")
    return Groq(api_key=key)

_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """You are a senior investigative fact-checker and news authenticity analyst.
Given a news article headline, body, and the number of independent sources covering the same story,
you return a structured JSON analysis. Be concise and precise. Do not hallucinate facts."""

_PROMPT_TEMPLATE = """Analyse this news article and return ONLY a JSON object — no markdown, no explanation.

HEADLINE: {headline}

BODY (first 800 chars):
{body_excerpt}

SOURCES COVERING THIS STORY: {source_count} independent source(s)
SOURCE NAMES: {source_names}

Return this exact JSON structure:
{{
  "credibility": "concrete|speculative|misleading|false",
  "credibility_score": <integer 0-100, where 100 = fully concrete>,
  "credibility_reason": "<one sentence explaining the rating>",
  "fake_check": "credible|unverified|potentially_misleading|likely_false",
  "fake_reason": "<one sentence explaining the fake-check verdict>",
  "key_claims": ["<claim 1>", "<claim 2>"],
  "red_flags": ["<flag 1 or empty list if none>"]
}}

Definitions:
- concrete: backed by named officials, verifiable events, or hard data
- speculative: uses hedging language (could, may, might, sources say) with no named attribution
- misleading: factual but framed to distort; selective omission; misleading headline
- false: contradicts known facts or multiple authoritative sources

- credible: story checks out, well-sourced, no red flags
- unverified: story could be true but lacks enough sourcing to confirm
- potentially_misleading: framing or context concerns, check before publishing
- likely_false: significant evidence contradicts the claims"""


def analyze_article(article: Dict, coverage: List[Dict]) -> Dict:
    """
    Run all three verification levels on an article.

    Args:
        article:  Main scraped article dict (must have 'heading' and 'body'/'story').
        coverage: List of coverage articles found for this story.

    Returns:
        Dict with keys: credibility, credibility_score, credibility_reason,
                        fake_check, fake_reason, key_claims, red_flags,
                        trending, trending_reason, source_count, overall
    """
    headline    = article.get("heading", article.get("title", ""))
    body        = article.get("body", article.get("story", article.get("content", "")))
    body_excerpt = (body or "")[:800]

    # Build source list from main + coverage
    all_sources = [article] + (coverage or [])
    source_names = list({a.get("source_name", "Unknown") for a in all_sources if a.get("source_name")})
    source_count = len(source_names)

    # ── Level 3: Trending (instant, no LLM) ─────────────────────────────────
    trending       = source_count >= 3
    trending_label = "trending" if trending else "not_trending"
    trending_reason = (
        f"Covered by {source_count} independent source{'s' if source_count != 1 else ''}: "
        + ", ".join(source_names[:4])
        + ("…" if len(source_names) > 4 else "")
    )

    # ── Levels 1 & 2: Groq LLM ───────────────────────────────────────────────
    llm_result = {}
    try:
        client = _get_groq()
        prompt = _PROMPT_TEMPLATE.format(
            headline=headline,
            body_excerpt=body_excerpt,
            source_count=source_count,
            source_names=", ".join(source_names) or "Unknown",
        )
        resp = client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        llm_result = json.loads(raw)
        logger.info(f"News check credibility={llm_result.get('credibility')} fake={llm_result.get('fake_check')} for: {headline[:60]}")
    except Exception as e:
        logger.warning(f"News checker LLM failed: {e}")
        llm_result = {
            "credibility":        "unverified",
            "credibility_score":  50,
            "credibility_reason": "LLM analysis unavailable.",
            "fake_check":         "unverified",
            "fake_reason":        "LLM analysis unavailable.",
            "key_claims":         [],
            "red_flags":          [],
        }

    # ── Derive overall verdict ────────────────────────────────────────────────
    cred  = llm_result.get("credibility", "speculative")
    fake  = llm_result.get("fake_check",  "unverified")
    score = int(llm_result.get("credibility_score", 50))

    if cred == "concrete" and fake == "credible":
        overall = "VERIFIED"
    elif cred in ("false",) or fake == "likely_false":
        overall = "LIKELY FALSE"
    elif cred == "misleading" or fake == "potentially_misleading":
        overall = "USE CAUTION"
    elif cred == "speculative" or fake == "unverified":
        overall = "UNVERIFIED"
    else:
        overall = "UNVERIFIED"

    return {
        "credibility":        cred,
        "credibility_score":  score,
        "credibility_reason": llm_result.get("credibility_reason", ""),
        "fake_check":         fake,
        "fake_reason":        llm_result.get("fake_reason", ""),
        "key_claims":         llm_result.get("key_claims", []),
        "red_flags":          llm_result.get("red_flags", []),
        "trending":           trending_label,
        "trending_reason":    trending_reason,
        "source_count":       source_count,
        "overall":            overall,
    }
