"""SQLite storage for chat sessions and messages."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,            -- 'user' or 'assistant'
    content TEXT NOT NULL,
    metadata TEXT,                 -- JSON string, assistant messages only
    created_at TEXT NOT NULL       -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    def init(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def ping(self) -> bool:
        with self.connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return True

    # -- sessions ---------------------------------------------------------
    def get_system_prompt(self, session_id: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row["system_prompt"] if row else None

    def session_exists(self, session_id: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
        return row is not None

    # -- messages ---------------------------------------------------------
    def get_history(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return messages oldest first. With ``limit`` only the most recent N."""
        with self.connection() as conn:
            if limit is not None:
                rows = conn.execute(
                    "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? "
                    "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        history = []
        for row in rows:
            item: dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["metadata"]:
                item["metadata"] = json.loads(row["metadata"])
            history.append(item)
        return history

    def save_exchange(
        self,
        session_id: str,
        user_message: str,
        assistant_reply: str,
        metadata: dict[str, Any],
        system_prompt: str | None,
    ) -> None:
        """Persist a successful exchange atomically (both rows or neither)."""
        now = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt)",
                (session_id, system_prompt, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'user', ?, NULL, ?)",
                (session_id, user_message, now),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (session_id, assistant_reply, json.dumps(metadata), _now()),
            )
