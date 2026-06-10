"""
OSI News Automation – Intent Classifier
========================================
Zero-shot story-type and aspect classification using two open-source components:

  • Intent / story-type classification:
      Model: cross-encoder/nli-deberta-v3-small  (HuggingFace, ~90 MB)
      Method: transformers zero-shot-classification pipeline
      No training, no GPU required.

  • Chunk aspect classification:
      Same NLI pipeline, applied per paragraph.

Story types (9):
    breaking_news, political, business, science_health,
    conflict_crime, human_interest, sports, analysis, general

Aspect taxonomy (17):
    event_core, background_context, casualties_damage, government_response,
    international_reaction, expert_analysis, civilian_testimony,
    economic_impact, humanitarian_situation, legal_judicial,
    future_developments, military_operations, political_dynamics,
    sports_result, sports_narrative, science_findings, health_impact
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from loguru import logger

# ─────────────────────────────────────────────────────────────────
# LABEL DEFINITIONS  (what the NLI model scores against)
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
# NLI PIPELINE — lazy-loaded singleton
# ─────────────────────────────────────────────────────────────────

_nli_pipeline = None


def _get_pipeline():
    global _nli_pipeline
    if _nli_pipeline is not None:
        return _nli_pipeline
    try:
        from transformers import pipeline
        logger.info("Loading NLI classifier (cross-encoder/nli-deberta-v3-small)…")
        _nli_pipeline = pipeline(
            "zero-shot-classification",
            model="cross-encoder/nli-deberta-v3-small",
            device=-1,          # CPU
        )
        logger.info("NLI classifier ready.")
    except Exception as e:
        logger.error(f"Failed to load NLI pipeline: {e}")
        _nli_pipeline = None
    return _nli_pipeline


# ─────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────

def _nli_classify(text: str, candidate_labels: List[str]) -> Dict[str, float]:
    """
    Run zero-shot classification on *text* against *candidate_labels*.
    Returns {label: score} dict.  Falls back to uniform scores on failure.
    """
    pipe = _get_pipeline()
    if pipe is None:
        return {lbl: 1.0 / len(candidate_labels) for lbl in candidate_labels}
    try:
        # Truncate to 512 tokens (model limit); DeBERTa tokenises ~3 chars/token
        truncated = text[:1400]
        result = pipe(truncated, candidate_labels, multi_label=False)
        return dict(zip(result["labels"], result["scores"]))
    except Exception as e:
        logger.warning(f"NLI classification failed: {e}")
        return {lbl: 1.0 / len(candidate_labels) for lbl in candidate_labels}


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────

def classify_story_type(articles: List[Dict]) -> Dict:
    """
    Classify a cluster of articles into a single story type.

    Builds a short cluster representation (headline + 200-char snippet
    from each article, up to 5) and scores it against all story-type labels.

    Returns:
        {"story_type": str, "confidence": float}
    """
    parts = []
    for art in articles[:5]:
        heading = art.get("heading", "")
        snippet = art.get("story", "")[:200]
        parts.append(f"{heading}. {snippet}")
    cluster_text = " | ".join(parts)

    candidate_labels = list(STORY_TYPE_LABELS.values())
    label_keys = list(STORY_TYPE_LABELS.keys())

    scores = _nli_classify(cluster_text, candidate_labels)

    # Map description strings back to short label keys
    best_description = max(scores, key=scores.__getitem__)
    best_key = label_keys[candidate_labels.index(best_description)]
    best_score = scores[best_description]

    logger.info(f"Story type: {best_key} (confidence={best_score:.3f})")
    return {"story_type": best_key, "confidence": float(best_score)}


def classify_aspects_present(articles: List[Dict], threshold: float = 0.10) -> List[str]:
    """
    Return aspect labels that appear in the cluster at or above *threshold*.

    Scores the full text of each article against all 17 aspects and takes
    the union of aspects that exceed the threshold in any single article.

    Always includes "event_core".
    """
    candidate_labels = list(ASPECT_LABELS.values())
    label_keys = list(ASPECT_LABELS.keys())

    found: Dict[str, float] = {}

    for art in articles:
        text = (art.get("heading", "") + ". " + art.get("story", ""))[:1000]
        scores = _nli_classify(text, candidate_labels)
        for desc, score in scores.items():
            key = label_keys[candidate_labels.index(desc)]
            if score > found.get(key, 0.0):
                found[key] = score

    present = [lbl for lbl, score in found.items() if score >= threshold]
    if "event_core" not in present:
        present.insert(0, "event_core")

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
