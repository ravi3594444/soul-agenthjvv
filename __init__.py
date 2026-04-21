"""
Opportunity Engine — AI Tool Discovery & Alert Bot

Finds RECENT AI tools with free credits using DuckDuckGo web search
+ HN/Reddit/GitHub. Scores them with a Rust-accelerated engine and
sends Telegram alerts. Includes Arai — a CrewAI-powered multi-agent
conversational chatbot.

Modules:
  main.py           — Orchestrator CLI (scrape → score → alert → chat)
  scrapers.py       — DuckDuckGo + HN + Reddit + GitHub scrapers
  scorer.py         — Multi-LLM heuristic extraction + scoring engine
  db.py             — SQLite persistence with 3-layer dedup
  telegram_alert.py — Telegram alert sender
  chat_bot.py       — Arai: conversational Telegram chatbot
  crew_agents.py    — CrewAI multi-agent system (4 agents, 3 workflows)
  rust_scorer/      — Optional Rust PyO3 extension for native acceleration

Usage:
  python main.py              # Full pipeline
  python main.py --chat       # Start Arai chatbot
  python main.py --scrape     # Scrape only
  python main.py --score      # Score only
  python main.py --alert      # Alert only
  python main.py --stats      # DB stats
"""

__version__ = "2.0.0"
__author__ = "Opportunity Engine"
