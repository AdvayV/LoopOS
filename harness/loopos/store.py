"""SQLite persistence for loop records and the ordered event log."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Any


class Store:
    def __init__(self, path: Path) -> None:
        self._lock = Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS loops (
                id TEXT PRIMARY KEY,
                created REAL NOT NULL,
                spec TEXT NOT NULL,
                status TEXT NOT NULL,
                info TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                loop_id TEXT,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                data TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS events_by_loop ON events(loop_id, seq);
            """
        )

    def next_loop_id(self) -> str:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM loops").fetchone()
        return f"l{row[0] + 1}"

    def save_loop(self, loop_id: str, spec: dict[str, Any], status: str, info: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO loops (id, created, spec, status, info) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, info = excluded.info",
                (loop_id, time.time(), json.dumps(spec), status, json.dumps(info)),
            )

    def loops(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM loops ORDER BY created").fetchall()
        return [
            {"id": r["id"], "created": r["created"], "spec": json.loads(r["spec"]),
             "status": r["status"], "info": json.loads(r["info"])}
            for r in rows
        ]

    def event(self, loop_id: str | None, kind: str, message: str, data: dict[str, Any] | None = None) -> int:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (loop_id, ts, kind, message, data) VALUES (?, ?, ?, ?, ?)",
                (loop_id, time.time(), kind, message, json.dumps(data or {})),
            )
        return int(cursor.lastrowid)

    def events(self, loop_id: str | None = None, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE seq > ?"
        params: list[Any] = [after]
        if loop_id:
            query += " AND loop_id = ?"
            params.append(loop_id)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [
            {"seq": r["seq"], "loop_id": r["loop_id"], "ts": r["ts"], "kind": r["kind"],
             "message": r["message"], "data": json.loads(r["data"])}
            for r in reversed(rows)
        ]

    def close(self) -> None:
        with self._lock:
            self._db.close()
