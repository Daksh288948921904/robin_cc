"""
OSI News Automation – Intent Classifier
========================================
Keyword-based story-type and aspect classification.
No model load — runs in microseconds.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from loguru import logger

# ─────────────────────────────────────────────────────────────────
# LABEL DEFINITIONS  (retained for reference / external consumers)
# ─────────────────────────────────────────────────────────────────

STORY_TYPE_LABELS: Dict[str, str] = {
    "breaking_news":   "This is a breaking news story about a sudden, recent event",
    "political":       "This is a political news story about elections, government, or policy",
    "business":        "This is a business or economy story about markets, trade, or companies",
    "science_health":  "This is a science or health story about research, medicine, or technology",
    "conflict_crime":  "This is a conflict or crime story about war, military, terrorism, or crime",
    "human_interest":  "This is a human interest story about personal experiences or community impact",
    "sports":          "This is a sports story about a match, tournament, or athlete",
    "analysis":        "This is an analysis or investigative piece examining a complex issue in depth",
    "general":         "This is a general news story that does not fit a specific category",
}

ASPECT_LABELS: Dict[str, str] = {
    "event_core":             "This text describes the central event or main development of the story",
    "background_context":     "This text provides historical background or context explaining how we arrived here",
    "casualties_damage":      "This text reports deaths, injuries, destruction, or displacement figures",
    "government_response":    "This text contains official government statements, decisions, or policy actions",
    "international_reaction": "This text describes reactions from other countries or international organisations",
    "expert_analysis":        "This text contains expert commentary, academic analysis, or professional opinion",
    "civilian_testimony":     "This text contains first-hand accounts or quotes from ordinary citizens or witnesses",
    "economic_impact":        "This text describes financial, trade, market, or economic consequences",
    "humanitarian_situation": "This text describes aid, refugee conditions, displacement, or civilian welfare",
    "legal_judicial":         "This text covers laws, court proceedings, prosecutions, or legal rulings",
    "future_developments":    "This text discusses future actions, upcoming events, or next steps",
    "military_operations":    "This text describes military tactics, troop movements, weapons, or combat operations",
    "political_dynamics":     "This text covers power struggles, party positions, electoral dynamics, or alliances",
    "sports_result":          "This text reports scores, match results, standings, or performance statistics",
    "sports_narrative":       "This text tells the story of a match, tournament, or sporting event",
    "science_findings":       "This text reports research results, study findings, or scientific conclusions",
    "health_impact":          "This text describes medical consequences, public health effects, or treatment outcomes",
}


# ─────────────────────────────────────────────────────────────────
# STORY-TYPE KEYWORDS  (keyword-based, no model needed)
# ─────────────────────────────────────────────────────────────────

_STORY_TYPE_KW: Dict[str, List[str]] = {
    "conflict_crime":  ["war", "attack", "killed", "military", "troops", "bomb", "missile",
                        "conflict", "battle", "gunfire", "shooting", "murder", "crime", "police",
                        "arrested", "terror", "explosion", "casualt", "airstrike", "offensive"],
    "political":       ["election", "parliament", "president", "prime minister", "government",
                        "senator", "congress", "vote", "party", "democrat", "republican",
                        "legislation", "policy", "minister", "cabinet", "campaign", "ballot"],
    "business":        ["market", "economy", "trade", "gdp", "inflation", "stock", "company",
                        "revenue", "profit", "investment", "financial", "billion", "startup",
                        "merger", "acquisition", "ceo", "earnings", "recession", "growth"],
    "sports":          ["match", "game", "tournament", "champion", "score", "goal", "league",
                        "player", "coach", "team", "season", "stadium", "final", "win", "lost",
                        "nba", "nfl", "fifa", "wnba", "olympic", "athlete", "playoff"],
    "science_health":  ["research", "study", "scientist", "vaccine", "disease", "health",
                        "medical", "hospital", "treatment", "cancer", "drug", "clinical",
                        "discovery", "published", "journal", "genome", "ai model", "space"],
    "human_interest":  ["community", "family", "inspire", "volunteer", "charity", "rescue",
                        "story of", "overcome", "courage", "local hero", "celebrate", "reunion"],
    "analysis":        ["analysis", "investigat", "deep dive", "explains", "why ", "how ",
                        "context", "examining", "perspective", "opinion", "editorial", "insight"],
    "breaking_news":   ["breaking", "just in", "urgent", "developing", "alert", "latest",
                        "update:", "overnight", "hours ago", "minutes ago", "this morning"],
}


def _keyword_score(text: str, keywords: List[str]) -> int:
    t = text.lower()
    return sum(1 for kw in keywords if kw in t)


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────

def classify_story_type(articles: List[Dict]) -> Dict:
    """
    Classify a cluster of articles into a single story type using keyword matching.
    Runs in microseconds — no model load required.

    Returns:
        {"story_type": str, "confidence": float}
    """
    parts = []
    for art in articles[:5]:
        heading = art.get("heading", "")
        snippet = art.get("story", "")[:300]
        parts.append(f"{heading}. {snippet}")
    cluster_text = " | ".join(parts)

    scores: Dict[str, int] = {
        stype: _keyword_score(cluster_text, kws)
        for stype, kws in _STORY_TYPE_KW.items()
    }

    best = max(scores, key=scores.__getitem__)
    total = sum(scores.values()) or 1
    confidence = scores[best] / total

    if scores[best] == 0:
        best = "general"
        confidence = 0.5

    logger.info(f"Story type: {best} (confidence={confidence:.3f})")
    return {"story_type": best, "confidence": float(confidence)}


def classify_aspects_present(articles: List[Dict], threshold: int = 1) -> List[str]:
    """
    Return aspect labels present in the cluster using keyword matching.
    Always includes "event_core".
    """
    parts = []
    for art in articles:
        parts.append(art.get("heading", "") + ". " + art.get("story", "")[:500])
    cluster_text = " ".join(parts)

    present = ["event_core"]
    for aspect, keywords in _CHUNK_KW.items():
        if _keyword_score(cluster_text, keywords) >= threshold:
            present.append(aspect)

    logger.info(f"Aspects present ({len(present)}): {present}")
    return present


# Keyword sets for instant chunk aspect classification (no model needed).
# Keys match ASPECT_LABELS. Each word/phrase is matched on lowercase text.
_CHUNK_KW: Dict[str, List[str]] = {
    "casualties_damage":      ["killed", "dead", "died", "death", "injur", "wound", "casualt", "fatali", "bodies", "missing", "toll"],
    "military_operations":    ["troops", "soldier", "military", "forces", "airstrike", "strike", "bomb", "missile", "army", "navy", "combat", "weapon", "artillery", "drone", "offensive", "battalion"],
    "government_response":    ["government", "minister", "president", "prime minister", "official", "parliament", "legislat", "policy", "announced", "statement", "declar", "cabinet", "administration"],
    "international_reaction": ["united nations", "nato", "european union", "international", "foreign minister", "sanction", "condemn", "allies", "g7", "g20", "embassy", "diplomacy"],
    "expert_analysis":        ["expert", "analyst", "researcher", "professor", "economist", "according to", "study shows", "findings", "analysis", "think tank", "institute"],
    "civilian_testimony":     ["resident", "witness", "survivor", "victim", "family", "community", "people said", "told reporters", "described", "refugee", "displaced"],
    "economic_impact":        ["economy", "market", "billion", "million", "trade", "gdp", "inflation", "price", "financial", "investment", "stock", "currency", "growth", "recession"],
    "humanitarian_situation": ["aid", "refugee", "displaced", "shelter", "food supply", "water", "relief", "humanitarian", "starvation", "evacuat"],
    "legal_judicial":         ["court", "lawsuit", "legal", "judge", "verdict", "trial", "prosecut", "arrest", "charged", "sentence", "convict", "indictment"],
    "future_developments":    ["will ", "next week", "upcoming", "planned", "scheduled", "expected to", "due to", "anticipated", "proposed", "deadline"],
    "background_context":     ["history", "since ", "decade", "year ago", "background", "context", "previous", "earlier", "former", "traditionally", "has long", "for years"],
    "political_dynamics":     ["party", "election", "vote", "opposition", "coalition", "political", "democrat", "republican", "senator", "congress", "campaign", "polling"],
    "science_findings":       ["research", "study", "scientist", "laborator", "experiment", "discovery", "published", "journal", "clinical trial", "data shows"],
    "health_impact":          ["health", "disease", "patient", "hospital", "treatment", "medical", "vaccine", "symptom", "outbreak", "pandemic", "diagnosis"],
    "sports_result":          ["score", " won ", " won.", "defeated", "champion", "title", "goal", "points", "standings", "final score", "qualifying"],
    "sports_narrative":       ["played", "game", "match", "tournament", "team", "player", "coach", "stadium", "season", "league", "manager"],
}


def classify_chunk_aspect(chunk_text: str) -> Tuple[str, float]:
    """
    Classify a paragraph chunk into the best-matching aspect using keyword
    matching — runs in microseconds vs. ~1s for the NLI model.

    Returns (aspect_label, confidence).
    """
    text = chunk_text.lower()
    scores: Dict[str, int] = {}
    for aspect, keywords in _CHUNK_KW.items():
        scores[aspect] = sum(1 for kw in keywords if kw in text)

    best = max(scores, key=scores.__getitem__)
    count = scores[best]
    if count == 0:
        return ("event_core", 0.5)
    # Normalise to a 0-1 confidence proxy
    confidence = min(1.0, count / 3.0)
    return (best, confidence)
