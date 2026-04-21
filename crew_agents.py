"""
crew_agents.py — CrewAI multi-agent system.

Improvements vs v2:
  - Agent instances are singletons (created once, reused across crew runs)
  - Uses llm_client._llm_status for provider availability check
  - init_providers() now correctly reflects llm_client state
  - Removed duplicate _test_provider() logic (was redundant with llm_client)
"""

import os
from functools import lru_cache

from crewai import Agent, Task, Crew, Process
from dotenv import load_dotenv

import llm_client

load_dotenv()

# ── Provider status (delegated from llm_client) ───────────────────────────────

_llm_status: dict[str, bool] = {}


def init_providers() -> bool:
    """
    Test all three preferred providers and cache their status.
    Delegates probing to llm_client to avoid duplicating HTTP calls.
    """
    print("\n[crew] Testing LLM providers...")

    for name, env_var, model, endpoint, _ in llm_client._BACKENDS_CONFIG:
        key = os.getenv(env_var, "")
        if not key:
            _llm_status[name] = False
            continue
        b = llm_client._probe(name, model, key, endpoint, min_interval=0)
        _llm_status[name] = b is not None
        tag = "✅" if _llm_status[name] else "❌"
        print(f"  {tag} {name} ({model})")

    working = [k for k, v in _llm_status.items() if v]
    print(f"  [crew] {len(working)} providers working: {', '.join(working) or 'NONE'}\n")
    return len(working) > 0


# ── LLM assignment per agent ──────────────────────────────────────────────────

def _get_researcher_llm() -> str | None:
    """Researcher: Groq (fast, cheap) → Mistral fallback."""
    if _llm_status.get("groq"):   return "groq/llama-3.3-70b-versatile"
    if _llm_status.get("mistral"): return "mistral/mistral-large-latest"
    return None

def _get_analyst_llm() -> str | None:
    """Analyst: Mistral (best reasoning) → Gemini fallback."""
    if _llm_status.get("mistral"): return "mistral/mistral-large-latest"
    if _llm_status.get("gemini"):  return "gemini/gemini-2.0-flash"
    return None

def _get_writer_llm() -> str | None:
    """Writer: Gemini (fast, good formatting) → Mistral fallback."""
    if _llm_status.get("gemini"):  return "gemini/gemini-2.0-flash"
    if _llm_status.get("mistral"): return "mistral/mistral-large-latest"
    return None

def _get_reviewer_llm() -> str | None:
    """Reviewer: Mistral (careful, thorough) → Gemini fallback."""
    if _llm_status.get("mistral"): return "mistral/mistral-large-latest"
    if _llm_status.get("gemini"):  return "gemini/gemini-2.0-flash"
    return None


# ── Singleton agents ──────────────────────────────────────────────────────────
# Agents are created once and reused. Re-instantiating on every crew.kickoff()
# was wasteful and caused unnecessary LLM probe calls.

_agents: dict[str, Agent] = {}


def _get_agent(role_key: str, role: str, goal: str, backstory: str,
               llm_fn) -> Agent:
    if role_key not in _agents:
        _agents[role_key] = Agent(
            role=role, goal=goal, backstory=backstory,
            verbose=True, allow_delegation=False, llm=llm_fn(),
        )
    return _agents[role_key]


def researcher_agent() -> Agent:
    return _get_agent(
        "researcher", "Senior AI Researcher",
        goal=(
            "Find the most relevant, up-to-date information about AI tools, "
            "free credits, open-source launches, and new releases. "
            "Prioritize RECENT sources (last 7 days)."
        ),
        backstory=(
            "You're a veteran tech researcher who lives on HN, Reddit, and Twitter. "
            "You have a sixth sense for spotting real free credit offers vs. marketing BS. "
            "You always verify from multiple sources and flag uncertain claims."
        ),
        llm_fn=_get_researcher_llm,
    )


def analyst_agent() -> Agent:
    return _get_agent(
        "analyst", "AI Industry Analyst",
        goal=(
            "Evaluate AI tools objectively — score their value, compare alternatives, "
            "and separate genuinely useful tools from overhyped garbage."
        ),
        backstory=(
            "You're a no-nonsense analyst who cuts through the hype. You evaluate tools "
            "based on real free credit value, actual usefulness, open-source quality, "
            "and freshness of the opportunity."
        ),
        llm_fn=_get_analyst_llm,
    )


def writer_agent() -> Agent:
    return _get_agent(
        "writer", "Technical Writer",
        goal=(
            "Transform raw research and analysis into crystal-clear, "
            "actionable summaries that help users make quick decisions."
        ),
        backstory=(
            "You write like a senior tech journalist — concise, direct, no fluff. "
            "Your summaries always include: what it is, why it matters, the cost "
            "(or lack thereof), and the direct link."
        ),
        llm_fn=_get_writer_llm,
    )


def reviewer_agent() -> Agent:
    return _get_agent(
        "reviewer", "Fact Checker & Quality Reviewer",
        goal=(
            "Review all output for accuracy, catch false credit claims, "
            "verify that free offers are real and not expired."
        ),
        backstory=(
            "You're the final gatekeeper. You've seen too many 'free credits' posts "
            "that turned out to be expired promotions or require enterprise accounts. "
            "You cross-reference claims and flag anything suspicious."
        ),
        llm_fn=_get_reviewer_llm,
    )


# ── Tasks ─────────────────────────────────────────────────────────────────────

def _deep_research_task(topic: str) -> Task:
    return Task(
        description=(
            f"Research this topic thoroughly: {topic}\n\n"
            "Find out:\n"
            "1. What is it? (one clear paragraph)\n"
            "2. Is it free / open-source / has free credits? Exact amount?\n"
            "3. When was it released or last updated?\n"
            "4. What are the main alternatives?\n"
            "5. Is it useful or just hype?\n"
            "6. Direct link to get started\n\n"
            "Use web search. Prioritize sources from the last 7 days."
        ),
        expected_output=(
            f"Comprehensive brief on '{topic}' with: description, free tier info, "
            "release date, pros/cons, top 2–3 alternatives, verdict, direct link."
        ),
        agent=researcher_agent(),
    )


def _compare_task(tools: str) -> Task:
    return Task(
        description=(
            f"Compare these AI tools head-to-head: {tools}\n\n"
            "For each tool evaluate: pricing, key features, ease of use, "
            "community support, real-world usefulness.\n"
            "Create a clear comparison table and give a recommendation."
        ),
        expected_output=(
            "Structured comparison: quick table (price, features, ease, verdict), "
            "detailed breakdown per tool, best-for recommendations, final pick."
        ),
        agent=analyst_agent(),
    )


def _find_opportunities_task() -> Task:
    from datetime import datetime
    year = datetime.now().year
    return Task(
        description=(
            f"Search for the BEST current AI tool opportunities in {year} — "
            "free credits, open-source launches, new free tiers, startup programs.\n\n"
            "Search for:\n"
            "- New AI tools launched this week with free tiers\n"
            "- Companies giving away free API credits ($10+)\n"
            "- Open-source AI projects trending on GitHub\n"
            "- New free alternatives to expensive AI tools\n\n"
            f"Be specific: exact dollar amounts, tool names, direct links. "
            f"Only include offers CURRENTLY active ({year})."
        ),
        expected_output=(
            "Prioritized list of 5–10 real opportunities. "
            "Each entry: tool name, one-sentence description, free credits / value, "
            "why it's worth trying, direct link. Sorted highest-value first."
        ),
        agent=researcher_agent(),
    )


def _review_and_format_task() -> Task:
    return Task(
        description=(
            "Review the research output from the previous task.\n"
            "Check: Are any claims potentially outdated or false? "
            "Are dollar amounts realistic? Are links likely to work? "
            "Is anything important missing?\n\n"
            "Format the final output as a clean, Telegram-friendly message."
        ),
        expected_output=(
            "Polished, fact-checked summary for Telegram: "
            "bold tool names, emoji for visual hierarchy, "
            "concise sections (2–3 lines max), direct links, brief overview at top."
        ),
        agent=reviewer_agent(),
    )


# ── Crew runners ──────────────────────────────────────────────────────────────

def run_deep_research(topic: str) -> str:
    print(f"[crew] Deep research: {topic}")
    crew = Crew(
        agents=[researcher_agent(), reviewer_agent()],
        tasks=[_deep_research_task(topic), _review_and_format_task()],
        process=Process.sequential,
        verbose=False,
    )
    return str(crew.kickoff())


def run_compare_tools(tools: str) -> str:
    print(f"[crew] Compare: {tools}")
    crew = Crew(
        agents=[researcher_agent(), analyst_agent(), writer_agent()],
        tasks=[
            _compare_task(tools),
            Task(
                description=(
                    f"Based on the comparison of '{tools}', create a final "
                    "clean Telegram-friendly summary with a clear recommendation."
                ),
                expected_output=(
                    "Clean comparison: 📋 one-line per tool, 🏆 winner + reason, "
                    "💰 best free option, 🔍 direct links."
                ),
                agent=writer_agent(),
            ),
        ],
        process=Process.sequential,
        verbose=False,
    )
    return str(crew.kickoff())


def run_find_opportunities() -> str:
    print("[crew] Find opportunities")
    crew = Crew(
        agents=[researcher_agent(), analyst_agent(), reviewer_agent()],
        tasks=[_find_opportunities_task(), _review_and_format_task()],
        process=Process.sequential,
        verbose=False,
    )
    return str(crew.kickoff())
