"""
Country resolver — pure-Python regex scan of article title.
No spaCy or geonamescache required (uses them as optional enhancements).
Works on any server with only stdlib.
"""

import re

# ---------------------------------------------------------------------------
# Master lookup: token → country  (longest match wins — sorted at load time)
# ---------------------------------------------------------------------------

# Countries + common aliases
_COUNTRIES: dict[str, str] = {
    # Americas
    "united states of america": "USA", "united states": "USA",
    "u.s.a": "USA", "u.s": "USA", "usa": "USA", "us": "USA",
    "american": "USA", "americans": "USA",
    "canada": "Canada", "canadian": "Canada",
    "mexico": "Mexico", "mexican": "Mexico",
    "brazil": "Brazil", "brazilian": "Brazil",
    "argentina": "Argentina", "argentinian": "Argentina",
    "colombia": "Colombia", "colombian": "Colombia",
    "venezuela": "Venezuela", "venezuelan": "Venezuela",
    "chile": "Chile", "chilean": "Chile",
    "peru": "Peru", "peruvian": "Peru",
    "cuba": "Cuba", "cuban": "Cuba",
    "haiti": "Haiti",
    # Europe
    "united kingdom": "UK", "great britain": "UK",
    "britain": "UK", "british": "UK",
    "england": "UK", "english": "UK",
    "scotland": "UK", "scottish": "UK",
    "wales": "UK", "welsh": "UK",
    "u.k": "UK", "uk": "UK",
    "france": "France", "french": "France",
    "germany": "Germany", "german": "Germany",
    "italy": "Italy", "italian": "Italy",
    "spain": "Spain", "spanish": "Spain",
    "netherlands": "Netherlands", "dutch": "Netherlands",
    "belgium": "Belgium", "belgian": "Belgium",
    "sweden": "Sweden", "swedish": "Sweden",
    "norway": "Norway", "norwegian": "Norway",
    "denmark": "Denmark", "danish": "Denmark",
    "finland": "Finland", "finnish": "Finland",
    "poland": "Poland", "polish": "Poland",
    "ukraine": "Ukraine", "ukrainian": "Ukraine",
    "russia": "Russia", "russian": "Russia",
    "turkey": "Turkey", "turkish": "Turkey",
    "greece": "Greece", "greek": "Greece",
    "portugal": "Portugal", "portuguese": "Portugal",
    "switzerland": "Switzerland", "swiss": "Switzerland",
    "austria": "Austria", "austrian": "Austria",
    "hungary": "Hungary", "hungarian": "Hungary",
    "czechia": "Czech Republic", "czech": "Czech Republic",
    "romania": "Romania", "romanian": "Romania",
    "serbia": "Serbia", "serbian": "Serbia",
    "croatia": "Croatia", "croatian": "Croatia",
    "slovakia": "Slovakia", "slovak": "Slovakia",
    "bulgaria": "Bulgaria", "bulgarian": "Bulgaria",
    # Asia
    "china": "China", "chinese": "China",
    "india": "India", "indian": "India",
    "japan": "Japan", "japanese": "Japan",
    "south korea": "South Korea", "korean": "South Korea",
    "north korea": "North Korea",
    "pakistan": "Pakistan", "pakistani": "Pakistan",
    "afghanistan": "Afghanistan", "afghan": "Afghanistan",
    "iran": "Iran", "iranian": "Iran",
    "iraq": "Iraq", "iraqi": "Iraq",
    "syria": "Syria", "syrian": "Syria",
    "israel": "Israel", "israeli": "Israel",
    "palestine": "Palestine", "palestinian": "Palestine",
    "lebanon": "Lebanon", "lebanese": "Lebanon",
    "saudi arabia": "Saudi Arabia", "saudi": "Saudi Arabia",
    "united arab emirates": "UAE", "u.a.e": "UAE", "uae": "UAE", "emirati": "UAE",
    "qatar": "Qatar", "qatari": "Qatar",
    "kuwait": "Kuwait", "kuwaiti": "Kuwait",
    "bahrain": "Bahrain",
    "jordan": "Jordan", "jordanian": "Jordan",
    "yemen": "Yemen", "yemeni": "Yemen",
    "oman": "Oman",
    "indonesia": "Indonesia", "indonesian": "Indonesia",
    "malaysia": "Malaysia", "malaysian": "Malaysia",
    "philippines": "Philippines", "philippine": "Philippines",
    "vietnam": "Vietnam", "vietnamese": "Vietnam",
    "thailand": "Thailand", "thai": "Thailand",
    "myanmar": "Myanmar", "burmese": "Myanmar",
    "bangladesh": "Bangladesh", "bangladeshi": "Bangladesh",
    "sri lanka": "Sri Lanka", "sri lankan": "Sri Lanka",
    "nepal": "Nepal", "nepali": "Nepal",
    "singapore": "Singapore", "singaporean": "Singapore",
    "taiwan": "Taiwan", "taiwanese": "Taiwan",
    "hong kong": "Hong Kong",
    "cambodia": "Cambodia", "cambodian": "Cambodia",
    "laos": "Laos",
    "mongolia": "Mongolia", "mongolian": "Mongolia",
    "kazakhstan": "Kazakhstan",
    "uzbekistan": "Uzbekistan",
    # Middle East / general
    "middle east": "Middle East",
    "gaza": "Middle East", "west bank": "Middle East",
    # Africa
    "nigeria": "Nigeria", "nigerian": "Nigeria",
    "ethiopia": "Ethiopia", "ethiopian": "Ethiopia",
    "kenya": "Kenya", "kenyan": "Kenya",
    "south africa": "South Africa", "south african": "South Africa",
    "egypt": "Egypt", "egyptian": "Egypt",
    "ghana": "Ghana", "ghanaian": "Ghana",
    "tanzania": "Tanzania", "tanzanian": "Tanzania",
    "uganda": "Uganda", "ugandan": "Uganda",
    "sudan": "Sudan", "sudanese": "Sudan",
    "somalia": "Somalia", "somali": "Somalia",
    "mozambique": "Mozambique",
    "zimbabwe": "Zimbabwe", "zimbabwean": "Zimbabwe",
    "zambia": "Zambia", "zambian": "Zambia",
    "morocco": "Morocco", "moroccan": "Morocco",
    "algeria": "Algeria", "algerian": "Algeria",
    "tunisia": "Tunisia", "tunisian": "Tunisia",
    "libya": "Libya", "libyan": "Libya",
    "cameroon": "Cameroon", "cameroonian": "Cameroon",
    "ivory coast": "Ivory Coast",
    "senegal": "Senegal", "senegalese": "Senegal",
    "mali": "Mali", "malian": "Mali",
    "burkina faso": "Burkina Faso",
    "niger": "Niger",
    "chad": "Chad",
    "rwanda": "Rwanda", "rwandan": "Rwanda",
    "democratic republic of congo": "Congo", "drc": "Congo",
    "congo": "Congo", "congolese": "Congo",
    # Oceania
    "australia": "Australia", "australian": "Australia",
    "new zealand": "New Zealand", "new zealander": "New Zealand",
}

# Major cities → country
_CITIES: dict[str, str] = {
    "washington": "USA", "new york": "USA", "los angeles": "USA",
    "chicago": "USA", "houston": "USA", "san francisco": "USA",
    "boston": "USA", "miami": "USA", "seattle": "USA",
    "london": "UK", "manchester": "UK", "birmingham": "UK",
    "edinburgh": "UK", "glasgow": "UK",
    "paris": "France", "marseille": "France", "lyon": "France",
    "berlin": "Germany", "munich": "Germany", "frankfurt": "Germany",
    "hamburg": "Germany",
    "rome": "Italy", "milan": "Italy", "naples": "Italy",
    "madrid": "Spain", "barcelona": "Spain",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "moscow": "Russia", "st petersburg": "Russia",
    "kyiv": "Ukraine", "kiev": "Ukraine",
    "istanbul": "Turkey", "ankara": "Turkey",
    "beijing": "China", "shanghai": "China", "guangzhou": "China",
    "shenzhen": "China", "chengdu": "China",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan",
    "seoul": "South Korea", "busan": "South Korea",
    "mumbai": "India", "delhi": "India", "new delhi": "India",
    "bangalore": "India", "kolkata": "India", "chennai": "India",
    "hyderabad": "India", "pune": "India", "ahmedabad": "India",
    "karachi": "Pakistan", "lahore": "Pakistan", "islamabad": "Pakistan",
    "kabul": "Afghanistan",
    "tehran": "Iran",
    "baghdad": "Iraq",
    "damascus": "Syria",
    "jerusalem": "Israel", "tel aviv": "Israel",
    "beirut": "Lebanon",
    "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia",
    "dubai": "UAE", "abu dhabi": "UAE",
    "doha": "Qatar",
    "cairo": "Egypt", "alexandria": "Egypt",
    "lagos": "Nigeria", "abuja": "Nigeria",
    "addis ababa": "Ethiopia",
    "nairobi": "Kenya",
    "johannesburg": "South Africa", "cape town": "South Africa",
    "kinshasa": "Congo",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia",
    "auckland": "New Zealand",
    "toronto": "Canada", "montreal": "Canada", "vancouver": "Canada",
    "mexico city": "Mexico",
    "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "buenos aires": "Argentina",
    "bogota": "Colombia",
    "singapore": "Singapore",
    "bangkok": "Thailand",
    "jakarta": "Indonesia",
    "manila": "Philippines",
    "hanoi": "Vietnam", "ho chi minh": "Vietnam",
    "yangon": "Myanmar",
    "dhaka": "Bangladesh",
    "colombo": "Sri Lanka",
    "kathmandu": "Nepal",
    "taipei": "Taiwan",
    "pyongyang": "North Korea",
}

# Pre-compile sorted by length (longest match first to avoid partial matches)
_LOOKUP: list[tuple[re.Pattern, str]] = []


def _build_lookup():
    global _LOOKUP
    combined = {**_CITIES, **_COUNTRIES}  # cities first so "New York" beats "York"
    items = sorted(combined.items(), key=lambda x: -len(x[0]))
    # s? makes pattern match both singular and plural (Iranian / Iranians)
    _LOOKUP = [(re.compile(r'\b' + re.escape(k) + r's?\b', re.IGNORECASE), v)
               for k, v in items]


_build_lookup()


def resolve_country(article: dict) -> str:
    """
    Scan article title for country/city keywords. Returns country or empty string.
    """
    heading = (article.get("heading") or "").strip()
    if not heading:
        return ""
    for pattern, country in _LOOKUP:
        if pattern.search(heading):
            return country
    return ""


def enrich_articles(articles: list[dict]) -> None:
    """Set article['region'] from title scan. Leaves field unchanged if nothing found."""
    for a in articles:
        country = resolve_country(a)
        if country:
            a["region"] = country
        # city: check if the matched term was a city
        heading = (a.get("heading") or "").lower()
        for city_name, city_country in _CITIES.items():
            if re.search(r'\b' + re.escape(city_name) + r'\b', heading, re.IGNORECASE):
                a["city"] = city_name.title()
                break
