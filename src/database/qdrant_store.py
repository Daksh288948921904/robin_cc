"""
OSI News Automation – Qdrant Vector Store
==========================================
Thin wrapper around Qdrant for storing and retrieving story chunks.

Collection schema:
  Name   : story_chunks
  Vectors: 1024-dim (mxbai-embed-large via Ollama), cosine distance
  Payload: cluster_id, aspect, aspect_confidence, chunk_text,
           chunk_index, source_name, source_article_id, session_id,
           story_type, quotes, scraped_at

Chunk IDs are deterministic: sha256(cluster_id + source_article_id + chunk_index)
so re-chunking the same cluster is safe (upsert is idempotent).
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from loguru import logger


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

COLLECTION_NAME = "story_chunks"
VECTOR_SIZE     = 1024          # mxbai-embed-large output dims


# ─────────────────────────────────────────────────────────────────
# EMBEDDING  (Jina in production, Ollama for local dev)
# ─────────────────────────────────────────────────────────────────

def _embed_jina(text: str) -> Optional[List[float]]:
    """Embed a single text via Jina AI API."""
    result = _embed_batch_jina([text])
    return result[0] if result else None


def _embed_batch_jina(texts: List[str]) -> List[Optional[List[float]]]:
    """Embed all texts in one Jina API call (much faster than N individual calls)."""
    if not texts:
        return []
    api_key = os.getenv("JINA_API_KEY", "").strip()
    model   = os.getenv("JINA_EMBED_MODEL", "jina-embeddings-v3").strip()
    try:
        resp = httpx.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "input": texts},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        result: List[Optional[List[float]]] = [None] * len(texts)
        for item in data:
            idx = item.get("index", 0)
            if 0 <= idx < len(result):
                result[idx] = item["embedding"]
        return result
    except Exception as e:
        logger.warning(f"Jina batch embed error: {e}")
        return [None] * len(texts)


def _embed_ollama(text: str) -> Optional[List[float]]:
    """Embed via local Ollama server (mxbai-embed-large, 1024-dim)."""
    url   = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large")
    try:
        resp = httpx.post(
            f"{url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        vec = resp.json().get("embedding", [])
        return vec if vec else None
    except Exception as e:
        logger.warning(f"Ollama embed error: {e}")
        return None


def _embed(text: str) -> Optional[List[float]]:
    """
    Embed text using Jina (if JINA_API_KEY is set) or Ollama (local dev).
    Both produce 1024-dim vectors compatible with the story_chunks collection.
    """
    if os.getenv("JINA_API_KEY"):
        return _embed_jina(text)
    return _embed_ollama(text)


def embed_text(text: str) -> Optional[List[float]]:
    """Public helper: embed a string and return a float list."""
    return _embed(text)


# ─────────────────────────────────────────────────────────────────
# QDRANT CLIENT — lazy singleton
# ─────────────────────────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from qdrant_client import QdrantClient
        url     = os.getenv("QDRANT_URL", "http://localhost:6333").strip()
        api_key = (os.getenv("QDRANT_API_KEY") or "").strip() or None
        _client = QdrantClient(url=url, api_key=api_key, timeout=30)
        logger.info(f"Qdrant client connected to {url}")
    except ImportError:
        logger.error("qdrant-client not installed. Run: pip install qdrant-client")
        _client = None
    except Exception as e:
        logger.error(f"Qdrant connection failed: {e}")
        _client = None
    return _client


# ─────────────────────────────────────────────────────────────────
# COLLECTION SETUP
# ─────────────────────────────────────────────────────────────────

def ensure_collection() -> bool:
    """Create the story_chunks collection and payload indexes if they don't exist."""
    client = _get_client()
    if client is None:
        return False
    try:
        from qdrant_client.models import Distance, VectorParams, PayloadSchemaType
        existing = [c.name for c in client.get_collections().collections]
        if COLLECTION_NAME not in existing:
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info(f"Created Qdrant collection '{COLLECTION_NAME}' ({VECTOR_SIZE}-dim cosine)")

        # Qdrant requires payload indexes for filtered scroll/search queries.
        # Create them if missing — safe to call on an existing indexed field.
        for field in ("cluster_id", "aspect", "session_id"):
            try:
                client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass  # index already exists — ignore

        return True
    except Exception as e:
        logger.error(f"ensure_collection failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────
# CHUNK ID
# ─────────────────────────────────────────────────────────────────

def _chunk_id(cluster_id: str, source_article_id: str, chunk_index: int) -> str:
    """Deterministic UUID-style ID from cluster + article + position."""
    raw = f"{cluster_id}::{source_article_id}::{chunk_index}"
    h = hashlib.sha256(raw.encode()).hexdigest()
    # Format as pseudo-UUID for Qdrant (it accepts plain ints or UUIDs)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# ─────────────────────────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────────────────────────

def save_chunks(chunks: List[Dict]) -> int:
    """
    Upsert a list of chunk dicts into Qdrant.

    Each chunk dict must contain:
        cluster_id, source_article_id, chunk_index, chunk_text,
        aspect, aspect_confidence, source_name, session_id,
        story_type, quotes, scraped_at

    Returns the number of chunks successfully stored.
    """
    client = _get_client()
    if client is None:
        return 0
    if not ensure_collection():
        return 0

    try:
        from qdrant_client.models import PointStruct
    except ImportError:
        logger.error("qdrant-client not available")
        return 0

    # Embed all chunks in a single API call instead of one-at-a-time
    texts = [c["chunk_text"] for c in chunks]
    if os.getenv("JINA_API_KEY"):
        vectors = _embed_batch_jina(texts)
    else:
        vectors = [_embed_ollama(t) for t in texts]
    logger.info(f"Embedded {len(texts)} chunks in one batch call")

    points = []
    for chunk, vec in zip(chunks, vectors):
        if vec is None:
            logger.warning(f"Skipping chunk (embed failed): {chunk['chunk_text'][:60]}")
            continue

        point_id = _chunk_id(
            chunk["cluster_id"],
            chunk["source_article_id"],
            chunk["chunk_index"],
        )

        scraped_at = chunk.get("scraped_at")
        if isinstance(scraped_at, datetime):
            scraped_at = scraped_at.isoformat()
        elif scraped_at is None:
            scraped_at = datetime.utcnow().isoformat()

        payload = {
            "cluster_id":        chunk["cluster_id"],
            "source_article_id": chunk.get("source_article_id", ""),
            "source_name":       chunk.get("source_name", ""),
            "chunk_text":        chunk["chunk_text"],
            "chunk_index":       chunk["chunk_index"],
            "aspect":            chunk["aspect"],
            "aspect_confidence": chunk["aspect_confidence"],
            "quotes":            chunk.get("quotes", []),
            "story_type":        chunk.get("story_type", "general"),
            "session_id":        chunk.get("session_id", ""),
            "scraped_at":        scraped_at,
        }

        points.append(PointStruct(id=point_id, vector=vec, payload=payload))

    if not points:
        return 0

    try:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
        logger.info(f"Upserted {len(points)} chunks into Qdrant (cluster={chunks[0]['cluster_id']!r})")
        return len(points)
    except Exception as e:
        logger.error(f"Qdrant upsert failed: {e}")
        return 0


# ─────────────────────────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────────────────────────

def get_chunks_by_aspect(
    cluster_id: str,
    aspects: List[str],
    top_k: int = 5,
) -> List[Dict]:
    """
    Retrieve up to *top_k* chunks for a cluster that match any of the given aspects.

    Uses a Qdrant filter on (cluster_id AND aspect IN aspects) then
    sorts by aspect_confidence descending.

    Returns a list of payload dicts (chunk_text, quotes, source_name, …).
    """
    client = _get_client()
    if client is None:
        return []

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

        conditions = [
            FieldCondition(key="cluster_id", match=MatchValue(value=cluster_id)),
            FieldCondition(key="aspect", match=MatchAny(any=aspects)),
        ]

        results = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=conditions),
            limit=max(top_k * 3, 20),   # over-fetch, then re-rank by confidence
            with_payload=True,
            with_vectors=False,
        )[0]   # scroll returns (points, next_page_offset)

        # Sort by aspect_confidence desc, then cap to top_k
        sorted_results = sorted(
            results,
            key=lambda p: p.payload.get("aspect_confidence", 0.0),
            reverse=True,
        )[:top_k]

        return [p.payload for p in sorted_results]

    except Exception as e:
        logger.error(f"get_chunks_by_aspect failed: {e}")
        return []


def semantic_search(
    query_text: str,
    cluster_id: str,
    aspects: List[str],
    top_k: int = 5,
) -> List[Dict]:
    """
    Semantic vector search within a cluster filtered to specific aspects.

    Useful when you want the chunks *most similar* to a section query
    rather than simply top-confidence chunks.
    """
    client = _get_client()
    if client is None:
        return []

    query_vec = _embed(query_text)
    if query_vec is None:
        return get_chunks_by_aspect(cluster_id, aspects, top_k)

    try:
        from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

        conditions = [
            FieldCondition(key="cluster_id", match=MatchValue(value=cluster_id)),
            FieldCondition(key="aspect", match=MatchAny(any=aspects)),
        ]

        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vec,
            query_filter=Filter(must=conditions),
            limit=top_k,
            with_payload=True,
        )
        return [r.payload for r in response.points]

    except Exception as e:
        logger.error(f"semantic_search failed: {e}")
        return get_chunks_by_aspect(cluster_id, aspects, top_k)


# ─────────────────────────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────────────────────────

def delete_chunks_for_session(session_id: str) -> int:
    """Delete all chunks belonging to a given scraping session."""
    client = _get_client()
    if client is None:
        return 0
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        result = client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
            ),
        )
        deleted = getattr(result, "deleted_count", 0) or 0
        logger.info(f"Deleted {deleted} Qdrant chunks for session {session_id!r}")
        return deleted
    except Exception as e:
        logger.error(f"delete_chunks_for_session failed: {e}")
        return 0
