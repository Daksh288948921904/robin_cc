"""
Country resolver — spaCy NER (GPE + ORG + NORP) on article title + geonamescache lookup.
No domain/source-name heuristics. Returns empty string when no location found.
"""

_nlp = None
_country_names: dict[str, str] = {}   # lowercase → display name
_city_to_country: dict[str, str] = {} # lowercase city → country display name

# Demonyms / NORPs spaCy recognises → country
_NORP_MAP: dict[str, str] = {
    "american": "USA", "americans": "USA",
    "iranian": "Iran", "iranians": "Iran",
    "russian": "Russia", "russians": "Russia",
    "chinese": "China",
    "indian": "India", "indians": "India",
    "british": "UK",
    "french": "France",
    "german": "Germany", "germans": "Germany",
    "ukrainian": "Ukraine", "ukrainians": "Ukraine",
    "israeli": "Israel", "israelis": "Israel",
    "palestinian": "Middle East", "palestinians": "Middle East",
    "saudi": "Saudi Arabia",
    "pakistani": "Pakistan", "pakistanis": "Pakistan",
    "afghan": "Afghanistan", "afghans": "Afghanistan",
    "korean": "South Korea",
    "japanese": "Japan",
    "australian": "Australia", "australians": "Australia",
    "canadian": "Canada", "canadians": "Canada",
    "brazilian": "Brazil", "brazilians": "Brazil",
    "turkish": "Turkey", "turks": "Turkey",
    "syrian": "Syria", "syrians": "Syria",
    "lebanese": "Lebanon",
    "mexican": "Mexico", "mexicans": "Mexico",
    "venezuelan": "Venezuela",
    "ethiopian": "Ethiopia", "ethiopians": "Ethiopia",
    "kenyan": "Kenya", "kenyans": "Kenya",
    "nigerian": "Nigeria", "nigerians": "Nigeria",
    "congolese": "Congo",
    "sudanese": "Sudan",
    "european": "Europe",
    "nato": "Europe",
}

# Country-like ORG/acronym aliases spaCy mis-tags as ORG
_ORG_ALIAS_MAP: dict[str, str] = {
    "uae": "UAE", "u.a.e.": "UAE",
    "eu": "Europe", "european union": "Europe",
    "un": "USA",  # UN HQ is NY — treat as global/skip? map loosely
    "who": "",    # skip — not a country
    "imf": "",
    "nato": "Europe",
    "opec": "Middle East",
    "asean": "Asia",
    "gcc": "Middle East",
    "brics": "",
}


def _load():
    global _nlp, _country_names, _city_to_country
    if _nlp is not None:
        return
    import spacy
    import geonamescache

    _nlp = spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])

    gc = geonamescache.GeonamesCache()
    countries = gc.get_countries()

    _country_names = {c["name"].lower(): c["name"] for c in countries.values()}
    for iso, c in countries.items():
        _country_names[iso.lower()] = c["name"]

    _country_names.update({
        "usa": "USA", "us": "USA", "u.s.": "USA", "u.s.a.": "USA",
        "united states": "USA", "united states of america": "USA",
        "uk": "UK", "u.k.": "UK", "britain": "UK",
        "great britain": "UK", "england": "UK", "scotland": "UK", "wales": "UK",
        "uae": "UAE", "u.a.e.": "UAE", "emirates": "UAE",
        "south korea": "South Korea", "north korea": "North Korea",
        "hong kong": "China",
        "taiwan": "Taiwan",
        "congo": "Congo", "drc": "Congo",
        "west bank": "Middle East", "gaza": "Middle East",
        "middle east": "Middle East",
    })

    for city in gc.get_cities().values():
        code = city.get("countrycode", "")
        if code in countries and city.get("population", 0) > 15000:
            _city_to_country[city["name"].lower()] = countries[code]["name"]


def resolve_country_and_city(article: dict) -> tuple[str, str]:
    """
    Extract (country, city) from the article title via NER.
    Returns ("", "") when nothing found.
    """
    heading = (article.get("heading") or "").strip()
    if not heading:
        return "", ""

    try:
        _load()
        doc = _nlp(heading)

        for ent in doc.ents:
            name = ent.text.strip().lower()

            if ent.label_ == "GPE":
                if name in _city_to_country:
                    return _city_to_country[name], ent.text.strip().title()
                if name in _country_names:
                    return _country_names[name], ""

            elif ent.label_ == "NORP":
                mapped = _NORP_MAP.get(name, "")
                if mapped:
                    return mapped, ""

            elif ent.label_ == "ORG":
                mapped = _ORG_ALIAS_MAP.get(name, None)
                if mapped is not None and mapped:
                    return mapped, ""
                if name in _country_names:
                    return _country_names[name], ""

    except Exception:
        pass

    return "", ""


def resolve_country(article: dict) -> str:
    country, _ = resolve_country_and_city(article)
    return country


def enrich_articles(articles: list[dict]) -> None:
    """Set article['region'] and article['city'] from NER. Leaves fields unchanged if nothing found."""
    for a in articles:
        country, city = resolve_country_and_city(a)
        if country:
            a["region"] = country
        if city:
            a["city"] = city
