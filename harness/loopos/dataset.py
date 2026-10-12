"""Turn worker traces into a fine-tuning dataset (chat-format JSONL)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from . import paths
from .store import Store


def _traces(root: Path) -> Iterator[dict[str, Any]]:
    for trace in sorted((root / "loops").glob("*/trace.jsonl")):
        with open(trace) as lines:
            for line in lines:
                if line.strip():
                    yield json.loads(line)


def keep(record: dict[str, Any], loop_status: str, mode: str) -> bool:
    """`success`: usable steps from loops that reached done, plus any step that raised the score."""
    if mode == "all":
        return True
    if record["refused"] or record["action"].get("action") == "invalid":
        return False
    if record["score_after"] > record["score_before"] or record["event"] == "done":
        return True
    return loop_status == "done" and record["event"] not in {"livelock", "stalled"}


def export(root: Path, out: Path, mode: str = "success") -> dict[str, int]:
    statuses = {loop["id"]: loop["status"] for loop in Store(paths.database(root)).loops()}
    seen: set[str] = set()
    counts = {"read": 0, "written": 0, "duplicates": 0}
    with open(out, "w") as sink:
        for record in _traces(root):
            counts["read"] += 1
            if not keep(record, statuses.get(record["loop"], "unknown"), mode):
                continue
            digest = hashlib.sha256(json.dumps(record["messages"], sort_keys=True).encode()).hexdigest()
            if digest in seen:
                counts["duplicates"] += 1
                continue
            seen.add(digest)
            sink.write(json.dumps({
                "messages": record["messages"],
                "meta": {key: record[key] for key in ("loop", "iteration", "model", "score_before", "score_after", "event")}
                        | {"loop_status": statuses.get(record["loop"], "unknown")},
            }) + "\n")
            counts["written"] += 1
    return counts
