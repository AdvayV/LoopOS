"""Durable records for explicitly requested syscalls, independent of model loops."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path


def process_token(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


class Calls:
    def __init__(self, root: Path):
        self.db = sqlite3.connect(root / "calls.db", timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
            workspace TEXT NOT NULL, started REAL NOT NULL, ended REAL,
            pid INTEGER NOT NULL, token TEXT, status TEXT NOT NULL, result TEXT)""")

    def close(self):
        self.db.close()

    def start(self, action: dict, workspace: Path) -> int:
        cursor = self.db.execute(
            "INSERT INTO calls(action, workspace, started, pid, token, status) VALUES(?,?,?,?,?,?)",
            (json.dumps(action), str(workspace), time.time(), os.getpid(),
             process_token(os.getpid()), "running"))
        return cursor.lastrowid

    def finish(self, call_id: int, result: dict, status: str | None = None):
        status = status or ("succeeded" if result["ok"] else "refused" if result.get("refused") else "failed")
        self.db.execute("UPDATE calls SET ended=?, status=?, result=? WHERE id=?",
                        (time.time(), status, json.dumps(result), call_id))

    def get(self, call_id: int | None = None) -> dict | None:
        row = (self.db.execute("SELECT * FROM calls ORDER BY id DESC LIMIT 1").fetchone()
               if call_id is None else self.db.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone())
        if row is None:
            return None
        record = dict(row)
        if record["status"] == "running" and process_token(record["pid"]) != record["token"]:
            result = {"ok": False, "output": "Execution was interrupted; no completed result was recorded. "
                      "A mutating call may have partially changed the workspace."}
            # Do not overwrite a result committed by the executor since our SELECT.
            self.db.execute("UPDATE calls SET ended=?,status='interrupted',result=? WHERE id=? AND status='running'",
                            (time.time(), json.dumps(result), record["id"]))
            return self.get(record["id"])
        record["action"] = json.loads(record["action"])
        record["result"] = json.loads(record["result"]) if record["result"] else None
        return record
