"""
Groq-powered Image Prompt Generator
=====================================
Replaces the generic category-based prompt with an article-specific,
LLM-generated photojournalism prompt.

Flow:
  article (heading + sub_heading + story lead)
      → Groq LLaMA 3.3 70B
      → detailed photojournalism prompt string
      → passed into existing image generation pipeline
"""

import os
import re
from typing import Dict, Optional

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# KNOWN FIGURES — physical/contextual descriptions for common news subjects
# Used as a hint injected into the Groq user message so the model can describe
# the person accurately without hallucinating appearance.
# ---------------------------------------------------------------------------

KNOWN_FIGURES: Dict[str, str] = {
    # South Asia
    "narendra modi":   "Indian Prime Minister Narendra Modi, South Asian male in his 70s, white hair, often in a half-sleeve kurta or formal Modi jacket, authoritative presence",
    "modi":            "Indian Prime Minister Narendra Modi, South Asian male in his 70s, white hair, traditional Indian attire or formal Modi jacket",
    "amit shah":       "Indian Home Minister Amit Shah, stocky South Asian male in his 60s, formal suit or kurta, stern expression",
    "rahul gandhi":    "Indian Congress leader Rahul Gandhi, South Asian male in his 50s, white kurta-pyjama, earnest expression",
    "yogi adityanath": "Uttar Pradesh CM Yogi Adityanath, Hindu monk in saffron robes, shaved head, assertive presence",
    "arvind kejriwal": "Delhi CM Arvind Kejriwal, slim South Asian male in his 50s, half-sleeve shirt, glasses",
    "mamata banerjee": "West Bengal CM Mamata Banerjee, Indian female politician in her 60s, white sari with blue border",
    "imran khan":      "Former Pakistani PM Imran Khan, tall Pakistani male in his 70s, athletic build, traditional shalwar kameez or suit",
    "shehbaz sharif":  "Pakistani PM Shehbaz Sharif, South Asian male in his 70s, formal suit",
    "sheikh hasina":   "Bangladesh PM Sheikh Hasina, South Asian female in her 70s, saree, reading glasses",

    # Middle East
    "netanyahu":       "Israeli PM Benjamin Netanyahu, Israeli male in his 70s, dark suit and tie, serious expression",
    "benjamin netanyahu": "Israeli PM Benjamin Netanyahu, Israeli male in his 70s, dark suit and tie",
    "erdogan":         "Turkish President Recep Tayyip Erdogan, Turkish male in his 70s, formal dark suit, commanding posture",
    "recep tayyip erdogan": "Turkish President Recep Tayyip Erdogan, Turkish male in his 70s, formal dark suit",
    "mohammed bin salman": "Saudi Crown Prince Mohammed bin Salman (MBS), Saudi male in his 40s, white thobe or military uniform",
    "mbs":             "Saudi Crown Prince Mohammed bin Salman, Saudi male in his 40s, white thobe or business suit",
    "khamenei":        "Iranian Supreme Leader Ali Khamenei, elderly Iranian cleric in black turban and robes",
    "raisi":           "Iranian President Ebrahim Raisi, middle-aged Iranian cleric in white turban and black robes",

    # Russia / China / Europe
    "putin":           "Russian President Vladimir Putin, Slavic male in his 70s, formal dark suit, cold calculating expression",
    "vladimir putin":  "Russian President Vladimir Putin, Slavic male in his 70s, formal dark suit",
    "zelensky":        "Ukrainian President Volodymyr Zelensky, Slavic male in his 40s, olive military t-shirt or jacket",
    "volodymyr zelensky": "Ukrainian President Volodymyr Zelensky, Slavic male in his 40s, olive military attire",
    "xi jinping":      "Chinese President Xi Jinping, East Asian male in his 70s, dark formal suit, composed expression",
    "xi":              "Chinese President Xi Jinping, East Asian male in his 70s, dark formal Mao-collar suit",
    "macron":          "French President Emmanuel Macron, French male in his 40s, slim-fit business suit, confident posture",
    "emmanuel macron": "French President Emmanuel Macron, French male in his 40s, slim business suit",
    "olaf scholz":     "German Chancellor Olaf Scholz, German male in his 60s, conservative business suit",
    "ursula von der leyen": "EU Commission President Ursula von der Leyen, European female in her 60s, formal business attire",

    # United States
    "biden":           "US President Joe Biden, elderly white American male in his 80s, white hair, dark suit",
    "joe biden":       "US President Joe Biden, elderly white American male in his 80s, white hair, dark suit",
    "trump":           "Former US President Donald Trump, senior white American male in his 70s, signature orange tan, dark business suit and red tie",
    "donald trump":    "Former US President Donald Trump, white American male in his 70s, orange tan, dark suit, red tie",
    "kamala harris":   "US Vice President Kamala Harris, South Asian-Black American female in her 60s, formal pantsuit",
    "harris":          "US Vice President Kamala Harris, South Asian-Black American female in her 60s, formal pantsuit",
    "elon musk":       "Tech billionaire Elon Musk, South African-American male in his 50s, casual t-shirt or business casual, tall build",
    "musk":            "Tech billionaire Elon Musk, American male in his 50s, often in casual attire, tall imposing figure",
    "mark zuckerberg": "Meta CEO Mark Zuckerberg, young American male in his 40s, signature grey t-shirt or simple attire, blank expression",
    "jeff bezos":      "Amazon founder Jeff Bezos, bald American male in his 60s, athletic build, business casual",
    "jerome powell":   "Fed Chair Jerome Powell, senior white American male in his 70s, conservative grey suit",

    # UK / Commonwealth
    "rishi sunak":     "UK Prime Minister Rishi Sunak, British South Asian male in his 40s, slim formal suit, youthful appearance",
    "sunak":           "UK Prime Minister Rishi Sunak, British South Asian male in his 40s, slim formal suit",
    "keir starmer":    "UK Labour leader Keir Starmer, British male in his 60s, formal dark suit",
    "king charles":    "King Charles III, elderly British male in his 70s, formal military uniform or dark suit with royal sash",
    "charles":         "King Charles III of Britain, elderly male in formal attire or military uniform",

    # Sports
    "virat kohli":     "Indian cricket star Virat Kohli, athletic South Asian male in his 30s, cricket whites or team jersey, intense competitive expression",
    "kohli":           "Indian cricket captain Virat Kohli, athletic South Asian male in his 30s",
    "rohit sharma":    "Indian cricket captain Rohit Sharma, South Asian male in his 30s, cricket uniform",
    "ms dhoni":        "Cricket legend MS Dhoni, South Asian male in his 40s, calm expression, cricket or casual attire",
    "lionel messi":    "Football legend Lionel Messi, Argentine male in his 30s, compact athletic build, Inter Miami or Argentina jersey",
    "messi":           "Football legend Lionel Messi, Argentine male in his 30s, athletic build",
    "cristiano ronaldo": "Football star Cristiano Ronaldo, Portuguese male in his 40s, tall muscular athletic build",
    "ronaldo":         "Football star Cristiano Ronaldo, Portuguese male, tall and muscular, athletic wear",
    "neymar":          "Brazilian footballer Neymar, South American male in his 30s, slim athletic build",

    # Other prominent figures
    "pope francis":    "Pope Francis, elderly Argentine Catholic Pope in white papal vestments and white zucchetto",
    "dalai lama":      "The Dalai Lama, elderly Tibetan Buddhist monk in maroon and saffron robes, warm smile",
    "kim jong":        "North Korean leader Kim Jong-un, stocky Korean male in his 40s, dark Mao suit, authoritarian aura",
    "antonio guterres": "UN Secretary-General António Guterres, Portuguese male in his 70s, formal dark suit",
}


# ---------------------------------------------------------------------------
# GROQ SYSTEM PROMPT
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert news photo art director and AI image prompt engineer working for a major wire service.

Your task: given a news article's headline, subtitle, and story lead, write ONE detailed image generation prompt for a photorealistic news photograph.

STRICT RULES:
1. If a named person appears, describe them physically and contextually (age, appearance, attire, pose, setting).
2. Anchor every detail to THIS specific article — not a generic category photo.
3. Structure: [subject + action] [specific setting/backdrop] [atmosphere/mood] [lighting] [camera style]
4. Length: 2–3 sentences. No bullet points. No explanation. Output ONLY the prompt.
5. Begin with: "A Reuters/AP news photograph of..." or "An AFP documentary photograph of..."
6. Make it hyper-specific: a reader should immediately know which news event it depicts.
7. Do NOT describe: text overlays, watermarks, logos, blank rooms, or abstract concepts.
8. Close with: "photorealistic, Canon EOS 5D Mark IV, 35mm wide lens, natural available light, sharp focus, news agency quality"

CRITICAL COMPOSITION RULES (follow strictly):
- NEVER request a close-up or portrait shot of a face. Faces render poorly in AI image models.
- Always use WIDE or MEDIUM shots: full body, waist-up, or environmental scene.
- For people: show them from the SIDE, BACK, or at DISTANCE within their setting (e.g. "seen from behind at a podium", "walking through the courtroom", "standing at a distance before the judge's bench").
- Prefer showing the ENVIRONMENT and CONTEXT over the person's face: courtroom interior, street scene, press conference hall, military camp, etc.
- If the story has no named person, describe only the scene/location/event — no faces at all.
"""


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _get_all_groq_clients() -> list:
    """Return a list of Groq clients for all configured keys (for rotation on rate limit)."""
    from groq import Groq

    clients = []
    for var in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3",
                "GROQ_API_KEY_4", "GROQ_API_KEY_5"):
        key = os.getenv(var, "").strip()
        if key:
            clients.append((var, Groq(api_key=key)))
    if not clients:
        raise RuntimeError("No GROQ_API_KEY found in environment — set GROQ_API_KEY in .env")
    return clients


def _detect_known_figure(text: str) -> Optional[str]:
    """
    Return a visual description string if a known figure name appears in text.
    Checks longest keys first to avoid partial matches (e.g. 'mbs' vs 'mohammed bin salman').
    """
    text_lower = text.lower()
    for name in sorted(KNOWN_FIGURES, key=len, reverse=True):
        if name in text_lower:
            return KNOWN_FIGURES[name]
    return None


def _extract_lead_sentences(story: str, n: int = 2) -> str:
    """Return the first n sentences of the story, stripped of markdown."""
    clean = re.sub(r"[#*_`]", "", story).strip()
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    lead = " ".join(sentences[:n]).strip()
    return lead[:400]


# ---------------------------------------------------------------------------
# MAIN PUBLIC FUNCTION
# ---------------------------------------------------------------------------

def generate_groq_image_prompt(article: Dict) -> Optional[str]:
    """
    Use Groq LLaMA to generate a detailed, article-specific image prompt.

    Reads: heading, sub_heading (or subtitle), story, location from article dict.
    Returns the prompt string, or None if Groq is unavailable / fails.
    """
    heading = (article.get("heading") or "").strip()
    if not heading:
        logger.warning("generate_groq_image_prompt: article has no heading, skipping")
        return None

    # Support multiple field names for sub-headline
    sub_heading = (
        article.get("sub_heading")
        or article.get("subtitle")
        or article.get("subheadline")
        or article.get("sub_title")
        or ""
    ).strip()

    story_raw = article.get("story") or ""
    lead = _extract_lead_sentences(story_raw, n=2)
    location = (article.get("location") or article.get("dateline") or "").strip()

    # Check for a known figure — inject visual hint so Groq describes them accurately
    search_text = f"{heading} {sub_heading}"
    figure_hint = _detect_known_figure(search_text)

    # Build the user message
    parts = [f"HEADLINE: {heading}"]
    if sub_heading:
        parts.append(f"SUBTITLE: {sub_heading}")
    if location:
        parts.append(f"LOCATION: {location}")
    if lead:
        parts.append(f"STORY LEAD: {lead}")
    if figure_hint:
        parts.append(f"PERSON VISUAL REFERENCE (use this to describe the person accurately): {figure_hint}")

    user_message = "\n".join(parts) + "\n\nGenerate the image prompt now:"

    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        clients = _get_all_groq_clients()
    except RuntimeError as e:
        logger.warning(f"Groq not configured: {e}")
        return None

    for key_var, client in clients:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=220,
                temperature=0.35,
            )
            raw = response.choices[0].message.content.strip()
            prompt = raw.strip('"').strip("'").strip()
            logger.info(f"Groq image prompt [{key_var}] → {prompt[:130]}...")
            return prompt

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower() or "tokens" in err_str.lower():
                logger.warning(f"Groq rate limit on {key_var}, trying next key...")
                continue
            # Non-rate-limit error — log and bail immediately
            logger.warning(f"Groq image prompt failed on {key_var} ({e}), falling back to rule-based prompt")
            return None

    logger.warning("All Groq keys rate-limited for image prompt — falling back to rule-based prompt")
    return None
