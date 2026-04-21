"""
telegram_alert.py — Telegram alerts for all 3 pipelines:
1. AI Tools / Free Credits
2. Hackathons
3. Local Business Leads
"""

import html
import os
from urllib.parse import quote as url_quote

import requests

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _post(msg: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] SKIPPED — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return False
    # Split long messages
    max_len = 4000
    chunks = []
    while len(msg) > max_len:
        split = msg.rfind("\n", 0, max_len)
        if split < max_len // 2:
            split = max_len
        chunks.append(msg[:split])
        msg = msg[split:].lstrip("\n")
    if msg:
        chunks.append(msg)
    for chunk in chunks:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=15,
            )
            r.raise_for_status()
            if not r.json().get("ok"):
                print(f"[telegram] API error: {r.json().get('description')}")
                return False
        except Exception as e:
            print(f"[telegram] send error: {e}")
            return False
    return True


def _safe_url(url: str) -> str:
    return url_quote(url, safe=":/?=&#%+@")


# ── 1. AI Tools alert ─────────────────────────────────────────────────────────

def send_alert(opp: dict) -> bool:
    score     = opp.get("score", 0)
    source    = html.escape(opp.get("source", "unknown"))
    title     = html.escape(opp.get("title", "No title")[:200])
    summary   = html.escape(opp.get("summary", "")[:300])
    breakdown = html.escape(opp.get("score_breakdown", ""))
    safe_url  = _safe_url(opp.get("url", ""))
    free_emoji = "✅" if opp.get("is_free") else "❌"
    credits   = opp.get("credits_value_usd", 0)
    credits_str = f"  |  💰 Credits: ${credits}" if credits else ""

    msg = (
        f"🚀 <b>Score {score}/16</b> — {source}\n\n"
        f"<b>{title}</b>\n\n"
        f"{summary}\n\n"
        f"{free_emoji} Free: {'Yes' if opp.get('is_free') else 'No'}{credits_str}\n"
        f"📊 {breakdown}\n\n"
        f'🔗 <a href="{safe_url}">Open Link</a>'
    )
    result = _post(msg)
    if result:
        print(f"[telegram] AI tool alert sent: {opp.get('title', '')[:60]}")
    return result


# ── 2. Hackathon alert ────────────────────────────────────────────────────────

def send_hackathon_alert(hack: dict) -> bool:
    score    = hack.get("score", 0)
    title    = html.escape(hack.get("title", "")[:200])
    org      = html.escape(hack.get("organizer", "Unknown"))
    prize    = hack.get("prize_usd", 0)
    deadline = html.escape(hack.get("deadline", "Not specified")[:100])
    desc     = html.escape(hack.get("description", "")[:250])
    source   = html.escape(hack.get("source", ""))
    safe_url = _safe_url(hack.get("url", ""))

    prize_str = f"💰 Prize: ${prize:,}" if prize else "🆓 No cash prize (experience/swag)"
    free_str  = "✅ Free to enter" if hack.get("is_free", True) else "⚠️ Paid entry"

    msg = (
        f"🏆 <b>HACKATHON ALERT — Score {score}/20</b>\n\n"
        f"<b>{title}</b>\n"
        f"🏢 {org} | {source}\n\n"
        f"{prize_str}\n"
        f"{free_str}\n"
        f"📅 Deadline: {deadline}\n\n"
        f"📝 {desc}\n\n"
        f'🔗 <a href="{safe_url}">Register Now</a>'
    )
    result = _post(msg)
    if result:
        print(f"[telegram] Hackathon alert sent: {hack.get('title', '')[:60]}")
    return result


# ── 3. Lead alert ──────────────────────────────────────────────────────────────

def send_lead_alert(lead: dict) -> bool:
    score    = lead.get("score", 0)
    name     = html.escape(lead.get("business_name", "")[:150])
    city     = html.escape(lead.get("city", "India"))
    category = html.escape(lead.get("category", "business").replace("_", " ").title())
    phone    = lead.get("phone", "")
    rating   = lead.get("rating", 0)
    pitch    = html.escape(lead.get("pitch", "")[:400])
    safe_url = _safe_url(lead.get("url", ""))

    rating_str = f"⭐ Rating: {rating}/5" if rating else "⭐ No rating found"
    phone_str  = f"📞 {phone}" if phone else "📞 Phone: not found"
    url_str    = f'\n🔗 <a href="{safe_url}">View Business</a>' if lead.get("url") else ""

    msg = (
        f"🎯 <b>FREELANCE LEAD — Score {score}/10</b>\n\n"
        f"<b>{name}</b>\n"
        f"📍 {city} | {category}\n"
        f"{rating_str}\n"
        f"{phone_str}"
        f"{url_str}\n\n"
        f"💬 <b>Pitch:</b>\n{pitch}"
    )
    result = _post(msg)
    if result:
        print(f"[telegram] Lead alert sent: {lead.get('business_name', '')[:60]}")
    return result


def test_connection() -> bool:
    if not BOT_TOKEN:
        print("[telegram] No TELEGRAM_BOT_TOKEN set"); return False
    if not CHAT_ID:
        print("[telegram] No TELEGRAM_CHAT_ID set"); return False
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10)
        r.raise_for_status()
        bot = r.json().get("result", {})
        print(f"[telegram] Connected as @{bot.get('username', 'unknown')}")
        return True
    except Exception as e:
        print(f"[telegram] test failed: {e}"); return False
