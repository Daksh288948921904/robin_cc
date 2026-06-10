"""
Groq-powered summariser for Apify social-media scrape results.
Produces "Netizens Reaction" analyses for Twitter and Instagram.
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _groq_client():
    from src.content_generation.groq_pool import get_groq_client
    client = get_groq_client()
    if client is None:
        raise RuntimeError("No GROQ_API_KEY* found in environment")
    return client


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

    client = _groq_client()
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
            "Write a 200–250 word analysis of how Twitter/X users are reacting to this news.\n"
            "Cover: overall sentiment, dominant narratives, notable reactions, and key debates.\n"
            "Use markdown headings and bullet points where helpful. No preamble — start directly."
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
        resp = client.chat.completions.create(
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
            max_tokens=600,
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
