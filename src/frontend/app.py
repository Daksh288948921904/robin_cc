"""
OSI News Automation System - Results Viewer Frontend
=====================================================
FastAPI web interface to view scraped articles and their formatting.
"""

import io
import json
import logging
import os
import sys
import traceback
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import check_password_hash

load_dotenv(PROJECT_ROOT / '.env')
import requests as _requests

_GN_URL = (os.environ.get('GLOBAL_NEWS_URL') or '').rstrip('/')
_GN_KEY = os.environ.get('GLOBAL_NEWS_API_KEY', '')

def _push_to_global_news(article: dict, feed_id: str = '') -> str:
    """Push article to Global News and return the GN UUID (empty string on failure)."""
    if not _GN_URL or not _GN_KEY:
        with open('/tmp/robin_publish_debug.log', 'a') as _dbg:
            _dbg.write(f"GN SKIP: no URL ({bool(_GN_URL)}) or KEY ({bool(_GN_KEY)})\n")
        return ''
    try:
        article['hocalwire_id'] = feed_id
        r = _requests.post(
            f'{_GN_URL}/api/ingest',
            json=article,
            headers={'X-API-Key': _GN_KEY},
            timeout=8,
        )
        with open('/tmp/robin_publish_debug.log', 'a') as _dbg:
            _dbg.write(f"GN PUSH: status={r.status_code} url={_GN_URL} tweets={len(article.get('selected_tweets', []))} resp={r.text[:200]}\n")
        if r.ok:
            return r.json().get('id', '')
    except Exception as _e:
        with open('/tmp/robin_publish_debug.log', 'a') as _dbg:
            _dbg.write(f"GN ERROR: {_e}\n")
        logger.warning('Global News push failed: %s', _e)
    return ''


def _auto_load_articles():
    """Load the most recent scraped JSON on startup so articles survive redeploys."""
    global SCRAPED_ARTICLES, _current_json_path
    try:
        output_dir = PROJECT_ROOT / 'output' / 'json'
        output_dir.mkdir(parents=True, exist_ok=True)
        json_files = list(output_dir.glob('scraped_*.json'))
        if not json_files:
            print('[Startup] No saved article files found — waiting for first scrape')
            return
        filepath = max(json_files, key=lambda p: p.stat().st_mtime)
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            SCRAPED_ARTICLES = data.get('articles', data)
        _current_json_path = filepath
        print(f'[Startup] Auto-loaded {len(SCRAPED_ARTICLES)} articles from {filepath.name}')
    except Exception as e:
        print(f'[Startup] Auto-load failed: {e}')


@asynccontextmanager
async def lifespan(app: FastAPI):
    _auto_load_articles()
    yield


app = FastAPI(lifespan=lifespan)

_USERS = {
    'Rohit@Robin.cc': (
        'scrypt:32768:8:1$h3hbtJrEc4fnIjeD$9b922665372248535fc502f66f587cedcfabd20b75d386b1f34453aba5280d08a146af93b1640b8f92c109f0be00299d3024b282189aefa10644b1d0740657ee'
    ),
    'Sudipta@Robin.cc': (
        'scrypt:32768:8:1$9BfAFr55acq3FBJE$4a0e5a6c8f7faf992a5ab6f3c01537ffcde0a83a9928fdb390c83eb33e9a21e828ccf54f312de0cedb392ff9cd07137e1305b97ab88f38e4473890db054c9f7f'
    ),
    'Pankaj@Robin.cc': (
        'scrypt:32768:8:1$q6pdvdgyB5Cn7UMw$35229e09e483cdf1367be4f05c895d11cdfdb71538c150eba201cc89a23d8db5940e794747c1c386dc11d9e62f6070f7ce4539745f4fbbb0e0b1b1b257bbaac0'
    ),
}

_log_level = getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)
logging.basicConfig(
    level=_log_level,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler('/tmp/robin_cc.log')],
    force=True,
)
logger = logging.getLogger(__name__)

_static_dir = Path(__file__).parent / 'static'
_static_dir.mkdir(parents=True, exist_ok=True)
app.mount('/static', StaticFiles(directory=str(_static_dir)), name='static')
templates = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))


# ── Middleware — order matters: last add_middleware call = outermost = runs first ──
# Execution order on incoming request: SessionMiddleware → auth → log → route

async def _auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith('/login') or path == '/logout' or path.startswith('/static/'):
        return await call_next(request)
    if not request.session.get('authenticated'):
        return RedirectResponse(url=f'/login?next={path}', status_code=302)
    return await call_next(request)


async def _log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info('%s %s → %s', request.method, request.url.path, response.status_code)
    return response


# Added in reverse execution order (each add_middleware wraps the existing stack)
app.add_middleware(BaseHTTPMiddleware, dispatch=_log_requests)    # runs 3rd
app.add_middleware(BaseHTTPMiddleware, dispatch=_auth_middleware)  # runs 2nd
_secret = os.environ.get('SECRET_KEY') or os.urandom(32).hex()
app.add_middleware(SessionMiddleware, secret_key=_secret, https_only=False)  # runs 1st


@app.exception_handler(404)
async def not_found(request: Request, exc):
    logger.warning('404 Not Found: %s', request.url.path)
    return JSONResponse(
        {'status': 'error', 'message': f'Not found: {request.url.path}'}, status_code=404
    )


@app.exception_handler(500)
async def server_error(request: Request, exc):
    logger.error('500 Internal Server Error: %s\n%s', request.url.path, traceback.format_exc())
    return JSONResponse({'status': 'error', 'message': str(exc)}, status_code=500)


# ── Auth routes ─────────────────────────────────────────────────
@app.get('/login')
async def login_get(request: Request):
    return templates.TemplateResponse(request, 'login.html', {'error': None})


@app.post('/login')
async def login_post(request: Request, username: str = Form(''), password: str = Form('')):
    pw_hash = _USERS.get(username.strip())
    if pw_hash and check_password_hash(pw_hash, password):
        request.session['authenticated'] = True
        next_url = request.query_params.get('next') or '/'
        return RedirectResponse(url=next_url, status_code=302)
    return templates.TemplateResponse(request, 'login.html', {'error': 'Invalid username or password.'})


@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login', status_code=302)


# ── Shared state ─────────────────────────────────────────────────
SCRAPED_ARTICLES = []
OUTPUT_DIR = PROJECT_ROOT / 'output' / 'json'
_current_json_path = None

COVERAGES:    dict = {}
SUMMARIES:    dict = {}
TIMELINES:    dict = {}
SOCIALS:      dict = {}
SOCIAL_POSTS: dict = {}
AI_IMAGES:       dict = {}
AI_IMAGE_PUBLIC: dict = {}
ACTIVITY:        dict = {}

_rag_state: dict = {}  # {str(idx): {'running': bool, 'done': bool, 'error': str|None}}
_autogen_state: dict = {}  # {str(idx): 'pending'|'running'|'done'|'error'}
_autogen_lock = threading.Lock()
_autogen_sem  = threading.Semaphore(3)  # max 3 concurrent

AI_IMAGES_STATIC_DIR = Path(__file__).parent / 'static' / 'ai_images'


def _upload_to_catbox(filepath: Path) -> str:
    try:
        import requests as _req
        with open(filepath, 'rb') as f:
            resp = _req.post(
                'https://catbox.moe/user/api.php',
                data={'reqtype': 'fileupload'},
                files={'fileToUpload': (filepath.name, f, 'image/png')},
                timeout=30,
            )
        resp.raise_for_status()
        url = resp.text.strip()
        if url.startswith('https://'):
            return url
        logger.warning(f"Catbox unexpected response: {url[:200]}")
        return ''
    except Exception as e:
        logger.warning(f"Catbox upload failed (will use Pollinations fallback): {e}")
        return ''


_scrape_state = {
    'running': False,
    'done': False,
    'count': 0,
    'error': None,
    'started_at': None,
}
_scrape_lock = threading.Lock()
_rag_lock    = threading.Lock()


def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _run_scrape(max_articles: int):
    global SCRAPED_ARTICLES, _scrape_state, COVERAGES, SUMMARIES, AI_IMAGES, AI_IMAGE_PUBLIC

    print(f"\n[Scraper] Starting multi-source RSS fetch — target {max_articles} articles")
    try:
        from src.scrapers.multi_source_rss import fetch_all_sources

        articles = fetch_all_sources(max_per_source=6, max_workers=12)

        try:
            from src.utils.country_resolver import enrich_articles
            enrich_articles(articles)
        except Exception as _ce:
            logger.warning("Country enrichment failed: %s", _ce)

        with _scrape_lock:
            SCRAPED_ARTICLES = articles
            _scrape_state['count'] = len(articles)
            _scrape_state['error'] = None

        COVERAGES.clear()
        SUMMARIES.clear()
        TIMELINES.clear()
        SOCIALS.clear()
        SOCIAL_POSTS.clear()
        AI_IMAGES.clear()
        AI_IMAGE_PUBLIC.clear()

        filepath = save_articles_to_json(articles)
        print(
            f"[Scraper] Done — {len(articles)} articles from "
            f"{len({a.get('source_name') for a in articles})} sources  →  {filepath}"
        )

        from src.utils.supabase_sync import upsert_articles as _sb_upsert
        _sb_upsert(articles)

    except Exception as e:
        print(f"[Scraper] ERROR:\n{traceback.format_exc()}")
        with _scrape_lock:
            _scrape_state['error'] = str(e)
    finally:
        with _scrape_lock:
            _scrape_state['running'] = False
            _scrape_state['done'] = True


# ── RAG pipeline helpers ─────────────────────────────────────────

def _rag_article_to_summary(article: dict, source_articles: list) -> dict:
    """Convert generate_article() output to the SUMMARIES dict format."""
    import re, random
    story = article.get('story', '')

    # Extract body: everything after the first ---
    body = story
    sep_idx = story.find('\n---\n')
    if sep_idx != -1:
        body = story[sep_idx + 5:].strip()

    category = 'World'
    cat_m = re.search(r'^CATEGORY:\s*(.+)$', story, re.MULTILINE)
    if cat_m:
        category = cat_m.group(1).strip()

    location = article.get('dateline', 'INTERNATIONAL').split(',')[0].strip()
    loc_m = re.search(r'^LOCATION:\s*(.+)$', story, re.MULTILINE)
    if loc_m:
        location = loc_m.group(1).strip()

    seen: set = set()
    sources_list = []
    for src_art in source_articles:
        name = src_art.get('source_name', 'Unknown')
        if name in seen:
            continue
        seen.add(name)
        sources_list.append({
            'name':       name,
            'headline':   src_art.get('heading', ''),
            'url':        src_art.get('source_url', ''),
            'region':     src_art.get('region', ''),
            'similarity': 1.0,
        })

    reporter_first = random.choice([
        'Aarav','Arjun','Rohan','Priya','Ananya','Kavya','Neha','Riya','Sanya',
    ])
    reporter_last = random.choice([
        'Sharma','Verma','Singh','Gupta','Patel','Joshi','Mehta','Nair','Iyer',
    ])

    return {
        'title':               article.get('heading', ''),
        'subtitle':            article.get('sub_heading', ''),
        'byline':              f"robin cc | {datetime.utcnow().strftime('%B %d, %Y')}",
        'location':            location,
        'category':            category,
        'body':                body,
        'raw_text':            story,
        'sources_list':        sources_list,
        'reporter':            f'{reporter_first} {reporter_last}',
        'generated_at':        article.get('generated_at', ''),
        'generation_pipeline': article.get('generation_pipeline', 'aspect_rag_v1'),
        'story_type':          article.get('story_type', ''),
        'source_quality':      article.get('source_quality', ''),
    }


def _run_rag_generate(idx: int, trend: dict, source_articles: list):
    key = str(idx)
    try:
        # Count total words across all source articles upfront
        total_words = sum(
            len((a.get('story', '') or a.get('body', '') or a.get('content', '')).split())
            for a in source_articles
        )
        logger.info(f'RAG generate idx={idx}: {len(source_articles)} sources, {total_words} total words')

        if len(source_articles) == 1 and total_words < 100:
            # Single tiny source — skip RAG, use direct LLM (1 call)
            logger.warning(f'Single source {total_words}w — skipping RAG, using direct LLM')
            from src.content_generation.multi_source_summary import generate_summary
            from src.content_generation.article_generator import _normalize_direct_article
            direct = generate_summary(source_articles[0], [])
            if direct:
                direct['generation_pipeline'] = 'direct_llm'
                direct['is_fallback'] = True
                article = _normalize_direct_article(direct, trend.get('topic', 'News Update'), source_articles)
            else:
                from src.content_generation.article_generator import generate_fallback_article
                article = generate_fallback_article(trend)
        else:
            from src.content_generation.article_generator import generate_article
            article = generate_article(trend)

        if not article:
            with _rag_lock:
                _rag_state[key] = {'running': False, 'done': True, 'error': 'Pipeline returned no article — sources may be too thin'}
            return
        summary = _rag_article_to_summary(article, source_articles)
        SUMMARIES[key] = summary
        with _rag_lock:
            _rag_state[key] = {'running': False, 'done': True, 'error': None}
        logger.info('RAG generation done for article %s: %s', idx, article.get('heading', '')[:60])
    except Exception as e:
        logger.error('RAG generation failed for idx %s: %s\n%s', idx, e, traceback.format_exc())
        with _rag_lock:
            _rag_state[key] = {'running': False, 'done': True, 'error': str(e)}


# ── Routes ───────────────────────────────────────────────────────
@app.get('/')
async def index(request: Request):
    import time
    response = templates.TemplateResponse(request, 'index.html', {
        'article_count': len(SCRAPED_ARTICLES),
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'static_v': str(int(time.time())),
    })
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response


@app.get('/api/articles')
async def get_articles(request: Request):
    return JSONResponse({
        'status': 'success',
        'count': len(SCRAPED_ARTICLES),
        'articles': SCRAPED_ARTICLES,
    })


@app.get('/api/articles/{index}')
async def get_article(request: Request, index: int):
    if 0 <= index < len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'success', 'article': SCRAPED_ARTICLES[index]})
    return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)


@app.patch('/api/articles/{index}')
async def patch_article(request: Request, index: int):
    if not (0 <= index < len(SCRAPED_ARTICLES)):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    article = SCRAPED_ARTICLES[index]
    cached  = SUMMARIES.get(str(index))
    if 'heading' in data:
        article['heading'] = data['heading']
        if cached:
            cached['title'] = data['heading']
    if 'sub_heading' in data:
        article['sub_heading'] = data['sub_heading']
        if cached:
            cached['subtitle']    = data['sub_heading']
            cached['sub_heading'] = data['sub_heading']
    _persist_articles()
    try:
        from src.utils.supabase_sync import patch_article as _sb_patch
        _sb_patch(index, article)
    except Exception as _se:
        logger.warning("Supabase patch failed: %s", _se)
    return JSONResponse({'status': 'success'})


@app.post('/api/scrape')
async def trigger_scrape(request: Request):
    global _scrape_state

    with _scrape_lock:
        if _scrape_state['running']:
            return JSONResponse({
                'status': 'running',
                'message': 'Scrape already in progress — check /api/scrape/status',
            }, status_code=202)

        max_articles = 10
        try:
            body = await request.json()
            max_articles = int(body.get('max_articles', 10))
        except Exception:
            pass

        _scrape_state = {
            'running': True,
            'done': False,
            'count': 0,
            'error': None,
            'started_at': datetime.now().isoformat(),
        }

    t = threading.Thread(target=_run_scrape, args=(max_articles,), daemon=True)
    t.start()

    return JSONResponse({
        'status': 'started',
        'message': f'Scraping started for up to {max_articles} articles. Poll /api/scrape/status for progress.',
    }, status_code=202)


@app.get('/api/scrape/status')
async def scrape_status(request: Request):
    with _scrape_lock:
        state = dict(_scrape_state)

    if state['running']:
        return JSONResponse({'status': 'running', 'message': 'Scraping in progress…'})
    if state['error']:
        return JSONResponse({'status': 'error', 'message': state['error']})
    if state['done']:
        return JSONResponse({
            'status': 'done',
            'count': state['count'],
            'message': f"Scraped {state['count']} articles",
        })
    return JSONResponse({'status': 'idle', 'message': 'No scrape has been started yet.'})


@app.post('/api/save')
async def save_to_json_route(request: Request):
    if not SCRAPED_ARTICLES:
        return JSONResponse({'status': 'error', 'message': 'No articles to save'}, status_code=400)
    filepath = save_articles_to_json(SCRAPED_ARTICLES)
    return JSONResponse({
        'status': 'success',
        'message': f'Saved {len(SCRAPED_ARTICLES)} articles',
        'filepath': str(filepath),
    })


@app.post('/api/load')
async def load_from_json(request: Request):
    global SCRAPED_ARTICLES, COVERAGES, SUMMARIES, TIMELINES, SOCIALS, \
           SOCIAL_POSTS, AI_IMAGES, AI_IMAGE_PUBLIC, _current_json_path
    COVERAGES.clear(); SUMMARIES.clear(); TIMELINES.clear()
    SOCIALS.clear(); SOCIAL_POSTS.clear()
    AI_IMAGES.clear(); AI_IMAGE_PUBLIC.clear()
    try:
        filename = None
        try:
            body = await request.json()
            filename = body.get('filename')
        except Exception:
            pass

        if filename:
            filepath = OUTPUT_DIR / filename
        else:
            ensure_output_dir()
            json_files = list(OUTPUT_DIR.glob('scraped_*.json'))
            if not json_files:
                return JSONResponse({'status': 'error', 'message': 'No saved files found'}, status_code=404)
            filepath = max(json_files, key=lambda p: p.stat().st_mtime)

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            SCRAPED_ARTICLES = data.get('articles', data)
        _current_json_path = filepath

        try:
            from src.utils.country_resolver import enrich_articles
            enrich_articles(SCRAPED_ARTICLES)
        except Exception as _ce:
            logger.warning("Country enrichment failed: %s", _ce)

        try:
            from src.utils.supabase_sync import upsert_articles as _sb_upsert
            _sb_upsert(SCRAPED_ARTICLES)
        except Exception as _se:
            logger.warning("Supabase sync after load failed: %s", _se)

        return JSONResponse({
            'status': 'success',
            'message': f'Loaded {len(SCRAPED_ARTICLES)} articles',
            'filepath': str(filepath),
        })
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


@app.get('/api/articles/{idx}/coverage')
async def get_coverage(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key = str(idx)
    if key in COVERAGES:
        return JSONResponse({'status': 'success', 'coverage': COVERAGES[key]})

    target = SCRAPED_ARTICLES[idx]
    try:
        from src.utils.similarity import find_similar
        results  = find_similar(target, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
        coverage = [{**art, 'similarity': score} for art, score in results]
    except Exception as e:
        logger.error('Coverage search error: %s', e)
        coverage = []

    COVERAGES[key] = coverage
    return JSONResponse({'status': 'success', 'coverage': coverage})


@app.post('/api/articles/{idx}/summary')
async def generate_summary(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key = str(idx)
    if key in SUMMARIES:
        return JSONResponse({'status': 'success', 'summary': SUMMARIES[key]})

    main_article = SCRAPED_ARTICLES[idx]

    if key not in COVERAGES:
        try:
            from src.utils.similarity import find_similar
            results = find_similar(main_article, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
            COVERAGES[key] = [{**art, 'similarity': score} for art, score in results]
        except Exception as e:
            logger.error('Coverage search error for summary: %s', e)
            COVERAGES[key] = []

    try:
        from src.content_generation.multi_source_summary import generate_summary as gen_sum
        summary = gen_sum(main_article, COVERAGES[key])
        if not summary:
            return JSONResponse({'status': 'error', 'message': 'LLM returned empty response'}, status_code=500)
    except Exception as e:
        logger.error('Summary generation error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)

    SUMMARIES[key] = summary
    return JSONResponse({'status': 'success', 'summary': summary})


@app.post('/api/articles/{idx}/rag-generate')
async def rag_generate(request: Request, idx: int):
    if not (0 <= idx < len(SCRAPED_ARTICLES)):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key = str(idx)

    # Already generated by auto-gen — return immediately
    if key in SUMMARIES:
        return JSONResponse({'status': 'done', 'summary': SUMMARIES[key]})

    with _rag_lock:
        if _rag_state.get(key, {}).get('running'):
            return JSONResponse({'status': 'running', 'message': 'Already generating'}, status_code=202)
        _rag_state[key] = {'running': True, 'done': False, 'error': None}

    main_article = SCRAPED_ARTICLES[idx]

    # Use pre-computed coverage as the cluster; fall back to just the main article
    coverage = COVERAGES.get(key, [])
    cluster_articles = [main_article] + [
        {k: v for k, v in art.items() if k != 'similarity'}
        for art in coverage[:9]
    ]

    trend = {
        'topic':      main_article.get('heading', main_article.get('topic', 'News Update')),
        'articles':   cluster_articles,
        'keywords':   main_article.get('keywords', []),
        'session_id': '',
    }

    t = threading.Thread(target=_run_rag_generate, args=(idx, trend, cluster_articles), daemon=True)
    t.start()

    def _watchdog(key: str, thread: threading.Thread):
        # 2 minutes: still running → show waiting message (not an error)
        thread.join(120)
        if thread.is_alive():
            with _rag_lock:
                if _rag_state.get(key, {}).get('running'):
                    _rag_state[key] = {'running': True, 'done': False, 'error': None,
                                       'message': 'Too many sources — please wait, this may take a moment…'}
            logger.info(f'RAG watchdog: idx={key} still running at 2min, showing wait message')
        else:
            return
        # 5 minutes total: give up and show error
        thread.join(180)
        if thread.is_alive():
            with _rag_lock:
                if _rag_state.get(key, {}).get('running'):
                    _rag_state[key] = {'running': False, 'done': True, 'error': 'Generation timed out after 5 minutes — try again or check source quality'}
            logger.warning(f'RAG watchdog: idx={key} timed out after 5min')

    threading.Thread(target=_watchdog, args=(key, t), daemon=True).start()

    return JSONResponse({'status': 'started', 'message': 'RAG generation started'}, status_code=202)


@app.get('/api/articles/{idx}/rag-status')
async def rag_status(request: Request, idx: int):
    key = str(idx)
    with _rag_lock:
        state = dict(_rag_state.get(key, {'running': False, 'done': False, 'error': None}))

    if state.get('running'):
        return JSONResponse({'status': 'running', 'message': state.get('message', '')})
    if state.get('error'):
        return JSONResponse({'status': 'error', 'message': state['error']})
    if state.get('done') or key in SUMMARIES:
        summary = SUMMARIES.get(key)
        if summary:
            return JSONResponse({'status': 'done', 'summary': summary})
        return JSONResponse({'status': 'error', 'message': 'Generation completed but no summary stored'})
    return JSONResponse({'status': 'idle'})


@app.post('/api/articles/{idx}/news-check')
async def news_check_route(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key      = str(idx)
    article  = SCRAPED_ARTICLES[idx]

    if article.get('news_check'):
        return JSONResponse({'status': 'success', 'check': article['news_check']})

    coverage = COVERAGES.get(key, [])

    try:
        from src.content_generation.news_checker import analyze_article
        result = analyze_article(article, coverage)
        article['news_check'] = result
        return JSONResponse({'status': 'success', 'check': result})
    except Exception as e:
        logger.error('News check error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


@app.post('/api/articles/{idx}/timeline')
async def generate_timeline_route(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key = str(idx)
    if key in TIMELINES:
        return JSONResponse({'status': 'success', 'timeline': TIMELINES[key]})

    main_article = SCRAPED_ARTICLES[idx]

    if key not in COVERAGES:
        try:
            from src.utils.similarity import find_similar
            results = find_similar(main_article, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
            COVERAGES[key] = [{**art, 'similarity': score} for art, score in results]
        except Exception as e:
            logger.error('Coverage search error for timeline: %s', e)
            COVERAGES[key] = []

    try:
        from src.content_generation.timeline_generator import generate_timeline
        events = generate_timeline(main_article, COVERAGES[key])
        if not events:
            return JSONResponse({'status': 'error', 'message': 'Timeline returned empty'}, status_code=500)
    except Exception as e:
        logger.error('Timeline generation error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)

    TIMELINES[key] = events
    return JSONResponse({'status': 'success', 'timeline': events})


@app.post('/api/articles/{idx}/social')
async def generate_social(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key = str(idx)
    if key in SOCIALS:
        return JSONResponse({'status': 'success', 'social': SOCIALS[key]})

    headline = SCRAPED_ARTICLES[idx].get('heading', '')
    if not headline:
        return JSONResponse({'status': 'error', 'message': 'Article has no headline'}, status_code=400)

    try:
        from src.scrapers.apify_scrape import scrape_twitter
        from src.content_generation.apify_scrape_summary import generate_social_summary

        tw_posts = scrape_twitter(headline, max_items=10)
        twitter_data = generate_social_summary('twitter', tw_posts, headline)

        result = {'twitter': twitter_data}
    except Exception as e:
        logger.error('Social scrape error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)

    SOCIALS[key] = result
    return JSONResponse({'status': 'success', 'social': result})


@app.post('/api/articles/{idx}/social-posts')
async def generate_social_posts_route(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key = str(idx)
    if key in SOCIAL_POSTS:
        sp = SOCIAL_POSTS[key]
        return JSONResponse({
            'status': 'success',
            'posts': sp['posts'],
            'image_url': sp.get('image_url', ''),
            'tv_script': sp.get('tv_script', ''),
            'podcast_script': sp.get('podcast_script', ''),
        })

    main_article = SCRAPED_ARTICLES[idx]
    cached       = SUMMARIES.get(key)

    article_for_post = {
        'heading':      (cached.get('title') if cached else None) or main_article.get('heading', ''),
        'story':        (cached.get('body')  if cached else None) or main_article.get('story', ''),
        'location':     (cached.get('location') if cached else None) or main_article.get('location', ''),
        'source_count': 10,
    }
    image_url = (
        AI_IMAGE_PUBLIC.get(key)
        or main_article.get('image_url', '')
        or main_article.get('top_image', '')
    )

    try:
        from src.api_integrations.social_media_poster import (
            generate_social_posts, generate_tv_script, generate_podcast_script,
        )
        posts          = generate_social_posts(article_for_post, article_url='', image_url=image_url)
        tv_script      = generate_tv_script(article_for_post, duration_seconds=120)
        podcast_script = generate_podcast_script(article_for_post, duration_seconds=60)
    except Exception as e:
        logger.error('Social posts generation error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)

    SOCIAL_POSTS[key] = {
        'posts': posts, 'image_url': image_url,
        'tv_script': tv_script, 'podcast_script': podcast_script,
    }
    return JSONResponse({
        'status': 'success',
        'posts': posts, 'image_url': image_url,
        'tv_script': tv_script, 'podcast_script': podcast_script,
    })


@app.post('/api/articles/{idx}/push-social')
async def push_social_posts(request: Request, idx: int):
    key = str(idx)
    sp = SOCIAL_POSTS.get(key)
    if not sp or not sp.get('posts'):
        return JSONResponse({'status': 'error', 'message': 'Generate social posts first'}, status_code=400)

    posts     = sp['posts']
    image_url = sp.get('image_url', '')
    heading   = (SUMMARIES.get(key) or {}).get('title') or (
        SCRAPED_ARTICLES[idx].get('heading', 'Article') if 0 <= idx < len(SCRAPED_ARTICLES) else 'Article'
    )

    slack_url = os.environ.get('SLACK_WEBHOOK_URL', '').strip()
    results   = {}

    if slack_url:
        try:
            platform_rows = [
                ('instagram', '📸 Instagram', posts.get('instagram', '')),
                ('twitter',   '🐦 X / Twitter', posts.get('twitter', '')),
                ('facebook',  '👥 Facebook', posts.get('facebook', '')),
                ('linkedin',  '💼 LinkedIn', posts.get('linkedin', '')),
            ]
            blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🚀 Social Push — {heading[:72]}", "emoji": True},
                },
            ]
            if image_url:
                blocks.append({"type": "image", "image_url": image_url, "alt_text": heading})
            for _key, label, content in platform_rows:
                if content:
                    blocks.append({
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*{label}*\n{content[:2900]}"},
                    })
                    blocks.append({"type": "divider"})

            r = _requests.post(slack_url, json={"blocks": blocks}, timeout=8)
            if r.ok:
                results = {p: 'ok' for p, _, c in platform_rows if c}
            else:
                results = {p: f'err:{r.status_code}' for p, _, c in platform_rows if c}
                return JSONResponse({'status': 'error', 'message': f'Slack returned {r.status_code}', 'results': results}, status_code=502)
        except Exception as e:
            logger.warning('push_social_posts Slack error: %s', e)
            return JSONResponse({'status': 'error', 'message': str(e)}, status_code=502)
    else:
        return JSONResponse({'status': 'error', 'message': 'No SLACK_WEBHOOK_URL configured'}, status_code=400)

    return JSONResponse({'status': 'success', 'results': results})


@app.post('/api/articles/{idx}/preview-publish')
async def preview_publish_to_hocalwire(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key    = str(idx)
    cached = SUMMARIES.get(key)
    if not cached:
        return JSONResponse({
            'status': 'error',
            'message': 'Generate the AI Synthesis first before publishing.',
        }, status_code=400)

    main_article = SCRAPED_ARTICLES[idx]
    try:
        body_json = await request.json()
    except Exception:
        body_json = {}

    title          = body_json.get('edited_title') or cached.get('title') or main_article.get('heading', '')
    body           = body_json.get('edited_body')  or cached.get('body', '')
    subtitle       = (body_json.get('edited_subtitle') or cached.get('subtitle') or cached.get('sub_heading') or main_article.get('sub_heading', '')).strip()
    selected_image = body_json.get('selected_image')
    category       = body_json.get('category') or cached.get('category') or 'General'

    if not title or not body:
        return JSONResponse({'status': 'error', 'message': 'Summary has no title or body'}, status_code=400)

    try:
        from src.api_integrations.hocalwire_uploader import format_article_for_cms
        from src.content_generation.location_extractor import extract_location_and_category

        _si = selected_image if selected_image and not selected_image.startswith('/static/') else None
        image_url = (
            _si or AI_IMAGE_PUBLIC.get(key)
            or main_article.get('image_url', '') or main_article.get('top_image', '')
        )
        reporter = cached.get('reporter', '')

        article_payload = {
            'heading':     title,
            'sub_heading': subtitle,
            'story':       body,
            'image_url':   image_url,
            'language':    'en',
            'location':    cached.get('location') or main_article.get('location', 'Hyderabad'),
            'reporter':    reporter,
        }

        try:
            location, category_id, category_name = extract_location_and_category(article_payload)
        except Exception:
            location      = article_payload['location']
            category_id   = os.getenv('HOCALWIRE_CATEGORY_ID', '770')
            category_name = 'General'

        return JSONResponse({
            'status':       'success',
            'heading':      title,
            'sub_heading':  subtitle,
            'html_story':   format_article_for_cms(body),
            'image_url':    image_url,
            'location':     location,
            'category':     category_name,
            'category_id':  category_id,
            'language':     'en',
            'news_type':    os.getenv('HOCALWIRE_NEWS_TYPE', 'CITIZEN_FEED'),
            'reporter':     reporter,
        })
    except Exception as e:
        logger.error('Hocalwire preview error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


@app.post('/api/articles/{idx}/publish')
async def publish_to_hocalwire(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key    = str(idx)
    cached = SUMMARIES.get(key)
    if not cached:
        return JSONResponse({
            'status': 'error',
            'message': 'Generate the AI Synthesis first before publishing.',
        }, status_code=400)

    main_article = SCRAPED_ARTICLES[idx]
    try:
        body_json = await request.json()
    except Exception:
        body_json = {}

    title          = body_json.get('edited_title') or cached.get('title') or main_article.get('heading', '')
    body           = body_json.get('edited_body')  or cached.get('body', '')
    subtitle       = (body_json.get('edited_subtitle') or cached.get('subtitle') or cached.get('sub_heading') or main_article.get('sub_heading', '')).strip()
    selected_image = body_json.get('selected_image')
    selected_tweets = body_json.get('selected_tweets') or []
    with open('/tmp/robin_publish_debug.log', 'a') as _dbg:
        import json as _json
        _dbg.write(f"PUBLISH idx={idx} tweets_count={len(selected_tweets)} tweets={_json.dumps(selected_tweets[:1]) if selected_tweets else '[]'}\n")
        _dbg.write(f"BODY_KEYS={list(body_json.keys())}\n")

    if not title or not body:
        return JSONResponse({'status': 'error', 'message': 'Summary has no title or body'}, status_code=400)

    try:
        from src.api_integrations.hocalwire_uploader import upload_to_hocalwire

        _si = selected_image if selected_image and not selected_image.startswith('/static/') else None
        image_url = (
            _si or AI_IMAGE_PUBLIC.get(key)
            or main_article.get('image_url', '') or main_article.get('top_image', '')
        )

        article_payload = {
            'heading':     title,
            'sub_heading': subtitle,
            'story':       body,
            'image_url':   image_url,
            'language':    'en',
            'location':    cached.get('location') or main_article.get('location', 'Hyderabad'),
            'reporter':    cached.get('reporter', ''),
            'selected_tweets': selected_tweets,
        }

        success = upload_to_hocalwire(article_payload)
        if success:
            feed_id = article_payload.get('hocalwire_feed_id', '')
            main_article['upload_status']     = 'uploaded'
            main_article['hocalwire_feed_id'] = feed_id
            main_article['uploaded_at']       = article_payload.get('uploaded_at', '')
            _record_activity(key, main_article, published_hocalwire=True)
            try:
                from src.utils.country_resolver import resolve_country as _resolve_country
                _country = (
                    _resolve_country({'heading': f"{title} {subtitle}"})
                    or main_article.get('region', '')
                    or main_article.get('country', '')
                )
            except Exception:
                _country = ''
            try:
                from src.utils.supabase_sync import insert_published as _sb_pub
                _sb_pub(
                    idx=idx, heading=title, sub_heading=subtitle,
                    hocalwire_id=feed_id, reporter=cached.get('reporter', ''),
                    category=cached.get('category', ''), image_url=image_url,
                    story=body, country=_country,
                )
            except Exception as _se:
                logger.warning("Supabase insert_published failed: %s", _se)
            gn_id = _push_to_global_news({
                'heading':     title,
                'sub_heading': subtitle,
                'story':       body,
                'image_url':   image_url,
                'category':    cached.get('category', 'World'),
                'location':    cached.get('location') or main_article.get('location', ''),
                'reporter':    cached.get('reporter', ''),
                'server_idx':  idx,
                'country':     _country,
                'selected_tweets': selected_tweets,
            }, feed_id)
            if gn_id:
                main_article['global_news_id']      = gn_id
                main_article['global_news_heading']  = title
                _persist_articles()
            return JSONResponse({
                'status': 'success',
            })

        return JSONResponse({
            'status': 'error',
            'message': article_payload.get('upload_error', 'Hocalwire rejected the upload.'),
        }, status_code=502)

    except Exception as e:
        logger.error('Hocalwire publish error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


@app.post('/api/articles/{idx}/download')
async def download_docx(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key    = str(idx)
    cached = SUMMARIES.get(key)
    if not cached:
        return JSONResponse({
            'status': 'error',
            'message': 'No summary generated yet — call POST /api/articles/<id>/summary first',
        }, status_code=400)

    summary = dict(cached)
    try:
        body_json = await request.json()
    except Exception:
        body_json = {}
    if body_json.get('edited_title'):
        summary['title'] = body_json['edited_title']
    if body_json.get('edited_subtitle'):
        summary['subtitle']    = body_json['edited_subtitle']
        summary['sub_heading'] = body_json['edited_subtitle']
    if body_json.get('edited_body'):
        summary['body'] = body_json['edited_body']

    try:
        from src.utils.docx_generator import create_docx
        buf = create_docx(summary, COVERAGES.get(key, []), SCRAPED_ARTICLES[idx])
    except Exception as e:
        logger.error('DOCX generation error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)

    safe_title = (summary.get('title') or 'osi-news-summary')
    safe_title = ''.join(c if c.isalnum() or c in ' -_' else '' for c in safe_title)[:60].strip().replace(' ', '_')

    return StreamingResponse(
        io.BytesIO(buf.read()),
        media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers={'Content-Disposition': f'attachment; filename="robincc_{safe_title}.docx"'},
    )


@app.post('/api/articles/{idx}/image')
async def generate_ai_image(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    key   = str(idx)
    force = request.query_params.get('force', 'false').lower() == 'true'
    if not force and key in AI_IMAGES:
        return JSONResponse({'status': 'success', 'image_url': AI_IMAGES[key], 'model_label': 'FLUX.1-schnell'})

    AI_IMAGES.pop(key, None)
    AI_IMAGE_PUBLIC.pop(key, None)

    article = SCRAPED_ARTICLES[idx]

    try:
        import hashlib
        import requests as _req
        from src.image_generation.image_creator import build_image_prompt

        AI_IMAGES_STATIC_DIR.mkdir(parents=True, exist_ok=True)

        try:
            from src.image_generation.prompt_generator import generate_groq_image_prompt
            prompt = generate_groq_image_prompt(article) or build_image_prompt(article)
        except Exception as _pe:
            logger.warning(f"Groq prompt failed, using rule-based: {_pe}")
            prompt = build_image_prompt(article)

        logger.info(f"Image prompt: {prompt[:120]}...")

        together_key = os.environ.get('TOGETHER_API_KEY', '').strip()
        if not together_key:
            return JSONResponse({'status': 'error', 'message': 'TOGETHER_API_KEY not set in .env'}, status_code=500)

        image_bytes = None
        used_model  = 'FLUX.1-schnell'

        try:
            resp = _req.post(
                'https://api.together.xyz/v1/images/generations',
                headers={'Authorization': f'Bearer {together_key}', 'Content-Type': 'application/json'},
                json={'model': 'black-forest-labs/FLUX.1-schnell', 'prompt': prompt,
                      'n': 1, 'steps': 4, 'response_format': 'b64_json'},
                timeout=120,
            )
            if resp.status_code == 200:
                import base64
                image_bytes = base64.b64decode(resp.json()['data'][0]['b64_json'])
                logger.info(f"FLUX.1-schnell: {len(image_bytes)//1024} KB")
            else:
                logger.warning(f"Together.ai: {resp.status_code} {resp.text[:200]}")
        except Exception as _me:
            logger.warning(f"Together.ai error: {_me}")

        if not image_bytes:
            return JSONResponse({'status': 'error', 'message': 'Image generation failed — check TOGETHER_API_KEY and try again'}, status_code=500)

        timestamp  = datetime.now().strftime('%Y%m%d_%H%M%S')
        title_hash = hashlib.md5(article.get('heading', '').encode()).hexdigest()[:8]
        filename   = f"aiimg_{timestamp}_{title_hash}.jpg"
        filepath   = AI_IMAGES_STATIC_DIR / filename

        try:
            from PIL import Image as _PILImage
            _PILImage.open(io.BytesIO(image_bytes)).save(str(filepath), format='JPEG', quality=90)
        except Exception:
            filepath.write_bytes(image_bytes)

        url = f"/static/ai_images/{filename}"
        AI_IMAGES[key] = url

        stable_url = _upload_to_catbox(filepath)
        if stable_url:
            AI_IMAGE_PUBLIC[key] = stable_url
            logger.info(f"Catbox URL: {stable_url}")

        return JSONResponse({
            'status': 'success', 'image_url': url,
            'prompt_used': prompt, 'model_label': used_model, 'model_id': used_model,
        })

    except Exception as e:
        logger.error('AI image generation error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


@app.post('/api/articles/{idx}/video')
async def trigger_video_workflow(request: Request, idx: int):
    import urllib.request as _urllib_req

    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    try:
        body_json = await request.json()
    except Exception:
        body_json = {}

    webhook_url = body_json.get('webhook_url') or os.environ.get('VIDEO_WEBHOOK_URL', '').strip()
    if not webhook_url:
        return JSONResponse({
            'status': 'error',
            'message': 'No webhook URL configured. Set VIDEO_WEBHOOK_URL in .env or pass webhook_url in the request body.',
        }, status_code=400)

    key       = str(idx)
    article   = SCRAPED_ARTICLES[idx]
    payload   = {
        'article':   article,
        'summary':   SUMMARIES.get(key),
        'coverage':  COVERAGES.get(key, []),
        'image_url': AI_IMAGE_PUBLIC.get(key) or AI_IMAGES.get(key) or article.get('image_url', ''),
        'source':    'robin-cc',
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')

    try:
        req = _urllib_req.Request(
            webhook_url, data=payload_bytes,
            headers={'Content-Type': 'application/json', 'User-Agent': 'robin-cc/1.0'},
            method='POST',
        )
        with _urllib_req.urlopen(req, timeout=15) as resp:
            status_code = resp.getcode()
    except Exception as e:
        logger.error('Video workflow webhook error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=502)

    if 200 <= status_code < 300:
        _record_activity(key, article, video_sent=True)
        return JSONResponse({'status': 'success', 'message': f'Sent to video workflow (HTTP {status_code})'})
    return JSONResponse({'status': 'error', 'message': f'Webhook returned HTTP {status_code}'}, status_code=502)


RIG_SERVER_URL = os.environ.get('RIG_SERVER_URL', 'http://localhost:8000')


def _clean_script_for_tts(text: str, script_type: str = 'tv') -> str:
    import re
    text = re.sub(r'\[WORDS:\s*\d+\]\s*\[TIME:\s*~?\d+s?\]', '', text)
    if script_type == 'podcast':
        text = re.sub(r'\[JINGLE:[^\]]*\]\s*\n?', '', text)
        text = re.sub(r'^\[(HEADLINE|TEASER|SUMMARY|SIGN-OFF)\]\s*$', '', text, flags=re.MULTILINE)
    return text.replace('//', ',').strip()


@app.post('/api/articles/{idx}/generate-audio')
async def generate_audio(request: Request, idx: int):
    import requests as _req

    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'error': 'Article not found'}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    script_type = body.get('script_type', 'tv')

    key    = str(idx)
    cached = SOCIAL_POSTS.get(key, {})
    raw_script = cached.get('tv_script' if script_type == 'tv' else 'podcast_script', '')
    if not raw_script:
        return JSONResponse({'error': 'No script generated yet — open the script modal first'}, status_code=404)

    try:
        resp = _req.post(
            f'{RIG_SERVER_URL}/generate',
            json={'text': _clean_script_for_tts(raw_script, script_type),
                  'profile_id': 'anchor_male_en', 'allow_draft_fallback': True},
            timeout=90,
        )
    except Exception as e:
        logger.error('Rig TTS server unreachable: %s', e)
        return JSONResponse({'error': f'TTS server unreachable: {e}'}, status_code=503)

    if resp.status_code != 200:
        logger.error('Rig /generate returned %s: %s', resp.status_code, resp.text[:200])
        return JSONResponse({'error': f'TTS server error {resp.status_code}'}, status_code=502)

    return Response(
        resp.content,
        media_type='audio/wav',
        headers={'Content-Disposition': f'inline; filename="script_{script_type}_{idx}.wav"'},
    )


@app.post('/api/articles/{idx}/send-to-inbox')
async def send_to_inbox(request: Request, idx: int):
    import requests as _req

    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'error': 'Article not found'}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    script_type = body.get('script_type', 'tv')

    key     = str(idx)
    cached  = SOCIAL_POSTS.get(key, {})
    article = SCRAPED_ARTICLES[idx]
    raw_script = cached.get('tv_script' if script_type == 'tv' else 'podcast_script', '')

    if not raw_script:
        return JSONResponse({'error': 'No script generated yet — open the script modal first'}, status_code=404)

    try:
        resp = _req.post(
            f'{RIG_SERVER_URL}/intake',
            json={'script_type': script_type, 'title': article.get('heading', 'Untitled'),
                  'script_text': raw_script, 'article_idx': idx},
            timeout=10,
        )
    except Exception as e:
        logger.error('Rig intake unreachable: %s', e)
        return JSONResponse({'error': f'Rig Studio unreachable: {e}'}, status_code=503)

    if resp.status_code != 200:
        logger.error('Rig /intake returned %s', resp.status_code)
        return JSONResponse({'error': f'Rig Studio error {resp.status_code}'}, status_code=502)

    _record_activity(key, article, tv_sent=(script_type == 'tv'), radio_sent=(script_type != 'tv'))
    return JSONResponse({'status': 'ok', 'item': resp.json()})


def _record_activity(key: str, article: dict, *, published_hocalwire=False,
                     video_sent=False, tv_sent=False, radio_sent=False):
    entry = ACTIVITY.setdefault(key, {
        'headline':            article.get('heading', 'Untitled'),
        'source_name':         article.get('source_name', ''),
        'published_hocalwire': False, 'published_at':  None,
        'video_sent':          False, 'video_sent_at': None,
        'tv_sent':             False, 'tv_sent_at':    None,
        'radio_sent':          False, 'radio_sent_at': None,
    })
    now = datetime.now().isoformat()
    if published_hocalwire:
        entry['published_hocalwire'] = True
        entry['published_at']        = now
    if video_sent:
        entry['video_sent']    = True
        entry['video_sent_at'] = now
    if tv_sent:
        entry['tv_sent']    = True
        entry['tv_sent_at'] = now
    if radio_sent:
        entry['radio_sent']    = True
        entry['radio_sent_at'] = now


@app.get('/api/activity')
async def get_activity(request: Request):
    items = [
        {'idx': k, **v}
        for k, v in ACTIVITY.items()
        if v.get('published_hocalwire') or v.get('video_sent')
           or v.get('tv_sent') or v.get('radio_sent')
    ]
    items.sort(
        key=lambda x: x.get('published_at') or x.get('video_sent_at')
            or x.get('tv_sent_at') or x.get('radio_sent_at') or '',
        reverse=True,
    )
    return JSONResponse({'status': 'success', 'count': len(items), 'activity': items})


@app.get('/api/published-feed')
async def published_feed(request: Request):
    try:
        from src.utils.supabase_sync import get_published_feed
        rows = get_published_feed()
        if rows is None:
            return JSONResponse({'status': 'error', 'message': 'Supabase not configured'}, status_code=500)
        return JSONResponse({'status': 'success', 'articles': rows})
    except Exception as e:
        logger.error('published_feed error: %s', e)
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


@app.get('/api/articles/{idx}/status')
async def get_article_status(request: Request, idx: int):
    entry = ACTIVITY.get(str(idx), {})
    return JSONResponse({
        'status':              'success',
        'published_hocalwire': entry.get('published_hocalwire', False),
        'video_sent':          entry.get('video_sent', False),
        'tv_sent':             entry.get('tv_sent', False),
        'radio_sent':          entry.get('radio_sent', False),
        'published_at':        entry.get('published_at'),
        'video_sent_at':       entry.get('video_sent_at'),
        'tv_sent_at':          entry.get('tv_sent_at'),
        'radio_sent_at':       entry.get('radio_sent_at'),
    })


@app.post('/api/articles/{idx}/lead-story')
async def set_lead_story(request: Request, idx: int):
    if not _GN_URL or not _GN_KEY:
        return JSONResponse({'status': 'error', 'message': 'Global News not configured'}, status_code=400)
    try:
        body_json = await request.json()
    except Exception:
        body_json = {}
    clear = body_json.get('clear', False)
    article = SCRAPED_ARTICLES[idx] if 0 <= idx < len(SCRAPED_ARTICLES) else {}
    cached  = SUMMARIES.get(str(idx), {})
    gn_id   = article.get('global_news_id', '')
    heading = article.get('global_news_heading') or cached.get('title') or article.get('heading', '')
    try:
        if clear:
            payload = {'server_idx': None, 'heading': None, 'global_news_id': None}
        else:
            payload = {'server_idx': idx, 'heading': heading, 'global_news_id': gn_id or None}
        r = _requests.post(
            f'{_GN_URL}/api/lead-story',
            json=payload,
            headers={'X-API-Key': _GN_KEY},
            timeout=8,
        )
        if r.ok:
            return JSONResponse({'status': 'success', 'active': not clear})
        return JSONResponse(
            {'status': 'error', 'message': f'Global News returned {r.status_code}: {r.text[:120]}'},
            status_code=502,
        )
    except Exception as e:
        logger.warning('Global News lead-story failed: %s', e)
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=502)


@app.post('/api/articles/{idx}/chat')
async def article_chat(request: Request, idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return JSONResponse({'status': 'error', 'message': 'Article not found'}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_message = (body.get('message') or '').strip()
    history      = body.get('history') or []

    if not user_message:
        return JSONResponse({'status': 'error', 'message': 'Empty message'}, status_code=400)

    article    = SCRAPED_ARTICLES[idx]
    title      = article.get('heading') or article.get('title', '')
    content    = article.get('story') or article.get('content') or article.get('body') or ''
    source     = article.get('source_name', '')
    cluster_id = article.get('cluster_id', '').strip()

    # ── RAG mode: article was generated by the RAG pipeline ──────────
    rag_context = ''
    mode = 'standard'
    if cluster_id:
        try:
            from src.database.qdrant_store import semantic_search_cluster
            chunks = semantic_search_cluster(user_message, cluster_id, top_k=8)
            if chunks:
                mode = 'rag'
                parts = []
                for c in chunks:
                    src  = c.get('source_name', '')
                    text = c.get('chunk_text', '').strip()
                    if text:
                        parts.append(f"[{src}] {text}" if src else text)
                rag_context = "\n\n---\n\n".join(parts)
        except Exception as e:
            logger.warning(f'RAG chat retrieval failed ({e}), falling back to standard')

    if mode == 'rag':
        system_prompt = (
            f"You are a news analyst assistant. The user is reading an article titled: \"{title}\".\n\n"
            f"The following excerpts were retrieved from the article's source material and are most relevant to the user's question:\n\n"
            f"{rag_context}\n\n"
            f"Instructions:\n"
            f"- Answer using ONLY the retrieved excerpts above. Quote them directly where helpful.\n"
            f"- If the excerpts don't fully answer the question, say so and use your general knowledge to supplement, noting it's not from the source material.\n"
            f"- If the question is completely unrelated to the article, politely redirect.\n"
            f"- Be concise and factual."
        )
    else:
        system_prompt = (
            f"You are a news analyst assistant. The user is reading the following article and may ask questions about it or related topics.\n\n"
            f"SOURCE: {source}\nTITLE: {title}\n\nARTICLE:\n{content[:6000]}\n\n"
            f"Instructions:\n"
            f"- If the question is directly answered in the article, quote or summarise from it.\n"
            f"- If the question is related to the article's topic but not explicitly covered, answer using your knowledge and note that the article doesn't mention it.\n"
            f"- If the question is completely unrelated to the article, politely redirect the user to ask something about the article.\n"
            f"- Always be concise and factual."
        )

    messages = [{'role': 'system', 'content': system_prompt}]
    for h in history[-10:]:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': user_message})

    try:
        from src.utils.groq_pool import groq_completion
        resp   = groq_completion(messages=messages, model='llama-3.3-70b-versatile',
                                 temperature=0.3, max_tokens=512)
        answer = resp.choices[0].message.content.strip()
        return JSONResponse({'status': 'success', 'answer': answer, 'mode': mode})
    except Exception as e:
        logger.error('Article chat error: %s\n%s', e, traceback.format_exc())
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


# ── Helpers ──────────────────────────────────────────────────────
def save_articles_to_json(articles):
    global _current_json_path
    ensure_output_dir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath  = OUTPUT_DIR / f'scraped_{timestamp}.json'
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump({'scraped_at': datetime.now().isoformat(),
                   'count': len(articles), 'articles': articles},
                  f, indent=2, ensure_ascii=False, default=str)
    _current_json_path = filepath
    print(f"Saved articles to: {filepath}")
    return filepath


def _persist_articles():
    if not _current_json_path:
        return
    with open(_current_json_path, 'w', encoding='utf-8') as f:
        json.dump({'scraped_at': datetime.now().isoformat(),
                   'count': len(SCRAPED_ARTICLES), 'articles': SCRAPED_ARTICLES},
                  f, indent=2, ensure_ascii=False, default=str)


def load_articles(articles_list):
    global SCRAPED_ARTICLES
    SCRAPED_ARTICLES = articles_list


# ── Auto-generation worker ──────────────────────────────────
def _autogen_single(idx: int):
    """Run full generation pipeline for one article: coverage → RAG → social → news-check → image."""
    key = str(idx)
    with _autogen_lock:
        _autogen_state[key] = 'running'

    try:
        article = SCRAPED_ARTICLES[idx]
        headline = article.get('heading', '')

        # 1. Coverage
        if key not in COVERAGES:
            try:
                from src.utils.similarity import find_similar
                results = find_similar(article, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
                COVERAGES[key] = [{**art, 'similarity': score} for art, score in results]
            except Exception as e:
                logger.warning("Autogen coverage %s: %s", key, e)
                COVERAGES[key] = []

        # 2. RAG synthesis
        if key not in SUMMARIES:
            try:
                coverage = COVERAGES.get(key, [])
                cluster = [article] + [{k: v for k, v in art.items() if k != 'similarity'} for art in coverage[:9]]
                trend = {
                    'topic': headline or 'News Update',
                    'articles': cluster,
                    'keywords': article.get('keywords', []),
                    'session_id': '',
                }
                _run_rag_generate(idx, trend, cluster)
            except Exception as e:
                logger.warning("Autogen RAG %s: %s", key, e)

        # 3. Social/tweets
        if key not in SOCIALS:
            try:
                from src.scrapers.apify_scrape import scrape_twitter
                from src.content_generation.apify_scrape_summary import generate_social_summary
                tw_posts = scrape_twitter(headline, max_items=10)
                twitter_data = generate_social_summary('twitter', tw_posts, headline)
                SOCIALS[key] = {'twitter': twitter_data}
            except Exception as e:
                logger.warning("Autogen social %s: %s", key, e)

        # 4. News check
        try:
            from src.content_generation.news_checker import analyze_article
            cached = SUMMARIES.get(key)
            text = (cached.get('body') if cached else None) or article.get('story', '')
            title = (cached.get('title') if cached else None) or headline
            nc = analyze_article(title, text, article.get('source_name', ''), SCRAPED_ARTICLES, idx)
            article['news_check'] = nc
        except Exception as e:
            logger.warning("Autogen news-check %s: %s", key, e)

        # 5. AI image
        if key not in AI_IMAGES:
            try:
                import hashlib
                import requests as _req
                from src.image_generation.image_creator import build_image_prompt

                AI_IMAGES_STATIC_DIR.mkdir(parents=True, exist_ok=True)
                try:
                    from src.image_generation.prompt_generator import generate_groq_image_prompt
                    prompt = generate_groq_image_prompt(article) or build_image_prompt(article)
                except Exception:
                    prompt = build_image_prompt(article)

                together_key = os.environ.get('TOGETHER_API_KEY', '').strip()
                if together_key:
                    resp = _req.post(
                        'https://api.together.xyz/v1/images/generations',
                        headers={'Authorization': f'Bearer {together_key}', 'Content-Type': 'application/json'},
                        json={'model': 'black-forest-labs/FLUX.1-schnell', 'prompt': prompt,
                              'n': 1, 'steps': 4, 'response_format': 'b64_json'},
                        timeout=120,
                    )
                    if resp.status_code == 200:
                        import base64
                        image_bytes = base64.b64decode(resp.json()['data'][0]['b64_json'])
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        title_hash = hashlib.md5(headline.encode()).hexdigest()[:8]
                        fname = f"aiimg_{timestamp}_{title_hash}.jpg"
                        fpath = AI_IMAGES_STATIC_DIR / fname
                        with open(fpath, 'wb') as f:
                            f.write(image_bytes)
                        local_url = f"/static/ai_images/{fname}"
                        AI_IMAGES[key] = local_url
                        public_url = _upload_to_catbox(fpath) or local_url
                        AI_IMAGE_PUBLIC[key] = public_url
            except Exception as e:
                logger.warning("Autogen image %s: %s", key, e)

        with _autogen_lock:
            _autogen_state[key] = 'done'
        logger.info("Autogen complete for article %s: %s", key, headline[:60])

    except Exception as e:
        with _autogen_lock:
            _autogen_state[key] = 'error'
        logger.error("Autogen failed for %s: %s", key, e)


def _autogen_worker(idx: int):
    """Wraps _autogen_single with semaphore for concurrency control."""
    with _autogen_sem:
        _autogen_single(idx)


@app.post('/api/auto-generate')
async def auto_generate(request: Request):
    """Start auto-generation for a batch of article indices."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    indices = body.get('indices', [])
    if not indices:
        return JSONResponse({'status': 'error', 'message': 'No indices provided'}, status_code=400)

    started = []
    for idx in indices:
        if not (0 <= idx < len(SCRAPED_ARTICLES)):
            continue
        key = str(idx)
        with _autogen_lock:
            if _autogen_state.get(key) in ('running', 'done'):
                continue
            _autogen_state[key] = 'pending'
        threading.Thread(target=_autogen_worker, args=(idx,), daemon=True).start()
        started.append(idx)

    return JSONResponse({'status': 'started', 'count': len(started), 'indices': started})


@app.get('/api/auto-generate/status')
async def auto_generate_status(request: Request):
    """Return generation status for all articles."""
    with _autogen_lock:
        return JSONResponse({'status': 'success', 'states': dict(_autogen_state)})


if __name__ == '__main__':
    import uvicorn
    print("\n" + "=" * 60)
    print("OSI News Automation - Results Viewer (dev mode)")
    print("=" * 60)
    print(f"\nProject root: {PROJECT_ROOT}")
    print(f"Output dir:   {OUTPUT_DIR}")
    print("\nStarting server at http://localhost:5008")
    print("Press Ctrl+C to stop\n")
    uvicorn.run('src.frontend.app:app', host='0.0.0.0', port=5008, reload=False)
