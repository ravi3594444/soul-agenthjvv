"""
chat_bot.py — Arai v4: AI assistant with access to all 3 pipelines + web search.
Arai can chat AND has tools: /opp, /hackathons, /leads, /search, /deep, /compare, /deals
"""

import html
import os
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import quote as url_quote

import requests
import llm_client
import db
from db import get_chat_history, append_chat_message, clear_chat_history

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
MAX_HISTORY   = 30
MAX_CRASHES   = 5

ARAI_SYSTEM = """\
You are Arai — a brilliant AI assistant inside the Opportunity Engine v4.

Your capabilities:
- Chat about anything (tech, coding, business, life)
- Find FREE AI tools and credits (use /opp)
- Find hackathons and competitions (use /hackathons)
- Find local business leads for freelancing (use /leads)
- Search the web for latest news (use /search)
- Deep research with CrewAI agents (/deep, /compare, /deals)

Your personality:
- Sharp, direct, talks like a tech-savvy friend
- Casual language, occasional emojis
- Excited about free AI tools and freelance opportunities
- Mix Hindi/English naturally when talking to Indian users
- Always gives real, actionable information
- For LATEST NEWS: always say "Use /search <topic> for real-time results"
- Never pretend to be human. You're Arai, an AI, and proud of it.

Important: You do NOT have real-time internet access in chat mode.
For current news/events, always direct users to use /search command.\
"""


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _send(chat_id, text, parse_mode="HTML"):
    if not BOT_TOKEN: return False
    max_len = 4000
    chunks = []
    while len(text) > max_len:
        split = text.rfind("\n", 0, max_len)
        if split < max_len // 2: split = max_len
        chunks.append(text[:split])
        text = text[split:].lstrip("\n")
    if text: chunks.append(text)
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": chunk,
                      "parse_mode": parse_mode, "disable_web_page_preview": True},
                timeout=15,
            )
        except Exception as e:
            print(f"[arai] send error: {e}")
    return True


def _typing(chat_id):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"}, timeout=5,
        )
    except: pass


def _llm_chat(messages):
    b = llm_client.get_backend()
    if not b:
        return "No LLM backend available. Check your API keys."
    try:
        return b.chat(messages, max_tokens=2048, temperature=0.7)
    except Exception as e:
        err = str(e)
        if "429" in err: return "Rate limited — try again in a moment."
        return f"LLM error ({llm_client.backend_name()}). Try again."


# ── Pipeline runners (called from commands) ───────────────────────────────────

def _run_tools(chat_id):
    try:
        from scrapers import scrape_all
        from scorer import score_item, init_scorer
        _typing(chat_id)
        db.init_db()
        items = scrape_all()
        if not items: return "No new AI tools found right now. Try again in a bit!"
        new_items = []
        for it in items:
            if db.insert_opportunity(it):
                result = score_item(it)
                it["score"] = result["score"]
                it["credits"] = result["credits_value_usd"]
                db.update_score(
                    it.get("id", 0) or 0, result["score"], result["breakdown"],
                    result["summary"], result["is_free"], result["credits_value_usd"],
                )
                new_items.append(it)
        if not new_items:
            return f"Scraped {len(items)} items but all duplicates. Nothing new!"
        new_items.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = new_items[:8]
        msg = f"<b>🚀 {len(new_items)} new AI tools found!</b>\n<i>(top {len(top)} by score)</i>\n\n"
        for i, item in enumerate(top, 1):
            score = item.get("score", 0)
            credits = item.get("credits", 0)
            title = html.escape(item["title"][:80])
            safe_url = html.escape(url_quote(item["url"], safe=":/?=&#%+@"))
            credit_str = f" | 💰 ${credits}" if credits > 0 else ""
            msg += f"<b>{i}.</b> [{score}/16]{credit_str} {title}\n"
            msg += f'<a href="{safe_url}">Open →</a>\n\n'
        return msg
    except Exception as e:
        return f"Pipeline error: {e}"


def _run_hackathons(chat_id):
    try:
        from hackathon_scraper import scrape_all_hackathons
        from hackathon_scorer import score_hackathon
        _typing(chat_id)
        _send(chat_id, "🏆 Searching for hackathons...")
        items = scrape_all_hackathons()
        if not items: return "No new hackathons found right now."
        new_items = []
        for it in items:
            if db.insert_hackathon(it):
                result = score_hackathon(it)
                it["score"] = result["score"]
                db.update_hackathon_score(it.get("id", 0) or 0, result["score"])
                new_items.append(it)
        if not new_items:
            return f"Found {len(items)} hackathons but all already in database."
        new_items.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = new_items[:6]
        msg = f"<b>🏆 {len(new_items)} hackathons found!</b>\n\n"
        for i, hack in enumerate(top, 1):
            score = hack.get("score", 0)
            prize = hack.get("prize_usd", 0)
            title = html.escape(hack["title"][:80])
            org = html.escape(hack.get("organizer", "")[:50])
            deadline = html.escape(hack.get("deadline", "Check site")[:60])
            safe_url = html.escape(url_quote(hack["url"], safe=":/?=&#%+@"))
            prize_str = f"💰 ${prize:,}" if prize else "🆓 Free/swag"
            msg += f"<b>{i}. {title}</b>\n"
            msg += f"🏢 {org} | {prize_str}\n"
            msg += f"📅 {deadline}\n"
            msg += f'<a href="{safe_url}">Register →</a>\n\n'
        return msg
    except Exception as e:
        return f"Hackathon search error: {e}"


def _run_leads(chat_id, city=""):
    try:
        from leads_scraper import scrape_all_leads
        from leads_scorer import score_lead
        _typing(chat_id)
        _send(chat_id, "🎯 Searching for local business leads...")
        items = scrape_all_leads()
        if not items: return "No new leads found right now."
        # Filter by city if specified
        if city:
            filtered = [it for it in items if city.lower() in it.get("city", "").lower()]
            if filtered: items = filtered
        new_items = []
        for it in items:
            if db.insert_lead(it):
                result = score_lead(it)
                it["score"] = result["score"]
                it["pitch"] = result["pitch"]
                db.update_lead_score(it.get("id", 0) or 0, result["score"], result["pitch"])
                new_items.append(it)
        if not new_items:
            return f"Found {len(items)} businesses but all already in database."
        new_items.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = new_items[:5]
        msg = f"<b>🎯 {len(new_items)} freelance leads found!</b>\n\n"
        for i, lead in enumerate(top, 1):
            score = lead.get("score", 0)
            name = html.escape(lead["business_name"][:80])
            city_str = html.escape(lead.get("city", ""))
            phone = lead.get("phone", "")
            pitch = html.escape(lead.get("pitch", "")[:200])
            phone_str = f"\n📞 {phone}" if phone else ""
            safe_url = html.escape(url_quote(lead.get("url", "#"), safe=":/?=&#%+@"))
            msg += f"<b>{i}. {name}</b> [{score}/10]\n"
            msg += f"📍 {city_str}{phone_str}\n"
            msg += f"💬 {pitch}\n"
            if lead.get("url"):
                msg += f'<a href="{safe_url}">View →</a>\n'
            msg += "\n"
        return msg
    except Exception as e:
        return f"Leads search error: {e}"


def _run_search(chat_id, query):
    try:
        from duckduckgo_search import DDGS
        _typing(chat_id)
        results = []
        with DDGS() as ddgs:
            items = list(ddgs.text(query + " 2026", max_results=5))
            for item in items:
                title = html.escape(item.get("title", "")[:100])
                body = html.escape(item.get("body", "")[:200])
                url = html.escape(url_quote(item.get("href", ""), safe=":/?=&#%+@"))
                results.append(f"<b>{title}</b>\n{body}\n<a href=\"{url}\">Read →</a>")
        if not results:
            return "No results found. Try a different query."
        msg = f"<b>🔍 Search: {html.escape(query)}</b>\n\n"
        msg += "\n\n".join(results)
        return msg
    except Exception as e:
        return f"Search error: {e}"


# ── Command handler ───────────────────────────────────────────────────────────

def _handle_command(chat_id, text):
    cmd = text.strip()
    cmd_lower = cmd.lower()

    if cmd_lower in ("/start", "/hello", "/hi"):
        return (
            "Hey! I'm <b>Arai v4</b> 🤖 — your AI sidekick for finding opportunities.\n\n"
            "<b>🚀 AI Tools:</b>\n"
            "/opp — find latest free AI tools &amp; credits\n\n"
            "<b>🏆 Hackathons:</b>\n"
            "/hackathons — find open hackathons &amp; competitions\n\n"
            "<b>🎯 Freelance Leads:</b>\n"
            "/leads — find local businesses to pitch\n"
            "/leads Patna — filter by city\n\n"
            "<b>🔍 Search:</b>\n"
            "/search &lt;query&gt; — real-time web search\n\n"
            "<b>🤖 AI Agents (CrewAI):</b>\n"
            "/deep &lt;topic&gt; — deep research\n"
            "/compare &lt;tool1 vs tool2&gt; — comparison\n"
            "/deals — find best current deals\n\n"
            "<b>💬 Chat:</b>\n"
            "/stats — database stats\n"
            "/clear — clear chat history\n"
            "Anything else → just chat with me! 🚀"
        )

    if cmd_lower in ("/help", "/commands"):
        return (
            "<b>Arai v4 Commands:</b>\n\n"
            "/opp — AI tools &amp; free credits\n"
            "/hackathons — competitions &amp; prizes\n"
            "/leads — local freelance leads\n"
            "/leads &lt;city&gt; — leads for specific city\n"
            "/search &lt;query&gt; — web search\n"
            "/deep &lt;topic&gt; — deep research\n"
            "/compare &lt;x vs y&gt; — comparison\n"
            "/deals — best current deals\n"
            "/stats — database stats\n"
            "/clear — clear chat history"
        )

    if cmd_lower == "/clear":
        clear_chat_history(chat_id)
        return "Memory cleared! Fresh start 🧹"

    if cmd_lower == "/stats":
        try:
            stats = db.get_stats()
            return (
                f"<b>📊 Opportunity Engine Stats:</b>\n\n"
                f"🚀 AI Tools: {stats['total']} total | {stats['high_score']} high-score | {stats['alerted']} alerted\n"
                f"🏆 Hackathons: {stats['hackathons']} found\n"
                f"🎯 Leads: {stats['leads']} found\n"
                f"✅ Scored: {stats['scored']}"
            )
        except Exception as e:
            return f"Stats error: {e}"

    return None


# ── Main polling loop ──────────────────────────────────────────────────────────

def run_chat_bot():
    if not BOT_TOKEN:
        print("[arai] ERROR: No TELEGRAM_BOT_TOKEN"); return

    print("\n" + "=" * 60)
    print("  ARAI v4 — Telegram Bot")
    print("  Polling for messages...")
    print("=" * 60 + "\n")

    db.init_db()
    ok = llm_client.init(preference_order=["mistral", "gemini", "groq", "together", "openrouter"])
    if not ok:
        print("[arai] WARNING: No LLM — only commands will work")

    try:
        from crew_agents import init_providers
        init_providers()
    except Exception as e:
        print(f"[arai] CrewAI init warning: {e}")

    offset = 0
    crash_count = 0
    print(f"[arai] Ready! Send /start in Telegram\n")

    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                timeout=35,
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                time.sleep(2); continue

            for update in data.get("result", []):
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text    = msg.get("text", "")
                user    = msg.get("from", {}).get("first_name", "User")

                if not text or not chat_id: continue

                # Security gate
                #if OWNER_CHAT_ID and str(chat_id) != OWNER_CHAT_ID:
                    #_send(chat_id, "Sorry, Arai is a private assistant.")
                   # continue

                cmd = text.strip()
                cmd_lower = cmd.lower()

                # Built-in commands
                cmd_resp = _handle_command(chat_id, text)
                if cmd_resp:
                    _send(chat_id, cmd_resp); continue

                # Pipeline commands
                if cmd_lower in ("/opp", "/opportunities", "/find"):
                    _send(chat_id, "🔍 Searching for latest AI tools & credits...")
                    _send(chat_id, _run_tools(chat_id)); continue

                if cmd_lower in ("/hackathons", "/hack", "/competitions"):
                    _send(chat_id, _run_hackathons(chat_id)); continue

                if cmd_lower.startswith("/leads"):
                    city = cmd[6:].strip() if len(cmd) > 6 else ""
                    _send(chat_id, _run_leads(chat_id, city)); continue

                if cmd_lower.startswith("/search "):
                    query = cmd[8:].strip()
                    if not query:
                        _send(chat_id, "Usage: /search <query>\nExample: /search latest AI tools 2026"); continue
                    _send(chat_id, _run_search(chat_id, query)); continue

                if cmd_lower.startswith("/deep "):
                    topic = cmd[6:].strip()
                    if not topic:
                        _send(chat_id, "Usage: /deep <topic>"); continue
                    _send(chat_id, "🔍 Deep research starting... (~30-60s)")
                    try:
                        from crew_agents import run_deep_research
                        result = run_deep_research(topic)
                        _send(chat_id, f"<b>📊 Deep Research: {html.escape(topic)}</b>\n\n{result}")
                    except Exception as e:
                        _send(chat_id, f"CrewAI error: {e}")
                    continue

                if cmd_lower.startswith("/compare "):
                    tools = cmd[9:].strip()
                    _send(chat_id, "⚖️ Comparing... (~30-60s)")
                    try:
                        from crew_agents import run_compare_tools
                        result = run_compare_tools(tools)
                        _send(chat_id, f"<b>⚖️ {html.escape(tools)}</b>\n\n{result}")
                    except Exception as e:
                        _send(chat_id, f"CrewAI error: {e}")
                    continue

                if cmd_lower in ("/deals", "/agent"):
                    _send(chat_id, "🎯 Finding best deals... (~30-90s)")
                    try:
                        from crew_agents import run_find_opportunities
                        result = run_find_opportunities()
                        _send(chat_id, f"<b>🎯 Best Current AI Deals</b>\n\n{result}")
                    except Exception as e:
                        _send(chat_id, f"CrewAI error: {e}")
                    continue

                # Regular chat
                print(f"[arai] {user}: {text[:80]}")
                _typing(chat_id)
                append_chat_message(chat_id, "user", text)
                history = get_chat_history(chat_id, limit=MAX_HISTORY)
                messages = [{"role": "system", "content": ARAI_SYSTEM}] + history
                response = _llm_chat(messages)
                append_chat_message(chat_id, "assistant", response)
                _send(chat_id, response)
                print(f"[arai] → {response[:80]}")

            crash_count = 0

        except KeyboardInterrupt:
            print("\n[arai] Stopped"); break
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            crash_count += 1
            print(f"[arai] ERROR (crash {crash_count}/{MAX_CRASHES}): {e}")
            traceback.print_exc()
            if crash_count >= MAX_CRASHES:
                print("[arai] Too many crashes. Stopping.")
                if OWNER_CHAT_ID:
                    try: _send(int(OWNER_CHAT_ID), f"⚠️ Arai crashed {MAX_CRASHES} times. Error: {html.escape(str(e))}")
                    except: pass
                break
            wait = min(3 * (2 ** (crash_count - 1)), 30)
            print(f"[arai] Restarting in {wait}s...")
            time.sleep(wait)
