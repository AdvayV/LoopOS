from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any


class EventStore:
    """SQLite event store for simulation history and research exports."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'created'
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    loop_id TEXT,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                """
            )

    def create_run(self, started_at: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'created')",
                (started_at,),
            )
            return int(cursor.lastrowid)

    def set_run_status(self, run_id: int, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))

    def add_event(self, run_id: int, event: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events
                    (run_id, sequence, timestamp, event_type, loop_id, message, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event["sequence"],
                    event["timestamp"],
                    event["type"],
                    event.get("loop_id"),
                    event["message"],
                    json.dumps(event.get("payload", {})),
                ),
            )

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id, r.started_at, r.status, COUNT(e.id) AS event_count
                FROM runs r LEFT JOIN events e ON e.run_id = r.id
                GROUP BY r.id ORDER BY r.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, run_id: int, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, timestamp, event_type AS type, loop_id, message, payload
                FROM events WHERE run_id = ? ORDER BY sequence ASC LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event["payload"])
            events.append(event)
        return events

