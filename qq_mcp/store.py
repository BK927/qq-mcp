"""Bounded event retention and durable at-most-once send reservations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import QQError


class Store:
    def __init__(self, path: Path, retention: int = 86400, max_messages: int = 2000):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=5)
        if os.name != "nt":
            os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                provider TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
                message_id TEXT NOT NULL, author TEXT NOT NULL, content TEXT NOT NULL,
                received REAL NOT NULL, timestamp TEXT NOT NULL,
                PRIMARY KEY(provider, kind, target, message_id)
            );
            CREATE INDEX IF NOT EXISTS event_target ON events(provider,kind,target,received);
            CREATE TABLE IF NOT EXISTS sends (
                key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reply_sequences (
                target TEXT NOT NULL, message_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                created REAL NOT NULL, PRIMARY KEY(target, message_id)
            );
        """)
        self.retention = retention
        self.max_messages = max_messages
        self.prune()

    def prune(self) -> None:
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM events WHERE received < ?", (now - self.retention,))
            self.db.execute("""DELETE FROM events WHERE rowid NOT IN
                (SELECT rowid FROM events ORDER BY received DESC LIMIT ?)""", (self.max_messages,))
            self.db.execute("DELETE FROM sends WHERE created < ?", (now - 604800,))
            self.db.execute("DELETE FROM reply_sequences WHERE created < ?", (now - 86400,))

    def insert(self, provider: str, kind: str, target: str, message_id: str,
               author: str, content: str, timestamp: str = "") -> bool:
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?)", (
                provider, kind, target, message_id[:128], author[:128], content[:8000],
                time.time(), timestamp[:64],
            ))
        self.prune()
        return cursor.rowcount == 1

    def context(self, provider: str, kind: str, target: str, limit: int) -> list[dict[str, Any]]:
        self.prune()
        rows = self.db.execute("""SELECT message_id,author,content,received,timestamp FROM events
            WHERE provider=? AND kind=? AND target=? ORDER BY received DESC LIMIT ?""",
            (provider, kind, target, limit)).fetchall()
        return [dict(zip(("message_id", "author", "content", "received_at", "timestamp"), row,
                         strict=True)) for row in reversed(rows)]

    def reserve(self, key: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Reserve BEFORE dispatch; pending/unknown sends never automatically retry."""
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.prune()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT fingerprint,status,result FROM sends WHERE key=?", (key,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise QQError("Idempotency key is already bound to a different request")
                self.db.commit()
                return {"status": row[1], "replayed": True, **json.loads(row[2])}
            count = self.db.execute("SELECT COUNT(*) FROM sends").fetchone()[0]
            if count >= 10000:
                raise QQError("Send ledger is full; wait for seven-day retention expiry")
            self.db.execute("INSERT INTO sends VALUES(?,?,?,?,?)", (
                key, fingerprint, "unknown", json.dumps({"detail": "Dispatch reserved; do not retry with a new key until delivery is checked"}), time.time(),
            ))
            self.db.commit()
            return None
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, key: str, status: str, result: dict[str, Any]) -> dict[str, Any]:
        with self.db:
            self.db.execute("UPDATE sends SET status=?,result=? WHERE key=?",
                            (status, json.dumps(result), key))
        return {"status": status, "replayed": False, **result}

    def next_sequence(self, target: str, message_id: str) -> int:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT sequence FROM reply_sequences WHERE target=? AND message_id=?",
                                  (target, message_id)).fetchone()
            sequence = row[0] + 1 if row else 1
            self.db.execute("INSERT OR REPLACE INTO reply_sequences VALUES(?,?,?,?)",
                            (target, message_id, sequence, time.time()))
            self.db.commit()
            return sequence
        except BaseException:
            self.db.rollback()
            raise

    def forget(self, provider: str, kind: str, target: str) -> int:
        with self.db:
            cursor = self.db.execute("DELETE FROM events WHERE provider=? AND kind=? AND target=?",
                                     (provider, kind, target))
        return cursor.rowcount

    def has_message(self, provider: str, kind: str, target: str, message_id: str) -> bool:
        self.prune()
        return self.db.execute("""SELECT 1 FROM events
            WHERE provider=? AND kind=? AND target=? AND message_id=?""",
            (provider, kind, target, message_id)).fetchone() is not None

    def close(self) -> None:
        self.db.close()
