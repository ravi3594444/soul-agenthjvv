"""
hackathon_scorer.py — Scores hackathons and generates summaries.
"""

import re
import llm_client


def score_hackathon(hack: dict) -> dict:
    """Score a hackathon 0-20. Returns {score, breakdown}."""
    score = 0
    parts = []

    # Prize money
    prize = hack.get("prize_usd", 0) or 0
    if prize >= 10000:
        score += 6; parts.append("prize≥$10k +6")
    elif prize >= 5000:
        score += 5; parts.append("prize≥$5k +5")
    elif prize >= 1000:
        score += 4; parts.append("prize≥$1k +4")
    elif prize >= 500:
        score += 3; parts.append("prize≥$500 +3")
    elif prize > 0:
        score += 2; parts.append("prize>$0 +2")

    # Free to enter
    if hack.get("is_free", True):
        score += 3; parts.append("free entry +3")

    # Source quality
    source = hack.get("source", "")
    if source == "devpost":
        score += 3; parts.append("devpost +3")
    elif source == "unstop":
        score += 2; parts.append("unstop +2")
    else:
        score += 1; parts.append("other source +1")

    # Has description
    desc = hack.get("description", "") or ""
    if len(desc) > 50:
        score += 1; parts.append("has description +1")

    # LLM enhancement if available
    if llm_client.is_available() and desc:
        try:
            prompt = f"""Rate this hackathon opportunity 1-7 (7=best). Reply with ONLY a number.

Title: {hack.get('title', '')}
Prize: ${prize}
Description: {desc[:300]}

Consider: prize value, legitimacy, accessibility for Indian developers.
Rating (1-7):"""
            llm_score_str = llm_client.call_llm(prompt, max_tokens=5).strip()
            llm_score = int(re.search(r'\d+', llm_score_str).group())
            llm_score = min(max(llm_score, 1), 7)
            score += llm_score
            parts.append(f"LLM +{llm_score}")
        except Exception:
            pass

    return {
        "score": min(score, 20),
        "breakdown": ", ".join(parts),
    }
