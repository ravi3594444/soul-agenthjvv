"""
scrapers.py — DuckDuckGo-first web search + HN + Reddit + GitHub Trending.

Improvements vs v2:
  - All scrapers run concurrently (ThreadPoolExecutor) — ~4× faster
  - Dedup data loaded in a single DB query (was 3 separate connections)
  - Year/month in Tavily & Serper queries is now dynamic (was hardcoded "2025")
  - Retry jitter prevents thundering-herd against DDG
  - Rust dead-import removed (rust_hash was imported but never called)
  - _load_existing_* helpers replaced by db.load_dedup_data()
"""

import hashlib
import os
import re
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

RECENCY_DAYS = int(os.getenv("RECENCY_DAYS", "7"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Optional Rust acceleration ─────────────────────────────────────────────────
try:
    from scorer_core import contains_any_keyword as _rust_keywords
    _USE_RUST = True
    print("[scrapers] Rust core loaded")
except ImportError:
    _USE_RUST = False
    print("[scrapers] Rust core not built — using Python fallback")

# ── API keys ───────────────────────────────────────────────────────────────────
TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")
SERPER_KEY  = os.getenv("SERPER_API_KEY", "")

# ── Keyword lists ──────────────────────────────────────────────────────────────
AI_KEYWORDS = [
    "ai", "llm", "gpt", "claude", "gemini", "agent", "model", "free",
    "credits", "open source", "open-source", "launch", "release", "api",
    "chatbot", "rag", "embedding", "fine-tun", "vector", "mcp",
    "copilot", "openai", "anthropic", "google ai", "mistral", "meta ai",
    "stability", "hugging face", "langchain", "llamaindex", "autogen",
]

DDG_QUERIES = [
    'AI tool free credits "{year}"',
    'free AI API credits launch {year}',
    '"free tier" AI tool launch {month} {year}',
    'AI startup free credits offer {year}',
    '"free credits" AI model API {year}',
    'new AI tool completely free launch {year}',
    '"$100 free credits" AI {year}',
    '"$50 free credits" AI {year}',
    'open source AI tool launch free {year}',
    'free AI coding tool launch {month} {year}',
]


# ── URL / title normalisation ─────────────────────────────────────────────────

_TRACKING_PARAMS = frozenset([
    "utm_source", "utm_medium", "utm_campaign", "utm_content",
    "utm_term", "ref", "referrer", "fbclid", "gclid", "source",
    "sr", "s", "trk", "trkCampaign",
])


def _normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        clean = [
            p for p in (parsed.query.split("&") if parsed.query else [])
            if "=" not in p or p.split("=")[0].lower() not in _TRACKING_PARAMS
        ]
        query = "&".join(clean)
        path  = parsed.path.rstrip("/") or "/"
        return f"{parsed.scheme}://{parsed.netloc.lower()}{path}{'?' + query if query else ''}"
    except Exception:
        return url.lower()


def _normalize_title(title: str) -> str:
    t = title.lower().strip()
    for old, new in [
        ("\u2010", "-"), ("\u2011", "-"), ("\u2012", "-"), ("\u2013", "-"),
        ("\u2014", "-"), ("\u2015", "-"), ("\u2018", "'"), ("\u2019", "'"),
        ("\u201c", '"'), ("\u201d", '"'), ("\u2026", "..."),
    ]:
        t = t.replace(old, new)
    t = re.sub(r"[^a-z0-9 \-]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _fuzzy_hash(title: str) -> str:
    norm = _normalize_title(title)[:150]
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


def _hash(title: str, url: str) -> str:
    combined = _normalize_url(url)[:200] + "|" + _normalize_title(title)[:200]
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def _is_recent(posted_at: datetime) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return posted_at >= cutoff


def _looks_relevant(text: str) -> bool:
    if _USE_RUST:
        return _rust_keywords(text, AI_KEYWORDS)
    t = text.lower()
    return any(kw in t for kw in AI_KEYWORDS)


def _extract_date_from_text(text: str, url: str = "") -> datetime:
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{2}/\d{2}/\d{4})",
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* \d{1,2},? \d{4}",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                from dateutil import parser as dp
                return dp.parse(m.group(0)).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    # No date found — return now() so item passes recency; scored with no recency
    # bonus since days_ago will be 0 but that's handled in scorer.
    return datetime.now(timezone.utc)


def _enrich_item(item: dict) -> dict:
    item["fuzzy_title_hash"] = _fuzzy_hash(item.get("title", ""))
    item["normalized_url"]   = _normalize_url(item.get("url", ""))
    return item


# ── Dedup filter ──────────────────────────────────────────────────────────────

def _dedup_against_db(
    items: list[dict],
    existing_hashes: set,
    existing_urls: set,
    existing_fuzzy: set,
) -> tuple[list[dict], int]:
    result, skipped = [], 0
    for item in items:
        item = _enrich_item(item)
        h, nu, fz = item["dedup_hash"], item["normalized_url"], item["fuzzy_title_hash"]
        if h in existing_hashes or nu in existing_urls or fz in existing_fuzzy:
            skipped += 1
            continue
        result.append(item)
        existing_hashes.add(h)
        existing_urls.add(nu)
        existing_fuzzy.add(fz)
    return result, skipped


# ── DuckDuckGo ────────────────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = 15) -> list[dict]:
    """HTML search with jittered retry and optional df=w filter."""
    items = []
    attempts = [
        {"q": query, "df": "w", "num": max_results},
        {"q": query, "num": max_results},
    ]
    for idx, params in enumerate(attempts):
        try:
            r = requests.get(
                "https://html.duckduckgo.com/html/",
                params=params, headers=HEADERS, timeout=20,
            )
            if r.status_code in (403, 429):
                print(f"[ddg] rate limited ({r.status_code}), attempt {idx + 1}")
                if idx == 0:
                    time.sleep(2 + random.uniform(0, 1.5))
                    continue
                break
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for result in soup.select(".result")[:max_results]:
                title_el   = result.select_one(".result__title")
                snippet_el = result.select_one(".result__snippet")
                link_el    = result.select_one(".result__url")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href  = link_el.get("href", "") if link_el else ""
                if "uddg=" in href:
                    qs  = parse_qs(urlparse(href).query)
                    url = unquote(qs.get("uddg", [href])[0])
                else:
                    url = href
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                if title and url:
                    items.append({
                        "title": title, "url": url, "snippet": snippet,
                        "raw_text": f"{title} — {snippet}",
                    })
            if items:
                break
        except requests.exceptions.Timeout:
            print(f"[ddg] timeout attempt {idx + 1}")
            continue
        except Exception as e:
            print(f"[ddg] error: {e}")
            if idx == 0:
                time.sleep(1 + random.uniform(0, 1))
                continue
            break
    return items


def _ddg_instant_api(query: str, max_results: int = 5) -> list[dict]:
    items = []
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers=HEADERS, timeout=10,
        )
        data = r.json()
        if data.get("Abstract") and data.get("AbstractURL"):
            items.append({
                "title":    data.get("Heading") or data["Abstract"][:80],
                "url":      data["AbstractURL"],
                "snippet":  data["Abstract"][:300],
                "raw_text": f"{data.get('Heading', '')} — {data['Abstract'][:300]}",
            })
        for topic in data.get("RelatedTopics", [])[:5]:
            if isinstance(topic, dict):
                t_text = topic.get("Text", "")
                t_url  = topic.get("FirstURL", "")
                if t_text and t_url and _looks_relevant(t_text):
                    items.append({
                        "title": t_text[:100], "url": t_url,
                        "snippet": t_text[:300], "raw_text": t_text[:500],
                    })
    except Exception as e:
        print(f"[ddg] instant API error: {e}")
    return items


def scrape_duckduckgo() -> list[dict]:
    items = []
    now        = datetime.now(timezone.utc)
    month_name = now.strftime("%B")
    year       = now.strftime("%Y")
    queries    = [q.format(year=year, month=month_name) for q in DDG_QUERIES]

    print(f"[scrape] DuckDuckGo — {len(queries)} queries ...")
    seen = set()
    for q in queries:
        for r in _ddg_instant_api(q) + _ddg_search(q, max_results=10):
            h = _hash(r["title"], r["url"])
            if h in seen:
                continue
            seen.add(h)
            if not _looks_relevant(r["title"] + " " + r.get("snippet", "")):
                continue
            posted = _extract_date_from_text(r.get("raw_text", ""), r["url"])
            if not _is_recent(posted):
                continue
            items.append({
                "title": r["title"], "url": r["url"],
                "source": "duckduckgo", "posted_at": posted.isoformat(),
                "raw_text": r.get("raw_text", r["title"]), "dedup_hash": h,
            })

    print(f"[scrape] DuckDuckGo → {len(items)} items")
    return items


# ── Tavily ─────────────────────────────────────────────────────────────────────

def scrape_tavily() -> list[dict]:
    if not TAVILY_KEY:
        return []
    year  = datetime.now().year
    items = []
    queries = [
        f"new AI tool free credits launch {year}",
        f"free AI API tier credits {year}",
        f"open source AI model release this week {year}",
    ]
    print("[scrape] Tavily ...")
    for q in queries:
        try:
            r = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_KEY, "query": q, "search_depth": "basic",
                      "max_results": 10, "include_answer": False, "topic": "news"},
                timeout=15,
            )
            r.raise_for_status()
            for res in r.json().get("results", []):
                title, url, content = res.get("title",""), res.get("url",""), res.get("content","")
                if not title or not url:
                    continue
                if not _looks_relevant(title + " " + content[:200]):
                    continue
                posted = _extract_date_from_text(content[:200], url)
                if not _is_recent(posted):
                    continue
                items.append({
                    "title": title, "url": url, "source": "tavily",
                    "posted_at": posted.isoformat(),
                    "raw_text": content[:800], "dedup_hash": _hash(title, url),
                })
        except Exception as e:
            print(f"[tavily] failed: {e}")
    print(f"[scrape] Tavily → {len(items)} items")
    return items


# ── Serper ─────────────────────────────────────────────────────────────────────

def scrape_serper() -> list[dict]:
    if not SERPER_KEY:
        return []
    year  = datetime.now().year
    items = []
    queries = [
        f'new AI tool "free credits" OR "free tier" launch {year}',
        f'site:reddit.com AI tool free credits launch {year}',
        'site:news.ycombinator.com AI free credits open source',
    ]
    print("[scrape] Serper ...")
    for q in queries:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                json={"q": q, "tbs": "qdr:w", "num": 10},
                headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"},
                timeout=15,
            )
            r.raise_for_status()
            for org in r.json().get("organic", []):
                title, url   = org.get("title",""), org.get("link","")
                snippet, date = org.get("snippet",""), org.get("date","")
                if not title or not url or not _looks_relevant(title + " " + snippet):
                    continue
                posted = _extract_date_from_text(date + " " + snippet, url)
                if not _is_recent(posted):
                    continue
                items.append({
                    "title": title, "url": url, "source": "serper",
                    "posted_at": posted.isoformat(),
                    "raw_text": snippet, "dedup_hash": _hash(title, url),
                })
        except Exception as e:
            print(f"[serper] failed: {e}")
    print(f"[scrape] Serper → {len(items)} items")
    return items


# ── Hacker News ────────────────────────────────────────────────────────────────

def scrape_hn() -> list[dict]:
    items = []
    cutoff_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)).timestamp()
    )
    queries = ["AI free credits", "free AI tool", "open source AI launch",
               "Show HN AI", "LLM free tier"]
    print("[scrape] Hacker News ...")
    for q in queries:
        try:
            r = requests.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": q, "tags": "story",
                        "numericFilters": f"created_at_i>{cutoff_ts}",
                        "hitsPerPage": 20},
                headers=HEADERS, timeout=15,
            )
            r.raise_for_status()
            for hit in r.json().get("hits", []):
                url   = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                title = hit.get("title") or ""
                if not title or not _looks_relevant(title):
                    continue
                posted = datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc)
                if not _is_recent(posted):
                    continue
                items.append({
                    "title": title, "url": url, "source": "hackernews",
                    "posted_at": posted.isoformat(),
                    "raw_text": (
                        f"{title} | points: {hit.get('points', 0)} "
                        f"| comments: {hit.get('num_comments', 0)}"
                    ),
                    "dedup_hash": _hash(title, url),
                })
        except Exception as e:
            print(f"[hn] '{q}' failed: {e}")
    print(f"[scrape] HN → {len(items)} items")
    return items


# ── Reddit ─────────────────────────────────────────────────────────────────────

def scrape_reddit() -> list[dict]:
    items = []
    subs  = ["LocalLLaMA", "singularity", "SideProject", "artificial",
             "OpenAI", "MachineLearning"]
    print("[scrape] Reddit ...")
    for sub in subs:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/new.json",
                params={"limit": 30}, headers=HEADERS, timeout=15,
            )
            r.raise_for_status()
            for child in r.json().get("data", {}).get("children", []):
                d      = child["data"]
                posted = datetime.fromtimestamp(d["created_utc"], tz=timezone.utc)
                if not _is_recent(posted):
                    continue
                title = d.get("title", "")
                url   = (d.get("url_overridden_by_dest")
                         or f"https://reddit.com{d.get('permalink', '')}")
                if not _looks_relevant(title + " " + d.get("selftext", "")[:300]):
                    continue
                items.append({
                    "title": title, "url": url,
                    "source": f"reddit/{sub}",
                    "posted_at": posted.isoformat(),
                    "raw_text": (d.get("selftext", "") or title)[:800],
                    "dedup_hash": _hash(title, url),
                })
        except Exception as e:
            print(f"[reddit] r/{sub} failed: {e}")
    print(f"[scrape] Reddit → {len(items)} items")
    return items


# ── GitHub Trending ────────────────────────────────────────────────────────────

def scrape_github_trending() -> list[dict]:
    items = []
    print("[scrape] GitHub Trending ...")
    try:
        r = requests.get(
            "https://github.com/trending?since=weekly",
            headers=HEADERS, timeout=15,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for repo in soup.select("article.Box-row"):
            link = repo.select_one("h2 a")
            if not link:
                continue
            slug    = link.get("href", "").strip("/")
            url     = f"https://github.com/{slug}"
            desc_el = repo.select_one("p")
            desc    = desc_el.get_text(strip=True) if desc_el else ""
            if not _looks_relevant(slug + " " + desc):
                continue
            title = slug + (f" — {desc}" if desc else "")
            items.append({
                "title": title, "url": url, "source": "github_trending",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "raw_text": desc, "dedup_hash": _hash(slug, url),
            })
    except Exception as e:
        print(f"[github] failed: {e}")
    print(f"[scrape] GitHub → {len(items)} items")
    return items


# ── Master orchestrator ────────────────────────────────────────────────────────

def scrape_all() -> list[dict]:
    """
    Run all scrapers concurrently, dedup against DB, return only RECENT items.
    """
    from db import load_dedup_data  # avoid circular at module load
    existing_hashes, existing_urls, existing_fuzzy = load_dedup_data()

    # Build scraper list
    scrapers = [
        scrape_duckduckgo,
        scrape_hn,
        scrape_reddit,
        scrape_github_trending,
    ]
    if TAVILY_KEY:
        scrapers.append(scrape_tavily)
    if SERPER_KEY:
        scrapers.append(scrape_serper)

    # Run concurrently
    raw_items: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(scrapers), 6)) as pool:
        future_map = {pool.submit(fn): fn.__name__ for fn in scrapers}
        for future in as_completed(future_map):
            try:
                raw_items.extend(future.result())
            except Exception as e:
                print(f"[scrape] {future_map[future]} raised: {e}")

    # Dedup
    skip_total = 0
    new_items:  list[dict] = []
    for item in raw_items:
        filtered, skip = _dedup_against_db(
            [item], existing_hashes, existing_urls, existing_fuzzy
        )
        new_items.extend(filtered)
        skip_total += skip

    # Final hash pass (catch cross-source dupes)
    seen:    set  = set()
    deduped: list = []
    for item in new_items:
        if item["dedup_hash"] not in seen:
            seen.add(item["dedup_hash"])
            deduped.append(item)

    print(
        f"\n[scrape] TOTAL: {len(raw_items)} raw → {len(deduped)} unique "
        f"({skip_total} already-in-DB skipped)"
    )
    return deduped
