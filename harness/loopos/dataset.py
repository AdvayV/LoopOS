"""Turn worker traces into a fine-tuning dataset (chat-format JSONL)."""
from __future__ import annotations

import hashlib
import json
import os
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
    if record["action"].get("action") == "finish" and record["score_after"] < 1.0:
        return False  # a later repair must not make premature completion a teaching target
    if record["score_after"] > record["score_before"] or record["event"] == "done":
        return True
    return loop_status == "done" and record["event"] not in {"livelock", "stalled"}


def is_validation(loop_id: str, fraction: float) -> bool:
    """Deterministic split by loop, so one loop's iterations never straddle train and validation."""
    bucket = int(hashlib.sha256(loop_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < fraction


FINAL = {"done", "gave_up", "budget", "stopped", "failed"}


def loop_outcomes(root: Path) -> dict[str, str]:
    """Each loop's final status, read from its own trace (the last record of a finished loop carries
    it), falling back to the database. Traces copied from another machine need no database."""
    outcomes: dict[str, str] = {}
    database = paths.database(root)
    if database.exists():
        outcomes.update({loop["id"]: loop["status"] for loop in Store(database).loops()})
    for record in _traces(root):
        if record.get("status") in FINAL:
            outcomes[record["loop"]] = record["status"]
    return outcomes


def export(root: Path, out: Path, mode: str = "success", val_fraction: float = 0.0) -> dict[str, int]:
    """Write training examples to `out`; with `val_fraction`, a held-aside share of loops goes to
    `<out stem>.val.jsonl` instead."""
    statuses = loop_outcomes(root)
    seen: set[str] = set()
    counts = {"read": 0, "written": 0, "validation": 0, "duplicates": 0}
    val_path = out.with_name(out.stem + ".val" + out.suffix)
    with open(out, "w") as train, open(val_path, "w") if val_fraction else open(os.devnull, "w") as val:
        for record in _traces(root):
            counts["read"] += 1
            if not keep(record, statuses.get(record["loop"], "unknown"), mode):
                continue
            digest = hashlib.sha256(json.dumps(record["messages"], sort_keys=True).encode()).hexdigest()
            if digest in seen:
                counts["duplicates"] += 1
                continue
            seen.add(digest)
            line = json.dumps({
                "messages": record["messages"],
                "meta": {key: record[key] for key in ("loop", "iteration", "model", "score_before", "score_after", "event")}
                        | {"loop_status": statuses.get(record["loop"], "unknown")},
            }) + "\n"
            if val_fraction and is_validation(record["loop"], val_fraction):
                val.write(line)
                counts["validation"] += 1
            else:
                train.write(line)
                counts["written"] += 1
    return counts
