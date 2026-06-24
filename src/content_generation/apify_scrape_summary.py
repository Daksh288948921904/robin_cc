"""
Groq-powered summariser for Apify social-media scrape results.
Produces "Netizens Reaction" analyses for Twitter and Instagram.
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _groq_completion(**kwargs):
    from src.utils.groq_pool import groq_completion
    return groq_completion(**kwargs)


def generate_social_summary(platform: str, posts: list, headline: str) -> dict:
    """
    Generate an AI summary of social-media reactions.

    Args:
        platform: "twitter" or "instagram"
        posts:    list of post dicts from apify_scrape
        headline: the news headline being reacted to

    Returns dict:
        {platform, summary (markdown str), posts, generated_at}
    """
    if not posts:
        return {
            "platform":     platform,
            "summary":      "No public posts found for this story.",
            "posts":        [],
            "generated_at": datetime.utcnow().isoformat(),
        }

    model  = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    if platform == "twitter":
        snippets = "\n\n".join(
            f"@{p.get('username','?')} "
            f"({p.get('likes',0)} ❤  {p.get('reposts',0)} 🔁  {p.get('replies',0)} 💬):\n"
            f"{(p.get('text') or '')[:280]}"
            for p in posts[:15]
        )
        prompt = (
            f'News headline: "{headline}"\n\n'
            f"Twitter/X reactions ({len(posts)} tweets):\n{snippets}\n\n"
            "Write a 300–400 word in-depth analysis of how netizens on Twitter/X are reacting "
            "to this news. Structure it as follows:\n\n"
            "1. **Sentiment Overview** — Start with the dominant emotional tone. Classify the "
            "overall sentiment clearly (e.g. angry, excited, hopeful, disappointed, divided, "
            "sarcastic, celebratory, anxious, indifferent). If sentiment is mixed, state the "
            "approximate split (e.g. '~60% supportive, ~30% critical, ~10% mocking').\n\n"
            "2. **Key Perspectives** — Cover EVERY distinct viewpoint found in the tweets. "
            "Group similar opinions together. Include supporters, critics, skeptics, those "
            "making jokes/memes, and any neutral or analytical takes. Do not skip minority "
            "opinions — even a single dissenting voice matters.\n\n"
            "3. **Notable Reactions** — Highlight the most impactful tweets (highest engagement "
            "or most thought-provoking). Quote or closely paraphrase 2-3 standout reactions "
            "with attribution (@username).\n\n"
            "4. **Public Mood** — End with a one-line summary of the overall netizen mood "
            "(e.g. 'Netizens are largely furious and demanding accountability' or "
            "'The reaction is celebratory with widespread national pride').\n\n"
            "Use markdown formatting with headings and bullet points. "
            "No preamble — start directly with the sentiment overview."
        )
    else:
        snippets = "\n\n".join(
            f"@{p.get('author','?')} ({p.get('likes',0)} ❤  {p.get('comments_count',0)} 💬):\n"
            f"Caption: {(p.get('caption') or '')[:200]}\n"
            + "\n".join(f"  → {c.get('text','')}" for c in (p.get("comments") or [])[:3])
            for p in posts[:12]
        )
        prompt = (
            f'News headline: "{headline}"\n\n'
            f"Instagram posts & comments ({len(posts)} posts):\n{snippets}\n\n"
            "Write a 200–250 word analysis of how Instagram users are reacting to this news.\n"
            "Cover: general sentiment in captions and comments, emotional tone, key themes, public mood.\n"
            "Use markdown headings and bullet points where helpful. No preamble — start directly."
        )

    try:
        resp = _groq_completion(
            model=model,
            messages=[
                {
                    "role":    "system",
                    "content": (
                        "You are a social media analyst summarising public reactions to breaking news. "
                        "Be concise, factual, and insightful. Avoid filler phrases."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=1000,
        )
        summary_text = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("Groq summary error (%s): %s", platform, e)
        summary_text = f"Summary generation failed: {e}"

    return {
        "platform":     platform,
        "summary":      summary_text,
        "posts":        posts,
        "generated_at": datetime.utcnow().isoformat(),
    }
