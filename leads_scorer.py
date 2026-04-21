"""
leads_scorer.py — Scores local business leads and generates Hinglish pitches.
"""

import re
import llm_client


PITCH_SYSTEM = """You are an expert freelance sales assistant for Indian markets.
Generate short, friendly Hinglish (Hindi+English mix) WhatsApp pitch messages 
for small local businesses. Be casual, specific, and value-focused.
Maximum 4 lines. No formal language. Sound like a helpful local friend."""


def score_lead(lead: dict) -> dict:
    """Score a lead 1-10 and generate a pitch. Returns {score, pitch}."""
    score = 4  # base
    parts = []

    name = lead.get("business_name", "")
    desc = (lead.get("description", "") or "").lower()
    city = lead.get("city", "")
    category = lead.get("category", "")
    phone = lead.get("phone", "")
    rating = lead.get("rating", 0.0) or 0.0

    # Has phone number — can actually contact them
    if phone:
        score += 2; parts.append("has phone +2")

    # Low/no rating — needs reputation help
    if 0 < rating < 3.5:
        score += 2; parts.append("low rating +2")
    elif rating == 0:
        score += 1; parts.append("no rating +1")

    # No website signals
    if any(w in desc for w in ["no website", "no online", "offline only"]):
        score += 2; parts.append("no website +2")

    # City tier (Tier 1 cities = more businesses)
    tier1 = ["Delhi", "Mumbai", "Bangalore", "Chennai", "Hyderabad", "Kolkata", "Pune"]
    if city in tier1:
        score += 1; parts.append("tier1 city +1")

    # Category value
    high_value = ["real_estate", "travel", "hospitality", "education"]
    if category in high_value:
        score += 1; parts.append("high-value category +1")

    # Generate pitch with LLM
    pitch = _generate_pitch(name, city, category, desc, rating)

    return {
        "score": min(score, 10),
        "breakdown": ", ".join(parts),
        "pitch": pitch,
    }


def _generate_pitch(name: str, city: str, category: str, desc: str, rating: float) -> str:
    """Generate a Hinglish pitch for the business."""
    if not llm_client.is_available():
        return _fallback_pitch(name, city, category)

    try:
        services = _suggest_services(category, rating)
        prompt = f"""Write a short WhatsApp message to pitch digital services to this business.

Business: {name}
City: {city}
Type: {category}
About: {desc[:200]}
Rating: {rating}/5
Suggested services: {services}

Write in Hinglish (mix of Hindi and English). Be friendly and specific.
4 lines max. Start with "Namaste" or "Hello". Mention one specific service."""

        pitch = llm_client.call_llm(
            prompt,
            system=PITCH_SYSTEM,
            max_tokens=150,
            temperature=0.7,
        ).strip()
        return pitch
    except Exception as e:
        return _fallback_pitch(name, city, category)


def _suggest_services(category: str, rating: float) -> str:
    mapping = {
        "restaurant": "WhatsApp ordering system, Google My Business, online menu",
        "retail": "WhatsApp catalog, Instagram shop, website",
        "salon": "online booking system, Instagram presence, Google reviews",
        "real_estate": "property listing website, lead generation",
        "education": "website with online enrollment, WhatsApp broadcast",
        "travel": "booking website, Google My Business",
        "automotive": "online appointment booking, Google reviews",
        "pharmacy": "WhatsApp ordering, home delivery system",
        "hospitality": "booking website, TripAdvisor setup, Google My Business",
    }
    service = mapping.get(category, "website, WhatsApp chatbot, social media")
    if rating and rating < 3.5:
        service += ", reputation management"
    return service


def _fallback_pitch(name: str, city: str, category: str) -> str:
    return (
        f"Namaste! Main ek freelance developer hoon {city} se. "
        f"Maine dekha ki {name} ki online presence aur improve ho sakti hai. "
        f"Website, WhatsApp chatbot, ya social media — affordable rates mein bana sakta hoon. "
        f"Interested hain? Free consultation ke liye reply karein! 🙏"
    )
