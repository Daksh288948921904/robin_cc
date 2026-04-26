"""
Location and Category Extractor for Articles
============================================
Uses LLM to intelligently extract the primary location from article content
and maps locations to appropriate categories.
"""

import os
import re
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# Try to import Groq
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq library not available")


# ===========================================
# LOCATION TO CATEGORY MAPPING
# ===========================================

# Simplified category mappings for English categories only
# Using generic categories that display in English on Hocalwire
LOCATION_CATEGORY_MAP = {
    # All International locations → default category 770
    'united states': ('770', 'World'),
    'usa': ('770', 'World'),
    'washington': ('770', 'World'),
    'new york': ('770', 'World'),
    'dubai': ('770', 'World'),
    'uae': ('770', 'World'),

    'united kingdom': ('770', 'World'),
    'uk': ('770', 'World'),
    'london': ('770', 'World'),
    'heathrow': ('770', 'World'),

    'china': ('770', 'World'),
    'beijing': ('770', 'World'),

    'russia': ('770', 'World'),
    'moscow': ('770', 'World'),

    'iran': ('770', 'World'),
    'tehran': ('770', 'World'),

    'yemen': ('770', 'World'),
    'syria': ('770', 'World'),
    'iraq': ('770', 'World'),
    'afghanistan': ('770', 'World'),

    'pakistan': ('770', 'World'),
    'islamabad': ('770', 'World'),
    'karachi': ('770', 'World'),

    'france': ('770', 'World'),
    'paris': ('770', 'World'),

    'germany': ('770', 'World'),
    'berlin': ('770', 'World'),

    'japan': ('770', 'World'),
    'tokyo': ('770', 'World'),

    'australia': ('770', 'World'),
    'sydney': ('770', 'World'),

    'greenland': ('770', 'World'),
    'denmark': ('770', 'World'),

    # Indian locations → default category 770
    'mumbai': ('770', 'India'),
    'pune': ('770', 'India'),
    'delhi': ('770', 'India'),
    'new delhi': ('770', 'India'),
    'bangalore': ('770', 'India'),
    'bengaluru': ('770', 'India'),
    'chennai': ('770', 'India'),
    'kolkata': ('770', 'India'),
    'hyderabad': ('770', 'India'),
    'ahmedabad': ('770', 'India'),
    'jaipur': ('770', 'India'),
    'lucknow': ('770', 'India'),
    'india': ('770', 'India'),
}

# Default category for unrecognized locations
DEFAULT_CATEGORY = ('770', 'General')


def get_groq_client():
    """Get Groq client for LLM extraction."""
    if not GROQ_AVAILABLE:
        return None
    
    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        return None
    
    try:
        return Groq(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize Groq client: {e}")
        return None


def extract_location_from_content(article_content: str, article_heading: str) -> str:
    """
    Extract the primary location from article content using LLM.
    
    Args:
        article_content: Full article text
        article_heading: Article headline
        
    Returns:
        Extracted location name
    """
    client = get_groq_client()
    
    if not client:
        # Fallback: try to extract from content using regex
        return extract_location_fallback(article_content, article_heading)
    
    # Build prompt for LLM
    prompt = f"""You are a location extraction expert. Analyze this news article and identify the PRIMARY LOCATION where the events are taking place.

Article Headline: {article_heading}

Article Content (first 1000 chars): {article_content[:1000]}

Instructions:
1. Identify the main location where the news events are happening
2. Return ONLY the location name (city or country)
3. If multiple locations, return the MOST IMPORTANT one
4. Do NOT return the dateline location if it differs from where events occur
5. For international news, return the country name
6. For India news, return the city/state name

Examples:
- Article about Iran protests → Return: Iran
- Article about Mumbai floods → Return: Mumbai
- Article about US-China trade → Return: United States (or China, whichever is primary)
- Article about Greenland acquisition → Return: Greenland

Respond with ONLY the location name, nothing else."""

    try:
        response = client.chat.completions.create(
            model=os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile'),
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=0.1,  # Low temperature for consistent extraction
            max_tokens=50
        )
        
        location = response.choices[0].message.content.strip()
        
        # Clean up the response
        location = location.replace('"', '').replace("'", '').strip()
        
        logger.info(f"LLM extracted location: {location}")
        return location
        
    except Exception as e:
        logger.error(f"LLM location extraction failed: {e}")
        return extract_location_fallback(article_content, article_heading)


def extract_location_fallback(article_content: str, article_heading: str) -> str:
    """
    Fallback method to extract location using regex patterns.
    
    Args:
        article_content: Article text
        article_heading: Article headline
        
    Returns:
        Extracted location
    """
    # Combine heading and content for analysis
    text = f"{article_heading} {article_content[:500]}".lower()
    
    # Check for country/city mentions
    for location_key in LOCATION_CATEGORY_MAP.keys():
        if location_key in text:
            # Count occurrences
            count = text.count(location_key)
            if count >= 2:  # Location mentioned multiple times likely indicates primary location
                return location_key.title()
    
    # Try to extract from dateline pattern
    dateline_pattern = r'^([A-Z\s]+),\s+\w+\s+\d+'
    match = re.search(dateline_pattern, article_content)
    if match:
        return match.group(1).title()
    
    # Default
    return "India"


def get_category_for_location(location: str) -> Tuple[str, str]:
    """
    Get category ID and name for a location.
    
    Args:
        location: Location name
        
    Returns:
        Tuple of (category_id, category_name)
    """
    if not location:
        return DEFAULT_CATEGORY
    
    location_lower = location.lower().strip()
    
    # Direct match
    if location_lower in LOCATION_CATEGORY_MAP:
        return LOCATION_CATEGORY_MAP[location_lower]
    
    # Partial match
    for loc_key, (cat_id, cat_name) in LOCATION_CATEGORY_MAP.items():
        if loc_key in location_lower or location_lower in loc_key:
            return (cat_id, cat_name)
    
    # Default
    return DEFAULT_CATEGORY


def extract_location_and_category(article: Dict) -> Tuple[str, str, str]:
    """
    Extract primary location and corresponding category from article.
    
    Args:
        article: Article dictionary with 'heading' and 'story'
        
    Returns:
        Tuple of (location, category_id, category_name)
    """
    heading = article.get('heading', '')
    story = article.get('story', '')
    
    # Extract location using LLM
    location = extract_location_from_content(story, heading)
    
    # Get category for location
    category_id, category_name = get_category_for_location(location)
    
    logger.info(f"Extracted: Location='{location}', Category='{category_name}' (ID: {category_id})")
    
    return location, category_id, category_name


# ===========================================
# TESTING
# ===========================================

if __name__ == "__main__":
    print("\n" + "="*80)
    print("LOCATION AND CATEGORY EXTRACTION TEST")
    print("="*80)
    
    test_article = {
        "heading": "Leaked Reports Reveal Human Rights Abuses",
        "story": """NEW DELHI, January 16 – A disturbing trend of human rights abuses has emerged from leaked reports and testimonies, shedding light on the brutal treatment of individuals in Iran and Yemen. In Iran, leaked photos obtained by BBC News reveal the faces of hundreds killed during the country's violent crackdown on anti-government protests. Meanwhile, in Yemen, a former detainee has come forward to share his harrowing experience of torture in a UAE-run prison."""
    }
    
    location, cat_id, cat_name = extract_location_and_category(test_article)
    
    print(f"\n📍 Primary Location: {location}")
    print(f"📂 Category: {cat_name} (ID: {cat_id})")
    print("\n" + "="*80)
