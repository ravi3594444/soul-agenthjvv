# Opportunity Engine v3.0

AI Tool Discovery Bot — finds **recent** AI tools offering free credits, scores them with multi-LLM extraction, and alerts you via Telegram. Includes **Arai** — a CrewAI-powered multi-agent chatbot.

## What's New in v3

| # | Fix | Impact |
|---|-----|--------|
| 🔴 | **Security gate enabled** — Arai only responds to the owner's `TELEGRAM_CHAT_ID` | Prevents strangers from burning your API credits |
| 🔴 | **`scored` column** — separates "not yet scored" from "scored zero" | Eliminates infinite re-scoring of zero-score items |
| 🟠 | **`llm_client.py`** — single shared LLM backend (was copy-pasted ×3) | One place to configure, one place to debug |
| 🟠 | **Concurrent scrapers** (`ThreadPoolExecutor`) — all 4–6 sources run in parallel | ~4× faster scrape phase |
| 🟠 | **Single DB round-trip** for dedup data (was 3 separate queries) | Faster startup, less lock contention |
| 🟡 | **Per-item error handling** in scoring — one bad item no longer kills the run | More resilient pipeline |
| 🟡 | **Persistent conversation memory** — Arai stores history in SQLite, survives restarts | No more forgotten context after crashes |
| 🟡 | **Dynamic year in all queries** — no more hardcoded "2025" in Tavily/Serper | Still works in 2026, 2027, ... |
| 🟡 | **Singleton CrewAI agents** — agents created once, reused across crew runs | Faster `/deep`, `/compare`, `/deals` |
| 🟢 | `html.escape()` on URLs in Telegram alerts | Fixes broken links with `&` in query strings |
| 🟢 | `init_db()` called before `--chat` mode | `/opp` no longer fails on fresh installs |
| 🟢 | Retry jitter on DuckDuckGo | Prevents thundering-herd when multiple instances run |
| 🟢 | `requirements.txt` upper-bound pins | No surprise breakage from crewai 2.x |

## How It Works

```
DuckDuckGo (primary, no key) ──┐
HN Algolia (free) ──────────────┤  [concurrent]
Reddit JSON (free) ─────────────┼──► 3-Layer Dedup ──► Multi-LLM Score ──► Telegram Alert
GitHub Trending (free) ─────────┤
Tavily/Serper (optional) ───────┘

Arai Chatbot (CrewAI):
  /deep <topic>    → Researcher + Fact Checker
  /compare <tools> → Researcher + Analyst + Writer
  /deals           → Researcher + Analyst + Fact Checker
  <anything>       → Persistent conversation (memory survives restarts)
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure API keys
cp .env.example .env
# Edit .env — at minimum set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

# 3. Run
python main.py              # Full pipeline (scrape + score + alert)
python main.py --chat       # Start Arai chatbot
```

### API Keys

| Key | Required? | Purpose |
|-----|-----------|---------|
| `TELEGRAM_BOT_TOKEN` | For alerts & chat | Get from @BotFather |
| `TELEGRAM_CHAT_ID` | For alerts & security | Your Telegram chat ID |
| `GROQ_API_KEY` | Optional | LLM scoring (fast, free tier) |
| `MISTRAL_API_KEY` | Optional | Best quality chat (preferred for Arai) |
| `GEMINI_API_KEY` | Optional | Google LLM fallback |
| `TOGETHER_API_KEY` | Optional | Another LLM fallback |
| `OPENROUTER_API_KEY` | Optional | Free models available |
| `TAVILY_API_KEY` | Optional | Extra search source |
| `SERPER_API_KEY` | Optional | Google search fallback |

**Minimum to run**: zero API keys — heuristic scoring works without any.  
**Best experience**: `MISTRAL_API_KEY` + `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

## CLI Commands

```bash
python main.py                  # Full pipeline
python main.py --scrape         # Only scrape
python main.py --score          # Only score existing items
python main.py --alert          # Only send Telegram alerts
python main.py --chat           # Start Arai chatbot (runs forever)
python main.py --stats          # Show database stats
python main.py --test-telegram  # Test Telegram connection
```

## Arai Chatbot Commands

```
🔥 Multi-Agent (CrewAI):
  /deep <topic>              — Deep research on any AI tool
  /compare <tool1 vs tool2>  — Head-to-head comparison
  /deals                     — Find best current deals

📊 Opportunity Engine:
  /opp                       — Search for new AI deals now
  /stats                     — Database stats

💬 Chat:
  /clear                     — Clear conversation history (persistent)
  /help                      — Show all commands
  (anything else)            — Chat with Arai (memory persists across restarts)
```

## File Structure

```
opportunity-engine/
├── main.py           # Orchestrator CLI
├── llm_client.py     # ★ NEW: shared LLM backend (replaces 3× duplicated init)
├── scrapers.py       # Concurrent DuckDuckGo + HN + Reddit + GitHub scrapers
├── scorer.py         # Heuristic + LLM + Rust scoring engine
├── db.py             # SQLite persistence (opportunities + chat history)
├── telegram_alert.py # Telegram push notifications
├── chat_bot.py       # Arai: conversational Telegram chatbot
├── crew_agents.py    # CrewAI multi-agent system (singleton agents)
├── rust_scorer/      # Optional Rust PyO3 extension
│   ├── Cargo.toml
│   └── src/lib.rs
├── build_rust.sh     # Rust build script
├── .env.example      # Environment template
├── requirements.txt  # Pinned dependencies
└── README.md
```

## Scoring Formula

| Signal | Points |
|--------|--------|
| Free tool or open source | +4 |
| Free credits ≥ $20 | +4 |
| Free credits $5–$19 | +2 |
| New model release | +3 |
| Posted < 7 days ago | +2 |
| Posted 7–14 days ago | +1 |
| ≥ 100 upvotes/points | +1 |
| Incredible value bonus (credits ≥ $50) | +2 |
| **Max score** | **16** |

Alert threshold default: **7** (configurable via `ALERT_THRESHOLD` in `.env`).

## Optional Rust Extension

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
chmod +x build_rust.sh && ./build_rust.sh
```

Speeds up scoring and hashing. Python fallback is automatic if not built.

## Free Deployment (Render Cron Job)

```bash
# Push to GitHub
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/opportunity-engine.git
git push -u origin main

# Render → New → Cron Job
# Build Command:  pip install -r requirements.txt
# Start Command:  python main.py
# Schedule:       0 */6 * * *   (every 6 hours)
# Add env vars:   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MISTRAL_API_KEY

# For Arai chatbot (always-on) → use a Render Web Service instead:
# Start Command:  python main.py --chat
```
