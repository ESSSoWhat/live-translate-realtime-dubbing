"""Shared-disk store for desktop SSO handoff (Wix → app without localhost redirect)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

_DB_PATH = Path(tempfile.gettempdir()) / "live_translate_desktop_handoffs.sqlite3"
_TTL_SEC = 600.0  # 10 minutes


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=15, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS handoffs (
            session_id TEXT PRIMARY KEY,
            api_key TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def put_handoff(session_id: str, api_key: str) -> None:
    """Store api_key for session_id (overwrites existing)."""
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO handoffs (session_id, api_key, created_at) VALUES (?, ?, ?)",
            (session_id, api_key, now),
        )
        conn.execute("DELETE FROM handoffs WHERE created_at < ?", (now - _TTL_SEC,))
        conn.commit()


def take_handoff(session_id: str) -> str | None:
    """Return api_key once and delete the row. Expired rows are purged."""
    now = time.time()
    with _connect() as conn:
        conn.execute("DELETE FROM handoffs WHERE created_at < ?", (now - _TTL_SEC,))
        row = conn.execute(
            "SELECT api_key FROM handoffs WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute("DELETE FROM handoffs WHERE session_id = ?", (session_id,))
        conn.commit()
        return str(row[0])
