"""
OSI News Automation System - Results Viewer Frontend
=====================================================
A Flask-based web interface to view scraped articles and their formatting.
"""

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

from flask import Flask, render_template, jsonify, request, send_file
from dotenv import load_dotenv
import logging
import io

# Load environment variables
load_dotenv(PROJECT_ROOT / '.env')

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')

# ── Terminal error logging ──────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
app.logger.setLevel(logging.DEBUG)

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

# ── Shared state ───────────────────────────────────────────────
SCRAPED_ARTICLES = []
OUTPUT_DIR = PROJECT_ROOT / 'output' / 'json'

# Per-article caches (keyed by article index as string)
COVERAGES: dict  = {}   # index → list of similar article dicts
SUMMARIES: dict  = {}   # index → summary dict from multi_source_summary
TIMELINES: dict  = {}   # index → list of {time, event} dicts
SOCIALS:   dict  = {}   # index → {twitter: {...}, instagram: {...}}

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
    global SCRAPED_ARTICLES, _scrape_state, COVERAGES, SUMMARIES

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

        # Invalidate stale coverage / summary / timeline caches on new fetch
        COVERAGES.clear()
        SUMMARIES.clear()
        TIMELINES.clear()
        SOCIALS.clear()

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
def index():
    return render_template('index.html',
                           article_count=len(SCRAPED_ARTICLES),
                           last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


@app.route('/api/articles')
def get_articles():
    return jsonify({
        'status': 'success',
        'count': len(SCRAPED_ARTICLES),
        'articles': SCRAPED_ARTICLES
    })


@app.route('/api/articles/<int:index>')
def get_article(index):
    if 0 <= index < len(SCRAPED_ARTICLES):
        return jsonify({'status': 'success', 'article': SCRAPED_ARTICLES[index]})
    return jsonify({'status': 'error', 'message': 'Article not found'}), 404


@app.route('/api/scrape', methods=['POST'])
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
def load_from_json():
    global SCRAPED_ARTICLES, COVERAGES, SUMMARIES, TIMELINES, SOCIALS
    COVERAGES.clear()
    SUMMARIES.clear()
    TIMELINES.clear()
    SOCIALS.clear()
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
            'image_url': main_article.get('image_url', ''),
            'language':  'en',
        }

        success = upload_to_hocalwire(article_payload)
        if success:
            feed_id = article_payload.get('hocalwire_feed_id', '')
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
    print("\n" + "="*60)
    print("OSI News Automation - Results Viewer")
    print("="*60)
    print(f"\nProject root: {PROJECT_ROOT}")
    print(f"Output dir:   {OUTPUT_DIR}")
    print("\nStarting server at http://localhost:5001")
    print("Press Ctrl+C to stop\n")

    app.run(debug=False, host='0.0.0.0', port=5001, use_reloader=False,
            threaded=True)
