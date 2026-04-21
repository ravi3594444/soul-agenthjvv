"""
leads_scraper.py — Finds local Indian businesses needing digital services.
Uses DuckDuckGo search — no API key needed.

Strategy: search for businesses in Indian cities that likely have no website,
low online presence, or poor reviews — ideal freelance targets.
"""

import hashlib
import random
import re
import time
from datetime import datetime, timezone

from duckduckgo_search import DDGS

# ── Indian cities to target ───────────────────────────────────────────────────

INDIAN_CITIES = [
    "Patna", "Delhi", "Mumbai", "Bangalore", "Chennai", "Hyderabad",
    "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Lucknow", "Surat",
    "Nagpur", "Indore", "Bhopal", "Chandigarh", "Coimbatore", "Kochi",
    "Visakhapatnam", "Agra", "Varanasi", "Ranchi", "Guwahati",
]

# ── Business categories that need digital services ────────────────────────────

BUSINESS_CATEGORIES = [
    ("restaurant", "restaurant"),
    ("mobile repair shop", "mobile_repair"),
    ("clothing store", "retail"),
    ("hardware store", "hardware"),
    ("pharmacy", "pharmacy"),
    ("travel agency", "travel"),
    ("coaching institute", "education"),
    ("salon beauty parlour", "salon"),
    ("real estate agent", "real_estate"),
    ("car mechanic garage", "automotive"),
    ("hotel guest house", "hospitality"),
    ("grocery store", "grocery"),
    ("printing shop", "printing"),
    ("electrical shop", "electrical"),
    ("furniture shop", "furniture"),
]

# ── Services we offer ─────────────────────────────────────────────────────────

SERVICES = [
    "website development",
    "WhatsApp chatbot",
    "social media management",
    "Google My Business setup",
    "online ordering system",
    "digital marketing",
]


def _dedup_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_phone(text: str) -> str:
    phones = re.findall(r"(?:\+91[-\s]?)?[6-9]\d{9}", text)
    return phones[0] if phones else ""


def _extract_rating(text: str) -> float:
    ratings = re.findall(r"(\d\.\d)\s*(?:stars?|rating|/5|out of 5)", text.lower())
    if ratings:
        try:
            return float(ratings[0])
        except:
            pass
    return 0.0


def _score_lead_heuristic(title: str, description: str) -> int:
    """Quick heuristic score before LLM scoring."""
    score = 5  # base score
    text = (title + " " + description).lower()

    # Positive signals — needs our services
    if "no website" in text: score += 3
    if "no online" in text: score += 2
    if any(w in text for w in ["poor review", "bad review", "low rating"]): score += 2
    if any(w in text for w in ["traditional", "offline", "walk-in only"]): score += 2
    if any(w in text for w in ["small business", "local shop", "family business"]): score += 1
    if "india" in text or any(city.lower() in text for city in INDIAN_CITIES): score += 1

    # Negative signals — already has digital presence
    if any(w in text for w in ["website:", "www.", ".com", "online store"]): score -= 3
    if "app available" in text: score -= 2

    return min(max(score, 1), 10)


def scrape_leads_duckduckgo() -> list[dict]:
    """Search DuckDuckGo for Indian businesses that need digital services."""
    results = []
    seen = set()

    # Pick random subset of cities and categories each run
    cities = random.sample(INDIAN_CITIES, min(5, len(INDIAN_CITIES)))
    categories = random.sample(BUSINESS_CATEGORIES, min(6, len(BUSINESS_CATEGORIES)))

    queries = []
    for city in cities[:3]:
        for cat_name, cat_type in categories[:2]:
            queries.append(f"{cat_name} in {city} contact number")
            queries.append(f"best {cat_name} {city} phone address")

    # Also search for businesses explicitly needing websites
    for city in cities[:2]:
        queries.append(f"local business {city} no website 2026")
        queries.append(f"small business {city} needs digital marketing")

    random.shuffle(queries)

    try:
        with DDGS() as ddgs:
            for query in queries[:8]:  # limit to avoid rate limiting
                try:
                    items = list(ddgs.text(query, max_results=4))
                    for item in items:
                        url = item.get("href", "")
                        title = item.get("title", "")
                        description = item.get("body", "")

                        if not title or not url:
                            continue

                        # Skip irrelevant results
                        skip_domains = ["wikipedia", "amazon", "flipkart", "zomato",
                                        "swiggy", "justdial.com/search", "youtube",
                                        "facebook.com/search", "twitter", "linkedin.com/search"]
                        if any(d in url.lower() for d in skip_domains):
                            continue

                        # Deduplicate by title
                        title_hash = _dedup_hash(title)
                        if title_hash in seen:
                            continue
                        seen.add(title_hash)

                        # Detect city and category from query
                        detected_city = next((c for c in INDIAN_CITIES if c.lower() in query.lower()), "India")
                        detected_cat = next((ct for cn, ct in categories if cn.lower() in query.lower()), "business")

                        heuristic_score = _score_lead_heuristic(title, description)

                        results.append({
                            "business_name": title[:150],
                            "url": url,
                            "city": detected_city,
                            "category": detected_cat,
                            "description": description[:400],
                            "phone": _extract_phone(description),
                            "rating": _extract_rating(description),
                            "source": "duckduckgo",
                            "scraped_at": _now(),
                            "dedup_hash": _dedup_hash(url + title),
                            "heuristic_score": heuristic_score,
                            "pitch": "",  # filled by scorer
                        })

                    time.sleep(random.uniform(1.5, 3.0))
                except Exception as e:
                    print(f"[leads] query error '{query[:40]}': {e}")
                    time.sleep(4)

    except Exception as e:
        print(f"[leads] DuckDuckGo error: {e}")

    # Sort by heuristic score, return top leads
    results.sort(key=lambda x: x["heuristic_score"], reverse=True)
    print(f"[leads] Found {len(results)} leads")
    return results[:30]  # cap at 30 per run


def scrape_all_leads() -> list[dict]:
    return scrape_leads_duckduckgo()
