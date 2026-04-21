"""
robin cc News — Article Similarity Search
==========================================
Finds articles covering the same story across different sources
using Sentence Transformers + cosine vector similarity (semantic search).

Model: all-MiniLM-L6-v2  (fast, ~80 MB, very accurate)
       Switch to all-mpnet-base-v2 for higher accuracy at the cost of speed.

No rule-based keyword matching — embeddings capture meaning even when
wording differs between outlets covering the same event.
"""

import logging
import numpy as np
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None   # loaded lazily on first call


def _get_model():
    """Load (or return cached) SentenceTransformer model."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence-transformer model: %s", _MODEL_NAME)
            _model = SentenceTransformer(_MODEL_NAME)
            logger.info("Sentence-transformer model loaded.")
        except ImportError:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )
    return _model


def _article_text(article: Dict) -> str:
    """
    Build a short text representation for embedding.
    Title is repeated to give it more semantic weight.
    """
    title   = (article.get("heading", "") or "").strip()
    summary = (article.get("story",   "") or "")[:300].strip()
    return f"{title}. {title}. {summary}".strip()


def find_similar(
    target: Dict,
    all_articles: List[Dict],
    threshold: float = 0.35,
    max_results: int = 10,
) -> List[Tuple[Dict, float]]:
    """
    Find articles in *all_articles* that cover the same story as *target*
    using semantic embedding similarity (Sentence Transformers).

    Rules:
      - Must come from a different source_name.
      - Cosine similarity must meet *threshold* (0.0–1.0; default 0.35).
      - Returns up to *max_results*, sorted by similarity descending.

    Args:
        target:       The primary article to match against.
        all_articles: Full article pool to search.
        threshold:    Minimum cosine similarity to include a result.
        max_results:  Maximum number of results to return.

    Returns:
        List of (article_dict, similarity_score) tuples.
    """
    model      = _get_model()
    target_src = (target.get("source_name") or "").strip().lower()

    # Filter to different-source candidates only
    candidates = [
        art for art in all_articles
        if (art.get("source_name") or "").strip().lower() != target_src
    ]
    if not candidates:
        return []

    # Build texts and encode everything in one batch
    target_text     = _article_text(target)
    candidate_texts = [_article_text(a) for a in candidates]

    all_texts  = [target_text] + candidate_texts
    # normalize_embeddings=True → unit vectors → cosine sim = dot product
    embeddings = model.encode(
        all_texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=64,
    )

    target_emb     = embeddings[0]
    candidate_embs = embeddings[1:]

    # Cosine similarities via vectorised dot product (fast)
    scores = np.dot(candidate_embs, target_emb).tolist()

    scored: List[Tuple[Dict, float]] = [
        (art, round(score, 3))
        for art, score in zip(candidates, scores)
        if score >= threshold
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]
