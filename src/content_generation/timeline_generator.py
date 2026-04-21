"""
OSI News — Timeline Generator
===============================
Calls Groq to extract a short, chronological event timeline from
the main article and its coverage sources.

Returns a list of {time, event} dicts ordered oldest → newest.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_groq_client():
    from groq import Groq
    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("No GROQ_API_KEY found in environment")
    return Groq(api_key=key)


def _build_prompt(main_article: Dict, coverage: List[Dict]) -> str:
    main_title  = main_article.get("heading", "Untitled")
    main_source = main_article.get("source_name", "Unknown")
    main_pub    = main_article.get("publish_date", "")
    main_body   = (main_article.get("story", "") or "")[:1500]

    digest_lines = []
    for i, art in enumerate(coverage[:8], 1):
        src   = art.get("source_name", "Unknown")
        title = art.get("heading", "")
        pub   = art.get("publish_date", "")
        body  = (art.get("story", "") or "")[:400]
        digest_lines.append(
            f"SOURCE {i} — {src}  (published: {pub})\n"
            f"Headline: {title}\n"
            f"Content:  {body}"
        )

    source_digest = "\n\n".join(digest_lines) if digest_lines else "(No additional coverage)"

    return f"""You are a timeline editor. Extract a CHRONOLOGICAL timeline of key events from these news sources covering the SAME story.

PRIMARY SOURCE ({main_source}) — published: {main_pub}
Headline: {main_title}
{main_body}

ADDITIONAL COVERAGE:
{source_digest}

OUTPUT RULES:
- 4 to 8 events, chronological (oldest first)
- "time" field: use real clock time or date if available (e.g. "09:30", "Apr 18", "Monday"), else use relative terms ("Earlier", "Days ago", "Recently", "Today")
- "event" field: 4-8 words MAXIMUM — ultra-concise, factual, active voice
- English only, no fabrication
- Output ONLY a raw JSON array — no markdown, no code fences, no explanation

Example output:
[
  {{"time": "Apr 15", "event": "Talks collapse, deal withdrawn"}},
  {{"time": "Apr 16", "event": "Emergency summit convened"}},
  {{"time": "09:00", "event": "Leaders sign ceasefire agreement"}},
  {{"time": "Today", "event": "Markets rally on news"}}
]

Now extract the timeline:"""


def generate_timeline(
    main_article: Dict,
    coverage: List[Dict],
    model: str = "llama-3.3-70b-versatile",
) -> Optional[List[Dict]]:
    """
    Generate a chronological event timeline using Groq.

    Args:
        main_article: Primary article dict.
        coverage:     Similar articles from other sources.
        model:        Groq model to use.

    Returns:
        List of {time, event} dicts or raises on failure.
    """
    client = _get_groq_client()
    prompt = _build_prompt(main_article, coverage)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise timeline extraction engine. "
                    "Output ONLY a raw JSON array — no markdown code fences, "
                    "no commentary, no extra text. Just the JSON array."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.15,
        max_tokens=450,
    )

    raw = response.choices[0].message.content.strip()
    return _parse(raw)


def _parse(raw: str) -> List[Dict]:
    """Parse LLM output → clean list of {time, event} dicts."""
    # Strip markdown code fences if the model added them
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Extract the first JSON array found
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if arr_match:
        raw = arr_match.group(0)

    events = json.loads(raw)

    cleaned = []
    for ev in events:
        if isinstance(ev, dict) and ev.get("time") and ev.get("event"):
            cleaned.append(
                {
                    "time":  str(ev["time"]).strip()[:25],
                    "event": str(ev["event"]).strip()[:100],
                }
            )

    return cleaned
