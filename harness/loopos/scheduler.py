"""Scheduling the shared model: loops take turns, highest effective priority first.

effective priority = base priority + (turns waited // aging)

A turn spans exactly one model call, so switching happens only at iteration boundaries.
Waiting raises priority, so low-priority loops cannot starve; ties go to the longest waiter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class _Waiter:
    loop_id: str
    priority: int
    order: int
    waited: int = 0


class ModelScheduler:
    def __init__(self, slots: int = 1, aging: int = 3) -> None:
        self.slots = slots
        self.aging = aging
        self.holders: set[str] = set()
        self.waiting: dict[str, _Waiter] = {}
        self.last_decision: dict | None = None
        self._order = 0

    def effective(self, waiter: _Waiter) -> int:
        return waiter.priority + waiter.waited // self.aging

    def request(self, loop_id: str, priority: int) -> None:
        if loop_id in self.holders or loop_id in self.waiting:
            return
        self._order += 1
        self.waiting[loop_id] = _Waiter(loop_id, priority, self._order)

    def release(self, loop_id: str) -> None:
        self.holders.discard(loop_id)

    def forget(self, loop_id: str) -> None:
        """Drop a loop entirely (killed, crashed, finished)."""
        self.holders.discard(loop_id)
        self.waiting.pop(loop_id, None)

    def grant(self, eligible: Callable[[str], bool] = lambda _: True) -> list[str]:
        """Fill free slots from eligible waiters. Returns the loops granted a turn."""
        granted: list[str] = []
        while len(self.holders) < self.slots:
            candidates = [w for w in self.waiting.values() if eligible(w.loop_id)]
            if not candidates:
                break
            ranked = sorted(candidates, key=lambda w: (-self.effective(w), w.order))
            chosen = ranked[0]
            self.last_decision = {
                "selected": chosen.loop_id,
                "candidates": [{"loop": w.loop_id, "base": w.priority, "aging_bonus": w.waited // self.aging,
                                "score": self.effective(w)} for w in ranked],
            }
            del self.waiting[chosen.loop_id]
            self.holders.add(chosen.loop_id)
            granted.append(chosen.loop_id)
            for other in ranked[1:]:
                other.waited += 1
        return granted
