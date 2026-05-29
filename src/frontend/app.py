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

# Per-article caches (keyed by article index as string)
COVERAGES:  dict = {}   # index → list of similar article dicts
SUMMARIES:  dict = {}   # index → summary dict from multi_source_summary
TIMELINES:  dict = {}   # index → list of {time, event} dicts
SOCIALS:    dict = {}   # index → {twitter: {...}, instagram: {...}}
AI_IMAGES:       dict = {}   # index → /static/ai_images/<filename> URL (local)
AI_IMAGE_PUBLIC: dict = {}  # index → publicly accessible image URL (Pollinations or HF)
ACTIVITY:        dict = {}  # index → {headline, source_name, published_hocalwire, published_at, video_sent, video_sent_at}

# Static directory where AI-generated images are saved and served
AI_IMAGES_STATIC_DIR = Path(__file__).parent / 'static' / 'ai_images'

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

        with _scrape_lock:
            SCRAPED_ARTICLES = articles
            _scrape_state['count'] = len(articles)
            _scrape_state['error'] = None

        # Invalidate stale caches on new fetch
        COVERAGES.clear()
        SUMMARIES.clear()
        TIMELINES.clear()
        SOCIALS.clear()
        AI_IMAGES.clear()
        AI_IMAGE_PUBLIC.clear()

        filepath = save_articles_to_json(articles)
        print(f"[Scraper] Done — {len(articles)} articles from "
              f"{len({a.get('source_name') for a in articles})} sources  →  {filepath}")

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
    return render_template('index.html',
                           article_count=len(SCRAPED_ARTICLES),
                           last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


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
    global SCRAPED_ARTICLES, COVERAGES, SUMMARIES, TIMELINES, SOCIALS, AI_IMAGES, AI_IMAGE_PUBLIC
    COVERAGES.clear()
    SUMMARIES.clear()
    TIMELINES.clear()
    SOCIALS.clear()
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

    # Allow caller to pass edited title/body
    body_json = request.get_json(silent=True) or {}
    title = body_json.get('edited_title') or cached.get('title') or main_article.get('heading', '')
    body  = body_json.get('edited_body')  or cached.get('body', '')

    if not title or not body:
        return jsonify({'status': 'error', 'message': 'Summary has no title or body'}), 400

    try:
        from src.api_integrations.hocalwire_uploader import upload_to_hocalwire

        article_payload = {
            'heading':   title,
            'story':     body,
            'image_url': AI_IMAGE_PUBLIC.get(key) or main_article.get('image_url', '') or main_article.get('top_image', ''),
            'language':  'en',
            'location':  cached.get('location') or main_article.get('location', 'Hyderabad'),
        }

        success = upload_to_hocalwire(article_payload)
        if success:
            feed_id = article_payload.get('hocalwire_feed_id', '')
            _record_activity(key, main_article, published_hocalwire=True)
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
    Generate an AI image for article[idx] using SD Turbo.
    Result is cached; repeated calls return the same URL instantly.
    Returns JSON: { status, image_url }
    """
    if idx < 0 or idx >= len(SCRAPED_ARTICLES):
        return jsonify({'status': 'error', 'message': 'Article not found'}), 404

    key = str(idx)
    if key in AI_IMAGES:
        return jsonify({'status': 'success', 'image_url': AI_IMAGES[key]})

    article = SCRAPED_ARTICLES[idx]

    try:
        from src.image_generation.image_creator import (
            build_image_prompt, build_negative_prompt,
            generate_with_huggingface, generate_with_pollinations,
            get_hf_token,
        )
        import hashlib

        AI_IMAGES_STATIC_DIR.mkdir(parents=True, exist_ok=True)

        try:
            from src.image_generation.prompt_generator import generate_groq_image_prompt
            prompt = generate_groq_image_prompt(article) or build_image_prompt(article)
        except Exception:
            prompt = build_image_prompt(article)
        negative_prompt = build_negative_prompt()

        import urllib.parse, time as _time

        image_bytes = None
        public_image_url = None

        token = get_hf_token()
        if token:
            image_bytes = generate_with_huggingface(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=512,
                height=512,
                num_inference_steps=4,
                guidance_scale=0.0,
            )

        if not image_bytes:
            # Build Pollinations URL before downloading — it's publicly accessible
            seed = int(_time.time())
            clean_prompt = prompt.split(', Canon EOS')[0] if ', Canon EOS' in prompt else prompt
            encoded_prompt = urllib.parse.quote(clean_prompt)
            public_image_url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width=512&height=512&seed={seed}&nologo=true"
            )
            image_bytes = generate_with_pollinations(prompt=prompt, width=512, height=512)

        if not image_bytes:
            return jsonify({'status': 'error', 'message': 'All image sources failed'}), 500

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        title_hash = hashlib.md5(article.get('heading', '').encode()).hexdigest()[:8]
        filename = f"sdturbo_{timestamp}_{title_hash}.png"
        filepath = AI_IMAGES_STATIC_DIR / filename

        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            img.save(str(filepath), format='PNG')
        except Exception:
            filepath.write_bytes(image_bytes)

        url = f"/static/ai_images/{filename}"
        AI_IMAGES[key] = url
        if public_image_url:
            AI_IMAGE_PUBLIC[key] = public_image_url
        return jsonify({'status': 'success', 'image_url': url, 'prompt_used': prompt})

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


def _record_activity(key: str, article: dict, *, published_hocalwire=False, video_sent=False):
    """Upsert an activity entry for the given article index key."""
    entry = ACTIVITY.setdefault(key, {
        'headline':            article.get('heading', 'Untitled'),
        'source_name':         article.get('source_name', ''),
        'published_hocalwire': False,
        'published_at':        None,
        'video_sent':          False,
        'video_sent_at':       None,
    })
    now = datetime.now().isoformat()
    if published_hocalwire:
        entry['published_hocalwire'] = True
        entry['published_at']        = now
    if video_sent:
        entry['video_sent']    = True
        entry['video_sent_at'] = now


@app.route('/api/activity')
@login_required
def get_activity():
    """Return all articles that have been published or video-sent."""
    items = [
        {'idx': k, **v}
        for k, v in ACTIVITY.items()
        if v.get('published_hocalwire') or v.get('video_sent')
    ]
    items.sort(key=lambda x: x.get('published_at') or x.get('video_sent_at') or '', reverse=True)
    return jsonify({'status': 'success', 'count': len(items), 'activity': items})


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
        'published_at':        entry.get('published_at'),
        'video_sent_at':       entry.get('video_sent_at'),
    })


def save_articles_to_json(articles):
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
    print(f"Saved articles to: {filepath}")
    return filepath


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
    print("\nStarting server at http://localhost:5001")
    print("Press Ctrl+C to stop\n")

    app.run(debug=False, host='0.0.0.0', port=5001, use_reloader=False,
            threaded=True)
