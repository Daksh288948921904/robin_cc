"""
WSGI entry point for production deployment (gunicorn).

Usage:
    gunicorn -c gunicorn.conf.py wsgi:app
"""

import sys
import os
from pathlib import Path

# Ensure the project root is on sys.path so all src.* imports resolve
PROJECT_ROOT = Path(__file__).parent.absolute()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.frontend.app import app, load_articles, OUTPUT_DIR

# ── Auto-load latest saved articles on server start ─────────────
# This means a server restart never leaves the feed empty.
def _autoload():
    import json
    try:
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

_autoload()
