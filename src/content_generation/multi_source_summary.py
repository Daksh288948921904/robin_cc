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
import random
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MALE_FIRST = [
    "Aarav", "Arjun", "Rohan", "Vikram", "Karan", "Nikhil", "Siddharth",
    "Rahul", "Amit", "Aditya", "Varun", "Yash", "Pranav", "Shivam", "Dev",
    "Akash", "Harsh", "Manish", "Rajesh", "Sandeep", "Deepak", "Gaurav",
    "Ishaan", "Kabir", "Lakshay", "Mayank", "Neeraj", "Om", "Parth", "Rishi",
]
_FEMALE_FIRST = [
    "Aanya", "Priya", "Sneha", "Ananya", "Kavya", "Neha", "Pooja", "Riya",
    "Sanya", "Tanvi", "Aditi", "Bhavna", "Divya", "Esha", "Gauri", "Isha",
    "Jyoti", "Kritika", "Lavanya", "Meera", "Nisha", "Pallavi", "Radhika",
    "Shruti", "Tanya", "Urvashi", "Vidya", "Yamini", "Zara", "Simran",
]
_LAST = [
    "Sharma", "Verma", "Singh", "Gupta", "Patel", "Joshi", "Mehta", "Nair",
    "Iyer", "Reddy", "Rao", "Pillai", "Mishra", "Pandey", "Tiwari", "Kumar",
    "Agarwal", "Shah", "Bose", "Chatterjee", "Mukherjee", "Kapoor", "Malhotra",
    "Khanna", "Sinha", "Trivedi", "Desai", "Deshpande", "Kulkarni", "Bhatt",
]


def _random_indian_name() -> str:
    """Return a random Indian full name (male or female)."""
    pool = _MALE_FIRST if random.random() < 0.5 else _FEMALE_FIRST
    return f"{random.choice(pool)} {random.choice(_LAST)}"


def _get_groq_client():
    from src.content_generation.groq_pool import get_groq_client
    client = get_groq_client()
    if client is None:
        raise RuntimeError("No GROQ_API_KEY* found in environment")
    return client


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

    return f"""You are a senior investigative news editor at robin cc, a global news channel with a reputation for deep, authoritative journalism.

TASK: Synthesise {n_sources} source(s) covering the SAME story into ONE original, publication-ready news article. Your article must read as if written by a single expert journalist who has digested every source and is now telling the definitive version of events.

━━━ PRIMARY SOURCE ({main_source}) ━━━
Headline: {main_title}
{main_body}

━━━ ADDITIONAL COVERAGE ━━━
{source_digest}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
━━━ WRITING RULES (ALL MANDATORY) ━━━
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LANGUAGE & LENGTH
• Every word of output — TITLE, SUBTITLE, BYLINE, LOCATION, all section headings, all body paragraphs — MUST be in English only. Translate any non-English source material accurately before writing.
• Total body length: 800–1000 words minimum across 6–7 thematic sections. Do not pad; make every sentence earn its place.

STRUCTURE & NARRATIVE
• Open with a hard-hitting lede of 2–3 sentences: the single most important consequence or revelation first, not background.
• Divide the body into 6–7 sections, each with a 3–5 word heading that names the theme, actor, or tension — not a generic label like "Background" or "Conclusion".
• Do NOT write a conclusion section. The final section must advance the story — a forward-looking development, an unresolved tension, or the next expected move — written as narrative, not summary.
• Each story must have a unique structure. Vary where you place the human element, the data, the quotes, and the stakes to fit this specific story. Never use a template structure.
• Weave all source perspectives into ONE authoritative narrative voice. The reader must not sense multiple sources being stitched together.

QUOTES (CRITICAL — 3 to 4 REQUIRED)
• Extract 3–4 direct quotes verbatim from the source material above. Do NOT paraphrase into indirect speech; use the exact words in quotation marks.
• Embed each quote naturally within the paragraph where it fits the theme — never drop a quote in isolation.
• If a quote SUPPORTS the theme of that section, let it reinforce the narrative.
• If a quote CONTRADICTS what officials or actors are doing on the ground, expose the contradiction explicitly in the surrounding prose: show what they said versus what they did. This tension is your strongest journalistic tool — use it.
• Do NOT fabricate, invent, or approximate any quote. Only use quotes that appear verbatim in the sources above.

ATTRIBUTION & FACTS
• Write all facts directly and authoritatively — no "according to", "reported by", "sources say", or any similar hedge anywhere in the body.
• Do NOT name any outlet, newspaper, broadcaster, or wire service inside the article body.
• Do NOT fabricate any fact, statistic, name, date, or detail not explicitly present in the sources above.
• All source credit goes solely in the Sources block after the article — never inline.

STYLE
• Active voice throughout. Cut all passive constructions unless essential.
• Vary sentence length: mix short punchy sentences with longer analytical ones.
• Journalism style: precise nouns, strong verbs, no filler adjectives.
• No editorialising or opinion — present facts and let the quotes speak.

━━━ HEADLINE RULES (CRITICAL) ━━━
These source headlines are BANNED — do not copy, paraphrase, or lightly rephrase any of them:
{forbidden_block}
Your TITLE must be an entirely original headline: new angle, stronger verb, or the single most important CONSEQUENCE of the story that the source headlines missed. Ask yourself: what changes because of this story? Start there.

━━━ SUBTITLE RULE (CRITICAL — ENFORCED) ━━━
The SUBTITLE must be a COMPLETE, DETAILED sentence — no character limit, just write a full, natural sentence.
It must name at least one key actor AND one specific consequence or development.
It must NOT share more than 3 words with the title — it must take a DIFFERENT angle, add NEW information not in the title, and answer WHO did WHAT and with WHAT CONSEQUENCE.
WRONG (too similar to title): Title is "Iran Targets Musk Firms in Gulf" → subtitle "Iran threatens Elon Musk's companies in the Middle East, Iranian state media declares." ← BANNED — same content, different words
RIGHT: "Tehran's warning, issued through state-run IRNA, specifically names SpaceX Starlink and Tesla operations as potential targets amid escalating regional tensions in the Gulf region."
WRONG (fragment): "Experts warn of impending monetary policy shift." ← incomplete context
RIGHT: "The Federal Reserve is expected to raise interest rates by 50 basis points next week as inflation data forces policymakers to act faster than markets anticipated, pressuring global bond markets."

━━━ OUTPUT FORMAT (follow exactly — do not add or remove any field) ━━━

TITLE: <your ORIGINAL headline — complete, strong verb, specific — must differ from every banned headline above>
SUBTITLE: <COMPLETE sentence, names key actor + specific consequence — see SUBTITLE RULE above>
BYLINE: robin cc | {today}
LOCATION: <most specific city or country dateline you can derive from the content>
CATEGORY: <single best-fit label — World / Technology / Politics / Sports / Business / Entertainment / Science / Health / Environment / Crime / Society / Comedy>
---
<lede — 2–3 sentences, most important fact first, no source names>

## <3–5 word section heading>

<2–3 paragraphs of core facts; embed 1–2 quotes here if they fit>

## <3–5 word section heading>

<2–3 paragraphs of context, key actors, escalation or reaction; embed quotes that support or contradict>

## <3–5 word section heading>

<2–3 paragraphs of secondary angles, human dimension, data>

## <3–5 word section heading>

<1–2 paragraphs of stakes, implications, or next expected development — written as narrative, not conclusion>

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
    prompt = _build_prompt(main_article, coverage)

    system_msg = (
        "You are a senior investigative journalist and news editor at robin cc, a global news channel. "
        "Your writing is authoritative, precise, and publication-ready — the standard of Reuters or AP but with narrative depth. "
        "You synthesise multiple sources into a single definitive account, integrating real quotes to expose agreements and contradictions. "
        "CRITICAL: Your entire output — every word — must be in English only. "
        "Translate any non-English source material accurately before writing. "
        "Output ONLY the formatted article. No preamble, no meta-commentary, no 'Here is the article' opener. "
        "Follow the output format exactly. Never skip a field."
    )

    from src.utils.groq_pool import groq_completion
    response = groq_completion(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ],
        model=model,
        temperature=0.4,
        max_tokens=2048,
    )
    raw_text = response.choices[0].message.content.strip()
    return _parse_response(raw_text, main_article, coverage)


def _parse_response(raw: str, main_article: Dict, coverage: List[Dict]) -> Dict:
    """Parse LLM output into structured fields."""
    import re

    title_match    = re.search(r"TITLE\s*:\s*(.+)",    raw, re.IGNORECASE)
    subtitle_match = re.search(r"SUBTITLE\s*:\s*(.+)", raw, re.IGNORECASE)
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

    subtitle = subtitle_match.group(1).strip() if subtitle_match else ""
    subtitle = re.sub(r"<[^>]+>", "", subtitle).strip(' "\'')
    subtitle = re.sub(r"\*+", "", subtitle).strip()
    if subtitle.upper() in ("SUB", "SUBTITLE", "NONE", "N/A", ""):
        subtitle = ""

    # Enforce minimum subtitle length — if too short, pull the lede sentence from the body
    if len(subtitle) < 100:
        # Extract first complete sentence from the raw body (after the --- separator)
        sep_pos = raw.find("---")
        body_preview = raw[sep_pos+3:].strip() if sep_pos != -1 else raw
        # Remove section headings and grab first real sentence
        body_preview = re.sub(r'^##[^\n]*\n', '', body_preview, flags=re.MULTILINE)
        first_sentences = re.split(r'(?<=[.!?])\s+', body_preview.strip())
        for sent in first_sentences:
            sent = sent.strip()
            if len(sent) >= 100:
                subtitle = sent[:220].rstrip('.,;') if len(sent) > 220 else sent
                break
            elif len(sent) >= 60 and len(subtitle) == 0:
                subtitle = sent  # better than nothing

    byline   = byline_match.group(1).strip()   if byline_match   else f"robin cc | {datetime.utcnow().strftime('%B %d, %Y')}"
    location = location_match.group(1).strip() if location_match else (main_article.get("region") or "")
    location = re.sub(r"\*+", "", location).strip()
    category = category_match.group(1).strip() if category_match else "World"

    # Use the --- separator as primary split point; fall back to line-by-line stripping
    sep_match = re.search(r'^---+\s*$', raw, re.MULTILINE)
    if sep_match:
        body = raw[sep_match.end():].strip()
    else:
        body = raw
        for pat in (r"TITLE:[^\n]*\n?", r"SUBTITLE:[^\n]*\n?", r"BYLINE:[^\n]*\n?",
                    r"LOCATION:[^\n]*\n?", r"CATEGORY:[^\n]*\n?", r"-{3,}\n?"):
            body = re.sub(pat, "", body, flags=re.IGNORECASE)
        body = body.strip()

    # Strip any LLM-generated Sources block at the end
    body = re.sub(r'\n*\*?\*?Sources?:?\*?\*?\s*\n[\s\S]*$', '', body, flags=re.IGNORECASE)

    # Strip any metadata lines the LLM echoed into the article body
    # (title, subtitle, byline, location, category sometimes repeat after the separator,
    #  sometimes wrapped in **bold** markdown)
    _meta_to_strip = {
        v.strip().lower()
        for v in (title, subtitle, byline, location, category)
        if v and v.strip()
    }
    # Patterns that should never appear as standalone lines in the body
    _DATE_PAT     = re.compile(
        r'^(?:'
        r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)[,\s]+'
        r'|(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d'
        r'|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}'
        r'|\d{4}-\d{2}-\d{2}'
        r')',
        re.IGNORECASE,
    )
    # A "location dateline" line: short (≤6 words), no sentence-ending verb,
    # matches known location vocabulary or ends with a country/city indicator.
    _DATELINE_PAT = re.compile(
        r'^(?:[A-Z][a-zA-Z\s\.\-]+,\s*[A-Z][a-zA-Z\s]+|[A-Z][A-Z\s]+)$'
    )

    _cleaned_lines = []
    for _line in body.splitlines():
        _s = _line.strip()
        # Strip markdown bold/italic markers before comparing
        _s_clean = re.sub(r'\*+|_{1,2}', '', _s).strip()
        if _s_clean.lower() in _meta_to_strip:
            continue
        if re.match(r'^robin\s+cc\s*\|', _s_clean, re.IGNORECASE):
            continue
        # Drop standalone date lines (e.g. "June 4, 2026", "Thursday, June 4")
        if _DATE_PAT.match(_s_clean):
            continue
        # Drop standalone dateline/location lines (e.g. "Washington D.C.", "NEW DELHI, India")
        # Only drop short lines (≤ 6 words) that look like a city/country dateline
        if _DATELINE_PAT.match(_s_clean) and len(_s_clean.split()) <= 6:
            continue
        _cleaned_lines.append(_line)
    body = "\n".join(_cleaned_lines).strip()

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
        "subtitle":     subtitle,
        "byline":       byline,
        "location":     location,
        "category":     category,
        "body":         body,
        "raw_text":     raw,
        "sources_list": sources_list,
        "reporter":     _random_indian_name(),
        "generated_at": datetime.utcnow().isoformat(),
    }
