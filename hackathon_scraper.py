"""
hackathon_scraper.py — Scrapes Devpost, Unstop, DoraHacks for hackathons.
No API key required — uses public endpoints and BeautifulSoup.
"""

import hashlib
import time
import random
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def _dedup_hash(url: str) -> str:
    return hashlib.md5(url.strip().lower().encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Devpost (JSON API — no key needed) ───────────────────────────────────────

def scrape_devpost() -> list[dict]:
    results = []
    try:
        r = requests.get(
            "https://devpost.com/api/hackathons",
            params={
                "challenge_type[]": "online",
                "status[]": "open",
                "order_by": "deadline",
                "per_page": 20,
            },
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        for h in data.get("hackathons", []):
            prize = 0
            prize_str = h.get("prize_amount", "") or ""
            # Extract numeric prize value
            import re
            nums = re.findall(r"[\d,]+", prize_str.replace(",", ""))
            if nums:
                try:
                    prize = int(nums[0])
                except:
                    prize = 0

            deadline = h.get("submission_period_dates", "")
            url = h.get("url", "")
            if not url.startswith("http"):
                url = "https://devpost.com" + url

            results.append({
                "title": h.get("title", ""),
                "url": url,
                "organizer": h.get("organization_name", "Devpost"),
                "prize_usd": prize,
                "deadline": deadline,
                "description": h.get("tagline", ""),
                "is_free": True,
                "source": "devpost",
                "scraped_at": _now(),
                "dedup_hash": _dedup_hash(url),
            })
        print(f"[hackathon] Devpost: {len(results)} hackathons")
    except Exception as e:
        print(f"[hackathon] Devpost error: {e}")
    return results


# ── DuckDuckGo search for hackathons ─────────────────────────────────────────

def scrape_ddg_hackathons() -> list[dict]:
    results = []
    queries = [
        "hackathon 2026 free registration open prize",
        "online hackathon April 2026 open registration",
        "AI hackathon 2026 cash prize open",
        "India hackathon 2026 free participate",
        "web3 hackathon 2026 open registration prize",
    ]
    seen = set()
    try:
        with DDGS() as ddgs:
            for query in queries[:3]:
                try:
                    items = list(ddgs.text(query, max_results=5))
                    for item in items:
                        url = item.get("href", "")
                        if not url or url in seen:
                            continue
                        # Filter for hackathon-related URLs
                        if not any(k in url.lower() for k in ["hackathon", "devpost", "unstop", "dorahacks", "hack"]):
                            # Check title/body
                            title = item.get("title", "").lower()
                            body = item.get("body", "").lower()
                            if not any(k in title + body for k in ["hackathon", "hack ", "prize", "competition"]):
                                continue
                        seen.add(url)
                        results.append({
                            "title": item.get("title", ""),
                            "url": url,
                            "organizer": "Unknown",
                            "prize_usd": 0,
                            "deadline": "",
                            "description": item.get("body", "")[:300],
                            "is_free": True,
                            "source": "duckduckgo",
                            "scraped_at": _now(),
                            "dedup_hash": _dedup_hash(url),
                        })
                    time.sleep(random.uniform(1, 2))
                except Exception as e:
                    print(f"[hackathon] DDG query error: {e}")
                    time.sleep(3)
    except Exception as e:
        print(f"[hackathon] DDG error: {e}")
    print(f"[hackathon] DuckDuckGo: {len(results)} hackathons")
    return results


# ── Unstop scraper ────────────────────────────────────────────────────────────

def scrape_unstop() -> list[dict]:
    results = []
    try:
        r = requests.get(
            "https://unstop.com/api/public/opportunity/search-result",
            params={"opportunity": "hackathons", "per_page": 20, "oppstatus": "open"},
            headers={**HEADERS, "Accept": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("data", {}).get("data", [])
        for item in items:
            url = f"https://unstop.com/{item.get('public_url', '')}"
            prize = 0
            prize_raw = item.get("prizes", {})
            if isinstance(prize_raw, dict):
                prize = prize_raw.get("total", 0) or 0
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "organizer": item.get("organisation", {}).get("name", "Unstop"),
                "prize_usd": prize,
                "deadline": item.get("end_date", ""),
                "description": item.get("tagline", "")[:300],
                "is_free": True,
                "source": "unstop",
                "scraped_at": _now(),
                "dedup_hash": _dedup_hash(url),
            })
        print(f"[hackathon] Unstop: {len(results)} hackathons")
    except Exception as e:
        print(f"[hackathon] Unstop error: {e}")
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def scrape_all_hackathons() -> list[dict]:
    all_items = []
    seen_hashes = set()

    for scraper in [scrape_devpost, scrape_unstop, scrape_ddg_hackathons]:
        try:
            items = scraper()
            for item in items:
                h = item["dedup_hash"]
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    all_items.append(item)
        except Exception as e:
            print(f"[hackathon] scraper error: {e}")

    print(f"[hackathon] Total unique: {len(all_items)}")
    return all_items
