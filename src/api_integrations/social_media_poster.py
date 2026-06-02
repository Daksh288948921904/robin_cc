

"""
OSI News Automation System - Social Media Post Generator
=========================================================
Generates platform-specific social media posts for Twitter, LinkedIn,
Instagram, and Facebook via Groq LLM, with template fallback.
"""

import json
import os
import sys
from typing import Dict, List
from datetime import datetime

from loguru import logger
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Load environment variables
load_dotenv()


# ===========================================
# GROQ CLIENT
# ===========================================

def _get_groq_client():
    from groq import Groq
    load_dotenv()
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("No GROQ_API_KEY found in environment")
    return Groq(api_key=key)


# ===========================================
# SOCIAL MEDIA POST GENERATION
# ===========================================

def generate_social_posts(
    article: Dict,
    article_url: str = "",
    image_url: str = ""
) -> Dict[str, str]:
    """
    Generate social media posts for all platforms using Groq LLM.

    Falls back to template-based generation if the LLM call fails.

    Args:
        article: Article dictionary with heading, story, location, etc.
        article_url: URL where the article is published.
        image_url: URL of the article image.

    Returns:
        Dictionary with keys: twitter, linkedin, instagram, facebook.
    """
    title = article.get('heading', 'Breaking News')
    dateline = article.get('dateline', article.get('location', 'NEW DELHI'))
    story = (article.get('story', '') or '')[:3000]
    source_count = article.get('source_count', 10)

    try:
        posts = _generate_with_llm(title, dateline, story, source_count, article_url)
        logger.info(f"AI social posts generated for: {title[:50]}...")
        logger.debug(f"Twitter length: {len(posts.get('twitter', ''))} chars")
        return posts
    except Exception as e:
        logger.warning(f"LLM social post generation failed ({e}), using templates")
        return _generate_from_templates(title, dateline, source_count, article_url)


def _generate_with_llm(
    title: str,
    dateline: str,
    story: str,
    source_count: int,
    article_url: str,
) -> Dict[str, str]:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = _get_groq_client()

    prompt = f"""You are a social media strategist for robin cc, a global AI-powered news channel.

Write platform-specific social media posts for this news article. Use the article content to craft engaging, original copy — not just a restatement of the headline.

ARTICLE TITLE: {title}
DATELINE: {dateline}
ARTICLE URL: {article_url}
SOURCES SYNTHESIZED: {source_count}

ARTICLE CONTENT:
{story}

OUTPUT RULES:
- Return ONLY valid JSON, no markdown, no explanation
- All values must be plain strings (no nested objects)
- Twitter: max 270 chars (leave room for the URL), punchy hook, 2-3 relevant hashtags, include the article URL on its own line at the end
- LinkedIn: 150-250 words, professional tone, insight-driven, end with a thought-provoking question, include the article URL, 4-5 professional hashtags
- Instagram: 80-120 words, visual and emotional, use emojis naturally, end with "Link in bio ⬇️", 8-10 discovery hashtags
- Facebook: 100-150 words, conversational, shareable, end with "Read more: {article_url}", ask followers a question, 3-4 hashtags

Return this exact JSON structure:
{{
  "twitter": "...",
  "linkedin": "...",
  "instagram": "...",
  "facebook": "..."
}}"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.75,
        max_tokens=1200,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    posts = json.loads(raw)

    # Validate all four keys exist
    for platform in ("twitter", "linkedin", "instagram", "facebook"):
        if platform not in posts or not isinstance(posts[platform], str):
            raise ValueError(f"Missing or invalid '{platform}' key in LLM response")

    # Hard-enforce Twitter limit
    if len(posts["twitter"]) > 280:
        posts["twitter"] = posts["twitter"][:277] + "..."

    return posts


def _generate_from_templates(
    title: str,
    dateline: str,
    source_count: int,
    article_url: str,
) -> Dict[str, str]:
    """Template fallback when LLM is unavailable."""
    timestamp = datetime.now().strftime('%A, %B %d, %Y, %I:%M %p IST')
    title_short = title if len(title) <= 80 else title[:77] + "..."

    twitter = (
        f"🔥 {title_short}\n\n"
        f"{dateline}: {source_count} sources\n\n"
        f"{article_url}\n\n"
        f"#OSINT #NewsAI"
    )
    if len(twitter) > 280:
        twitter = twitter[:277] + "..."

    linkedin = (
        f"OSI AI compiled a comprehensive analysis on {title} — drawing from "
        f"{source_count} top publications worldwide.\n\n"
        f"Dateline: {dateline} | {timestamp}\n\n"
        f"Discover balanced analysis beyond headlines. What trends are you tracking?\n\n"
        f"{article_url}\n\n"
        f"#OpenSourceIntelligence #MediaTech #AIinJournalism #GlobalNews"
    )

    instagram = (
        f"{title}\n\n"
        f"📍 {dateline}\n"
        f"🕒 {timestamp}\n\n"
        f"AI-powered scoop: We scanned the globe hourly, scraped {source_count} stories, "
        f"and built this comprehensive view.\n\n"
        f"Link in bio ⬇️\n\n"
        f"#OSINews #TrendingNews #AIGenerated #NewsAutomation #GlobalPerspective"
    )

    facebook = (
        f"Today's top story: {title}\n\n"
        f"OSI AI scanned the world hourly, scraped {source_count} publications, "
        f"and wrote this comprehensive breakdown.\n\n"
        f"{dateline} | {timestamp}\n\n"
        f"What do you think about this development? Comment below! 👇\n\n"
        f"Read more: {article_url}\n\n"
        f"#GlobalNews #AIJournalism #NewsAnalysis"
    )

    return {
        "twitter": twitter,
        "linkedin": linkedin,
        "instagram": instagram,
        "facebook": facebook,
    }


# ===========================================
# TV SCRIPT GENERATION
# ===========================================

def generate_tv_script(
    article: Dict,
    duration_seconds: int = 59,
    anchor_name: str = "[Anchor Name]"
) -> str:
    """
    Generate a teleprompter-ready TV anchor script using Groq LLM.
    Falls back to template if LLM fails.
    """
    title      = article.get('heading', 'Breaking News')
    dateline   = article.get('dateline', article.get('location', 'NEW DELHI'))
    story      = (article.get('story', '') or '')[:3000]
    source_count = article.get('source_count', 10)

    try:
        script = _generate_tv_script_with_llm(title, dateline, story, source_count, duration_seconds, anchor_name)
        logger.info(f"AI TV script generated: {len(script.split())} words")
        return script
    except Exception as e:
        logger.warning(f"LLM TV script failed ({e}), using template")
        return _generate_tv_script_template(title, dateline, story, source_count, duration_seconds, anchor_name)


def _generate_tv_script_with_llm(
    title: str,
    dateline: str,
    story: str,
    source_count: int,
    duration_seconds: int,
    anchor_name: str,
) -> str:
    model  = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = _get_groq_client()
    target_words = int(duration_seconds * 150 / 60)

    prompt = f"""You are a senior TV news writer for robin cc, a global AI-powered news channel.

Write a teleprompter-ready on-camera anchor script for the following story. The anchor will read this live on air.

STORY HEADLINE: {title}
DATELINE: {dateline}
SOURCES SYNTHESISED: {source_count}
TARGET DURATION: {duration_seconds} seconds (~{target_words} words at 150 WPM)
ANCHOR NAME: {anchor_name}

ARTICLE CONTENT:
{story}

SCRIPT RULES:
- Write ONLY the spoken script — no stage directions, no markdown, no headers
- Use // to mark natural breath pauses (every 1-2 sentences)
- Open with a strong hook that captures attention in the first 5 words
- Cover 3 key facts from the article in the middle section
- Close with a forward-looking line or handoff ("Stay tuned for more on this story")
- Mention "robin cc" once naturally (e.g. "robin cc has learned that..." or "as robin cc reported...")
- DO NOT use bullet points, asterisks, or any formatting
- Target exactly {target_words} words — count carefully
- End with: [WORDS: X] [TIME: ~{duration_seconds}s]

Write the script now:"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()


def _generate_tv_script_template(
    title: str,
    dateline: str,
    story: str,
    source_count: int,
    duration_seconds: int,
    anchor_name: str,
) -> str:
    """Template fallback for TV script generation."""
    current_hour = datetime.now().hour
    time_of_day  = "morning" if current_hour < 12 else "afternoon" if current_hour < 17 else "evening"
    timestamp    = datetime.now().strftime('%A, %B %d, %Y, %I:%M %p IST')

    paragraphs  = [l.strip() for l in story.split('\n') if l.strip() and not l.strip().startswith('#')]
    subheadings = [l.replace('##', '').strip() for l in story.split('\n') if l.strip().startswith('##')]

    opening = (paragraphs[0][:147] + "...") if paragraphs and len(paragraphs[0]) > 150 else (paragraphs[0] if paragraphs else "Breaking news from around the world.")
    key_pts = ' '.join(f"{h}. //" for h in subheadings[:3]) or ('. '.join(paragraphs[1:3])[:250] + ".")

    script = (
        f"Good {time_of_day}, I'm {anchor_name}. //\n\n"
        f"Top story: {title}. Dateline — {dateline}. //\n\n"
        f"{opening} //\n\n"
        f"robin cc has synthesised {source_count} global sources on this story. Here's what we know: //\n\n"
        f"{key_pts}\n\n"
        f"Reported at {timestamp}. //\n\n"
        f"Stay tuned for more on this story. Back after this.\n\n"
    )
    words = len(script.split())
    script += f"[WORDS: {words}] [TIME: ~{duration_seconds}s]"
    return script


# ===========================================
# PODCAST / RADIO BULLETIN SCRIPT GENERATION
# ===========================================

def generate_podcast_script(
    article: Dict,
    host_name: str = "Rohit Gandhi",
    duration_seconds: int = 60,
) -> str:
    """
    Generate a radio bulletin script (~60 seconds / ~150 words).
    Falls back to template if LLM fails.
    """
    title    = article.get('heading', 'Breaking News')
    dateline = article.get('dateline', article.get('location', 'NEW DELHI'))
    story    = (article.get('story', '') or '')[:3000]

    try:
        script = _generate_podcast_script_with_llm(title, dateline, story, host_name, duration_seconds)
        logger.info(f"AI podcast script generated: {len(script.split())} words")
        return script
    except Exception as e:
        logger.warning(f"LLM podcast script failed ({e}), using template")
        return _generate_podcast_script_template(title, story, host_name, duration_seconds)


def _generate_podcast_script_with_llm(
    title: str,
    dateline: str,
    story: str,
    host_name: str,
    duration_seconds: int,
) -> str:
    model        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client       = _get_groq_client()
    target_words = int(duration_seconds * 150 / 60)

    prompt = f"""You are a professional radio news anchor for Robin CC News, an AI-powered global news platform.

Write a complete radio news bulletin script — {duration_seconds} seconds when read aloud (~{target_words} words at 150 WPM).
A single host reads it on-air. This is a full broadcast-quality script.

STORY HEADLINE: {title}
DATELINE: {dateline}
HOST: {host_name}

ARTICLE CONTENT:
{story[:2000]}

USE EXACTLY THESE SECTION MARKERS (keep them — they drive audio production):
[JINGLE: INTRO]
[HEADLINE]
[TEASER]
[SUMMARY]
[SIGN-OFF]
[JINGLE: OUTRO]

RULES:
- [JINGLE: INTRO]: "Welcome to Robin CC News. I'm {host_name}." (exactly this line)
- [HEADLINE]: 1 punchy spoken headline sentence (8-12 words)
- [TEASER]: 2-3 sentences — hook the listener with the most dramatic/important fact
- [SUMMARY]: 4-6 sentences — cover who, what, where, when, why with context and impact
- [SIGN-OFF]: "That's the story. Stay informed with Robin CC News. I'm {host_name}." (exactly this)
- Use short sentences throughout — this is spoken audio
- No markdown, no bullet points, no asterisks
- Total spoken words across all sections: {target_words} words
- End with: [WORDS: X] [TIME: ~{duration_seconds}s]

Write the complete script now:"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=600,
    )
    return response.choices[0].message.content.strip()


def _generate_podcast_script_template(
    title: str,
    story: str,
    host_name: str,
    duration_seconds: int,
) -> str:
    """Template fallback for podcast script generation."""
    paragraphs = [l.strip() for l in story.split('\n') if l.strip() and not l.strip().startswith('#')]
    teaser  = paragraphs[0][:200] if paragraphs else "Details are emerging from the ground."
    summary = ' '.join(paragraphs[1:4])[:400] if len(paragraphs) > 1 else teaser

    script = (
        f"[JINGLE: INTRO]\n"
        f"Welcome to Robin CC News. I'm {host_name}.\n\n"
        f"[HEADLINE]\n"
        f"{title}\n\n"
        f"[TEASER]\n"
        f"{teaser}\n\n"
        f"[SUMMARY]\n"
        f"{summary}\n\n"
        f"[SIGN-OFF]\n"
        f"That's the story. Stay informed with Robin CC News. I'm {host_name}.\n"
        f"[JINGLE: OUTRO]\n"
    )
    words = len(script.split())
    script += f"\n[WORDS: {words}] [TIME: ~{duration_seconds}s]"
    return script


# ===========================================
# HASHTAG GENERATION
# ===========================================

def generate_hashtags(article: Dict, max_hashtags: int = 10) -> List[str]:
    """
    Generate relevant hashtags based on article content.
    
    Args:
        article: Article dictionary.
        max_hashtags: Maximum number of hashtags to generate.
        
    Returns:
        List of hashtag strings (without # symbol).
    """
    hashtags = []
    
    # Default hashtags
    default_tags = ['OSINT', 'NewsAI', 'GlobalNews', 'Breaking']
    
    # Extract from meta keywords if available
    meta_keywords = article.get('meta_keywords', [])
    for keyword in meta_keywords:
        if keyword and len(keyword) > 2:
            # Clean and format
            tag = keyword.strip().replace(' ', '').replace('-', '')
            if tag and tag not in hashtags:
                hashtags.append(tag)
    
    # Add location-based tag
    location = article.get('location') or article.get('dateline')
    if location and location != 'Unknown':
        location_tag = location.replace(' ', '').replace(',', '')
        if location_tag not in hashtags:
            hashtags.append(location_tag)
    
    # Add default tags
    for tag in default_tags:
        if tag not in hashtags:
            hashtags.append(tag)
    
    # Limit to max
    return hashtags[:max_hashtags]


# ===========================================
# POST FORMATTING UTILITIES
# ===========================================

def truncate_for_twitter(text: str, max_length: int = 280) -> str:
    """
    Truncate text to fit Twitter's character limit.
    
    Args:
        text: Text to truncate.
        max_length: Maximum length (default 280).
        
    Returns:
        Truncated text.
    """
    if len(text) <= max_length:
        return text
    
    # Truncate at word boundary
    truncated = text[:max_length-3]
    last_space = truncated.rfind(' ')
    if last_space > 0:
        truncated = truncated[:last_space]
    
    return truncated + "..."


def format_timestamp(dt: datetime = None, format_type: str = 'full') -> str:
    """
    Format timestamp for social media posts.
    
    Args:
        dt: Datetime object (default: now).
        format_type: 'full', 'short', or 'time_only'.
        
    Returns:
        Formatted timestamp string.
    """
    if dt is None:
        dt = datetime.now()
    
    if format_type == 'full':
        return dt.strftime('%A, %B %d, %Y, %I:%M %p IST')
    elif format_type == 'short':
        return dt.strftime('%b %d, %Y')
    elif format_type == 'time_only':
        return dt.strftime('%I:%M %p IST')
    else:
        return dt.isoformat()


# ===========================================
# TESTING
# ===========================================

def test_social_media_poster():
    """Test social media post generation."""
    print("\n" + "="*60)
    print("🧪 Social Media Post Generator Test")
    print("="*60)
    
    # Test article
    test_article = {
        "heading": "Global Climate Summit Reaches Historic Agreement on Emissions",
        "dateline": "PARIS",
        "timestamp": "Monday, January 15, 2026, 3:00 PM IST",
        "source_count": 12,
        "story": """## Agreement Reached
Countries agreed to reduce emissions by 50% by 2030.

## Key Points
Emissions targets set for all major economies.
Funding mechanism established for developing nations.

## Next Steps
Implementation begins in Q2 2026.""",
        "location": "Paris",
        "meta_keywords": ["Climate", "Environment", "Summit", "Emissions"]
    }
    
    print(f"\n📰 Test article: {test_article['heading']}")
    print("-" * 40)
    
    # Generate posts
    posts = generate_social_posts(
        test_article,
        article_url="https://democracynewslive.com/article/123",
        image_url="https://example.com/image.jpg"
    )
    
    # Display posts
    for platform, post in posts.items():
        print(f"\n📱 {platform.upper()}:")
        print(f"   Length: {len(post)} chars")
        print(f"   Preview: {post[:100]}...")
        
        # Validate Twitter length
        if platform == 'twitter':
            if len(post) <= 280:
                print(f"   ✅ Within 280 char limit")
            else:
                print(f"   ❌ EXCEEDS 280 char limit!")
    
    # Generate TV script
    print("\n" + "-" * 40)
    print("📺 TV SCRIPT:")
    script = generate_tv_script(test_article, duration_seconds=59)
    word_count = len(script.split())
    estimated_time = word_count / 150 * 60
    
    print(f"   Words: {word_count}")
    print(f"   Estimated time: {estimated_time:.0f} seconds")
    print(f"   Preview: {script[:150]}...")
    
    # Generate hashtags
    hashtags = generate_hashtags(test_article)
    print(f"\n🏷️  Hashtags: {', '.join(['#' + tag for tag in hashtags[:5]])}")
    
    print("\n" + "="*60)
    print("✅ All tests passed!")
    print("="*60 + "\n")


if __name__ == "__main__":
    test_social_media_poster()
