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

_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """You are a senior investigative fact-checker and news authenticity analyst at a professional newsroom.
Your job is to rigorously evaluate whether a news article is credible, speculative, misleading, or false.

Core rules:
- Every reason field must cite SPECIFIC text from the article (quote phrases directly where possible).
- If anything is flagged as non-credible, speculative, negative, or misleading, your reason must be 2-3 sentences explaining exactly what triggered that judgement.
- Do NOT write generic reasons. "The article uses hedging language" is insufficient — name the exact phrases.
- Do not hallucinate facts not present in the article."""

_PROMPT_TEMPLATE = """Analyse this news article and return ONLY a JSON object — no markdown, no explanation.

HEADLINE: {headline}

BODY (first 800 chars):
{body_excerpt}

SOURCES COVERING THIS STORY: {source_count} independent source(s)
SOURCE NAMES: {source_names}

Return this exact JSON structure:
{{
  "credibility": "concrete|speculative|misleading|false",
  "credibility_score": <integer 0-100, where 100 = fully concrete, 0 = clearly false>,
  "credibility_reason": "<2-3 sentences citing specific text — explain what makes this concrete, speculative, misleading, or false>",
  "fake_check": "credible|unverified|potentially_misleading|likely_false",
  "fake_reason": "<2-3 sentences citing specific evidence — explain the sourcing quality and any authenticity concerns>",
  "tone": "positive|negative|neutral",
  "tone_reason": "<2-3 sentences citing specific phrases — explain the emotional charge and framing>",
  "speculation_indicators": ["<exact hedging phrase from article, e.g. 'sources say', 'could trigger'>"],
  "negative_framing": ["<exact alarming/charged phrase from article when tone is negative — empty list if neutral/positive>"],
  "key_claims": ["<specific verifiable claim 1>", "<specific verifiable claim 2>", "<claim 3 if present>"],
  "red_flags": ["<specific concern 1 or empty list if none>"]
}}

Definitions — be strict:
- concrete: named officials on record, verifiable data, official documents, confirmed events
- speculative: hedging language (could, may, might, reportedly, sources say, is expected to) with no named on-record attribution
- misleading: factual but framed to distort — sensational headline, selective omission, decontextualised quotes
- false: directly contradicts known facts, official records, or multiple authoritative sources

- credible: story is well-sourced, named attributions, no red flags
- unverified: story may be true but relies on unnamed sources, single source, or lacks confirmation
- potentially_misleading: framing, headline, or selective context raises concern even if facts are correct
- likely_false: material claim contradicts known facts or authoritative sources

- positive: optimistic, constructive, or uplifting framing dominates
- negative: alarming, threatening, critical, distressing, or sensational framing dominates — even if factual
- neutral: factual, balanced, dry reporting with no strong emotional charge

Score guide:
90-100: Named officials, official data, multiple independent sources
70-89:  Named sources but limited corroboration, or single authoritative source
50-69:  Unnamed sources, hedging language throughout, plausible but unverified
30-49:  Heavy speculation, no named sources, significant unverified claims
0-29:   Contradicts known facts, likely fabricated, no credible sourcing"""


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

    # ── Levels 1 & 2: Groq LLM (key rotation + model fallback via pool) ──────
    llm_result = {}
    _prompt = _PROMPT_TEMPLATE.format(
        headline=headline,
        body_excerpt=body_excerpt,
        source_count=source_count,
        source_names=", ".join(source_names) or "Unknown",
    )
    try:
        from src.utils.groq_pool import groq_completion
        resp = groq_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": _prompt},
            ],
            model=_GROQ_MODEL,
            temperature=0.1,
            max_tokens=800,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        llm_result = json.loads(raw)
        logger.info(f"News check credibility={llm_result.get('credibility')} fake={llm_result.get('fake_check')} for: {headline[:60]}")
    except Exception as e:
        logger.warning(f"News checker pool exhausted: {e}")
        llm_result = {
            "credibility":           "unverified",
            "credibility_score":     50,
            "credibility_reason":    "All API keys rate-limited — please try again in a moment.",
            "fake_check":            "unverified",
            "fake_reason":           "All API keys rate-limited — please try again in a moment.",
            "tone":                  "neutral",
            "tone_reason":           "All API keys rate-limited — please try again in a moment.",
            "speculation_indicators": [],
            "negative_framing":      [],
            "key_claims":            [],
            "red_flags":             [],
        }

    # ── Derive overall verdict ────────────────────────────────────────────────
    cred  = llm_result.get("credibility", "speculative")
    fake  = llm_result.get("fake_check",  "unverified")
    score = int(llm_result.get("credibility_score", 50))
    tone  = llm_result.get("tone", "neutral")

    if cred == "concrete" and fake == "credible":
        overall = "VERIFIED"
    elif cred == "false" or fake == "likely_false":
        overall = "LIKELY FALSE"
    elif cred == "misleading" or fake == "potentially_misleading":
        overall = "USE CAUTION"
    elif cred == "speculative" or fake == "unverified":
        overall = "UNVERIFIED"
    else:
        overall = "UNVERIFIED"

    # ── Build alert_reason for any flagged condition ──────────────────────────
    alert_parts = []
    if overall != "VERIFIED":
        cr = llm_result.get("credibility_reason", "")
        fr = llm_result.get("fake_reason", "")
        if cr:
            alert_parts.append(cr)
        if fr and fr != cr:
            alert_parts.append(fr)
    if tone == "negative":
        tr = llm_result.get("tone_reason", "")
        if tr:
            alert_parts.append(tr)
    if score < 65 and not alert_parts:
        alert_parts.append(f"Credibility score is low ({score}/100). Treat claims with caution.")
    alert_reason = " ".join(alert_parts)

    return {
        "credibility":            cred,
        "credibility_score":      score,
        "credibility_reason":     llm_result.get("credibility_reason", ""),
        "fake_check":             fake,
        "fake_reason":            llm_result.get("fake_reason", ""),
        "tone":                   tone,
        "tone_reason":            llm_result.get("tone_reason", ""),
        "speculation_indicators": llm_result.get("speculation_indicators", []),
        "negative_framing":       llm_result.get("negative_framing", []),
        "key_claims":             llm_result.get("key_claims", []),
        "red_flags":              llm_result.get("red_flags", []),
        "alert_reason":           alert_reason,
        "trending":               trending_label,
        "trending_reason":        trending_reason,
        "source_count":           source_count,
        "overall":                overall,
    }
