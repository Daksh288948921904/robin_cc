"""
Shared Groq key-pool for all content-generation modules.

Reads GROQ_API_KEY, GROQ_API_KEY_2 … GROQ_API_KEY_N from environment.
On rate-limit (429 / TPD exhausted) call rotate_groq_key() to advance
to the next key.  Falls back to key 1 when all keys are rotated through.
"""

from __future__ import annotations

import os
from typing import List, Optional

from loguru import logger

_clients: List = []
_idx: int = 0


def _build_pool() -> List:
    from groq import Groq
    keys = []
    primary = os.getenv("GROQ_API_KEY")
    if primary:
        keys.append(primary)
    for i in range(2, 20):
        k = os.getenv(f"GROQ_API_KEY_{i}")
        if k:
            keys.append(k)
    if not keys:
        logger.error("No GROQ_API_KEY* env vars found")
        return []
    clients = []
    for i, key in enumerate(keys, 1):
        try:
            clients.append(Groq(api_key=key))
            logger.debug(f"Groq pool: key {i}/{len(keys)} ready (…{key[-6:]})")
        except Exception as e:
            logger.warning(f"Groq pool: key {i} init failed: {e}")
    logger.info(f"Groq key pool ready: {len(clients)} key(s)")
    return clients


def get_groq_client():
    """Return the currently active Groq client (builds pool on first call)."""
    global _clients, _idx
    if not _clients:
        _clients = _build_pool()
    if not _clients:
        return None
    return _clients[_idx % len(_clients)]


def rotate_groq_key(reason: str = "") -> bool:
    """Rotate to the next key. Returns True if a new key is now active."""
    global _idx
    if not _clients or len(_clients) <= 1:
        return False
    old = _idx
    _idx = (_idx + 1) % len(_clients)
    logger.info(f"Groq key rotated {old} → {_idx} ({reason})")
    return _idx != old
