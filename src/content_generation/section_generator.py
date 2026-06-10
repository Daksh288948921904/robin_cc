"""
OSI News Automation – Section Generator
=========================================
For each section in the flow plan, retrieves the relevant chunks from
Qdrant and generates the section body via Groq LLM.

One Groq call per section (typically 5-7 calls per article).
Each call receives:
  - The section heading and tone
  - Source chunks filtered by the section's aspects
  - Verbatim quotes extracted from those chunks (must appear in output)

Also generates the article lede (2-3 sentences) as a separate call.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from loguru import logger

from src.content_generation.models import FlowPlan, GeneratedSection, SectionPlan
from src.content_generation.aspect_chunker import collect_quotes_from_chunks


# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT (injected once — same for all sections)
# ─────────────────────────────────────────────────────────────────

_SECTION_SYSTEM = """You are a senior investigative journalist at robin cc.
Write ONE section of a news article.

MANDATORY RULES:
• Use ONLY the source chunks provided. Do NOT fabricate any fact, name, or quote.
• Active voice throughout. No passive constructions unless essential.
• Do NOT write "according to", "reported by", "sources say", or name any outlet.
• If verbatim quotes are listed, embed AT LEAST ONE naturally in the paragraph.
  Use the exact words between quotation marks — do not paraphrase.
• No meta-commentary ("This section covers…"). Start writing the journalism directly.
• No section heading — just the body paragraphs.
• Vary sentence length: mix short punchy sentences with longer analytical ones."""


# ─────────────────────────────────────────────────────────────────
# SECTION PROMPT BUILDER
# ─────────────────────────────────────────────────────────────────

def _build_section_prompt(
    section: SectionPlan,
    chunks: List[Dict],
    quotes: List[str],
) -> str:
    # Build chunk digest (up to 5 chunks, 500 chars each)
    chunk_lines = []
    for i, chunk in enumerate(chunks[:5], 1):
        src   = chunk.get("source_name", "Unknown")
        text  = chunk.get("chunk_text", "")[:500]
        chunk_lines.append(f"[Chunk {i} — {src}]\n{text}")
    chunk_block = "\n---\n".join(chunk_lines) or "(no source chunks available)"

    # Quote block
    if quotes:
        quote_lines = "\n".join(f'  • "{q}"' for q in quotes[:4])
        quote_block = f"\nVERBATIM QUOTES (embed at least one exactly as written):\n{quote_lines}\n"
    else:
        quote_block = ""

    # Tone instruction
    tone_hints = {
        "urgent":      "Write with immediacy. Short sentences. Most important fact first.",
        "factual":     "Write directly and precisely. Every claim grounded in the chunks.",
        "human":       "Centre a human experience. Specific people, not abstract categories.",
        "critical":    "Expose contradictions between what officials say and what the evidence shows.",
        "analytical":  "Explain causes and consequences. Label analytical interpretation explicitly.",
        "balanced":    "Present multiple perspectives without editorialising.",
        "narrative":   "Tell a story with a beginning, turning point, and outcome.",
        "forward":     "Focus on what happens next. Confirmed future events, not speculation.",
        "data-driven": "Lead with the most significant number. Every claim needs a figure.",
        "quotes-heavy":"Let the quotes carry the section. Weave 2-3 quotes with short bridging prose.",
        "direct":      "State the result in the first clause. No build-up.",
        "precise":     "Precise language. Distinguish confirmed findings from preliminary results.",
    }
    tone_instruction = tone_hints.get(section.tone, "Write directly and factually.")

    return (
        f"SECTION: {section.heading}\n"
        f"TONE: {section.tone} — {tone_instruction}\n"
        f"TARGET LENGTH: {section.word_target} words (±20%)\n"
        f"{quote_block}\n"
        f"SOURCE CHUNKS:\n---\n{chunk_block}\n---\n\n"
        f"Write the section body now (no heading, no preamble):"
    )


def _build_lede_prompt(chunks: List[Dict], quotes: List[str], topic: str) -> str:
    chunk_lines = []
    for i, chunk in enumerate(chunks[:3], 1):
        src  = chunk.get("source_name", "Unknown")
        text = chunk.get("chunk_text", "")[:400]
        chunk_lines.append(f"[Chunk {i} — {src}]\n{text}")
    chunk_block = "\n---\n".join(chunk_lines) or ""

    return (
        f"TASK: Write the opening lede for a news article about: {topic}\n\n"
        f"RULES:\n"
        f"• 2-3 sentences only.\n"
        f"• Most newsworthy fact first — the consequence or revelation, not the background.\n"
        f"• No outlet names. Active voice. No hedging.\n"
        f"• Do NOT write a heading.\n\n"
        f"SOURCE CHUNKS:\n---\n{chunk_block}\n---\n\n"
        f"Write the lede now:"
    )


# ─────────────────────────────────────────────────────────────────
# GROQ CALL WITH SIMPLE RETRY
# ─────────────────────────────────────────────────────────────────

def _call_groq(
    system: str,
    user: str,
    groq_client,
    max_tokens: int = 600,
    temperature: float = 0.35,
) -> Optional[str]:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    for attempt in range(3):
        try:
            resp = groq_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=0.95,
            )
            text = resp.choices[0].message.content or ""
            return text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                wait = 90 if attempt < 2 else 0
                logger.warning(f"Rate limited (attempt {attempt+1}), waiting {wait}s…")
                if wait:
                    time.sleep(wait)
            else:
                logger.warning(f"Groq call failed (attempt {attempt+1}): {e}")
            if attempt == 2:
                return None
    return None


# ─────────────────────────────────────────────────────────────────
# SECTION GENERATOR — main entry point
# ─────────────────────────────────────────────────────────────────

def generate_sections(
    flow: FlowPlan,
    cluster_id: str,
    topic: str,
    groq_client,
) -> Tuple[str, List[GeneratedSection]]:
    """
    Generate the lede and all body sections for a flow plan.

    Args:
        flow:         FlowPlan from flow_planner.
        cluster_id:   Qdrant cluster id for chunk retrieval.
        topic:        Article topic string (for lede prompt).
        groq_client:  Active Groq client.

    Returns:
        (lede_text, [GeneratedSection, …])
    """
    from src.database.qdrant_store import semantic_search, get_chunks_by_aspect

    # ── Lede ────────────────────────────────────────────────────────
    lede_chunks = semantic_search(
        query_text=topic,
        cluster_id=cluster_id,
        aspects=flow.lede_aspects,
        top_k=3,
    )
    lede_quotes = collect_quotes_from_chunks(lede_chunks)
    lede_prompt = _build_lede_prompt(lede_chunks, lede_quotes, topic)
    lede_text = _call_groq(_SECTION_SYSTEM, lede_prompt, groq_client, max_tokens=150) or ""
    logger.info(f"  Lede generated ({len(lede_text.split())} words)")

    # ── Body sections — generated in parallel (max 3 concurrent Groq calls) ──
    def _gen_one(section: SectionPlan) -> Optional[GeneratedSection]:
        section_query = f"{section.heading} {' '.join(section.aspects)}"
        chunks = semantic_search(
            query_text=section_query,
            cluster_id=cluster_id,
            aspects=section.aspects,
            top_k=5,
        )
        if not chunks:
            chunks = get_chunks_by_aspect(cluster_id, section.aspects, top_k=5)

        quotes = collect_quotes_from_chunks(chunks)
        prompt = _build_section_prompt(section, chunks, quotes)
        token_budget = max(200, int(section.word_target * 1.4))
        body = _call_groq(_SECTION_SYSTEM, prompt, groq_client, max_tokens=token_budget)

        if not body:
            logger.warning(f"  Section '{section.heading}' failed — skipping")
            return None

        lines = body.splitlines()
        body = "\n".join(l for l in lines if not l.strip().startswith("#")).strip()
        return GeneratedSection(
            heading=section.heading,
            body=body,
            aspects_used=section.aspects,
            quotes_embedded=quotes[:4],
            word_count=len(body.split()),
        )

    # Preserve section order: submit with explicit index, collect by index
    generated: List[GeneratedSection] = []
    results: Dict[int, Optional[GeneratedSection]] = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_gen_one, sec): i for i, sec in enumerate(flow.sections)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                logger.warning(f"  Section {idx} raised: {e}")
                result = None
            results[idx] = result

    for i in sorted(results):
        r = results[i]
        if r:
            generated.append(r)
            logger.info(f"  Section '{r.heading}' → {r.word_count} words")

    return lede_text, generated
