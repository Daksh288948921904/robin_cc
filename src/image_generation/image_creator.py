"""
OSI News — Image Prompt Builder
=================================
Builds photojournalism-quality prompts for AI image generation.
Actual generation is handled via Together.ai (see app.py).
"""

import os
import sys
from typing import Dict

from loguru import logger
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
load_dotenv()


# ---------------------------------------------------------------------------
# CATEGORY DETECTION
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    'conflict':   ['war', 'military', 'attack', 'troops', 'army', 'soldier', 'combat',
                   'missile', 'airstrike', 'bombing', 'drone', 'strike', 'armed forces'],
    'politics':   ['president', 'government', 'election', 'minister', 'parliament',
                   'congress', 'senate', 'summit', 'diplomatic', 'policy', 'vote'],
    'economy':    ['economy', 'market', 'stock', 'recession', 'inflation', 'gdp',
                   'bank', 'trade', 'finance', 'currency', 'investment'],
    'technology': ['artificial intelligence', 'ai', 'technology', 'cybersecurity',
                   'startup', 'robot', 'chip', 'smartphone', 'software', 'digital'],
    'climate':    ['climate', 'global warming', 'flood', 'wildfire', 'drought',
                   'pollution', 'renewable', 'solar', 'carbon', 'environment'],
    'disaster':   ['earthquake', 'hurricane', 'tsunami', 'explosion', 'fire',
                   'rescue', 'emergency', 'evacuation', 'disaster'],
    'health':     ['hospital', 'doctor', 'vaccine', 'disease', 'pandemic',
                   'health', 'medical', 'surgery', 'epidemic', 'virus'],
    'sports':     ['cricket', 'football', 'soccer', 'championship', 'olympics',
                   'tournament', 'athlete', 'league', 'match', 'player'],
    'housing':    ['housing', 'property', 'mortgage', 'rent', 'homeowner',
                   'house price', 'real estate', 'landlord', 'millennial', 'first-time buyer'],
    'protest':    ['protest', 'demonstration', 'rally', 'march', 'riot',
                   'activist', 'demonstrators', 'clashes'],
}


def detect_category(heading: str, story: str = "") -> str:
    text = (heading + " " + story[:300]).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return 'general'


# ---------------------------------------------------------------------------
# LOCATION MAP
# ---------------------------------------------------------------------------

_LOCATION_VISUALS = {
    'gaza':         'in Gaza, bombed buildings, rubble-strewn streets',
    'palestine':    'in Jerusalem Palestine, stone architecture, Arabic signage',
    'israel':       'in Jerusalem Israel, old city walls, stone buildings',
    'ukraine':      'in Ukraine, war-damaged buildings, Ukrainian flag visible',
    'russia':       'in Moscow Russia, Red Square Kremlin architecture',
    'china':        'in Beijing China, Chinese urban architecture',
    'iran':         'in Tehran Iran, Islamic architecture, Persian city streets',
    'afghanistan':  'in Kabul Afghanistan, mountainous backdrop',
    'india':        'in India, colourful street scene, Indian flag',
    'pakistan':     'in Pakistan, South Asian city street',
    'london':       'in London UK, Houses of Parliament or city architecture',
    'paris':        'in Paris France, Haussmann buildings, European streets',
    'washington':   'in Washington DC, Capitol building visible',
    'new york':     'in New York City, Manhattan skyline',
    'dubai':        'in Dubai, glass skyscrapers, modern gulf architecture',
    'uk':           'in the United Kingdom, British urban streetscape',
    'europe':       'in Europe, European city architecture',
    'middle east':  'in the Middle East, Islamic architecture, dusty streets',
    'africa':       'in Africa, African cityscape',
}


def _get_location_visual(article: Dict) -> str:
    loc = (article.get('location') or article.get('dateline') or '').lower()
    heading = article.get('heading', '').lower()
    story = article.get('story', '')[:300].lower()
    combined = f"{loc} {heading} {story}"
    for key, visual in _LOCATION_VISUALS.items():
        if key in combined:
            return visual + ', '
    if loc.strip():
        return f"in {loc.strip().title()}, "
    return ''


# ---------------------------------------------------------------------------
# RULE-BASED PROMPT BUILDER  (used when Groq is unavailable)
# ---------------------------------------------------------------------------

def build_image_prompt(article: Dict) -> str:
    """
    Build a scene-focused photojournalism prompt without mentioning any individuals.
    Used as fallback when Groq is rate-limited or unavailable.
    """
    heading  = article.get('heading', 'News Event')
    story    = article.get('story', '')
    content  = (heading + ' ' + story[:500]).lower()
    loc      = _get_location_visual(article)
    category = detect_category(heading, story)

    scene_map = {
        'conflict':   f"A Reuters documentary photograph of military activity {loc}"
                      f"soldiers in uniform with military vehicles in background, "
                      f"wide environmental shot, no faces visible, conflict zone journalism",
        'politics':   f"A Reuters press photograph of an empty government chamber or official building {loc}"
                      f"flags and podium visible, formal interior, "
                      f"no people, architecture-focused political journalism",
        'economy':    f"A Reuters business photograph {loc}"
                      f"stock exchange trading floor with digital price boards and tickers, "
                      f"wide-angle shot, financial district, no faces",
        'technology': f"A documentary photograph of a modern technology lab {loc}"
                      f"servers, circuit boards, screens with data visualisations, "
                      f"clean high-tech environment, no faces",
        'climate':    f"A National Geographic-style environmental photograph {loc}"
                      f"dramatic landscape showing climate impact, wide panoramic shot, "
                      f"powerful nature photography, no people",
        'disaster':   f"An AP emergency photograph {loc}"
                      f"rescue vehicles and emergency workers at disaster site, "
                      f"wide establishing shot, documentary journalism",
        'health':     f"A Reuters health photograph {loc}"
                      f"hospital corridor with medical equipment, "
                      f"clinical white environment, no faces",
        'sports':     f"A Getty Images sports photograph {loc}"
                      f"empty stadium or sports arena, pitch visible, "
                      f"professional venue photography",
        'housing':    f"A documentary photograph {loc}"
                      f"row of residential houses with For Sale signs, "
                      f"British terraced street, overcast sky, no people",
        'protest':    f"An AP news photograph of a protest {loc}"
                      f"crowd of people from behind holding banners and placards, "
                      f"wide street scene, no faces visible",
        'general':    f"A Reuters news photograph illustrating: {heading[:80]}, "
                      f"{loc}wide documentary scene, no faces",
    }

    prompt = scene_map.get(category, scene_map['general'])
    prompt += (", Canon EOS 5D Mark IV, 35mm wide lens, "
               "natural light, photorealistic, sharp focus, news agency quality")
    logger.debug(f"Rule-based prompt [{category}]: {prompt[:120]}...")
    return prompt
