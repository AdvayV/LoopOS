"""Monitor rules: scoring done-check output and spotting loops that are stuck."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_PYTEST_COUNTS = re.compile(r"(\d+) (passed|failed|errors?)\b")


def test_counts(output: str) -> tuple[int, int] | None:
    """(passed, total) from a pytest summary, or None if the output has none."""
    counts = {"passed": 0, "failed": 0, "error": 0}
    for number, kind in _PYTEST_COUNTS.findall(output):
        counts["error" if kind.startswith("error") else kind] += int(number)
    total = sum(counts.values())
    return (counts["passed"], total) if total else None


def score(exit_code: int, output: str) -> float:
    """Progress in [0, 1]. Exit 0 is done; pytest summaries give partial credit."""
    if exit_code == 0:
        return 1.0
    counts = test_counts(output)
    # Never report a failing check as complete.
    return min(counts[0] / counts[1], 0.99) if counts else 0.0


def fingerprint(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    # Ignore volatile numbers such as timings so "same failure, different duration" still matches.
    text = re.sub(r"\d+\.\d+s", "<t>", text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Rules:
    repeat_limit: int = 3   # same action with the same outcome this many times in a row
    cycle_window: int = 6   # alternating between at most two actions with no change in the check
    stall_limit: int = 8    # iterations without a new best score
    max_recoveries: int = 2


@dataclass(frozen=True)
class Verdict:
    kind: str  # "ok" | "livelock" | "stalled"
    reason: str = ""
    summary: str = ""  # a few words for terminal views


def assess(history: list[dict[str, Any]], since_best: int, rules: Rules) -> Verdict:
    """history entries: {"action": fp, "outcome": fp, "check": fp}, oldest first, since the last recovery."""
    recent = history[-rules.repeat_limit:]
    if len(recent) == rules.repeat_limit and len({(h["action"], h["outcome"]) for h in recent}) == 1:
        return Verdict("livelock", f"the same action produced the same result {rules.repeat_limit} times in a row",
                       f"same action, same result ×{rules.repeat_limit}")
    window = history[-rules.cycle_window:]
    if (len(window) == rules.cycle_window and len({h["action"] for h in window}) <= 2
            and len({h["check"] for h in window}) == 1):
        return Verdict("livelock", f"cycling between the same actions for {rules.cycle_window} iterations "
                                   "without changing the done check", "cycling between two actions")
    if since_best >= rules.stall_limit:
        return Verdict("stalled", f"no improvement for {since_best} iterations",
                       f"no progress in {since_best} iterations")
    return Verdict("ok")
