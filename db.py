"""
db.py — Supabase (PostgreSQL) persistence layer.
3 tables: opportunities, hackathons, leads + chat_history.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DB_URL = os.getenv("SUPABASE_DB_URL", "")

_SCHEMA = [
    # ── Opportunities (AI tools / free credits) ───────────────────────────────
    """CREATE TABLE IF NOT EXISTS opportunities (
        id                BIGSERIAL PRIMARY KEY,
        dedup_hash        TEXT      UNIQUE NOT NULL,
        fuzzy_title_hash  TEXT      NOT NULL DEFAULT '',
        normalized_url    TEXT      NOT NULL DEFAULT '',
        title             TEXT      NOT NULL,
        url               TEXT      NOT NULL,
        source            TEXT      NOT NULL,
        posted_at         TEXT      NOT NULL,
        scraped_at        TEXT      NOT NULL,
        raw_text          TEXT,
        score             INTEGER   NOT NULL DEFAULT 0,
        scored            INTEGER   NOT NULL DEFAULT 0,
        score_breakdown   TEXT,
        summary           TEXT,
        is_free           INTEGER   NOT NULL DEFAULT 0,
        credits_value_usd INTEGER   NOT NULL DEFAULT 0,
        alerted           INTEGER   NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_opp_score   ON opportunities(score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_opp_posted  ON opportunities(posted_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_opp_alerted ON opportunities(alerted)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_opp_norm_url ON opportunities(normalized_url) WHERE normalized_url <> ''",

    # ── Hackathons ────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS hackathons (
        id          BIGSERIAL PRIMARY KEY,
        dedup_hash  TEXT      UNIQUE NOT NULL,
        title       TEXT      NOT NULL,
        url         TEXT      NOT NULL,
        organizer   TEXT      NOT NULL DEFAULT '',
        prize_usd   INTEGER   NOT NULL DEFAULT 0,
        deadline    TEXT      NOT NULL DEFAULT '',
        description TEXT,
        is_free     INTEGER   NOT NULL DEFAULT 1,
        source      TEXT      NOT NULL,
        scraped_at  TEXT      NOT NULL,
        score       INTEGER   NOT NULL DEFAULT 0,
        scored      INTEGER   NOT NULL DEFAULT 0,
        alerted     INTEGER   NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_hack_score   ON hackathons(score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_hack_alerted ON hackathons(alerted)",

    # ── Local Business Leads ──────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS leads (
        id             BIGSERIAL PRIMARY KEY,
        dedup_hash     TEXT      UNIQUE NOT NULL,
        business_name  TEXT      NOT NULL,
        url            TEXT      NOT NULL DEFAULT '',
        city           TEXT      NOT NULL DEFAULT '',
        category       TEXT      NOT NULL DEFAULT '',
        description    TEXT,
        phone          TEXT      NOT NULL DEFAULT '',
        rating         REAL      NOT NULL DEFAULT 0,
        source         TEXT      NOT NULL,
        scraped_at     TEXT      NOT NULL,
        score          INTEGER   NOT NULL DEFAULT 0,
        scored         INTEGER   NOT NULL DEFAULT 0,
        pitch          TEXT      NOT NULL DEFAULT '',
        alerted        INTEGER   NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_leads_score   ON leads(score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_leads_city    ON leads(city)",
    "CREATE INDEX IF NOT EXISTS idx_leads_alerted ON leads(alerted)",

    # ── Chat history ──────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS chat_history (
        id         BIGSERIAL PRIMARY KEY,
        chat_id    BIGINT    NOT NULL,
        role       TEXT      NOT NULL,
        content    TEXT      NOT NULL,
        created_at TEXT      NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chat ON chat_history(chat_id, created_at DESC)",
]


@contextmanager
def get_conn():
    if not DB_URL:
        raise RuntimeError(
            "SUPABASE_DB_URL not set.\n"
            "Go to: Supabase → Settings → Database → Connection string → URI"
        )
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        for stmt in _SCHEMA:
            cur.execute(stmt)

        # Migrations for old DBs
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='opportunities'")
        cols = {r["column_name"] for r in cur.fetchall()}
        if "scored" not in cols:
            cur.execute("ALTER TABLE opportunities ADD COLUMN scored INTEGER NOT NULL DEFAULT 0")
            cur.execute("UPDATE opportunities SET scored=1 WHERE score > 0")

    print("[db] ready — Supabase (opportunities + hackathons + leads + chat)")


# ── Opportunities ─────────────────────────────────────────────────────────────

def insert_opportunity(item: dict) -> bool:
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO opportunities
                    (dedup_hash, fuzzy_title_hash, normalized_url,
                     title, url, source, posted_at, scraped_at, raw_text)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (dedup_hash) DO NOTHING
            """, (
                item["dedup_hash"], item.get("fuzzy_title_hash", ""),
                item.get("normalized_url", ""), item["title"], item["url"],
                item["source"], item["posted_at"],
                datetime.now(timezone.utc).isoformat(), item.get("raw_text", ""),
            ))
            return cur.rowcount > 0
    except psycopg2.IntegrityError:
        return False


def update_score(opp_id, score, breakdown, summary, is_free, credits_value_usd):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE opportunities SET score=%s, scored=1, score_breakdown=%s,
            summary=%s, is_free=%s, credits_value_usd=%s WHERE id=%s
        """, (score, breakdown, summary, int(is_free), credits_value_usd, opp_id))


def get_unscored() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM opportunities WHERE scored=0 ORDER BY posted_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_alertable(threshold: int) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM opportunities WHERE score>=%s AND alerted=0 AND scored=1
            ORDER BY score DESC, posted_at DESC
        """, (threshold,))
        return [dict(r) for r in cur.fetchall()]


def mark_alerted(opp_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE opportunities SET alerted=1 WHERE id=%s", (opp_id,))


def get_stats() -> dict:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM opportunities"); total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM opportunities WHERE scored=1"); scored = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM opportunities WHERE alerted=1"); alerted = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM opportunities WHERE score>=7"); high = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM hackathons"); h_total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM leads"); l_total = cur.fetchone()["n"]
        return {
            "total": total, "scored": scored, "alerted": alerted, "high_score": high,
            "hackathons": h_total, "leads": l_total,
        }


def load_dedup_data() -> tuple[set, set, set]:
    from scrapers import _normalize_url, _fuzzy_hash
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT dedup_hash, url, title FROM opportunities")
            rows = cur.fetchall()
        return (
            {r["dedup_hash"] for r in rows if r["dedup_hash"]},
            {_normalize_url(r["url"]) for r in rows if r["url"]},
            {_fuzzy_hash(r["title"]) for r in rows if r["title"]},
        )
    except Exception:
        return set(), set(), set()


# ── Hackathons ─────────────────────────────────────────────────────────────────

def insert_hackathon(item: dict) -> bool:
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO hackathons
                    (dedup_hash, title, url, organizer, prize_usd, deadline,
                     description, is_free, source, scraped_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (dedup_hash) DO NOTHING
            """, (
                item["dedup_hash"], item["title"], item["url"],
                item.get("organizer", ""), item.get("prize_usd", 0),
                item.get("deadline", ""), item.get("description", ""),
                int(item.get("is_free", True)),
                item["source"], item["scraped_at"],
            ))
            return cur.rowcount > 0
    except psycopg2.IntegrityError:
        return False


def update_hackathon_score(hack_id: int, score: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE hackathons SET score=%s, scored=1 WHERE id=%s", (score, hack_id))


def get_unscored_hackathons() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM hackathons WHERE scored=0 ORDER BY scraped_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_alertable_hackathons(threshold: int = 5) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM hackathons WHERE score>=%s AND alerted=0 AND scored=1
            ORDER BY score DESC
        """, (threshold,))
        return [dict(r) for r in cur.fetchall()]


def mark_hackathon_alerted(hack_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE hackathons SET alerted=1 WHERE id=%s", (hack_id,))


# ── Leads ──────────────────────────────────────────────────────────────────────

def insert_lead(item: dict) -> bool:
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO leads
                    (dedup_hash, business_name, url, city, category,
                     description, phone, rating, source, scraped_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (dedup_hash) DO NOTHING
            """, (
                item["dedup_hash"], item["business_name"], item.get("url", ""),
                item.get("city", ""), item.get("category", ""),
                item.get("description", ""), item.get("phone", ""),
                item.get("rating", 0.0), item["source"], item["scraped_at"],
            ))
            return cur.rowcount > 0
    except psycopg2.IntegrityError:
        return False


def update_lead_score(lead_id: int, score: int, pitch: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE leads SET score=%s, scored=1, pitch=%s WHERE id=%s",
            (score, pitch, lead_id)
        )


def get_unscored_leads() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM leads WHERE scored=0 ORDER BY scraped_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_alertable_leads(threshold: int = 7) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM leads WHERE score>=%s AND alerted=0 AND scored=1
            ORDER BY score DESC
        """, (threshold,))
        return [dict(r) for r in cur.fetchall()]


def mark_lead_alerted(lead_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE leads SET alerted=1 WHERE id=%s", (lead_id,))


# ── Chat history ──────────────────────────────────────────────────────────────

def get_chat_history(chat_id: int, limit: int = 60) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT role, content FROM chat_history
            WHERE chat_id=%s ORDER BY created_at DESC LIMIT %s
        """, (chat_id, limit))
        rows = cur.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def append_chat_message(chat_id: int, role: str, content: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO chat_history (chat_id, role, content, created_at) VALUES (%s,%s,%s,%s)",
            (chat_id, role, content, datetime.now(timezone.utc).isoformat()),
        )


def clear_chat_history(chat_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_history WHERE chat_id=%s", (chat_id,))


def chat_history_count(chat_id: int) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM chat_history WHERE chat_id=%s", (chat_id,))
        return cur.fetchone()["n"]
