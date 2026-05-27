"""
WSGI entry point for production deployment (gunicorn).

Usage:
    gunicorn -c gunicorn.conf.py wsgi:app
"""

import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.absolute()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.frontend.app import app, load_articles, OUTPUT_DIR


def _autoload():
    """Load articles from MongoDB first, fall back to local JSON files."""
    try:
        from src.database.mongo_client import get_client
        db = get_client()
        if db._ensure_connected():
            # Limit to 150 to keep memory under control on free Render instance
            raw = list(db.articles.find({}, {"_id": 0}).sort("scraped_at", -1).limit(150))
            if raw:
                load_articles(raw)
                print(f'[wsgi] Auto-loaded {len(raw)} articles from MongoDB')
                return
    except Exception as exc:
        print(f'[wsgi] MongoDB load failed: {exc}')

    try:
        import json
        json_files = list(OUTPUT_DIR.glob('scraped_*.json'))
        if not json_files:
            print('[wsgi] No saved articles found — feed will start empty.')
            return
        latest = max(json_files, key=lambda p: p.stat().st_mtime)
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        articles = data.get('articles', data if isinstance(data, list) else [])
        load_articles(articles)
        print(f'[wsgi] Auto-loaded {len(articles)} articles from {latest.name}')
    except Exception as exc:
        print(f'[wsgi] Auto-load failed (non-fatal): {exc}')


def _prewarm_model():
    """Load sentence-transformer model at startup so first request doesn't block."""
    try:
        from src.utils.similarity import _get_model
        _get_model()
        print('[wsgi] Sentence-transformer model pre-loaded')
    except Exception as exc:
        print(f'[wsgi] Model pre-warm failed (non-fatal): {exc}')


_autoload()
_prewarm_model()
