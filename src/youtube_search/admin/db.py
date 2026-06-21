"""SQLite database for admin: whitelist domains, analytics, config."""

import asyncio
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

DB_PATH = Path(os.getenv("ADMIN_DB_PATH", "admin.db"))

# Default PIN (27122002) — hashed immediately; raw value never stored
_DEFAULT_PIN = "27122002"

# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS whitelist_domains (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    domain   TEXT    NOT NULL UNIQUE,
    enabled  INTEGER NOT NULL DEFAULT 1,
    note     TEXT    NOT NULL DEFAULT '',
    added_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS api_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    method          TEXT    NOT NULL,
    path            TEXT    NOT NULL,
    status_code     INTEGER NOT NULL,
    origin_domain   TEXT    NOT NULL DEFAULT '',
    response_ms     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_api_requests_ts ON api_requests (ts);
"""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hash_pin(pin: str, salt: str | None = None) -> str:
    """Return 'salt:hash' string.  PBKDF2-SHA256, 260 000 iterations."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 260_000)
    return f"{salt}:{dk.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    candidate = _hash_pin(pin, salt)
    return secrets.compare_digest(candidate, stored)


# ── Init ─────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create tables and seed default PIN if not yet configured."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)

        # Seed default PIN if missing
        cur = await db.execute(
            "SELECT value FROM admin_config WHERE key = 'pin_hash'"
        )
        row = await cur.fetchone()
        if row is None:
            pin_hash = _hash_pin(_DEFAULT_PIN)
            await db.execute(
                "INSERT INTO admin_config (key, value) VALUES ('pin_hash', ?)",
                (pin_hash,),
            )
        await db.commit()


# ── Config ───────────────────────────────────────────────────────────────────

async def get_config(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT value FROM admin_config WHERE key = ?", (key,)
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def set_config(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO admin_config (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_pin_hash() -> str:
    return await get_config("pin_hash") or _hash_pin(_DEFAULT_PIN)


async def update_pin(new_pin: str) -> None:
    await set_config("pin_hash", _hash_pin(new_pin))


# ── Whitelist ─────────────────────────────────────────────────────────────────

async def list_whitelist() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, domain, enabled, note, added_at FROM whitelist_domains"
            " ORDER BY added_at DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_domain(domain: str, note: str = "") -> dict:
    domain = domain.lower().strip().rstrip("/")
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO whitelist_domains (domain, note, added_at)"
                " VALUES (?, ?, ?)",
                (domain, note, now),
            )
            await db.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Domain '{domain}' already in whitelist")
    return {"domain": domain, "enabled": True, "note": note, "added_at": now}


async def remove_domain(domain: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM whitelist_domains WHERE domain = ?", (domain,)
        )
        await db.commit()
        return cur.rowcount > 0


async def toggle_domain(domain: str, enabled: bool) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE whitelist_domains SET enabled = ? WHERE domain = ?",
            (1 if enabled else 0, domain),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_enabled_domains() -> set[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT domain FROM whitelist_domains WHERE enabled = 1"
        )
        rows = await cur.fetchall()
        return {r[0] for r in rows}


# ── Analytics ─────────────────────────────────────────────────────────────────

async def record_request(
    method: str, path: str, status_code: int,
    origin_domain: str = "", response_ms: int = 0,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO api_requests"
            " (ts, method, path, status_code, origin_domain, response_ms)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, method, path, status_code, origin_domain, response_ms),
        )
        # Keep only last 5000 rows
        await db.execute(
            "DELETE FROM api_requests WHERE id NOT IN"
            " (SELECT id FROM api_requests ORDER BY id DESC LIMIT 5000)"
        )
        await db.commit()


async def get_analytics() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total requests
        cur = await db.execute("SELECT COUNT(*) as n FROM api_requests")
        total = (await cur.fetchone())["n"]

        # Today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur = await db.execute(
            "SELECT COUNT(*) as n FROM api_requests WHERE ts LIKE ?",
            (f"{today}%",),
        )
        today_count = (await cur.fetchone())["n"]

        # Status code breakdown
        cur = await db.execute(
            "SELECT status_code, COUNT(*) as n FROM api_requests"
            " GROUP BY status_code ORDER BY n DESC LIMIT 10"
        )
        status_dist = [dict(r) for r in await cur.fetchall()]

        # Top paths
        cur = await db.execute(
            "SELECT path, COUNT(*) as n FROM api_requests"
            " GROUP BY path ORDER BY n DESC LIMIT 10"
        )
        top_paths = [dict(r) for r in await cur.fetchall()]

        # Recent 50
        cur = await db.execute(
            "SELECT ts, method, path, status_code, origin_domain, response_ms"
            " FROM api_requests ORDER BY id DESC LIMIT 50"
        )
        recent = [dict(r) for r in await cur.fetchall()]

        # Avg response time
        cur = await db.execute(
            "SELECT ROUND(AVG(response_ms)) as avg FROM api_requests"
        )
        avg_ms = (await cur.fetchone())["avg"] or 0

    return {
        "total": total,
        "today": today_count,
        "avg_response_ms": avg_ms,
        "status_distribution": status_dist,
        "top_paths": top_paths,
        "recent": recent,
    }
