"""
OSI News Automation – Flow Planner
====================================
Given the story type and the aspect inventory produced by the aspect
chunker, asks the Groq LLM (JSON mode, ~150 output tokens) to design
the optimal section structure for this specific story.

Each section in the plan includes:
  heading       — 3-5 word specific heading (not generic)
  aspects       — list of aspect labels to draw chunks from
  tone          — writing tone for the section generator
  word_target   — approximate target word count

If the LLM call fails, a hard-coded fallback plan for the story type
is used instead so the pipeline never blocks.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from loguru import logger

from src.content_generation.models import FlowPlan, SectionPlan

# ─────────────────────────────────────────────────────────────────
# FALLBACK PLANS  (used if Groq is unavailable)
# ─────────────────────────────────────────────────────────────────

_FALLBACK_PLANS: Dict[str, List[Dict]] = {
    "conflict_crime": [
        {"heading": "How It Began", "aspects": ["event_core", "military_operations"], "tone": "urgent", "word_target": 180},
        {"heading": "Lives and Losses", "aspects": ["casualties_damage", "civilian_testimony"], "tone": "human", "word_target": 160},
        {"heading": "Official Positions", "aspects": ["government_response"], "tone": "critical", "word_target": 140},
        {"heading": "World Reacts", "aspects": ["international_reaction", "expert_analysis"], "tone": "factual", "word_target": 130},
        {"heading": "What Comes Next", "aspects": ["future_developments", "legal_judicial"], "tone": "analytical", "word_target": 130},
    ],
    "political": [
        {"heading": "The Decision Made", "aspects": ["event_core", "political_dynamics"], "tone": "factual", "word_target": 170},
        {"heading": "Who Holds the Cards", "aspects": ["government_response", "political_dynamics"], "tone": "critical", "word_target": 150},
        {"heading": "Voices From the Floor", "aspects": ["expert_analysis", "civilian_testimony"], "tone": "balanced", "word_target": 140},
        {"heading": "Economic Ripples", "aspects": ["economic_impact"], "tone": "analytical", "word_target": 120},
        {"heading": "The Road Ahead", "aspects": ["future_developments", "international_reaction"], "tone": "forward", "word_target": 130},
    ],
    "business": [
        {"heading": "The Numbers That Matter", "aspects": ["event_core", "economic_impact"], "tone": "data-driven", "word_target": 180},
        {"heading": "Market Reaction", "aspects": ["economic_impact", "future_developments"], "tone": "analytical", "word_target": 150},
        {"heading": "Industry Voices", "aspects": ["expert_analysis", "government_response"], "tone": "balanced", "word_target": 130},
        {"heading": "Consumer and Worker Impact", "aspects": ["civilian_testimony", "humanitarian_situation"], "tone": "human", "word_target": 120},
        {"heading": "What Investors Watch Next", "aspects": ["future_developments", "international_reaction"], "tone": "forward", "word_target": 120},
    ],
    "science_health": [
        {"heading": "The Finding", "aspects": ["event_core", "science_findings"], "tone": "precise", "word_target": 170},
        {"heading": "How They Found It", "aspects": ["science_findings", "background_context"], "tone": "methodical", "word_target": 150},
        {"heading": "What It Means for People", "aspects": ["health_impact", "civilian_testimony"], "tone": "human", "word_target": 140},
        {"heading": "Expert Perspectives", "aspects": ["expert_analysis"], "tone": "balanced", "word_target": 120},
        {"heading": "Next Steps in Research", "aspects": ["future_developments"], "tone": "forward", "word_target": 110},
    ],
    "human_interest": [
        {"heading": "One Person's Story", "aspects": ["event_core", "civilian_testimony"], "tone": "narrative", "word_target": 200},
        {"heading": "A Wider Problem", "aspects": ["background_context", "humanitarian_situation"], "tone": "contextual", "word_target": 160},
        {"heading": "Voices of Those Affected", "aspects": ["civilian_testimony", "expert_analysis"], "tone": "personal", "word_target": 150},
        {"heading": "What the System Says", "aspects": ["government_response", "legal_judicial"], "tone": "critical", "word_target": 130},
        {"heading": "A Fragile Path Forward", "aspects": ["future_developments"], "tone": "hopeful", "word_target": 110},
    ],
    "sports": [
        {"heading": "The Final Score", "aspects": ["event_core", "sports_result"], "tone": "direct", "word_target": 150},
        {"heading": "How the Game Unfolded", "aspects": ["sports_narrative"], "tone": "narrative", "word_target": 200},
        {"heading": "Standout Performances", "aspects": ["sports_narrative", "expert_analysis"], "tone": "analytical", "word_target": 140},
        {"heading": "What It Means for the Table", "aspects": ["sports_result", "future_developments"], "tone": "contextual", "word_target": 120},
        {"heading": "Post-Match Reactions", "aspects": ["civilian_testimony", "expert_analysis"], "tone": "quotes-heavy", "word_target": 130},
    ],
    "breaking_news": [
        {"heading": "What Just Happened", "aspects": ["event_core"], "tone": "urgent", "word_target": 160},
        {"heading": "First Confirmed Details", "aspects": ["casualties_damage", "military_operations"], "tone": "factual", "word_target": 150},
        {"heading": "Official Response", "aspects": ["government_response"], "tone": "direct", "word_target": 130},
        {"heading": "Eyewitness Accounts", "aspects": ["civilian_testimony"], "tone": "human", "word_target": 120},
        {"heading": "What We Know and What We Don't", "aspects": ["future_developments", "background_context"], "tone": "transparent", "word_target": 130},
    ],
    "analysis": [
        {"heading": "The Core Argument", "aspects": ["event_core", "expert_analysis"], "tone": "analytical", "word_target": 180},
        {"heading": "Historical Precedent", "aspects": ["background_context", "legal_judicial"], "tone": "contextual", "word_target": 150},
        {"heading": "Who Gains, Who Loses", "aspects": ["political_dynamics", "economic_impact"], "tone": "critical", "word_target": 140},
        {"heading": "Counter-Arguments Examined", "aspects": ["expert_analysis", "international_reaction"], "tone": "balanced", "word_target": 130},
        {"heading": "Where This Leads", "aspects": ["future_developments"], "tone": "forward", "word_target": 120},
    ],
    "general": [
        {"heading": "The Development", "aspects": ["event_core"], "tone": "factual", "word_target": 170},
        {"heading": "Context and Background", "aspects": ["background_context"], "tone": "contextual", "word_target": 140},
        {"heading": "Responses and Reactions", "aspects": ["government_response", "expert_analysis"], "tone": "balanced", "word_target": 140},
        {"heading": "Impact on People", "aspects": ["civilian_testimony", "humanitarian_situation"], "tone": "human", "word_target": 130},
        {"heading": "Looking Forward", "aspects": ["future_developments"], "tone": "forward", "word_target": 110},
    ],
}


# ─────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are a senior news editor designing the structure for a single news article.
You will be given:
  - The story type
  - The aspect inventory (which thematic aspects have source material, and how many chunks each)
  - Source quality (rich / adequate / thin)

Your job: Design an ordered section plan for a 700-900 word article.

RULES:
1. Return ONLY valid JSON — no prose, no markdown fences.
2. Plan 5-7 sections. Each section has:
   - heading: 3-5 words, SPECIFIC to THIS story (not generic like "Background" or "Conclusion")
   - aspects: list of 1-3 aspect labels from the inventory to draw source material from
   - tone: one of urgent / factual / human / critical / analytical / balanced / narrative / forward
   - word_target: integer 100-220
3. Only use aspects that appear in the provided inventory.
4. The final section must advance the story — not summarise it.
5. Do NOT add a "lede_aspects" key — the lede is always built from event_core.

Output format (strict):
{
  "sections": [
    {"heading": "...", "aspects": ["..."], "tone": "...", "word_target": 180},
    ...
  ],
  "total_word_target": 800
}"""


# ─────────────────────────────────────────────────────────────────
# FLOW PLANNER
# ─────────────────────────────────────────────────────────────────

def plan_article_flow(
    story_type: str,
    aspect_inventory: Dict[str, int],
    source_quality: str,
    source_count: int,
    groq_client,
) -> FlowPlan:
    """
    Ask the Groq LLM to design an optimal section structure.

    Args:
        story_type:        e.g. "conflict_crime"
        aspect_inventory:  {aspect_label: chunk_count} from aspect_chunker
        source_quality:    "rich" | "adequate" | "thin"
        source_count:      number of source articles
        groq_client:       active Groq client instance

    Returns:
        FlowPlan — falls back to hard-coded plan if LLM call fails.
    """
    if not aspect_inventory:
        return _fallback_plan(story_type)

    # Build inventory description string
    inv_lines = [f"  {asp}: {cnt} chunk(s)" for asp, cnt in sorted(aspect_inventory.items(), key=lambda x: -x[1])]
    inventory_text = "\n".join(inv_lines)

    user_msg = (
        f"Story type: {story_type}\n"
        f"Source quality: {source_quality}\n"
        f"Source count: {source_count}\n\n"
        f"Aspect inventory:\n{inventory_text}\n\n"
        f"Design the section plan now."
    )

    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    try:
        response = groq_client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=400,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        sections = _parse_sections(data.get("sections", []), aspect_inventory)
        if not sections:
            raise ValueError("No valid sections parsed from LLM output")

        total = sum(s.word_target for s in sections)
        plan = FlowPlan(
            story_type=story_type,
            lede_aspects=["event_core"],
            sections=sections,
            total_word_target=total,
        )
        logger.info(f"Flow plan: {len(sections)} sections, ~{total} words")
        return plan

    except Exception as e:
        logger.warning(f"Flow planner LLM call failed ({e}), using fallback plan")
        return _fallback_plan(story_type)


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _parse_sections(raw_sections: list, inventory: Dict[str, int]) -> List[SectionPlan]:
    valid_aspects = set(inventory.keys())
    parsed = []
    for s in raw_sections:
        heading = str(s.get("heading", "")).strip()
        aspects = [a for a in s.get("aspects", []) if a in valid_aspects]
        tone    = str(s.get("tone", "factual"))
        target  = int(s.get("word_target", 150))

        if not heading or not aspects:
            continue  # skip malformed sections

        parsed.append(SectionPlan(
            heading=heading,
            aspects=aspects,
            tone=tone,
            word_target=target,
        ))
    return parsed


def _fallback_plan(story_type: str) -> FlowPlan:
    raw = _FALLBACK_PLANS.get(story_type) or _FALLBACK_PLANS["general"]
    sections = [
        SectionPlan(
            heading=s["heading"],
            aspects=s["aspects"],
            tone=s["tone"],
            word_target=s["word_target"],
        )
        for s in raw
    ]
    total = sum(s.word_target for s in sections)
    logger.info(f"Using fallback flow plan for '{story_type}': {len(sections)} sections")
    return FlowPlan(
        story_type=story_type,
        lede_aspects=["event_core"],
        sections=sections,
        total_word_target=total,
    )
