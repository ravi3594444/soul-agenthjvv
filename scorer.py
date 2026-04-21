"""
scorer.py — Heuristic extraction + optional LLM enhancement + Rust scoring.

Improvements vs v2:
  - Uses shared llm_client instead of its own copy of _init_llm / backend calls
  - _llm_extract is a single clean function (was 5 near-identical if/elif blocks)
  - init_scorer() delegates backend selection to llm_client.init()
  - _days_ago logs a warning on parse failure instead of silently returning 999
"""

import json
import os
import re
from datetime import datetime, timezone

import llm_client

# ── Rust acceleration (optional) ──────────────────────────────────────────────
try:
    from scorer_core import calculate_score as _rust_score
    _USE_RUST = True
except ImportError:
    _USE_RUST = False

# ── Heuristic patterns ─────────────────────────────────────────────────────────

FREE_PATTERNS = [
    r'\bfree\b', r'\bopen[\s-]source\b', r'\bfreemium\b', r'\bno cost\b',
    r'\bfree of charge\b', r'\bgratis\b', r'\bno payment\b', r'\bcompletely free\b',
    r'\bfree tier\b', r'\bfree plan\b', r'\bfree access\b', r'\bfree to use\b',
    r'\bcommunity edition\b', r'\bfree forever\b', r'\bat no cost\b',
]

CREDITS_PATTERNS = [
    r'\$\s*(\d+)\s*free\s*credits?',
    r'(\d+)\s*dollars?\s*(?:of\s*)?free\s*credits?',
    r'free\s*credits?\s*(?:worth|valued?\s*at)?\s*\$\s*(\d+)',
    r'(\d+)\s*USD\s*free\s*credits?',
    r'free\s*credit\s*of\s*\$\s*(\d+)',
    r'\$\s*(\d+)\s+in\s+free\s+credits?',
    r'\$\s*(\d+)\s+(?:\w+\s+){0,5}credits?',
    r'\$\s*(\d+)\w*credit',
    r'\$\s*(\d+)\s+\S*credit',
    r'\$\s*(\d[,.]?\d*\s*[kKmM])\w*credit',
    r'credits?\s*(?:worth|valued?\s*at|of|:)\s*\$\s*(\d+)',
    r'\$\s*(\d+)\s+(?:for|in|of)\s+credits?',
    r'(?:giv\w+\s+(?:away\s+)?)?\$\s*(\d+)\s+(?:\w+\s+){0,4}credits?',
    r'\$\s*(\d[,.]?\d*\s*[kKmM])\s+(?:\w+\s+){0,6}credits?',
    r'credits?\s*(?:worth|valued?\s*at|of)\s*\$\s*(\d[,.]?\d*\s*[kKmM])',
    r'\$\s*(\d[,.]?\d*\s*[kKmM])\b',
    r'\$\s*(\d[,.]?\d*)\s*[kKmM]\b',
]

MODEL_PATTERNS = [
    r'\bnew\s+model\b', r'\breleased?\b.*\bmodel\b', r'\bmodel\b.*\breleased?\b',
    r'\blaunch(?:ed|ing)?\b.*\b(?:AI|LLM|model)\b', r'\bannounce[sd]?\b.*\bmodel\b',
    r'\bv\d+\.\d+\b', r'\bversion\s+\d+', r'\bGPT[-\s]?\d', r'\bClaude\s+\d',
    r'\bGemini\s+\d', r'\bLlama\s+\d', r'\bMistral\b', r'\bnew\s+(?:AI|language)\s+model\b',
]

OSS_PATTERNS = [
    r'\bopen[\s-]source\b', r'\bgit(?:hub|lab)\b', r'\bMIT\s+license\b',
    r'\bApache\s+license\b', r'\bgpl\b', r'\bpublicly\s+available\b',
    r'\bself[\s-]host(?:ed|able)?\b', r'\bgithub\.com\b', r'\bsource\s+code\b',
]

INCREDIBLE_PATTERNS = [
    r'\$\s*(?:[5-9]\d{2,})\s*(?:in\s*)?(?:free\s*)?(?:credit|credits?)',
    r'\$\s*\d+[kK]\s*(?:in\s*)?(?:free\s*)?(?:credit|credits?)',
    r'unlimited\s+free',
    r'free\s+forever',
    r'completely\s+free\s+(?:no\s+limits?|unlimited)',
    r'(?:500|1000|5000)\s*free\s*credits?',
]

_PRICING_WORDS = [
    'costs?', 'price', 'pricing', 'per month', 'per year', 'subscription',
    'per token', 'per call', 'per request', 'per credit', 'charged',
    'billed', 'billing', 'pay', 'payment', 'expensive', 'cheapest',
    'plan:', 'plans:', '/month', '/year', '/mo', '/yr',
    'revenue', 'funding', 'raised', 'valuation', 'invest',
    'market cap', 'company is', 'startup is',
]


def _has_credit_context(text: str, start: int, end: int) -> bool:
    match_text = text[start:end].lower()
    has_km     = bool(re.search(r'[kkm]', match_text))
    window     = 200 if has_km else 80
    ctx        = text[max(0, start - window): end + window].lower()

    neg_strong = ['funding', 'raised', 'valuation', 'revenue', 'investment',
                  'market cap', 'series a', 'series b', 'series c', 'ipo',
                  'acquired', 'acquisition', 'bought for', 'sold for']
    if any(w in ctx for w in neg_strong):
        return False

    if 'credit' in match_text:
        if not re.search(r'per\s+credit|per\s+token|/credit|costs?\s+\$|\$\s+per', match_text):
            return True

    pos_strong = ['free credit', 'free credits', 'giving away', 'give away',
                  'on us', 'at no cost', 'free trial', 'free access',
                  'start for free', 'starter credit', 'welcome credit',
                  'sign up to get', 'signup bonus', 'credit bonus', 'grant',
                  'your credits', 'covered by']
    if any(w in ctx for w in pos_strong):
        return True

    if any(re.search(r'\b' + p + r'\b', ctx) for p in _PRICING_WORDS):
        return False

    has_credit = bool(re.search(r'\bcredits?\b', ctx)) or bool(re.search(r'\wcredit', ctx))
    offer_words = ['free', 'get', 'receive', 'includes', 'provides', 'offers',
                   'signup', 'sign up', 'welcome', 'new user', 'trial',
                   'bonus', 'grant', 'giving', 'start', 'covered']
    if has_credit and any(w in ctx for w in offer_words):
        return True

    return False


def _extract_credits_value(text: str) -> int:
    values = []
    text_lower = text.lower()
    for pat in CREDITS_PATTERNS:
        for m in re.finditer(pat, text_lower):
            if not _has_credit_context(text_lower, m.start(), m.end()):
                continue
            for g in m.groups():
                if g:
                    try:
                        c = g.strip()
                        if c.endswith('k'):
                            val = float(c[:-1]) * 1000
                        elif c.endswith('m'):
                            val = float(c[:-1]) * 1_000_000
                        else:
                            val = float(c.replace(',', ''))
                        values.append(int(val))
                    except (ValueError, IndexError):
                        pass
    return max(values) if values else 0


def _heuristic_extract(item: dict) -> dict:
    title    = item.get("title", "")
    raw_text = item.get("raw_text", "") or ""
    combined = (title + " " + raw_text).lower()

    is_free      = any(re.search(p, combined) for p in FREE_PATTERNS)
    is_oss       = any(re.search(p, combined) for p in OSS_PATTERNS)
    is_new_model = any(re.search(p, combined) for p in MODEL_PATTERNS)
    is_incredible = any(re.search(p, combined) for p in INCREDIBLE_PATTERNS)
    credits_value_usd = _extract_credits_value(combined)

    if credits_value_usd > 0:
        is_free = True
    if "github" in item.get("source", "").lower():
        is_oss = True

    summary = title[:120].strip()
    if len(summary) < 20 and raw_text:
        summary = raw_text[:120].strip()

    return {
        "is_free": is_free,
        "credits_value_usd": credits_value_usd,
        "is_open_source": is_oss,
        "is_new_model_release": is_new_model,
        "is_incredible_value": is_incredible,
        "summary": summary,
    }


# ── LLM extraction ─────────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """\
You are an AI tool analyst. Extract structured info from this AI tool / launch post.

Post title: {title}
Post text: {text}
Source: {source}
URL: {url}

Return ONLY valid JSON with these exact keys:
{{
  "is_free": true/false,
  "credits_value_usd": <integer, 0 if none>,
  "is_open_source": true/false,
  "is_new_model_release": true/false,
  "is_incredible_value": true/false,
  "summary": "<one sentence, max 25 words>"
}}

RULES:
- is_free = true ONLY if the tool is free or has a generous free tier
- credits_value_usd = dollar value of free credits offered
- is_incredible_value = true if free credits > $50 or tool does expensive things for free
- Do NOT invent facts. If unclear, use false / 0.
- Output ONLY the JSON object.\
"""


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    for pat in [r"```(?:json)?\s*(\{.*?\})\s*```", r"\{.*\}"]:
        m = re.search(pat, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1 if "```" in pat else 0))
            except json.JSONDecodeError:
                pass
    return {}


def _llm_extract(item: dict) -> dict:
    """Call the shared LLM client to extract structured data from an item."""
    if not llm_client.is_available():
        return {}
    prompt = EXTRACTION_PROMPT.format(
        title=item["title"][:300],
        text=(item.get("raw_text") or "")[:1500],
        source=item["source"],
        url=item["url"],
    )
    try:
        raw = llm_client.call_llm(prompt, max_tokens=300, temperature=0.05)
        return _extract_json(raw)
    except Exception as e:
        print(f"[scorer] LLM extract failed: {e}")
        return {}


# ── Scoring engine ─────────────────────────────────────────────────────────────

def _days_ago(iso: str) -> int:
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        print(f"[scorer] WARNING: could not parse date '{iso}', defaulting to 999")
        return 999


def _python_score(is_free, is_oss, credits, is_new_model, days_ago, upvotes):
    score, parts = 0, []
    if is_free or is_oss:
        score += 4; parts.append("free/OSS +4")
    if credits >= 20:
        score += 4; parts.append(f"credits ${credits} +4")
    elif credits >= 5:
        score += 2; parts.append(f"credits ${credits} +2")
    if is_new_model:
        score += 3; parts.append("new model +3")
    if days_ago < 7:
        score += 2; parts.append(f"{days_ago}d old +2")
    elif days_ago < 14:
        score += 1; parts.append(f"{days_ago}d old +1")
    if upvotes >= 100:
        score += 1; parts.append(f"{upvotes} upvotes +1")
    return score, ", ".join(parts) or "no signals"


def score_item(item: dict) -> dict:
    # Step 1: heuristic (always runs)
    facts = _heuristic_extract(item)

    # Step 2: LLM enhancement (optional)
    if llm_client.is_available():
        llm_facts = _llm_extract(item)
        if llm_facts:
            if llm_facts.get("credits_value_usd", 0) > facts["credits_value_usd"]:
                facts["credits_value_usd"] = llm_facts["credits_value_usd"]
            for flag in ("is_new_model_release", "is_incredible_value", "is_free"):
                if llm_facts.get(flag):
                    facts[flag] = True
            if llm_facts.get("summary") and len(llm_facts["summary"]) > 10:
                facts["summary"] = llm_facts["summary"]

    is_free      = bool(facts.get("is_free"))
    is_oss       = bool(facts.get("is_open_source"))
    credits      = int(facts.get("credits_value_usd") or 0)
    is_new_model = bool(facts.get("is_new_model_release"))
    is_incredible = bool(facts.get("is_incredible_value"))
    summary      = (facts.get("summary") or item["title"])[:300]

    # Extract upvotes from HN raw_text
    upvotes = 0
    raw = item.get("raw_text") or ""
    if "points:" in raw:
        try:
            upvotes = int(raw.split("points:")[1].split("|")[0].strip())
        except (ValueError, IndexError):
            pass

    days = _days_ago(item["posted_at"])

    if _USE_RUST:
        score, breakdown = _rust_score(is_free, is_oss, credits, is_new_model, days, upvotes)
    else:
        score, breakdown = _python_score(is_free, is_oss, credits, is_new_model, days, upvotes)

    if is_incredible and credits >= 50:
        score += 2
        breakdown += ", incredible value +2"

    return {
        "score": score,
        "breakdown": breakdown,
        "summary": summary,
        "is_free": is_free or is_oss,
        "credits_value_usd": credits,
    }


def init_scorer():
    """Initialise LLM backend (shared client). Falls back to heuristic if none available."""
    print("[scorer] Initializing ...")
    # Prefer fast cheap models for scoring; Mistral last (slow rate limit)
    ok = llm_client.init(preference_order=["groq","openrouter","together","gemini","mistral"])
    if not ok:
        print("[scorer] No LLM backend — heuristic mode (no API key needed)")
    else:
        print(f"[scorer] LLM mode — backend: {llm_client.backend_name()}")
