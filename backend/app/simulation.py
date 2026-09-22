from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .database import EventStore


class LoopState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    COMPLETED = "completed"
    STOPPED = "stopped"


TERMINAL_STATES = {LoopState.COMPLETED, LoopState.STOPPED}


@dataclass
class LoopControlBlock:
    id: str
    name: str
    task: str
    behavior: str
    base_priority: int
    max_iterations: int = 8
    token_budget: int = 4000
    time_budget_seconds: int = 120
    state: LoopState = LoopState.READY
    effective_priority: int = 0
    wait_ticks: int = 0
    iterations: int = 0
    tokens_used: int = 0
    elapsed_seconds: float = 0.0
    score: float = 0.0
    previous_score: float = 0.0
    stagnant_steps: int = 0
    repeated_drafts: int = 0
    current_draft: str = "Waiting for first draft"
    best_draft: str = ""
    best_score: float = 0.0
    last_fingerprint: str = ""
    termination_reason: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.effective_priority = self.base_priority

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


class LoopOSEngine:
    TICK_SECONDS = 0.85
    AGING_INTERVAL = 2
    CONVERGENCE_DELTA = 0.025
    CONVERGENCE_WINDOW = 2
    REPEAT_LIMIT = 2
    WATCHDOG_LIMIT = 3

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self.loops: dict[str, LoopControlBlock] = {}
        self.events: list[dict[str, Any]] = []
        self.run_id = 0
        self.sequence = 0
        self.scheduler_status = "idle"
        self.last_dispatched_id: str | None = None
        self.last_decision: dict[str, Any] | None = None
        self.scheduler_cycles = 0
        self.preemptions = 0
        self.iterations_avoided = 0
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._urgent_counter = 0
        self.reset()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _seed_loops(self) -> dict[str, LoopControlBlock]:
        return {
            "converge": LoopControlBlock(
                id="converge", name="Research Abstract Optimizer",
                task="Improve an abstract until its quality stabilizes.",
                behavior="converging", base_priority=6, max_iterations=9,
            ),
            "repeat": LoopControlBlock(
                id="repeat", name="Repeated Draft Detector",
                task="Detect an agent trapped in the same revision.",
                behavior="repeating", base_priority=4, max_iterations=8,
            ),
            "background": LoopControlBlock(
                id="background", name="Low-priority Literature Scan",
                task="Summarize background research while urgent work is absent.",
                behavior="background", base_priority=1, max_iterations=6,
            ),
        }

    def reset(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self.loops = self._seed_loops()
        self.events = []
        self.sequence = 0
        self.scheduler_status = "idle"
        self.last_dispatched_id = None
        self.last_decision = None
        self.scheduler_cycles = 0
        self.preemptions = 0
        self.iterations_avoided = 0
        self._urgent_counter = 0
        self.run_id = self.store.create_run(self._now())
        self._emit(
            "system",
            None,
            "Created three demo loops. Press Start to let the scheduler choose the first loop.",
        )

    def _emit(self, event_type: str, loop_id: str | None, message: str,
              payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "sequence": self.sequence, "timestamp": self._now(),
            "type": event_type, "loop_id": loop_id,
            "message": message, "payload": payload or {},
        }
        self.events.append(event)
        self.events = self.events[-250:]
        self.store.add_event(self.run_id, event)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def start(self) -> None:
        if self.scheduler_status == "running":
            return
        self.scheduler_status = "running"
        self.store.set_run_status(self.run_id, "running")
        self._emit(
            "scheduler",
            None,
            "Scheduler started. Each cycle runs one complete, safe iteration.",
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        if self.scheduler_status != "running":
            return
        self.scheduler_status = "paused"
        active = self.loops.get(self.last_dispatched_id or "")
        if active and active.state not in TERMINAL_STATES:
            active.state = LoopState.PAUSED
        self.store.set_run_status(self.run_id, "paused")
        self._emit("scheduler", None, "Scheduler paused at a safe boundary.")

    def resume(self) -> None:
        if self.scheduler_status != "paused":
            return
        for loop in self.loops.values():
            if loop.state == LoopState.PAUSED:
                loop.state = LoopState.READY
        self.start()
        self._emit("scheduler", None, "Scheduler resumed; paused work returned to the ready queue.")

    def inject_urgent(self) -> LoopControlBlock:
        self._urgent_counter += 1
        loop_id = f"urgent-{self._urgent_counter}"
        loop = LoopControlBlock(
            id=loop_id, name=f"Urgent Incident Response #{self._urgent_counter}",
            task="Produce a rapid incident-response recommendation.",
            behavior="urgent", base_priority=10, max_iterations=3,
            token_budget=1200, time_budget_seconds=30,
        )
        self.loops[loop_id] = loop
        self._emit(
            "arrival",
            loop_id,
            "Urgent work entered with base priority 10 and will be considered at the next safe boundary.",
            {"base_priority": 10, "safe_boundary": True},
        )
        return loop

    def stop_loop(self, loop_id: str) -> bool:
        loop = self.loops.get(loop_id)
        if not loop or loop.state in TERMINAL_STATES:
            return False
        loop.state = LoopState.STOPPED
        loop.termination_reason = "Stopped manually by the operator; best checkpoint retained."
        self._emit("termination", loop.id, loop.termination_reason)
        return True

    async def _run(self) -> None:
        while self.scheduler_status in {"running", "paused"}:
            if self.scheduler_status == "paused":
                await asyncio.sleep(0.2)
                continue
            if not self.step():
                self.scheduler_status = "completed"
                self.store.set_run_status(self.run_id, "completed")
                self._emit("scheduler", None, "No runnable loops remain; run completed.")
                return
            await asyncio.sleep(self.TICK_SECONDS)

    def _runnable(self) -> list[LoopControlBlock]:
        return [loop for loop in self.loops.values()
                if loop.state in {LoopState.READY, LoopState.WAITING, LoopState.RUNNING}]

    def step(self) -> bool:
        runnable = self._runnable()
        if not runnable:
            return False
        self.scheduler_cycles += 1
        for loop in runnable:
            if loop.id != self.last_dispatched_id:
                loop.wait_ticks += 1
            loop.effective_priority = loop.base_priority + loop.wait_ticks // self.AGING_INTERVAL

        ranking_key = lambda loop: (
            loop.effective_priority, loop.wait_ticks, -loop.iterations
        )
        ranked = sorted(runnable, key=ranking_key, reverse=True)
        selected = ranked[0]
        candidates = [
            {
                "id": loop.id,
                "name": loop.name,
                "state": loop.state.value,
                "base_priority": loop.base_priority,
                "aging_bonus": loop.wait_ticks // self.AGING_INTERVAL,
                "effective_priority": loop.effective_priority,
                "wait_ticks": loop.wait_ticks,
            }
            for loop in ranked
        ]
        selected_bonus = selected.wait_ticks // self.AGING_INTERVAL
        decision_message = (
            f"Selected {selected.name}: priority {selected.effective_priority} "
            f"= base {selected.base_priority} + aging {selected_bonus}; "
            f"highest of {len(runnable)} runnable loop{'s' if len(runnable) != 1 else ''}."
        )
        self.last_decision = {
            "cycle": self.scheduler_cycles,
            "selected_id": selected.id,
            "selected_name": selected.name,
            "message": decision_message,
            "candidates": candidates,
        }
        self._emit(
            "decision",
            selected.id,
            decision_message,
            self.last_decision,
        )
        previous = self.loops.get(self.last_dispatched_id or "")
        if (previous and previous.id != selected.id
                and previous.state not in TERMINAL_STATES
                and selected.effective_priority > previous.effective_priority):
            previous.state = LoopState.WAITING
            self.preemptions += 1
            self._emit(
                "preemption", previous.id,
                f"Paused {previous.name} after its completed iteration so higher-priority {selected.name} can run.",
                {
                    "preempted_by": selected.id,
                    "from_priority": previous.effective_priority,
                    "to_priority": selected.effective_priority,
                    "safe_boundary": True,
                },
            )

        selected.state = LoopState.RUNNING
        selected.wait_ticks = 0
        self.last_dispatched_id = selected.id
        self._emit(
            "dispatch",
            selected.id,
            f"Running iteration {selected.iterations + 1} of {selected.max_iterations}.",
            {"cycle": self.scheduler_cycles, "effective_priority": selected.effective_priority},
        )
        self._iterate(selected)
        if selected.state == LoopState.RUNNING:
            selected.state = LoopState.READY
        return True

    def _draft_for(self, loop: LoopControlBlock) -> tuple[str, float, int]:
        index = loop.iterations
        if loop.behavior == "converging":
            scores = [0.34, 0.58, 0.73, 0.82, 0.855, 0.87, 0.878]
            score = scores[min(index, len(scores) - 1)]
            return (f"Abstract revision {index + 1}: clearer contribution, evidence, and scope.",
                    score, 320)
        if loop.behavior == "repeating":
            draft = ("The agent repeats the same generic proposal without adding evidence."
                     if index >= 1 else
                     "The agent proposes a generic improvement without measurable evidence.")
            return draft, 0.41 if index == 0 else 0.42, 260
        if loop.behavior == "urgent":
            scores = [0.62, 0.82, 0.91]
            return (f"Incident plan {index + 1}: prioritize, contain, validate, and report.",
                    scores[min(index, 2)], 180)
        scores = [0.28, 0.37, 0.45, 0.53, 0.60, 0.66]
        return (f"Literature summary batch {index + 1} with new OS-agent findings.",
                scores[min(index, len(scores) - 1)], 230)

    def _iterate(self, loop: LoopControlBlock) -> None:
        draft, score, tokens = self._draft_for(loop)
        loop.previous_score = loop.score
        loop.score = score
        loop.current_draft = draft
        loop.tokens_used += tokens
        loop.elapsed_seconds = round(loop.elapsed_seconds + self.TICK_SECONDS, 2)
        loop.iterations += 1
        fingerprint = hashlib.sha256(draft.encode("utf-8")).hexdigest()[:12]
        loop.repeated_drafts = loop.repeated_drafts + 1 if fingerprint == loop.last_fingerprint else 0
        loop.last_fingerprint = fingerprint
        improvement = loop.score - loop.previous_score
        loop.stagnant_steps = (loop.stagnant_steps + 1
                               if loop.iterations > 1 and improvement < self.CONVERGENCE_DELTA
                               else 0)

        if score > loop.best_score:
            loop.best_score = score
            loop.best_draft = draft
            self._emit("checkpoint", loop.id, f"Best draft checkpoint updated to {score:.3f}.")
        loop.history.append({
            "iteration": loop.iterations, "score": score, "tokens": tokens,
            "draft": draft, "fingerprint": fingerprint,
        })
        self._emit(
            "iteration",
            loop.id,
            (
                f"Iteration {loop.iterations} scored {score:.3f} "
                f"({improvement:+.3f}); repetition {loop.repeated_drafts}/{self.REPEAT_LIMIT}, "
                f"stagnation {loop.stagnant_steps}/{self.WATCHDOG_LIMIT}."
            ),
            {
                "score": score,
                "score_change": round(improvement, 3),
                "tokens": tokens,
                "fingerprint": fingerprint,
                "repeated_drafts": loop.repeated_drafts,
                "repeat_limit": self.REPEAT_LIMIT,
                "stagnant_steps": loop.stagnant_steps,
                "watchdog_limit": self.WATCHDOG_LIMIT,
            },
        )

        if loop.repeated_drafts >= self.REPEAT_LIMIT:
            self._terminate(loop, "Repeated-draft detector stopped a livelocked loop.")
        elif (loop.behavior == "converging"
              and loop.stagnant_steps >= self.CONVERGENCE_WINDOW):
            self.iterations_avoided += max(loop.max_iterations - loop.iterations, 0)
            loop.state = LoopState.COMPLETED
            loop.termination_reason = "Converged: score improvement remained below threshold."
            self._emit("convergence", loop.id, loop.termination_reason)
        elif loop.stagnant_steps >= self.WATCHDOG_LIMIT:
            self._terminate(loop, "Watchdog stopped a loop with no meaningful progress.")
        elif loop.tokens_used >= loop.token_budget:
            self._terminate(loop, "Token budget exhausted; best checkpoint retained.")
        elif loop.elapsed_seconds >= loop.time_budget_seconds:
            self._terminate(loop, "Time budget exhausted; best checkpoint retained.")
        elif loop.iterations >= loop.max_iterations:
            loop.state = LoopState.COMPLETED
            loop.termination_reason = "Iteration target completed."
            self._emit("completion", loop.id, loop.termination_reason)

    def _terminate(self, loop: LoopControlBlock, reason: str) -> None:
        loop.state = LoopState.STOPPED
        loop.current_draft = loop.best_draft or loop.current_draft
        loop.score = loop.best_score or loop.score
        loop.termination_reason = reason
        self._emit("termination", loop.id, reason,
                   {"recovered_best_score": loop.best_score})

    def snapshot(self) -> dict[str, Any]:
        loops = [loop.public() for loop in self.loops.values()]
        terminal = [loop for loop in self.loops.values() if loop.state in TERMINAL_STATES]
        average_wait = (sum(loop.wait_ticks for loop in self.loops.values()) / len(self.loops)
                        if self.loops else 0)
        return {
            "run_id": self.run_id, "scheduler_status": self.scheduler_status,
            "loops": loops, "events": list(reversed(self.events[-80:])),
            "last_decision": self.last_decision,
            "policy": {
                "name": "Priority scheduling with aging",
                "formula": "effective priority = base priority + floor(wait ticks / 2)",
                "safe_preemption": "Loops can switch only after a complete iteration.",
                "convergence_delta": self.CONVERGENCE_DELTA,
                "convergence_window": self.CONVERGENCE_WINDOW,
                "repeat_limit": self.REPEAT_LIMIT,
                "watchdog_limit": self.WATCHDOG_LIMIT,
            },
            "metrics": {
                "total_loops": len(loops), "terminal_loops": len(terminal),
                "preemptions": self.preemptions,
                "iterations_avoided": self.iterations_avoided,
                "repeated_drafts_detected": sum(
                    1 for loop in self.loops.values()
                    if loop.repeated_drafts >= self.REPEAT_LIMIT
                ),
                "average_wait_ticks": round(average_wait, 1),
            },
        }
