"""
Groq API key pool with automatic rotation on rate-limit (429).

Keys are read from env vars GROQ_API_KEY, GROQ_API_KEY_2 … GROQ_API_KEY_N.
On every 429 the pool advances to the next key; if all keys are exhausted it
raises the last exception so the caller can handle it.

Usage:
    from src.utils.groq_pool import groq_completion

    resp = groq_completion(
        messages=[{"role": "user", "content": "Hello"}],
        model="llama-3.3-70b-versatile",
        temperature=0.4,
        max_tokens=2048,
    )
    text = resp.choices[0].message.content
"""

import os
import threading
from typing import List, Optional

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Key pool
# ---------------------------------------------------------------------------

def _load_keys() -> List[str]:
    """Read all GROQ_API_KEY* vars from env, in order."""
    keys: List[str] = []
    # Primary key
    k = os.getenv("GROQ_API_KEY", "").strip()
    if k:
        keys.append(k)
    # Numbered keys: GROQ_API_KEY_2 … GROQ_API_KEY_20
    for i in range(2, 21):
        k = os.getenv(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
        else:
            break   # stop at first gap
    return keys


_keys: List[str] = _load_keys()
_current_idx: int = 0          # index of the key currently in use
_lock = threading.Lock()        # protect index across concurrent requests


def _next_key(after_idx: int) -> Optional[str]:
    """Return the next key after `after_idx`, or None if pool exhausted."""
    global _current_idx  # noqa: PLW0603
    with _lock:
        next_idx = (after_idx + 1) % len(_keys) if _keys else 0
        # If we've wrapped all the way around, pool is exhausted
        if next_idx == 0 and after_idx != 0:
            return None
        _current_idx = next_idx
        return _keys[next_idx] if next_idx < len(_keys) else None


def current_key() -> Optional[str]:
    """Return the currently active key."""
    if not _keys:
        return None
    with _lock:
        return _keys[_current_idx]


# ---------------------------------------------------------------------------
# Drop-in Groq client factory
# ---------------------------------------------------------------------------

def _make_client(key: str):
    from groq import Groq
    return Groq(api_key=key)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_FALLBACK_MODELS = ["llama-3.1-8b-instant", "gemma2-9b-it"]


def groq_completion(
    messages: list,
    model: str = None,
    temperature: float = 0.4,
    max_tokens: int = 2048,
    **kwargs,
):
    """
    Call Groq chat completions with automatic key rotation AND model fallback.

    - On 429 from any key: rotates to the next key and retries same model.
    - After all keys exhausted for the primary model: tries fallback models
      across the full key pool again.
    - Raises the last exception if everything fails.
    """
    global _current_idx

    if not _keys:
        raise ValueError("No GROQ_API_KEY* found in environment")

    primary_model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    models_to_try = [primary_model] + [m for m in _FALLBACK_MODELS if m != primary_model]

    last_exc = None

    for attempt_model in models_to_try:
        # Try every key in the pool for this model
        tried = 0
        start_idx = _current_idx

        while tried < len(_keys):
            key = _keys[_current_idx]
            try:
                client = _make_client(key)
                resp = client.chat.completions.create(
                    model=attempt_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if attempt_model != primary_model:
                    logger.info(f"[groq_pool] Used fallback model '{attempt_model}' (key #{_current_idx + 1})")
                else:
                    logger.debug(f"[groq_pool] OK — model '{attempt_model}', key #{_current_idx + 1}")
                return resp

            except Exception as exc:
                err = str(exc)
                if "rate_limit_exceeded" in err or "429" in err:
                    logger.warning(
                        f"[groq_pool] Key #{_current_idx + 1} rate-limited on '{attempt_model}' — rotating…"
                    )
                    last_exc = exc
                    # Advance to next key
                    with _lock:
                        _current_idx = (_current_idx + 1) % len(_keys)
                    tried += 1
                    # If we've looped all the way back, break to next model
                    if _current_idx == start_idx:
                        break
                    continue
                else:
                    # Non-rate-limit error — don't rotate, just raise
                    raise

        logger.warning(f"[groq_pool] All {len(_keys)} keys rate-limited for '{attempt_model}', trying next model…")

    logger.error(f"[groq_pool] All models and all keys exhausted. Last error: {last_exc}")
    raise last_exc


def pool_status() -> dict:
    """Return a summary of the pool for health-check endpoints."""
    return {
        "total_keys": len(_keys),
        "active_key_index": _current_idx + 1,   # 1-based for display
        "models_fallback": _FALLBACK_MODELS,
    }
