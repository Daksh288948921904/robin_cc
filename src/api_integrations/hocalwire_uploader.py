"""
OSI News Automation System - Hocalwire Uploader
================================================
Uploads generated articles to Hocalwire CMS via their API.
Handles authentication, geocoding, image URLs, and batch uploads.

API Reference:
  - Base URL: https://democracynews.hocalwire.in/dev/h-api/
  - Auth: 's-id' header with API key
  - Login: POST /login?email=...&password=...
  - Create Feed: POST /createFeedV2
"""

import os
import sys
import time
import random
import string
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from functools import lru_cache

from loguru import logger
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Load environment variables
load_dotenv()

# Import location extractor
try:
    from src.content_generation.location_extractor import extract_location_and_category
    LOCATION_EXTRACTOR_AVAILABLE = True
except ImportError:
    logger.warning("Location extractor not available, will use default location detection")
    LOCATION_EXTRACTOR_AVAILABLE = False

# Cache for the authenticated session token
_hocalwire_session_token: Optional[str] = None

# Upload lifecycle statuses
# "uploading"  = POST has been sent, outcome unknown (timeout risk window)
# "uploaded"   = 200 response confirmed, feed ID received
# "failed"     = confirmed failure (non-timeout exception or API rejection)
_STATUS_UPLOADING = "uploading"
_STATUS_UPLOADED  = "uploaded"
_STATUS_FAILED    = "failed"


# ===========================================
# CONFIGURATION
# ===========================================

def is_hocalwire_upload_enabled() -> bool:
    """Check if Hocalwire upload is enabled in config."""
    return os.getenv('ENABLE_HOCALWIRE_UPLOAD', 'true').lower() == 'true'


def get_api_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Get API URL and key from environment."""
    api_url = os.getenv('HOCALWIRE_API_URL')
    api_key = os.getenv('HOCALWIRE_API_KEY')
    return api_url, api_key


def get_login_base_url() -> str:
    """Derive the base URL for login from the API URL."""
    api_url = os.getenv('HOCALWIRE_API_URL', '')
    # Strip the endpoint to get base, e.g. https://democracynews.hocalwire.in/dev/h-api/
    if '/createFeedV2' in api_url:
        return api_url.replace('/createFeedV2', '')
    if '/createfeedv2' in api_url.lower():
        return api_url[:api_url.lower().rfind('/createfeedv2')]
    return api_url.rstrip('/')


def login_to_hocalwire() -> Optional[str]:
    """
    Log in to Hocalwire using email/password credentials and return a session token.

    The Hocalwire API uses an 's-id' header for authentication, and a user session ID
    is required for article submission. This function:
    1. Uses the static session ID from .env if set
    2. Attempts login if HOCALWIRE_EMAIL and HOCALWIRE_PASSWORD are configured

    Returns:
        Session token/ID string, or None if login failed.
    """
    global _hocalwire_session_token

    # Return cached token if available
    if _hocalwire_session_token:
        return _hocalwire_session_token

    # Try dynamic login if credentials are available
    email = os.getenv('HOCALWIRE_EMAIL', '').strip()
    password = os.getenv('HOCALWIRE_PASSWORD', '').strip()
    api_key = os.getenv('HOCALWIRE_API_KEY', '').strip()

    if not email or not password:
        # Fall back to static session ID if no credentials
        static_session_id = os.getenv('HOCALWIRE_USER_SESSION_ID', '').strip()
        if static_session_id:
            logger.info("No email/password set — using static session ID from .env")
            _hocalwire_session_token = static_session_id
            return _hocalwire_session_token
        logger.warning("No HOCALWIRE_EMAIL/PASSWORD or HOCALWIRE_USER_SESSION_ID set. Using empty session.")
        return None

    base_url = get_login_base_url()
    login_url = f"{base_url}/login"

    try:
        logger.info(f"Logging into Hocalwire at {login_url}...")
        response = requests.post(
            login_url,
            params={'email': email, 'password': password},
            headers={'accept': '*/*', 's-id': api_key},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()

        # Extract session token from response
        logger.debug(f"Hocalwire login response: {result}")
        token = (
            result.get('sessionId') or
            result.get('session_id') or
            result.get('token') or
            (result.get('data', {}).get('sessionId') if isinstance(result.get('data'), dict) else None)
        )

        if token:
            logger.success(f"Logged in to Hocalwire successfully")
            _hocalwire_session_token = token
            return token
        else:
            logger.warning(f"Login succeeded but no session token in response: {result}")
            return None

    except requests.exceptions.HTTPError as e:
        logger.error(f"Hocalwire login HTTP error: {e}")
        return None
    except Exception as e:
        logger.error(f"Hocalwire login failed: {e}")
        return None


# ===========================================
# GEOCODING
# ===========================================

# Initialize geocoder lazily
_geolocator = None


def get_geolocator():
    """Get or initialize the geocoder."""
    global _geolocator
    if _geolocator is None:
        try:
            from geopy.geocoders import Nominatim
            _geolocator = Nominatim(user_agent="osi-news-automation/1.0")
        except ImportError:
            logger.warning("geopy not installed. Geocoding will use defaults.")
            return None
    return _geolocator


# Default coordinates for common cities
DEFAULT_COORDINATES = {
    'new delhi': (28.6139, 77.2090),
    'delhi': (28.6139, 77.2090),
    'mumbai': (19.0760, 72.8777),
    'bangalore': (12.9716, 77.5946),
    'bengaluru': (12.9716, 77.5946),
    'chennai': (13.0827, 80.2707),
    'kolkata': (22.5726, 88.3639),
    'hyderabad': (17.393323538169245, 78.369494773754326),
    'pune': (18.5204, 73.8567),
    'ahmedabad': (23.0225, 72.5714),
    'jaipur': (26.9124, 75.7873),
    'lucknow': (26.8467, 80.9462),
    'washington': (38.9072, -77.0369),
    'washington dc': (38.9072, -77.0369),
    'london': (51.5074, -0.1278),
    'paris': (48.8566, 2.3522),
    'moscow': (55.7558, 37.6173),
    'beijing': (39.9042, 116.4074),
    'tokyo': (35.6762, 139.6503),
    'sydney': (33.8688, 151.2093),
    'dubai': (25.2048, 55.2708),
    'singapore': (1.3521, 103.8198),
    'hong kong': (22.3193, 114.1694),
    'new york': (40.7128, -74.0060),
    'los angeles': (34.0522, -118.2437),
}


@lru_cache(maxsize=200)
def get_coordinates(location: str) -> Tuple[float, float]:
    """
    Get latitude/longitude coordinates for a location.
    
    Uses cache to avoid repeated API calls. Falls back to
    defaults for common cities.
    
    Args:
        location: City or location name.
        
    Returns:
        Tuple of (latitude, longitude).
    """
    if not location:
        return (17.393323538169245, 78.369494773754326)  # Hyderabad default
    
    # Normalize location
    location_lower = location.lower().strip()
    
    # Check defaults first
    for city, coords in DEFAULT_COORDINATES.items():
        if city in location_lower:
            return coords
    
    # Try geocoding
    geolocator = get_geolocator()
    if geolocator:
        try:
            geo_result = geolocator.geocode(location, timeout=10)
            if geo_result:
                logger.debug(f"Geocoded '{location}' to ({geo_result.latitude}, {geo_result.longitude})")
                return (geo_result.latitude, geo_result.longitude)
        except Exception as e:
            logger.warning(f"Geocoding failed for '{location}': {e}")
    
    # Default to Hyderabad
    logger.debug(f"Using default coordinates for '{location}'")
    return (17.393323538169245, 78.369494773754326)


# ===========================================
# SESSION ID GENERATION
# ===========================================

def generate_session_id(prefix: str = "SCRAPE") -> str:
    """
    Generate unique session ID for upload batch.
    
    Format: PREFIX_YYYYMMDD_HHMMSS_RANDOM
    
    Args:
        prefix: Prefix string for the session ID.
        
    Returns:
        Unique session ID string.
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    random_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"{prefix}_{timestamp}_{random_suffix}"


# ===========================================
# ARTICLE FORMATTING
# ===========================================

def format_article_for_cms(story: str) -> str:
    """
    Convert markdown-formatted article to semantic HTML for CMS display.

    Produces proper visual hierarchy:
    - ## headings  → <h2> with inline CSS for size distinction
    - ### headings → <h3> italic (defensive — parser usually strips these)
    - Bullet runs  → <ul><li> blocks with spacing
    - **bold**     → <strong> tags
    - Body text    → <p> with comfortable line-height and margins

    Args:
        story: Article content in markdown format.

    Returns:
        HTML-formatted article content with semantic structure.
    """
    if not story:
        return ""

    import re

    # ── Accumulators ──
    paragraph_lines: list = []
    list_items: list = []
    html_parts: list = []

    def flush_paragraph():
        """Flush accumulated body-text lines into a single <p> tag."""
        if paragraph_lines:
            text = ' '.join(paragraph_lines)
            html_parts.append(
                f'<p style="margin-bottom:0.9em; line-height:1.7;">{text}</p>'
            )
            paragraph_lines.clear()

    def flush_list():
        """Flush accumulated bullet items into a <ul> block."""
        if list_items:
            items_html = ''.join(
                f'<li style="margin-bottom:0.35em;">{item}</li>'
                for item in list_items
            )
            html_parts.append(
                f'<ul style="padding-left:1.4em; margin:0.7em 0; '
                f'line-height:1.7;">{items_html}</ul>'
            )
            list_items.clear()

    # ── Process each line ──
    lines = story.split('\n')

    for line in lines:
        # Step 1: Convert **bold** → <strong> BEFORE any other processing
        line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
        stripped = line.strip()

        if not stripped:
            # Empty line: flush both accumulators
            flush_list()
            flush_paragraph()

        elif stripped.startswith('## ') and not stripped.startswith('### '):
            # ## section header → <h2> with size distinction from body text
            flush_list()
            flush_paragraph()
            heading_text = stripped[3:].strip()
            html_parts.append(
                f'<h2 style="font-size:1.25em; font-weight:600; '
                f'margin-top:1.5em; margin-bottom:0.5em; '
                f'color:inherit;">{heading_text}</h2>'
            )

        elif stripped.startswith('### '):
            # ### subheadline → <h3> italic (defensive; parser normally strips this)
            flush_list()
            flush_paragraph()
            sub_text = stripped[4:].strip()
            html_parts.append(
                f'<h3 style="font-size:1.1em; font-weight:500; '
                f'font-style:italic; margin-top:0.3em; '
                f'margin-bottom:1em; color:inherit;">{sub_text}</h3>'
            )

        elif stripped.startswith('- ') or stripped.startswith('* '):
            # Bullet line: flush any open paragraph, add to list buffer
            flush_paragraph()
            bullet_text = stripped[2:].strip()
            list_items.append(bullet_text)

        else:
            # Body text: flush any open list, add to paragraph buffer
            flush_list()
            paragraph_lines.append(stripped)

    # ── Final flush ──
    flush_list()
    flush_paragraph()

    return '\n\n'.join(html_parts)


# ===========================================
# SINGLE ARTICLE UPLOAD
# ===========================================

def upload_to_hocalwire(
    article: Dict,
    image_url: str = "",
    session_id: str = None,
    dry_run: bool = False
) -> bool:
    """
    Upload a single article to Hocalwire API.
    
    Args:
        article: Article dictionary with 'heading' and 'story'.
        image_url: URL of the article's image.
        session_id: Unique session ID for this batch.
        dry_run: If True, don't actually upload (for testing).
        
    Returns:
        True if upload successful, False otherwise.
        
    Example:
        >>> success = upload_to_hocalwire(article, image_url, session_id)
        >>> if success:
        ...     print("Uploaded!")
    """
    # Check if enabled
    if not is_hocalwire_upload_enabled():
        logger.info("Hocalwire upload is disabled in config")
        return False
    
    # Get API credentials
    api_url, api_key = get_api_credentials()
    
    if not api_url or not api_key:
        logger.error("Hocalwire API credentials not configured in .env")
        logger.error("Set HOCALWIRE_API_URL and HOCALWIRE_API_KEY")
        return False
    
    # Generate session ID if not provided
    if not session_id:
        session_id = generate_session_id()
    
    # Validations
    heading = article.get('heading', '')
    sub_heading = article.get('sub_heading', '')
    story = article.get('story', '')
    
    if not heading or not story:
        logger.error("Article missing heading or story")
        return False
    
    # Convert markdown to HTML for better CMS display
    formatted_story = format_article_for_cms(story)
    
    # Extract intelligent location and category from article content
    if LOCATION_EXTRACTOR_AVAILABLE:
        try:
            # Use LLM to extract primary location and map to category
            location, category_id, category_name = extract_location_and_category(article)
            logger.info(f"Extracted location: {location}, Category: {category_name} (ID: {category_id})")
        except Exception as e:
            logger.warning(f"Location extraction failed: {e}, using fallback")
            # Fallback to article-provided location
            location = article.get('location') or article.get('dateline', 'India')
            category_id = os.getenv('HOCALWIRE_CATEGORY_ID', '770')
            category_name = 'General'
    else:
        # Fallback: use article-provided location
        location = article.get('location') or article.get('dateline', 'Hyderabad')
        category_id = os.getenv('HOCALWIRE_CATEGORY_ID', '770')
        category_name = 'General'
    
    # Clean up location format
    if location.isupper():
        location = location.title()
    
    # Get coordinates for the map
    lat, lon = get_coordinates(location)
    
    # Default image URL if not provided
    if not image_url:
        image_url = article.get('image_url', '')
    if not image_url:
        image_url = "https://www.hocalwire.com/images/logo.png"  # Fallback
    
    # Get user session ID (from login or static .env value)
    user_session_id = login_to_hocalwire() or os.getenv('HOCALWIRE_USER_SESSION_ID', '')

    # Get current date/time for published date
    published_date = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')

    payload = {
        "heading": heading,
        "sub_heading": sub_heading,  # Add subheading mapping here
        "mediaIds": image_url,
        "story": formatted_story,  # Use HTML-formatted content
        "categoryId": category_id,  # Use dynamically extracted category
        "location": location,
        "state": os.getenv('HOCALWIRE_DEFAULT_STATE', 'SUBMITTED'),
        "point_long": lon,
        "point_lat": lat,
        "language": article.get('language', 'en'),
        "sessionId": user_session_id,  # User's session ID from login/registration
        "news_type": os.getenv('HOCALWIRE_NEWS_TYPE', 'CITIZEN_FEED'),
        "publishedDate": published_date  # Add publish date to fix "Date: null" issue
    }
    
    # Dry run mode - just log and return
    if dry_run:
        logger.info(f"[DRY RUN] Would upload: {heading[:50]}...")
        logger.debug(f"[DRY RUN] Payload: {payload}")
        return True

    # ── Pre-flight: mark as uploading before the request fires ──────────────
    # This ensures that if the request times out, a subsequent retry
    # can detect the unknown-outcome state and skip re-posting to prevent
    # duplicate articles. Written outside the try block so it persists
    # even when an exception is raised.
    if article.get('_id'):
        try:
            from src.database.mongo_client import get_client as _get_client
            _db_pre = _get_client()
            if _db_pre._ensure_connected():
                _db_pre.articles.update_one(
                    {"_id": article['_id']},
                    {"$set": {
                        "upload_status": _STATUS_UPLOADING,
                        "upload_attempted_at": datetime.utcnow(),
                    }}
                )
                logger.debug(f"Marked article as '{_STATUS_UPLOADING}' before POST")
        except Exception as _pre_err:
            # Non-fatal — if this write fails, proceed with upload anyway
            # The retry guard will simply not fire for this article
            logger.warning(f"Pre-flight status write failed (non-fatal): {_pre_err}")

    try:
        # Set up headers (Hocalwire uses 's-id' header, not Authorization)
        headers = {
            "Content-Type": "application/json",
            "s-id": api_key
        }
        
        logger.info(f"📤 Uploading to Hocalwire: {heading[:50]}...")
        
        # Make API request
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=90
        )
        
        # Check for errors
        response.raise_for_status()
        
        # Parse response
        result = response.json()
        logger.debug(f"Hocalwire upload response ({response.status_code}): {result}")

        if result.get('status') == 'success' or result.get('feedId'):
            feed_id = str(result.get('feedId', 'unknown'))
            logger.success(f"✅ Uploaded successfully - Feed ID: {feed_id}")

            # Update article dict with upload info
            article['hocalwire_feed_id'] = feed_id
            article['upload_status'] = 'uploaded'
            article['uploaded_at'] = datetime.utcnow().isoformat()

            # Update in database — try by _id first, then by heading
            try:
                from src.database.mongo_client import get_client
                db = get_client()
                updated = False

                if article.get('_id'):
                    updated = db.update_upload_status(
                        article['_id'],
                        'uploaded',
                        hocalwire_feed_id=feed_id
                    )

                if not updated:
                    # Fallback: update by heading match (for articles whose save returned None)
                    heading = article.get('heading', '')
                    if heading and db._ensure_connected():
                        db.articles.update_one(
                            {'heading': heading, 'upload_status': {'$ne': 'uploaded'}},
                            {'$set': {
                                'upload_status': 'uploaded',
                                'hocalwire_feed_id': feed_id,
                                'uploaded_at': datetime.utcnow()
                            }}
                        )
                        logger.debug(f"Updated DB by heading fallback for: {heading[:50]}")

            except Exception as db_err:
                logger.warning(f"DB status update failed (upload still succeeded): {db_err}")

            return True
        else:
            error_msg = result.get('message', str(result))
            logger.error(f"Upload rejected by API: {error_msg}")
            article['upload_status'] = 'failed'
            article['upload_error'] = error_msg
            
            # Update in database with failure reason
            if '_id' in article:
                from src.database.mongo_client import get_client
                db = get_client()
                db.update_upload_status(
                    article['_id'],
                    'failed',
                    failure_reason=f"API rejected: {error_msg}",
                    increment_retry=True
                )
            
            return False
            
    except requests.exceptions.Timeout:
        error_msg = "Hocalwire API request timed out"
        logger.error(error_msg)
        # Keep status as 'uploading' — do NOT overwrite to 'failed'.
        # The pre-flight write already set 'uploading' in MongoDB.
        # Leaving it intact allows the pre-retry guard to detect this
        # ambiguous state and skip re-posting to prevent duplicates.
        article['upload_status'] = _STATUS_UPLOADING
        
        return False
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"Hocalwire API HTTP error: {e}"
        logger.error(error_msg)
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text[:200]}")
        article['upload_status'] = 'failed'
        
        # Update in database
        if '_id' in article:
            from src.database.mongo_client import get_client
            db = get_client()
            db.update_upload_status(
                article['_id'],
                'failed',
                failure_reason=f"HTTP error: {str(e)[:200]}",
                increment_retry=True
            )
        
        return False
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Hocalwire API request error: {e}"
        logger.error(error_msg)
        article['upload_status'] = 'failed'
        
        # Update in database
        if '_id' in article:
            from src.database.mongo_client import get_client
            db = get_client()
            db.update_upload_status(
                article['_id'],
                'failed',
                failure_reason=f"Network error: {str(e)[:200]}",
                increment_retry=True
            )
        
        return False
        
    except Exception as e:
        error_msg = f"Upload failed with unexpected error: {e}"
        logger.error(error_msg)
        article['upload_status'] = 'failed'
        
        # Update in database
        if '_id' in article:
            from src.database.mongo_client import get_client
            db = get_client()
            db.update_upload_status(
                article['_id'],
                'failed',
                failure_reason=f"Unexpected error: {str(e)[:200]}",
                increment_retry=True
            )
        
        return False


# ===========================================
# BATCH UPLOAD
# ===========================================

def upload_batch_to_hocalwire(
    articles: List[Dict],
    image_urls: Dict[int, str] = None,
    max_retries: int = 3,
    delay_between_uploads: float = 1.0,
    dry_run: bool = False
) -> Dict:
    """
    Upload multiple articles to Hocalwire with retry logic.
    
    Args:
        articles: List of article dictionaries.
        image_urls: Dict mapping article indices to image URLs.
        max_retries: Number of retry attempts for failed uploads.
        delay_between_uploads: Seconds to wait between uploads.
        dry_run: If True, don't actually upload.
        
    Returns:
        Dictionary with upload statistics.
        
    Example:
        >>> stats = upload_batch_to_hocalwire(articles, max_retries=3)
        >>> print(f"Uploaded {stats['successful']}/{stats['total']}")
    """
    if not articles:
        logger.warning("No articles to upload")
        return {'total': 0, 'successful': 0, 'failed': 0, 'retried': 0}
    
    # Check if enabled
    if not is_hocalwire_upload_enabled():
        logger.info("Hocalwire upload is disabled")
        return {'total': len(articles), 'successful': 0, 'failed': len(articles), 'retried': 0, 'disabled': True}
    
    # Generate single session ID for the batch
    session_id = generate_session_id()
    logger.info(f"Starting batch upload - Session: {session_id}")
    
    # Initialize stats
    stats = {
        'total': len(articles),
        'successful': 0,
        'failed': 0,
        'retried': 0,
        'session_id': session_id,
        'started_at': datetime.utcnow().isoformat(),
        'feed_ids': []
    }
    
    image_urls = image_urls or {}
    
    for i, article in enumerate(articles):
        logger.info(f"Uploading article {i + 1}/{len(articles)}...")
        
        image_url = image_urls.get(i, "")
        
        # Attempt upload with retries
        for attempt in range(max_retries):

            # ── Pre-retry guard ─────────────────────────────────────────────────
            # On any retry (attempt > 0), check whether the previous attempt
            # left the article in 'uploading' state — meaning the POST was sent
            # but timed out before we received confirmation.
            # In that case, skip the retry to prevent a duplicate upload.
            # The article remains in 'uploading' state for manual review.
            if attempt > 0 and not dry_run and article.get('_id'):
                try:
                    from src.database.mongo_client import get_client as _get_client
                    _db_check = _get_client()
                    if _db_check._ensure_connected():
                        current = _db_check.articles.find_one(
                            {"_id": article['_id']},
                            {"upload_status": 1}
                        )
                        if current and current.get('upload_status') == _STATUS_UPLOADING:
                            logger.warning(
                                f"⚠️  Article '{article.get('heading', '')[:50]}' is in "
                                f"'{_STATUS_UPLOADING}' state — previous attempt timed out "
                                f"but may have succeeded on server. Skipping retry to "
                                f"prevent duplicate. Review manually in Hocalwire dashboard."
                            )
                            stats['failed'] += 1
                            break
                except Exception as _guard_err:
                    # Non-fatal — if the guard check fails, proceed with retry
                    # Better to risk a duplicate than silently drop an article
                    logger.warning(f"Pre-retry guard check failed (will attempt retry): {_guard_err}")

            success = upload_to_hocalwire(article, image_url, session_id, dry_run)

            if success:
                stats['successful'] += 1
                if 'hocalwire_feed_id' in article:
                    stats['feed_ids'].append(article['hocalwire_feed_id'])
                break
            else:
                if attempt < max_retries - 1:
                    wait_time = delay_between_uploads * (2 ** attempt)  # Exponential backoff
                    logger.info(f"⏳ Retrying in {wait_time:.1f}s (Attempt {attempt + 2}/{max_retries})")
                    time.sleep(wait_time)
                    stats['retried'] += 1
                else:
                    stats['failed'] += 1
                    logger.warning(f"❌ Failed to upload article after {max_retries} attempts")
        
        # Rate limiting between uploads
        if i < len(articles) - 1:
            time.sleep(delay_between_uploads)
    
    stats['completed_at'] = datetime.utcnow().isoformat()
    
    # Log summary
    logger.info("="*50)
    logger.info(f"📊 Batch Upload Complete")
    logger.info(f"   Session: {session_id}")
    logger.info(f"   Total: {stats['total']}")
    logger.info(f"   Successful: {stats['successful']}")
    logger.info(f"   Failed: {stats['failed']}")
    logger.info(f"   Retries: {stats['retried']}")
    logger.info("="*50)
    
    return stats


# ===========================================
# UPLOAD STATUS TRACKING
# ===========================================

def get_upload_status(article: Dict) -> str:
    """Get the upload status of an article."""
    return article.get('upload_status', 'pending')


def mark_article_uploaded(article: Dict, feed_id: str) -> None:
    """Mark an article as successfully uploaded."""
    article['hocalwire_feed_id'] = feed_id
    article['upload_status'] = 'uploaded'
    article['uploaded_at'] = datetime.utcnow().isoformat()


def mark_article_failed(article: Dict, error: str) -> None:
    """Mark an article upload as failed."""
    article['upload_status'] = 'failed'
    article['upload_error'] = error
    article['failed_at'] = datetime.utcnow().isoformat()


# ===========================================
# TESTING
# ===========================================

def test_hocalwire_uploader():
    """Test Hocalwire uploader functionality."""
    print("\n" + "="*60)
    print("🧪 Hocalwire Uploader Test")
    print("="*60)
    
    # Check if enabled
    enabled = is_hocalwire_upload_enabled()
    print(f"\n📌 Hocalwire upload enabled: {enabled}")
    
    # Check credentials
    api_url, api_key = get_api_credentials()
    print(f"📌 API URL configured: {'Yes' if api_url else 'No'}")
    print(f"📌 API Key configured: {'Yes' if api_key else 'No'}")
    
    # Generate session ID
    session_id = generate_session_id()
    print(f"\n🔑 Session ID: {session_id}")
    
    # Test geocoding
    print("\n📍 Testing Geocoding...")
    test_locations = ['Mumbai', 'New Delhi', 'London', 'Unknown City']
    for loc in test_locations:
        lat, lon = get_coordinates(loc)
        print(f"   {loc}: ({lat:.4f}, {lon:.4f})")
    
    # Test article
    test_article = {
        "heading": "Test Article for Hocalwire Integration",
        "story": "This is a test article to verify the Hocalwire API integration is working correctly. "
                "The article contains sample content that demonstrates the upload functionality.",
        "location": "Mumbai",
        "language": "en"
    }
    
    print(f"\n📰 Test article: {test_article['heading']}")
    print("-" * 40)
    
    if not api_url or not api_key:
        print("\n⚠️ API credentials not configured")
        print("   Set HOCALWIRE_API_URL and HOCALWIRE_API_KEY in .env")
        print("   Testing with dry run mode...\n")
        
        success = upload_to_hocalwire(test_article, "", session_id, dry_run=True)
        if success:
            print("✅ Dry run successful - upload logic is correct")
    else:
        print("\n🚀 Attempting actual upload...")
        success = upload_to_hocalwire(test_article, "", session_id)
        
        if success:
            print(f"\n✅ Upload successful!")
            print(f"   Feed ID: {test_article.get('hocalwire_feed_id', 'N/A')}")
        else:
            print(f"\n⚠️ Upload failed")
            print(f"   Status: {test_article.get('upload_status', 'unknown')}")
    
    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    test_hocalwire_uploader()
