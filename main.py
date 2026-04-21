"""
main.py — Opportunity Engine v4 Orchestrator.

3 Pipelines:
  1. AI Tools    — scrape + score + alert free AI tools and credits
  2. Hackathons  — scrape + score + alert competitions
  3. Leads       — scrape + score + alert local business freelance opportunities

Usage:
  python main.py              # Full pipeline (all 3)
  python main.py --chat       # Start Arai chatbot
  python main.py --scrape     # Only scrape all 3
  python main.py --score      # Only score existing items
  python main.py --alert      # Only send alerts
  python main.py --tools      # Only AI tools pipeline
  python main.py --hackathons # Only hackathons pipeline
  python main.py --leads      # Only leads pipeline
  python main.py --stats      # Show stats
  python main.py --test-telegram
"""

import os
import sys
import time
from dotenv import load_dotenv
load_dotenv()

from db import (
    init_db, insert_opportunity, get_unscored, update_score,
    get_alertable, mark_alerted, get_stats,
    insert_hackathon, get_unscored_hackathons, update_hackathon_score,
    get_alertable_hackathons, mark_hackathon_alerted,
    insert_lead, get_unscored_leads, update_lead_score,
    get_alertable_leads, mark_lead_alerted,
)
from scrapers import scrape_all
from scorer import score_item, init_scorer
from hackathon_scraper import scrape_all_hackathons
from hackathon_scorer import score_hackathon
from leads_scraper import scrape_all_leads
from leads_scorer import score_lead
from telegram_alert import send_alert, send_hackathon_alert, send_lead_alert, test_connection
from chat_bot import run_chat_bot

ALERT_THRESHOLD      = int(os.getenv("ALERT_THRESHOLD", "7"))
HACKATHON_THRESHOLD  = int(os.getenv("HACKATHON_THRESHOLD", "6"))
LEAD_THRESHOLD       = int(os.getenv("LEAD_THRESHOLD", "6"))


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline 1 — AI Tools
# ══════════════════════════════════════════════════════════════════════════════

def run_scrape() -> int:
    print("\n" + "=" * 60)
    print("PIPELINE 1: AI TOOLS — scraping...")
    print("=" * 60)
    items = scrape_all()
    if not items:
        print("[main] No new AI tool items found.")
        return 0
    inserted = sum(1 for it in items if insert_opportunity(it))
    print(f"[main] {inserted} new AI tools inserted, {len(items)-inserted} duplicates skipped")
    return inserted


def run_score() -> int:
    print("\n" + "=" * 60)
    print("PIPELINE 1: AI TOOLS — scoring...")
    print("=" * 60)
    unscored = get_unscored()
    if not unscored:
        print("[main] No unscored AI tools."); return 0
    print(f"[main] Scoring {len(unscored)} items...")
    scored = 0
    for i, opp in enumerate(unscored, 1):
        print(f"  [{i}/{len(unscored)}] {opp['title'][:70]}")
        try:
            result = score_item(opp)
            update_score(opp["id"], result["score"], result["breakdown"],
                         result["summary"], result["is_free"], result["credits_value_usd"])
            scored += 1
            print(f"    → Score: {result['score']} | {result['breakdown']}\n")
        except Exception as e:
            print(f"    → ERROR: {e}\n")
        if i % 10 == 0:
            time.sleep(1)
    print(f"[main] Scored {scored} AI tools")
    return scored


def run_alert() -> int:
    print("\n" + "=" * 60)
    print(f"PIPELINE 1: AI TOOLS — alerting (threshold ≥ {ALERT_THRESHOLD})")
    print("=" * 60)
    to_alert = get_alertable(ALERT_THRESHOLD)
    if not to_alert:
        print(f"[main] No AI tools above threshold ({ALERT_THRESHOLD})."); return 0
    sent = 0
    for opp in to_alert:
        print(f"  [{opp['score']}] {opp['title'][:80]}")
        if send_alert(opp):
            mark_alerted(opp["id"]); sent += 1
    print(f"[main] Sent {sent}/{len(to_alert)} AI tool alerts")
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline 2 — Hackathons
# ══════════════════════════════════════════════════════════════════════════════

def run_hackathon_scrape() -> int:
    print("\n" + "=" * 60)
    print("PIPELINE 2: HACKATHONS — scraping...")
    print("=" * 60)
    items = scrape_all_hackathons()
    if not items:
        print("[main] No new hackathons found."); return 0
    inserted = sum(1 for it in items if insert_hackathon(it))
    print(f"[main] {inserted} new hackathons inserted")
    return inserted


def run_hackathon_score() -> int:
    print("\n" + "=" * 60)
    print("PIPELINE 2: HACKATHONS — scoring...")
    print("=" * 60)
    unscored = get_unscored_hackathons()
    if not unscored:
        print("[main] No unscored hackathons."); return 0
    scored = 0
    for i, hack in enumerate(unscored, 1):
        print(f"  [{i}/{len(unscored)}] {hack['title'][:70]}")
        try:
            result = score_hackathon(hack)
            update_hackathon_score(hack["id"], result["score"])
            scored += 1
            print(f"    → Score: {result['score']} | {result['breakdown']}\n")
        except Exception as e:
            print(f"    → ERROR: {e}\n")
    print(f"[main] Scored {scored} hackathons")
    return scored


def run_hackathon_alert() -> int:
    print("\n" + "=" * 60)
    print(f"PIPELINE 2: HACKATHONS — alerting (threshold ≥ {HACKATHON_THRESHOLD})")
    print("=" * 60)
    to_alert = get_alertable_hackathons(HACKATHON_THRESHOLD)
    if not to_alert:
        print(f"[main] No hackathons above threshold."); return 0
    sent = 0
    for hack in to_alert:
        print(f"  [{hack['score']}] {hack['title'][:80]}")
        if send_hackathon_alert(hack):
            mark_hackathon_alerted(hack["id"]); sent += 1
    print(f"[main] Sent {sent}/{len(to_alert)} hackathon alerts")
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline 3 — Local Business Leads
# ══════════════════════════════════════════════════════════════════════════════

def run_leads_scrape() -> int:
    print("\n" + "=" * 60)
    print("PIPELINE 3: LEADS — scraping local businesses...")
    print("=" * 60)
    items = scrape_all_leads()
    if not items:
        print("[main] No new leads found."); return 0
    inserted = sum(1 for it in items if insert_lead(it))
    print(f"[main] {inserted} new leads inserted")
    return inserted


def run_leads_score() -> int:
    print("\n" + "=" * 60)
    print("PIPELINE 3: LEADS — scoring + generating pitches...")
    print("=" * 60)
    unscored = get_unscored_leads()
    if not unscored:
        print("[main] No unscored leads."); return 0
    scored = 0
    for i, lead in enumerate(unscored, 1):
        print(f"  [{i}/{len(unscored)}] {lead['business_name'][:70]} — {lead['city']}")
        try:
            result = score_lead(lead)
            update_lead_score(lead["id"], result["score"], result["pitch"])
            scored += 1
            print(f"    → Score: {result['score']}/10\n")
        except Exception as e:
            print(f"    → ERROR: {e}\n")
        time.sleep(0.5)
    print(f"[main] Scored {scored} leads")
    return scored


def run_leads_alert() -> int:
    print("\n" + "=" * 60)
    print(f"PIPELINE 3: LEADS — alerting (threshold ≥ {LEAD_THRESHOLD})")
    print("=" * 60)
    to_alert = get_alertable_leads(LEAD_THRESHOLD)
    if not to_alert:
        print(f"[main] No leads above threshold."); return 0
    sent = 0
    for lead in to_alert[:10]:  # max 10 lead alerts per run
        print(f"  [{lead['score']}] {lead['business_name'][:80]} — {lead['city']}")
        if send_lead_alert(lead):
            mark_lead_alerted(lead["id"]); sent += 1
    print(f"[main] Sent {sent}/{len(to_alert)} lead alerts")
    return sent


# ══════════════════════════════════════════════════════════════════════════════
# Stats
# ══════════════════════════════════════════════════════════════════════════════

def run_stats():
    stats = get_stats()
    print("\n" + "=" * 60)
    print("DATABASE STATS")
    print("=" * 60)
    print(f"  AI Tools:    {stats['total']} total | {stats['scored']} scored | {stats['high_score']} high-score | {stats['alerted']} alerted")
    print(f"  Hackathons:  {stats['hackathons']} total")
    print(f"  Leads:       {stats['leads']} total")
    print("=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    start = time.time()
    args  = set(sys.argv[1:])
    init_db()

    # Chat mode — always-on bot
    if "--chat" in args:
        print("=" * 60)
        print("  ARAI v4 — AI Assistant + Opportunity Monitor")
        print("  Starting Telegram polling...")
        print("=" * 60)
        try:
            from health import start_health_server
            start_health_server()
        except Exception:
            pass
        init_scorer()
        run_chat_bot()
        return

    if "--test-telegram" in args:
        test_connection(); return

    if "--stats" in args and len(args) == 1:
        run_stats(); return

    print("=" * 60)
    print("  OPPORTUNITY ENGINE v4")
    print("  AI Tools + Hackathons + Freelance Leads")
    print(f"  Thresholds: tools≥{ALERT_THRESHOLD} | hackathons≥{HACKATHON_THRESHOLD} | leads≥{LEAD_THRESHOLD}")
    print("=" * 60)

    # Determine which pipelines to run
    run_all       = not args
    run_tools     = run_all or "--tools"     in args or "--scrape" in args or "--score" in args or "--alert" in args
    run_hackathon = run_all or "--hackathons" in args
    run_leads_p   = run_all or "--leads"     in args

    init_scorer()

    # Pipeline 1 — AI Tools
    if run_tools:
        if run_all or "--scrape" in args or "--tools" in args: run_scrape()
        if run_all or "--score"  in args or "--tools" in args: run_score()
        if run_all or "--alert"  in args or "--tools" in args: run_alert()

    # Pipeline 2 — Hackathons
    if run_hackathon:
        run_hackathon_scrape()
        run_hackathon_score()
        run_hackathon_alert()

    # Pipeline 3 — Leads
    if run_leads_p:
        run_leads_scrape()
        run_leads_score()
        run_leads_alert()

    run_stats()
    elapsed = time.time() - start
    print(f"\n[main] All pipelines completed in {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
