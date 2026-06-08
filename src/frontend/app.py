"""
OSI News Automation System - Results Viewer Frontend
=====================================================
A Flask-based web interface to view scraped articles and their formatting.
"""

import os
import sys
import json
import traceback
import threading
from datetime import datetime
from pathlib import Path

# Get absolute path to project root (parent of src/)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()

# Add project root to path for imports
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, render_template, jsonify, request, send_file, session, redirect, url_for
from werkzeug.security import check_password_hash
from functools import wraps
from dotenv import load_dotenv
import logging
import io

# Load environment variables
load_dotenv(PROJECT_ROOT / '.env')

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')

# Disable static file caching so CSS/JS changes are always picked up immediately
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ── Secret key (required for session security in production) ────
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32)

# ── Hardcoded auth credentials (exactly 2 users allowed) ────────
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

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

# ── Logging — level driven by LOG_LEVEL env var ─────────────────
_log_level = getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)
logging.basicConfig(
    level=_log_level,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
app.logger.setLevel(_log_level)

@app.after_request
def log_response(response):
    app.logger.info('%s %s → %s', request.method, request.path, response.status_code)
    return response

@app.errorhandler(404)
def not_found(_e):
    app.logger.warning('404 Not Found: %s', request.path)
    return jsonify({'status': 'error', 'message': f'Not found: {request.path}'}), 404

@app.errorhandler(500)
def server_error(e):
    app.logger.error('500 Internal Server Error: %s\n%s', request.path, traceback.format_exc())
    return jsonify({'status': 'error', 'message': str(e)}), 500

# ── Auth routes ────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        pw_hash = _USERS.get(username)
        if pw_hash and check_password_hash(pw_hash, password):
            session['authenticated'] = True
            next_url = request.args.get('next') or '/'
            return redirect(next_url)
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Shared state ───────────────────────────────────────────────
SCRAPED_ARTICLES = []
OUTPUT_DIR = PROJECT_ROOT / 'output' / 'json'
_current_json_path = None   # path of the JSON file currently loaded

# Per-article caches (keyed by article index as string)
COVERAGES:  dict = {}   # index → list of similar article dicts
SUMMARIES:  dict = {}   # index → summary dict from multi_source_summary
TIMELINES:  dict = {}   # index → list of {time, event} dicts
SOCIALS:    dict = {}   # index → {twitter: {...}, instagram: {...}}
SOCIAL_POSTS: dict = {}  # index → {twitter, linkedin, instagram, facebook} post text
AI_IMAGES:       dict = {}   # index → /static/ai_images/<filename> URL (local)
AI_IMAGE_PUBLIC: dict = {}  # index → publicly accessible image URL (Pollinations or HF)
ACTIVITY:        dict = {}  # index → {headline, source_name, published_hocalwire, published_at, video_sent, video_sent_at}

# Static directory where AI-generated images are saved and served
AI_IMAGES_STATIC_DIR = Path(__file__).parent / 'static' / 'ai_images'


def _upload_to_catbox(filepath: Path) -> str:
    """Upload a local image file to catbox.moe and return the permanent public URL.

    Catbox.moe is a free anonymous image host. The URL is permanent and serves
    the exact bytes we saved locally — so Hocalwire fetches the same image the
    user saw in the picker, not a regenerated Pollinations variant.

    Returns the URL string on success, or empty string on failure.
    """
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
        app.logger.warning(f"Catbox unexpected response: {url[:200]}")
        return ''
    except Exception as e:
        app.logger.warning(f"Catbox upload failed (will use Pollinations fallback): {e}")
        return ''

# Background scrape job state
_scrape_state = {
    'running': False,
    'done': False,
    'count': 0,
    'error': None,
    'started_at': None,
}
_scrape_lock = threading.Lock()


def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Background worker ──────────────────────────────────────────
def _run_scrape(max_articles: int):
    """
    Fetch headlines from 20+ major RSS sources in parallel.
    Results appear in ~15-30 s.
    """
    global SCRAPED_ARTICLES, _scrape_state, COVERAGES, SUMMARIES, AI_IMAGES, AI_IMAGE_PUBLIC

    print(f"\n[Scraper] Starting multi-source RSS fetch — target {max_articles} articles")
    try:
        from src.scrapers.multi_source_rss import fetch_all_sources

        # Fetch 6 headlines per source across all 22+ outlets.
        # Do NOT trim the total — similarity search needs articles from
        # every source to find cross-outlet coverage of the same story.
        articles = fetch_all_sources(
            max_per_source=6,
            max_workers=12,
        )

        try:
            from src.utils.country_resolver import enrich_articles
            enrich_articles(articles)
        except Exception as _ce:
            app.logger.warning("Country enrichment failed: %s", _ce)

        with _scrape_lock:
            SCRAPED_ARTICLES = articles
            _scrape_state['count'] = len(articles)
            _scrape_state['error'] = None

        # Invalidate stale caches on new fetch
        COVERAGES.clear()
        SUMMARIES.clear()
        TIMELINES.clear()
        SOCIALS.clear()
        SOCIAL_POSTS.clear()
        AI_IMAGES.clear()
        AI_IMAGE_PUBLIC.clear()

        filepath = save_articles_to_json(articles)
        print(f"[Scraper] Done — {len(articles)} articles from "
              f"{len({a.get('source_name') for a in articles})} sources  →  {filepath}")

        from src.utils.supabase_sync import upsert_articles as _sb_upsert
        _sb_upsert(articles)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[Scraper] ERROR:\n{tb}")
        with _scrape_lock:
            _scrape_state['error'] = str(e)
    finally:
        with _scrape_lock:
            _scrape_state['running'] = False
            _scrape_state['done'] = True


# ── Routes ─────────────────────────────────────────────────────
@app.route('/')
@login_required
def index():
    import time
    from flask import make_response
    resp = make_response(render_template('index.html',
                           article_count=len(SCRAPED_ARTICLES),
                           last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           static_v=str(int(time.time()))))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/api/articles')
@login_required
def get_articles():
    return jsonify({
        'status': 'success',
        'count': len(SCRAPED_ARTICLES),
        'articles': SCRAPED_ARTICLES
    })


@app.route('/api/articles/<int:index>')
@login_required
def get_article(index):
    if 0 <= index < len(SCRAPED_ARTICLES):
        return jsonify({'status': 'success', 'article': SCRAPED_ARTICLES[index]})
    return jsonify({'status': 'error', 'message': 'Article not found'}), 404


@app.route('/api/articles/<int:index>', methods=['PATCH'])
@login_required
def patch_article(index):
    if not (0 <= index < len(SCRAPED_ARTICLES)):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404
    data = request.get_json(silent=True) or {}
    article = SCRAPED_ARTICLES[index]
    cached = SUMMARIES.get(str(index))
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
        app.logger.warning("Supabase patch failed: %s", _se)
    return jsonify({'status': 'success'})


@app.route('/api/scrape', methods=['POST'])
@login_required
def trigger_scrape():
    """Start scraping in a background thread and return immediately."""
    global _scrape_state

    with _scrape_lock:
        if _scrape_state['running']:
            return jsonify({
                'status': 'running',
                'message': 'Scrape already in progress — check /api/scrape/status'
            }), 202

        max_articles = 10
        if request.json:
            max_articles = int(request.json.get('max_articles', 10))

        _scrape_state = {
            'running': True,
            'done': False,
            'count': 0,
            'error': None,
            'started_at': datetime.now().isoformat(),
        }

    t = threading.Thread(target=_run_scrape, args=(max_articles,), daemon=True)
    t.start()

    return jsonify({
        'status': 'started',
        'message': f'Scraping started for up to {max_articles} articles. Poll /api/scrape/status for progress.',
    }), 202


@app.route('/api/scrape/status')
@login_required
def scrape_status():
    """Poll this endpoint to check if background scraping is done."""
    with _scrape_lock:
        state = dict(_scrape_state)

    if state['running']:
        return jsonify({'status': 'running', 'message': 'Scraping in progress…'})

    if state['error']:
        return jsonify({'status': 'error', 'message': state['error']})

    if state['done']:
        return jsonify({
            'status': 'done',
            'count': state['count'],
            'message': f"Scraped {state['count']} articles",
        })

    return jsonify({'status': 'idle', 'message': 'No scrape has been started yet.'})


@app.route('/api/save', methods=['POST'])
@login_required
def save_to_json_route():
    if not SCRAPED_ARTICLES:
        return jsonify({'status': 'error', 'message': 'No articles to save'}), 400
    filepath = save_articles_to_json(SCRAPED_ARTICLES)
    return jsonify({
        'status': 'success',
        'message': f'Saved {len(SCRAPED_ARTICLES)} articles',
        'filepath': str(filepath)
    })


@app.route('/api/load', methods=['POST'])
@login_required
def load_from_json():
    global SCRAPED_ARTICLES, COVERAGES, SUMMARIES, TIMELINES, SOCIALS, SOCIAL_POSTS, AI_IMAGES, AI_IMAGE_PUBLIC, _current_json_path
    COVERAGES.clear()
    SUMMARIES.clear()
    TIMELINES.clear()
    SOCIALS.clear()
    SOCIAL_POSTS.clear()
    AI_IMAGES.clear()
    AI_IMAGE_PUBLIC.clear()
    try:
        filename = None
        if request.json:
            filename = request.json.get('filename')

        if filename:
            filepath = OUTPUT_DIR / filename
        else:
            ensure_output_dir()
            json_files = list(OUTPUT_DIR.glob('scraped_*.json'))
            if not json_files:
                return jsonify({'status': 'error', 'message': 'No saved files found'}), 404
            filepath = max(json_files, key=lambda p: p.stat().st_mtime)

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            SCRAPED_ARTICLES = data.get('articles', data)
        _current_json_path = filepath

        try:
            from src.utils.country_resolver import enrich_articles
            enrich_articles(SCRAPED_ARTICLES)
        except Exception as _ce:
            app.logger.warning("Country enrichment failed: %s", _ce)

        try:
            from src.utils.supabase_sync import upsert_articles as _sb_upsert
            _sb_upsert(SCRAPED_ARTICLES)
        except Exception as _se:
            app.logger.warning("Supabase sync after load failed: %s", _se)

        return jsonify({
            'status': 'success',
            'message': f'Loaded {len(SCRAPED_ARTICLES)} articles',
            'filepath': str(filepath)
        })
    except Exception as e:
        print(f"Load error: {e}")
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/articles/<int:idx>/coverage')
@login_required
def get_coverage(idx: int):
    """
    Return articles from OTHER sources that cover the same story as article[idx].
    Results are cached per-index for the lifetime of the current fetch session.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    if key in COVERAGES:
        return jsonify({'status': 'success', 'coverage': COVERAGES[key]})

    target = SCRAPED_ARTICLES[idx]
    try:
        from src.utils.similarity import find_similar
        results = find_similar(target, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
        coverage = [
            {**art, 'similarity': score}
            for art, score in results
        ]
    except Exception as e:
        app.logger.error('Coverage search error: %s', e)
        coverage = []

    COVERAGES[key] = coverage
    return jsonify({'status': 'success', 'coverage': coverage})


@app.route('/api/articles/<int:idx>/summary', methods=['POST'])
@login_required
def generate_summary(idx: int):
    """
    Generate a publishable multi-source article synthesising the main article
    and all coverage articles found for it. Result is cached.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    if key in SUMMARIES:
        return jsonify({'status': 'success', 'summary': SUMMARIES[key]})

    main_article = SCRAPED_ARTICLES[idx]

    # Ensure coverage is computed
    if key not in COVERAGES:
        try:
            from src.utils.similarity import find_similar
            results = find_similar(main_article, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
            COVERAGES[key] = [{**art, 'similarity': score} for art, score in results]
        except Exception as e:
            app.logger.error('Coverage search error for summary: %s', e)
            COVERAGES[key] = []

    coverage = COVERAGES[key]

    try:
        from src.content_generation.multi_source_summary import generate_summary as gen_sum
        summary = gen_sum(main_article, coverage)
        if not summary:
            return jsonify({'status': 'error', 'message': 'LLM returned empty response'}), 500
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Summary generation error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    SUMMARIES[key] = summary
    return jsonify({'status': 'success', 'summary': summary})


@app.route('/api/articles/<int:idx>/news-check', methods=['POST'])
@login_required
def news_check_route(idx: int):
    """
    Run three-level news verification on article[idx]:
      Level 1 — content credibility (concrete/speculative/misleading/false)
      Level 2 — fake-news likelihood (credible/unverified/potentially_misleading/likely_false)
      Level 3 — trending (coverage-count based, instant)
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    article  = SCRAPED_ARTICLES[idx]
    coverage = COVERAGES.get(key, [])

    try:
        from src.content_generation.news_checker import analyze_article
        result = analyze_article(article, coverage)
        return jsonify({'status': 'success', 'check': result})
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('News check error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/articles/<int:idx>/timeline', methods=['POST'])
@login_required
def generate_timeline_route(idx: int):
    """
    Generate a chronological event timeline for article[idx].
    Uses the main article + its coverage articles as input.
    Result is cached per-index.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    if key in TIMELINES:
        return jsonify({'status': 'success', 'timeline': TIMELINES[key]})

    main_article = SCRAPED_ARTICLES[idx]

    # Ensure coverage is computed
    if key not in COVERAGES:
        try:
            from src.utils.similarity import find_similar
            results = find_similar(main_article, SCRAPED_ARTICLES, threshold=0.35, max_results=10)
            COVERAGES[key] = [{**art, 'similarity': score} for art, score in results]
        except Exception as e:
            app.logger.error('Coverage search error for timeline: %s', e)
            COVERAGES[key] = []

    coverage = COVERAGES[key]

    try:
        from src.content_generation.timeline_generator import generate_timeline
        events = generate_timeline(main_article, coverage)
        if not events:
            return jsonify({'status': 'error', 'message': 'Timeline returned empty'}), 500
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Timeline generation error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    TIMELINES[key] = events
    return jsonify({'status': 'success', 'timeline': events})


@app.route('/api/articles/<int:idx>/social', methods=['POST'])
@login_required
def generate_social(idx: int):
    """
    Scrape Twitter and Instagram for reactions to article[idx]'s headline,
    then generate AI summaries. Result is cached per-index.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    if key in SOCIALS:
        return jsonify({'status': 'success', 'social': SOCIALS[key]})

    headline = SCRAPED_ARTICLES[idx].get('heading', '')
    if not headline:
        return jsonify({'status': 'error', 'message': 'Article has no headline'}), 400

    try:
        from src.scrapers.apify_scrape import scrape_twitter, scrape_instagram
        from src.content_generation.apify_scrape_summary import generate_social_summary

        tw_posts = scrape_twitter(headline, max_items=20)
        ig_posts = scrape_instagram(headline, max_items=15)

        twitter_data   = generate_social_summary('twitter',   tw_posts, headline)
        instagram_data = generate_social_summary('instagram', ig_posts, headline)

        result = {'twitter': twitter_data, 'instagram': instagram_data}
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Social scrape error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    SOCIALS[key] = result
    return jsonify({'status': 'success', 'social': result})


@app.route('/api/articles/<int:idx>/social-posts', methods=['POST'])
@login_required
def generate_social_posts_route(idx: int):
    """
    Generate platform-specific outbound social media post text for article[idx].
    Uses cached AI synthesis when available. Result is cached per-index.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    if key in SOCIAL_POSTS:
        cached_sp = SOCIAL_POSTS[key]
        return jsonify({'status': 'success', 'posts': cached_sp['posts'], 'image_url': cached_sp.get('image_url', ''), 'tv_script': cached_sp.get('tv_script', ''), 'podcast_script': cached_sp.get('podcast_script', '')})

    main_article = SCRAPED_ARTICLES[idx]
    cached = SUMMARIES.get(key)

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
        from src.api_integrations.social_media_poster import generate_social_posts, generate_tv_script, generate_podcast_script
        posts           = generate_social_posts(article_for_post, article_url='', image_url=image_url)
        tv_script       = generate_tv_script(article_for_post, duration_seconds=120)
        podcast_script  = generate_podcast_script(article_for_post, duration_seconds=60)
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Social posts generation error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    SOCIAL_POSTS[key] = {'posts': posts, 'image_url': image_url, 'tv_script': tv_script, 'podcast_script': podcast_script}
    return jsonify({'status': 'success', 'posts': posts, 'image_url': image_url, 'tv_script': tv_script, 'podcast_script': podcast_script})


@app.route('/api/articles/<int:idx>/preview-publish', methods=['POST'])
@login_required
def preview_publish_to_hocalwire(idx: int):
    """
    Return formatted preview of how the article will appear in Hocalwire.
    Runs location/category extraction and markdown→HTML conversion without uploading.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    cached = SUMMARIES.get(key)
    if not cached:
        return jsonify({
            'status': 'error',
            'message': 'Generate the AI Synthesis first before publishing.'
        }), 400

    main_article = SCRAPED_ARTICLES[idx]
    body_json = request.get_json(silent=True) or {}

    title          = body_json.get('edited_title') or cached.get('title') or main_article.get('heading', '')
    body           = body_json.get('edited_body')  or cached.get('body', '')
    subtitle       = (body_json.get('edited_subtitle') or cached.get('subtitle') or cached.get('sub_heading') or main_article.get('sub_heading', '')).strip()
    selected_image = body_json.get('selected_image')
    category       = body_json.get('category') or cached.get('category') or 'General'

    if not title or not body:
        return jsonify({'status': 'error', 'message': 'Summary has no title or body'}), 400

    try:
        from src.api_integrations.hocalwire_uploader import format_article_for_cms
        from src.content_generation.location_extractor import extract_location_and_category

        # A /static/ path is local to this server — Hocalwire cannot fetch it.
        # Fall through to the public URL stored at generation time.
        _si = selected_image if selected_image and not selected_image.startswith('/static/') else None
        image_url = (
            _si
            or AI_IMAGE_PUBLIC.get(key)
            or main_article.get('image_url', '')
            or main_article.get('top_image', '')
        )

        reporter = cached.get('reporter', '')

        article_payload = {
            'heading':     title,
            'sub_heading': subtitle.strip(),
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

        html_story = format_article_for_cms(body)

        return jsonify({
            'status':        'success',
            'heading':       title,
            'sub_heading':   subtitle.strip(),
            'html_story':    html_story,
            'image_url':     image_url,
            'location':      location,
            'category':      category_name,
            'category_id':   category_id,
            'language':      'en',
            'news_type':     os.getenv('HOCALWIRE_NEWS_TYPE', 'CITIZEN_FEED'),
            'reporter':      reporter,
        })

    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Hocalwire preview error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/articles/<int:idx>/publish', methods=['POST'])
@login_required
def publish_to_hocalwire(idx: int):
    """
    Publish the AI-synthesised summary for article[idx] to Hocalwire CMS.
    Requires a summary to have been generated first.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    cached = SUMMARIES.get(key)
    if not cached:
        return jsonify({
            'status': 'error',
            'message': 'Generate the AI Synthesis first before publishing.'
        }), 400

    main_article = SCRAPED_ARTICLES[idx]

    # Allow caller to pass edited title/body and chosen image
    body_json = request.get_json(silent=True) or {}
    title          = body_json.get('edited_title') or cached.get('title') or main_article.get('heading', '')
    body           = body_json.get('edited_body')  or cached.get('body', '')
    subtitle       = (body_json.get('edited_subtitle') or cached.get('subtitle') or cached.get('sub_heading') or main_article.get('sub_heading', '')).strip()
    selected_image = body_json.get('selected_image')  # explicit picker choice from frontend

    if not title or not body:
        return jsonify({'status': 'error', 'message': 'Summary has no title or body'}), 400

    try:
        from src.api_integrations.hocalwire_uploader import upload_to_hocalwire

        # A /static/ path is local to this server — Hocalwire cannot fetch it.
        # Fall through to the public URL stored at generation time.
        _si = selected_image if selected_image and not selected_image.startswith('/static/') else None
        image_url = (
            _si
            or AI_IMAGE_PUBLIC.get(key)
            or main_article.get('image_url', '')
            or main_article.get('top_image', '')
        )

        article_payload = {
            'heading':     title,
            'sub_heading': subtitle.strip(),
            'story':       body,
            'image_url':   image_url,
            'language':    'en',
            'location':    cached.get('location') or main_article.get('location', 'Hyderabad'),
            'reporter':    cached.get('reporter', ''),
        }

        success = upload_to_hocalwire(article_payload)
        if success:
            feed_id = article_payload.get('hocalwire_feed_id', '')
            main_article['upload_status']     = 'uploaded'
            main_article['hocalwire_feed_id'] = feed_id
            main_article['uploaded_at']       = article_payload.get('uploaded_at', '')
            _record_activity(key, main_article, published_hocalwire=True)
            try:
                from src.utils.supabase_sync import insert_published as _sb_pub
                _sb_pub(
                    idx=idx,
                    heading=title,
                    sub_heading=subtitle,
                    hocalwire_id=feed_id,
                    reporter=cached.get('reporter', ''),
                    category=cached.get('category', ''),
                    image_url=image_url,
                )
            except Exception as _se:
                app.logger.warning("Supabase insert_published failed: %s", _se)
            return jsonify({
                'status':  'success',
                'message': 'Published to Hocalwire successfully.',
                'feed_id': feed_id,
            })
        else:
            return jsonify({
                'status':  'error',
                'message': article_payload.get('upload_error', 'Hocalwire rejected the upload.'),
            }), 502

    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Hocalwire publish error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/articles/<int:idx>/download', methods=['POST'])
@login_required
def download_docx(idx: int):
    """
    Download the multi-source summary for article[idx] as a .docx file.

    Accepts an optional JSON body with:
      {
        "edited_title": "...",   (optional — overrides cached title)
        "edited_body":  "...",   (optional — overrides cached body, markdown format)
      }

    If neither field is provided, the cached summary is used unchanged.
    Summary must exist (call POST /api/articles/<id>/summary first).
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    cached = SUMMARIES.get(key)
    if not cached:
        return jsonify({
            'status': 'error',
            'message': 'No summary generated yet — call POST /api/articles/<id>/summary first'
        }), 400

    main_article = SCRAPED_ARTICLES[idx]
    coverage     = COVERAGES.get(key, [])

    # Merge any edits from the request body
    summary = dict(cached)
    body_json = request.get_json(silent=True) or {}
    if body_json.get('edited_title'):
        summary['title'] = body_json['edited_title']
    if body_json.get('edited_subtitle'):
        summary['subtitle'] = body_json['edited_subtitle']
        summary['sub_heading'] = body_json['edited_subtitle']
    if body_json.get('edited_body'):
        summary['body'] = body_json['edited_body']

    try:
        from src.utils.docx_generator import create_docx
        buf = create_docx(summary, coverage, main_article)
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('DOCX generation error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500

    safe_title = (summary.get('title') or 'osi-news-summary')
    safe_title = ''.join(c if c.isalnum() or c in ' -_' else '' for c in safe_title)[:60].strip().replace(' ', '_')
    filename   = f"robincc_{safe_title}.docx"

    return send_file(
        io.BytesIO(buf.read()),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=filename,
    )


@app.route('/api/articles/<int:idx>/image', methods=['POST'])
@login_required
def generate_ai_image(idx: int):
    """
    Generate an AI image for article[idx] via Together.ai FLUX.1-schnell.
    Prompt is built by Groq (article-specific) with rule-based fallback.
    Result cached in-memory; force=true bypasses cache.
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    force = request.args.get('force', 'false').lower() == 'true'
    if not force and key in AI_IMAGES:
        return jsonify({'status': 'success', 'image_url': AI_IMAGES[key], 'model_label': 'FLUX.1-schnell'})

    AI_IMAGES.pop(key, None)
    AI_IMAGE_PUBLIC.pop(key, None)

    article = SCRAPED_ARTICLES[idx]

    try:
        import requests as _req, hashlib
        from src.image_generation.image_creator import build_image_prompt

        AI_IMAGES_STATIC_DIR.mkdir(parents=True, exist_ok=True)

        # ── 1. Build prompt ──────────────────────────────────────
        try:
            from src.image_generation.prompt_generator import generate_groq_image_prompt
            prompt = generate_groq_image_prompt(article) or build_image_prompt(article)
        except Exception as _pe:
            app.logger.warning(f"Groq prompt failed, using rule-based: {_pe}")
            prompt = build_image_prompt(article)

        app.logger.info(f"Image prompt: {prompt[:120]}...")

        # ── 2. FLUX.1-schnell via Together.ai ───────────────────
        together_key = os.environ.get('TOGETHER_API_KEY', '').strip()
        if not together_key:
            return jsonify({'status': 'error',
                            'message': 'TOGETHER_API_KEY not set in .env'}), 500

        image_bytes = None
        used_model  = 'FLUX.1-schnell'

        try:
            app.logger.info("Together.ai → black-forest-labs/FLUX.1-schnell")
            resp = _req.post(
                'https://api.together.xyz/v1/images/generations',
                headers={
                    'Authorization': f'Bearer {together_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'black-forest-labs/FLUX.1-schnell',
                    'prompt': prompt,
                    'n': 1,
                    'steps': 4,
                    'response_format': 'b64_json',
                },
                timeout=120,
            )
            if resp.status_code == 200:
                import base64
                b64 = resp.json()['data'][0]['b64_json']
                image_bytes = base64.b64decode(b64)
                app.logger.info(f"FLUX.1-schnell: {len(image_bytes)//1024} KB")
            else:
                app.logger.warning(f"Together.ai: {resp.status_code} {resp.text[:200]}")
        except Exception as _me:
            app.logger.warning(f"Together.ai error: {_me}")

        if not image_bytes:
            return jsonify({'status': 'error',
                            'message': 'Image generation failed — check TOGETHER_API_KEY and try again'}), 500

        # ── 3. Save to disk ──────────────────────────────────────
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

        # ── 4. Upload to catbox for stable public URL (Hocalwire) ─
        stable_url = _upload_to_catbox(filepath)
        if stable_url:
            AI_IMAGE_PUBLIC[key] = stable_url
            app.logger.info(f"Catbox URL: {stable_url}")

        return jsonify({'status': 'success', 'image_url': url,
                        'prompt_used': prompt, 'model_label': used_model,
                        'model_id': used_model})

    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('AI image generation error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/articles/<int:idx>/video', methods=['POST'])
@login_required
def trigger_video_workflow(idx: int):
    """
    Push article (+ cached summary if available) to an external video-generation
    workflow via a user-supplied or env-configured webhook URL.

    Body (JSON, all optional):
      { "webhook_url": "https://..." }   # overrides VIDEO_WEBHOOK_URL env var
    """
    import urllib.request

    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    body_json   = request.get_json(silent=True) or {}
    webhook_url = (
        body_json.get('webhook_url')
        or os.environ.get('VIDEO_WEBHOOK_URL', '').strip()
    )

    if not webhook_url:
        return jsonify({
            'status': 'error',
            'message': 'No webhook URL configured. Set VIDEO_WEBHOOK_URL in .env or pass webhook_url in the request body.'
        }), 400

    key         = str(idx)
    article     = SCRAPED_ARTICLES[idx]
    summary     = SUMMARIES.get(key)
    coverage    = COVERAGES.get(key, [])
    image_url   = AI_IMAGE_PUBLIC.get(key) or AI_IMAGES.get(key) or article.get('image_url', '')

    payload = {
        'article':   article,
        'summary':   summary,
        'coverage':  coverage,
        'image_url': image_url,
        'source':    'robin-cc',
    }

    import json as _json
    payload_bytes = _json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')

    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload_bytes,
            headers={'Content-Type': 'application/json', 'User-Agent': 'robin-cc/1.0'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            status_code = resp.getcode()
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Video workflow webhook error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 502

    if 200 <= status_code < 300:
        _record_activity(key, article, video_sent=True)
        return jsonify({'status': 'success', 'message': f'Sent to video workflow (HTTP {status_code})'})

    return jsonify({
        'status': 'error',
        'message': f'Webhook returned HTTP {status_code}'
    }), 502


RIG_SERVER_URL = os.environ.get('RIG_SERVER_URL', 'http://localhost:8000')

def _clean_script_for_tts(text: str, script_type: str = 'tv') -> str:
    """Strip production markers from a script before sending to TTS."""
    import re
    # Remove word/time metadata footer
    text = re.sub(r'\[WORDS:\s*\d+\]\s*\[TIME:\s*~?\d+s?\]', '', text)
    if script_type == 'podcast':
        # Remove jingle cue lines entirely
        text = re.sub(r'\[JINGLE:[^\]]*\]\s*\n?', '', text)
        # Remove section header lines
        text = re.sub(r'^\[(HEADLINE|TEASER|SUMMARY|SIGN-OFF)\]\s*$', '', text, flags=re.MULTILINE)
    # Replace breath-pause markers with a comma pause
    text = text.replace('//', ',')
    return text.strip()


@app.route('/api/articles/<int:idx>/generate-audio', methods=['POST'])
@login_required
def generate_audio(idx: int):
    """
    Generate TTS audio for a TV script or podcast script via the Rig TTS server.

    Body (JSON):
      { "script_type": "tv" | "podcast" }

    Returns the WAV audio bytes directly (Content-Type: audio/wav).
    """
    import requests as _req

    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'error': 'Article not found'}), 404

    body       = request.get_json(silent=True) or {}
    script_type = body.get('script_type', 'tv')

    key = str(idx)
    cached = SOCIAL_POSTS.get(key, {})
    if script_type == 'tv':
        raw_script  = cached.get('tv_script', '')
        profile_id  = 'anchor_male_en'
    else:
        raw_script  = cached.get('podcast_script', '')
        profile_id  = 'anchor_male_en'

    if not raw_script:
        return jsonify({'error': 'No script generated yet — open the script modal first'}), 404

    clean_text = _clean_script_for_tts(raw_script, script_type)

    try:
        resp = _req.post(
            f'{RIG_SERVER_URL}/generate',
            json={'text': clean_text, 'profile_id': profile_id, 'allow_draft_fallback': True},
            timeout=90,
        )
    except Exception as e:
        app.logger.error('Rig TTS server unreachable: %s', e)
        return jsonify({'error': f'TTS server unreachable: {e}'}), 503

    if resp.status_code != 200:
        app.logger.error('Rig /generate returned %s: %s', resp.status_code, resp.text[:200])
        return jsonify({'error': f'TTS server error {resp.status_code}'}), 502

    from flask import Response as _Response
    return _Response(
        resp.content,
        content_type='audio/wav',
        headers={'Content-Disposition': f'inline; filename="script_{script_type}_{idx}.wav"'},
    )


@app.route('/api/articles/<int:idx>/send-to-inbox', methods=['POST'])
@login_required
def send_to_inbox(idx: int):
    """
    Submit a TV or podcast script to the Rig Studio intake queue.

    Body (JSON):
      { "script_type": "tv" | "podcast" }
    """
    import requests as _req

    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'error': 'Article not found'}), 404

    body        = request.get_json(silent=True) or {}
    script_type = body.get('script_type', 'tv')

    key    = str(idx)
    cached = SOCIAL_POSTS.get(key, {})
    article = SCRAPED_ARTICLES[idx]
    title   = article.get('heading', 'Untitled')

    if script_type == 'tv':
        raw_script = cached.get('tv_script', '')
    else:
        raw_script = cached.get('podcast_script', '')

    if not raw_script:
        return jsonify({'error': 'No script generated yet — open the script modal first'}), 404

    try:
        resp = _req.post(
            f'{RIG_SERVER_URL}/intake',
            json={
                'script_type': script_type,
                'title':       title,
                'script_text': raw_script,
                'article_idx': idx,
            },
            timeout=10,
        )
    except Exception as e:
        app.logger.error('Rig intake unreachable: %s', e)
        return jsonify({'error': f'Rig Studio unreachable: {e}'}), 503

    if resp.status_code != 200:
        app.logger.error('Rig /intake returned %s', resp.status_code)
        return jsonify({'error': f'Rig Studio error {resp.status_code}'}), 502

    _record_activity(key, article,
                     tv_sent=(script_type == 'tv'),
                     radio_sent=(script_type != 'tv'))
    return jsonify({'status': 'ok', 'item': resp.json()})


def _record_activity(key: str, article: dict, *, published_hocalwire=False,
                     video_sent=False, tv_sent=False, radio_sent=False):
    """Upsert an activity entry for the given article index key."""
    entry = ACTIVITY.setdefault(key, {
        'headline':            article.get('heading', 'Untitled'),
        'source_name':         article.get('source_name', ''),
        'published_hocalwire': False,
        'published_at':        None,
        'video_sent':          False,
        'video_sent_at':       None,
        'tv_sent':             False,
        'tv_sent_at':          None,
        'radio_sent':          False,
        'radio_sent_at':       None,
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


@app.route('/api/activity')
@login_required
def get_activity():
    """Return all articles that have been published or video-sent."""
    items = [
        {'idx': k, **v}
        for k, v in ACTIVITY.items()
        if v.get('published_hocalwire') or v.get('video_sent')
           or v.get('tv_sent') or v.get('radio_sent')
    ]
    items.sort(key=lambda x: x.get('published_at') or x.get('video_sent_at')
               or x.get('tv_sent_at') or x.get('radio_sent_at') or '', reverse=True)
    return jsonify({'status': 'success', 'count': len(items), 'activity': items})


@app.route('/api/published-feed')
@login_required
def published_feed():
    """Return published articles from Supabase for the activity feed."""
    try:
        from src.utils.supabase_sync import get_published_feed
        rows = get_published_feed()
        if rows is None:
            return jsonify({'status': 'error', 'message': 'Supabase not configured'}), 500
        return jsonify({'status': 'success', 'articles': rows})
    except Exception as e:
        app.logger.error('published_feed error: %s', e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/articles/<int:idx>/status')
@login_required
def get_article_status(idx: int):
    """Return publish/video status for a single article."""
    key   = str(idx)
    entry = ACTIVITY.get(key, {})
    return jsonify({
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


@app.route('/api/articles/<int:idx>/chat', methods=['POST'])
@login_required
def article_chat(idx: int):
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    body = request.get_json(silent=True) or {}
    user_message = (body.get('message') or '').strip()
    history = body.get('history') or []  # list of {role, content}

    if not user_message:
        return jsonify({'status': 'error', 'message': 'Empty message'}), 400

    article = SCRAPED_ARTICLES[idx]
    title   = article.get('heading') or article.get('title', '')
    content = article.get('story') or article.get('content') or article.get('body') or ''
    source  = article.get('source_name', '')

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
    for h in history[-10:]:  # keep last 10 turns for context
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': user_message})

    try:
        from src.utils.groq_pool import groq_completion
        resp = groq_completion(
            messages=messages,
            model='llama-3.3-70b-versatile',
            temperature=0.3,
            max_tokens=512,
        )
        answer = resp.choices[0].message.content.strip()
        return jsonify({'status': 'success', 'answer': answer})
    except Exception as e:
        tb = traceback.format_exc()
        app.logger.error('Article chat error: %s\n%s', e, tb)
        return jsonify({'status': 'error', 'message': str(e)}), 500


def save_articles_to_json(articles):
    global _current_json_path
    ensure_output_dir()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'scraped_{timestamp}.json'
    filepath = OUTPUT_DIR / filename
    data = {
        'scraped_at': datetime.now().isoformat(),
        'count': len(articles),
        'articles': articles
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    _current_json_path = filepath
    print(f"Saved articles to: {filepath}")
    return filepath


def _persist_articles():
    """Overwrite the current JSON file with the live SCRAPED_ARTICLES state."""
    if not _current_json_path:
        return
    data = {
        'scraped_at': datetime.now().isoformat(),
        'count': len(SCRAPED_ARTICLES),
        'articles': SCRAPED_ARTICLES,
    }
    with open(_current_json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load_articles(articles_list):
    global SCRAPED_ARTICLES
    SCRAPED_ARTICLES = articles_list


if __name__ == '__main__':
    # Development only — production uses: gunicorn -c gunicorn.conf.py wsgi:app
    print("\n" + "="*60)
    print("OSI News Automation - Results Viewer (dev mode)")
    print("="*60)
    print(f"\nProject root: {PROJECT_ROOT}")
    print(f"Output dir:   {OUTPUT_DIR}")
    print("\nStarting server at http://localhost:5005")
    print("Press Ctrl+C to stop\n")

    app.run(debug=False, host='0.0.0.0', port=5005, use_reloader=False,
            threaded=True)
