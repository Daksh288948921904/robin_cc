"""
OSI News — Multi-Source Summary Generator
==========================================
Synthesises a publishable news article from a main article +
ONLY the similar/relevant coverage articles found by the similarity search.

The output is a channel-ready article that:
  • Uses ONLY the main article + its matching coverage (not all 130 articles)
  • Weaves all source perspectives into one coherent narrative
  • Names every contributing outlet explicitly
  • Is attribution-clean and publication-ready
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_groq_client():
    """Build a bare Groq client from environment variables."""
    from groq import Groq
    from dotenv import load_dotenv
    load_dotenv()

    # Try key pool: GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3 …
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("No GROQ_API_KEY found in environment")
    return Groq(api_key=key)


def _build_prompt(main_article: Dict, coverage: List[Dict]) -> str:
    """
    Build the synthesis prompt using ONLY the main article and its
    relevant coverage articles — never the full article pool.
    """
    main_title  = main_article.get("heading", "Untitled")
    main_source = main_article.get("source_name", "Unknown")
    main_body   = (main_article.get("story", "") or "")[:2000]

    # Build source digest from coverage articles only
    digest_lines = []
    for i, art in enumerate(coverage, 1):
        src   = art.get("source_name", "Unknown")
        title = art.get("heading", "")
        body  = (art.get("story", "") or "")[:600]
        url   = art.get("source_url", "")
        sim   = art.get("similarity", 0)
        digest_lines.append(
            f"SOURCE {i} — {src}  (relevance: {int(sim*100)}%)\n"
            f"Headline: {title}\n"
            f"Content:  {body}\n"
            f"URL:      {url}"
        )

    source_digest = "\n\n".join(digest_lines) if digest_lines else "(No additional coverage found)"
    today = datetime.utcnow().strftime("%B %d, %Y")
    n_sources = 1 + len(coverage)

    # Collect every source headline so the model knows exactly what to avoid
    all_source_headlines = [main_title] + [art.get("heading", "") for art in coverage if art.get("heading")]
    forbidden_block = "\n".join(f'  ✗ "{h}"' for h in all_source_headlines)

    return f"""You are a senior news editor at robin cc, a global news channel.

TASK: Write a comprehensive, publication-ready news article synthesising {n_sources} outlet(s) on the SAME story.

━━━ PRIMARY SOURCE ({main_source}) ━━━
Headline: {main_title}
{main_body}

━━━ ADDITIONAL COVERAGE ━━━
{source_digest}

━━━ WRITING RULES ━━━
1. LANGUAGE: The entire output — TITLE, BYLINE, LOCATION, all headings, all body paragraphs — MUST be in English only. If any source content is in another language, translate it accurately first, then write the article in English. Never output a single word of any other language.
2. Write 800-1000 words total, split across 6-7 thematic sections.
3. Each section heading MUST be 2–3 words only — short, punchy, and derived from the story's own themes and key actors.
4. Weave ALL source perspectives into ONE unified narrative voice. Do NOT name any outlet, newspaper, or broadcaster anywhere in the article body.
5. Write all facts directly and authoritatively — no "according to", "reported by", or any source attribution in the text.
6. All source credit goes ONLY in the structured Sources list at the end — never inline.
7. Active voice. Clear paragraph breaks. Journalism style.
8. Do NOT fabricate facts, quotes, or details not present above.
9. Derive the most specific dateline location you can from the story content (city or country). If unclear, use the region.
10. Output format EXACTLY as shown below (## marks each section):

━━━ HEADLINE RULES (CRITICAL) ━━━
These source headlines are BANNED — do not copy, paraphrase, or minimally rephrase them:
{forbidden_block}
Your TITLE must be a completely new headline: different words, different angle, stronger verb, or a more specific detail not in any headline above. Think: what is the most important CONSEQUENCE or IMPACT of this story?

TITLE: <your ORIGINAL headline — 8–14 words, must differ from all banned headlines above>
BYLINE: robin cc | {today}
LOCATION: <CITY or COUNTRY where the story is happening>
CATEGORY: <single category label — choose the single best fit from: World, Technology, Politics, Sports, Business, Entertainment, Science, Health, Environment, Crime, Society, Comedy>
---
<strong opening lede — 2–3 sentences, the single most important fact first>

## <2-3 word heading>

<2–3 paragraphs — core facts>

## <2-3 word heading>

<2–3 paragraphs — context, reactions, secondary angles>

## <2-3 word heading>

<1–2 paragraphs — implications or outlook>

Write the article now:
"""


def generate_summary(
    main_article: Dict,
    coverage: List[Dict],
    model: str = "llama-3.3-70b-versatile",
) -> Optional[Dict]:
    """
    Generate a publishable multi-source news article.

    Args:
        main_article: The primary article the user clicked on.
        coverage:     Similar articles from other sources ONLY
                      (output of similarity search, not all articles).
        model:        Groq model to use.

    Returns:
        Dict with keys: title, byline, body, sources_list, generated_at
        or None on failure.
    """
    try:
        client = _get_groq_client()
    except Exception as e:
        logger.error("Groq client init failed: %s", e)
        raise

    prompt = _build_prompt(main_article, coverage)

    system_msg = (
        "You are a professional news editor. Write publication-ready journalism. "
        "Be precise, factual, and name sources explicitly. "
        "CRITICAL: Your entire output must be in English only — title, headings, and body. "
        "If source material is in another language, translate it first, then write in English. "
        "Output ONLY the article in the requested format — no preamble, no commentary."
    )

    # Try primary model, then fall back to fast 8B model on rate limit
    models_to_try = [model, "llama-3.1-8b-instant"]
    last_exc = None
    for attempt_model in models_to_try:
        try:
            response = client.chat.completions.create(
                model=attempt_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.35,
                max_tokens=1100,
            )
            if attempt_model != model:
                logger.info(f"Used fallback model {attempt_model} (primary rate-limited)")
            raw_text = response.choices[0].message.content.strip()
            return _parse_response(raw_text, main_article, coverage)
        except Exception as exc:
            error_str = str(exc)
            if 'rate_limit_exceeded' in error_str or '429' in error_str:
                logger.warning(f"Model {attempt_model} rate-limited, trying next fallback...")
                last_exc = exc
                continue
            logger.error("Groq API call failed: %s", exc)
            raise

    logger.error("All models rate-limited: %s", last_exc)
    raise last_exc


def _parse_response(raw: str, main_article: Dict, coverage: List[Dict]) -> Dict:
    """Parse LLM output into structured fields."""
    import re

    title_match    = re.search(r"TITLE\s*:\s*(.+)",    raw, re.IGNORECASE)
    byline_match   = re.search(r"BYLINE\s*:\s*(.+)",   raw, re.IGNORECASE)
    location_match = re.search(r"LOCATION\s*:\s*(.+)", raw, re.IGNORECASE)
    category_match = re.search(r"CATEGORY\s*:\s*(.+)", raw, re.IGNORECASE)

    def _clean_title(t: str) -> str:
        """Strip markdown bold, angle-bracket placeholders, and stray punctuation."""
        t = re.sub(r"\*{1,2}|\_{1,2}", "", t)     # **bold** or __bold__
        t = re.sub(r"<[^>]+>", "", t)              # <template placeholders>
        t = t.strip(' "\'.,—–-')
        return t

    if title_match:
        title = _clean_title(title_match.group(1))
    else:
        # Fallback 1: first line of the raw response that looks like a headline
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith(("BYLINE", "LOCATION", "CATEGORY", "##", "---", "━")):
                title = _clean_title(line)
                if len(title.split()) >= 4:
                    break
        else:
            # Fallback 2: derive from first ## section heading
            sec_match = re.search(r"^##\s+(.+)$", raw, re.MULTILINE)
            title = sec_match.group(1).strip() if sec_match else (main_article.get("heading") or "News Briefing")

    # Safety net: if the generated title is still too similar to the source headline,
    # reframe it using the first section heading as a different angle.
    source_heading = (main_article.get("heading") or "").strip()
    if source_heading and title:
        gen_words = set(title.lower().split())
        src_words = set(source_heading.lower().split())
        overlap = len(gen_words & src_words) / max(len(src_words), 1)
        if overlap >= 0.65:
            sec_match = re.search(r"^##\s+(.+)$", raw, re.MULTILINE)
            if sec_match:
                section_label = sec_match.group(1).strip()
                subject = " ".join(source_heading.split()[:4])
                title = f"{section_label}: {subject}"
            else:
                first_sent = re.split(r'(?<=[.!?])\s', raw.lstrip(), maxsplit=1)
                if first_sent and len(first_sent[0].split()) >= 4:
                    title = first_sent[0][:90].rstrip('.')

    logger.info("Parsed title: %s", title)

    byline   = byline_match.group(1).strip()   if byline_match   else f"robin cc | {datetime.utcnow().strftime('%B %d, %Y')}"
    location = location_match.group(1).strip() if location_match else (main_article.get("region") or "")
    category = category_match.group(1).strip() if category_match else "World"

    # Strip header lines to get body
    body = raw
    for pat in (r"TITLE:[^\n]*\n?", r"BYLINE:[^\n]*\n?", r"LOCATION:[^\n]*\n?", r"CATEGORY:[^\n]*\n?", r"-{3,}\n?"):
        body = re.sub(pat, "", body)
    # Strip any LLM-generated Sources block at the end
    body = re.sub(r'\n*\*?\*?Sources?:?\*?\*?\s*\n[\s\S]*$', '', body, flags=re.IGNORECASE)
    body = body.strip()

    # Build deduplicated sources list from main + coverage ONLY
    sources_list = []
    seen: set = set()
    for art in [main_article] + coverage:
        src = art.get("source_name", "Unknown")
        if src not in seen:
            sources_list.append({
                "name":       src,
                "headline":   art.get("heading", ""),
                "url":        art.get("source_url", ""),
                "region":     art.get("region", ""),
                "similarity": art.get("similarity", 1.0 if art is main_article else 0),
            })
            seen.add(src)

    return {
        "title":        title,
        "byline":       byline,
        "location":     location,
        "category":     category,
        "body":         body,
        "raw_text":     raw,
        "sources_list": sources_list,
        "generated_at": datetime.utcnow().isoformat(),
    }
