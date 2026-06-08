"""
Supabase sync — uses the REST API directly via requests (no supabase package needed).
All functions are safe to call when Supabase is not configured; errors are logged silently.
"""

import os, logging, requests
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_SUPABASE_URL = None
_SUPABASE_KEY = None
_headers      = None


def _init():
    global _SUPABASE_URL, _SUPABASE_KEY, _headers
    if _headers is not None:
        return bool(_SUPABASE_URL)
    url = os.getenv('SUPABASE_URL', '').rstrip('/')
    key = os.getenv('SUPABASE_KEY', '')
    if not url or not key:
        return False
    _SUPABASE_URL = url
    _SUPABASE_KEY = key
    _headers = {
        'apikey':        key,
        'Authorization': f'Bearer {key}',
        'Content-Type':  'application/json',
        'Prefer':        'return=minimal',
    }
    log.info('Supabase REST client ready (%s)', url)
    return True


def _url(table):
    return f'{_SUPABASE_URL}/rest/v1/{table}'


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── Articles ──────────────────────────────────────────────────────────────────

def upsert_articles(articles: list):
    if not _init():
        return
    try:
        rows = [
            {
                'server_idx':  idx,
                'heading':     a.get('heading', ''),
                'sub_heading': a.get('sub_heading', ''),
                'source_name': a.get('source_name', ''),
                'source_url':  a.get('source_url', ''),
                'scraped_at':  a.get('scraped_at') or _now(),
                'payload':     a,
                'updated_at':  _now(),
            }
            for idx, a in enumerate(articles)
        ]
        hdrs = {**_headers, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
        for i in range(0, len(rows), 100):
            r = requests.post(_url('articles'), json=rows[i:i+100], headers=hdrs, timeout=15)
            r.raise_for_status()
        log.info('Supabase: upserted %d articles', len(articles))
    except Exception as e:
        log.warning('Supabase upsert_articles failed: %s', e)


def patch_article(idx: int, article: dict):
    if not _init():
        return
    try:
        row = {
            'server_idx':  idx,
            'heading':     article.get('heading', ''),
            'sub_heading': article.get('sub_heading', ''),
            'source_name': article.get('source_name', ''),
            'source_url':  article.get('source_url', ''),
            'scraped_at':  article.get('scraped_at') or _now(),
            'payload':     article,
            'updated_at':  _now(),
        }
        hdrs = {**_headers, 'Prefer': 'resolution=merge-duplicates,return=minimal'}
        r = requests.post(_url('articles'), json=row, headers=hdrs, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.warning('Supabase patch_article(%d) failed: %s', idx, e)


def load_articles():
    if not _init():
        return None
    try:
        r = requests.get(
            _url('articles'),
            headers={**_headers, 'Prefer': ''},
            params={'select': 'server_idx,payload', 'order': 'server_idx.asc'},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data:
            log.info('Supabase: loaded %d articles', len(data))
            return [row['payload'] for row in data]
    except Exception as e:
        log.warning('Supabase load_articles failed: %s', e)
    return None


# ── Published articles ────────────────────────────────────────────────────────

def insert_published(idx, heading, sub_heading, hocalwire_id='',
                     reporter='', category='', image_url='', body_html=''):
    if not _init():
        return
    try:
        # Resolve article UUID
        r = requests.get(
            _url('articles'),
            headers={**_headers, 'Prefer': ''},
            params={'select': 'id', 'server_idx': f'eq.{idx}', 'limit': '1'},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
        article_uuid = rows[0]['id'] if rows else None

        payload = {
            'article_id':   article_uuid,
            'heading':      heading,
            'sub_heading':  sub_heading,
            'hocalwire_id': hocalwire_id,
            'reporter':     reporter,
            'category':     category,
            'image_url':    image_url,
            'body_html':    body_html,
            'published_at': _now(),
        }
        r2 = requests.post(_url('published_articles'), json=payload, headers=_headers, timeout=10)
        r2.raise_for_status()
        log.info('Supabase: recorded publish for article idx=%d', idx)
    except Exception as e:
        log.warning('Supabase insert_published(%d) failed: %s', idx, e)


def get_published_feed():
    if not _init():
        return None
    try:
        r = requests.get(
            _url('published_articles'),
            headers={**_headers, 'Prefer': ''},
            params={'select': '*', 'order': 'published_at.desc'},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning('Supabase get_published_feed failed: %s', e)
    return None
