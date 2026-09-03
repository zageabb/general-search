from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / "instance" / "retrieval-cache.sqlite3"
LOCK = threading.Lock()


def cache_max_age_days() -> int:
    try:
        value = int(os.environ.get("GENERAL_SEARCH_CACHE_DAYS", "14"))
    except (TypeError, ValueError):
        value = 14
    return max(0, min(90, value))


def connect(path=DEFAULT_CACHE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            url TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
    """)
    return connection


def load_page(url, max_age_days=None, allow_stale=False, path=DEFAULT_CACHE):
    path = Path(path)
    if not path.exists():
        return None
    max_age_days = cache_max_age_days() if max_age_days is None else max_age_days
    with closing(connect(path)) as connection:
        row = connection.execute(
            "SELECT fetched_at, payload_json FROM pages WHERE url = ?", (url,)
        ).fetchone()
    if not row:
        return None
    try:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if not allow_stale and fetched_at < datetime.now(timezone.utc) - timedelta(days=max_age_days):
            return None
        payload = json.loads(row["payload_json"])
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    payload["cache_status"] = "stale" if allow_stale else "fresh"
    payload["cached_at"] = row["fetched_at"]
    return payload


def store_page(url, payload, path=DEFAULT_CACHE):
    if not payload.get("text"):
        return
    stored = {
        key: payload.get(key, "")
        for key in ("text", "url", "content_type", "published_at")
    }
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with LOCK, closing(connect(path)) as connection:
        with connection:
            connection.execute("""
                INSERT INTO pages (url, fetched_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    payload_json=excluded.payload_json
            """, (url, fetched_at, json.dumps(stored, ensure_ascii=False)))
