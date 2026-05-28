"""
robin cc News — Article Similarity Search
==========================================
Finds articles covering the same story across different sources.

Primary:  Sentence Transformers (all-MiniLM-L6-v2) — semantic / embedding search.
Fallback: TF-IDF + cosine similarity (scikit-learn) — used when sentence-transformers
          is not installed (e.g. Render free-tier UI server with OOM constraints).
"""

import logging
import numpy as np
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None        # SentenceTransformer instance, or False if unavailable
_USE_TFIDF = None    # resolved once on first call


def _article_text(article: Dict) -> str:
    title   = (article.get("heading", "") or "").strip()
    summary = (article.get("story",   "") or "")[:300].strip()
    return f"{title}. {title}. {summary}".strip()


def _resolve_backend():
    global _model, _USE_TFIDF
    if _USE_TFIDF is not None:
        return
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformer model: %s", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
        _USE_TFIDF = False
        logger.info("Sentence-transformer model loaded.")
    except (ImportError, Exception) as e:
        logger.warning("sentence-transformers unavailable (%s) — using TF-IDF fallback.", e)
        _model = None
        _USE_TFIDF = True


def _find_similar_embeddings(target_text, candidate_texts, candidates, threshold, max_results):
    all_texts  = [target_text] + candidate_texts
    embeddings = _model.encode(
        all_texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=64,
    )
    scores = np.dot(embeddings[1:], embeddings[0]).tolist()
    scored = [
        (art, round(score, 3))
        for art, score in zip(candidates, scores)
        if score >= threshold
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


def _find_similar_tfidf(target_text, candidate_texts, candidates, threshold, max_results):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity as sk_cosine

    all_texts = [target_text] + candidate_texts
    try:
        vec = TfidfVectorizer(stop_words='english', max_features=8000, sublinear_tf=True)
        tfidf = vec.fit_transform(all_texts)
    except ValueError:
        return []

    scores = sk_cosine(tfidf[0:1], tfidf[1:])[0].tolist()
    # TF-IDF scores tend to be lower than embedding cosine; lower the threshold
    adjusted = max(threshold * 0.5, 0.05)
    scored = [
        (art, round(score, 3))
        for art, score in zip(candidates, scores)
        if score >= adjusted
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


def find_similar(
    target: Dict,
    all_articles: List[Dict],
    threshold: float = 0.35,
    max_results: int = 10,
) -> List[Tuple[Dict, float]]:
    """
    Find articles from OTHER sources covering the same story as *target*.
    Uses sentence-transformers if available, otherwise TF-IDF via scikit-learn.
    """
    _resolve_backend()

    target_src = (target.get("source_name") or "").strip().lower()
    candidates = [
        art for art in all_articles
        if (art.get("source_name") or "").strip().lower() != target_src
    ]
    if not candidates:
        return []

    target_text     = _article_text(target)
    candidate_texts = [_article_text(a) for a in candidates]

    if _USE_TFIDF:
        return _find_similar_tfidf(target_text, candidate_texts, candidates, threshold, max_results)
    return _find_similar_embeddings(target_text, candidate_texts, candidates, threshold, max_results)
