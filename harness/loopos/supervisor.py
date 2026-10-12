"""The supervisor: LoopOS's kernel.

    python -m loopos.supervisor --home ~/.local/state/loopos     (normally started by `loopos daemon start`)

It owns every loop. Each loop's worker is a real child process in its own session (so the whole
process group can be paused with SIGSTOP, resumed with SIGCONT, or killed). The supervisor:
  * schedules the shared model between loops (priority + aging, switching at iteration boundaries),
  * performs every action a worker requests after checking it against the loop's policy,
  * runs the done check itself, so a worker cannot claim success,
  * snapshots the best workspace state and restores it on livelock or when giving up,
  * restarts crashed or hung workers from their saved state, and enforces iteration/time budgets.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import checkpoint, monitor, paths, sandbox, syscalls
from .protocol import Connection, LoopOSError, read_message, write_message
from .scheduler import ModelScheduler
from .store import Store

TERMINAL = {"done", "stopped", "gave_up", "budget", "failed"}
DEFAULTS: dict[str, Any] = {
    "priority": 5, "max_iters": 30, "max_minutes": 60, "provider": "ollama", "model": "qwen2.5-coder:7b",
    "ollama_url": "http://localhost:11434", "allow": list(syscalls.DEFAULT_ALLOW), "command_timeout": 120,
    "check_timeout": 300, "memory_mb": 2048, "model_timeout": 600,
}
PACKAGE_ROOT = str(Path(__file__).resolve().parent.parent)


@dataclass
class Loop:
    id: str
    spec: dict[str, Any]
    status: str = "starting"
    process: subprocess.Popen | None = field(default=None, repr=False)
    iteration: int = 0
    score: float = 0.0
    best_score: float = -1.0
    best_iteration: int = 0
    best_check: dict[str, Any] = field(default_factory=dict)
    since_best: int = 0
    restarts: int = 0
    recoveries: int = 0
    active_seconds: float = 0.0
    last_activity: float = field(default_factory=time.monotonic)
    waiting_model: bool = False
    holding_model: bool = False
    pause_requested: bool = False
    note: str = ""
    hint: str = ""
    check: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)

    PERSISTED = ("iteration", "score", "best_score", "best_iteration", "best_check", "since_best", "restarts",
                 "recoveries", "active_seconds", "note", "hint", "check")

    def info(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.PERSISTED}

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "goal": self.spec["goal"], "until": self.spec["until"],
            "priority": self.spec["priority"], "iteration": self.iteration, "max_iters": self.spec["max_iters"],
            "score": self.score, "best_score": max(self.best_score, 0.0), "restarts": self.restarts,
            "recoveries": self.recoveries, "note": self.note, "model": self.spec.get("model"),
            "provider": self.spec.get("provider"), "pid": self.process.pid if self.process else None,
            "waiting_model": self.waiting_model, "holding_model": self.holding_model,
            "active_seconds": round(self.active_seconds), "workspace": self.spec["workspace"],
        }


class Supervisor:
    def __init__(self, root: Path, *, tick: float = 1.0, hang_timeout: float = 900, max_restarts: int = 3,
                 rules: monitor.Rules = monitor.Rules(), slots: int = 1) -> None:
        self.root = root
        self.store = Store(paths.database(root))
        self.loops: dict[str, Loop] = {}
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.scheduler = ModelScheduler(slots)
        self.stopping = threading.Event()
        self.tick = tick
        self.hang_timeout = hang_timeout
        self.max_restarts = max_restarts
        self.rules = rules
        self.ready = threading.Event()

    # ---------------------------------------------------------------- lifecycle

    def serve(self) -> None:
        path = paths.socket_path(self.root)
        try:
            Connection(path, timeout=2).close()
        except LoopOSError:
            path.unlink(missing_ok=True)  # stale socket from a previous run
        else:
            raise LoopOSError(f"a supervisor is already running on {path}")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(64)
        server.settimeout(0.5)
        (self.root / "supervisor.pid").write_text(str(os.getpid()))
        self.store.event(None, "supervisor", f"Supervisor started (pid {os.getpid()}).")
        self._recover()
        threading.Thread(target=self._monitor_loop, name="monitor", daemon=True).start()
        self.ready.set()
        try:
            while not self.stopping.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
        finally:
            server.close()
            self._shutdown()
            path.unlink(missing_ok=True)
            (self.root / "supervisor.pid").unlink(missing_ok=True)

    def _shutdown(self) -> None:
        with self.cond:
            for loop in self.loops.values():
                if loop.process and loop.process.poll() is None:
                    self._signal(loop, signal.SIGKILL)
                    loop.process.wait(timeout=5)
                    loop.process = None
                    if loop.status not in TERMINAL:
                        loop.note = "interrupted: supervisor stopped; resumes when it starts again"
                        self._persist(loop)
            self.cond.notify_all()
        self.store.event(None, "supervisor", "Supervisor stopped.")
        self.store.close()

    def _recover(self) -> None:
        """Reload every loop; restart the unfinished ones from their saved worker state."""
        for record in self.store.loops():
            loop = Loop(record["id"], record["spec"], status=record["status"])
            for key, value in record["info"].items():
                if key in Loop.PERSISTED:
                    setattr(loop, key, value)
            self.loops[loop.id] = loop
            if loop.status in TERMINAL:
                continue
            if loop.status == "paused":
                loop.note = f"paused; resume with: loopos resume {loop.id}"
                self._persist(loop)
                continue
            self._event(loop, "recovered", "Supervisor restarted; resuming loop from its last saved iteration.")
            self._spawn(loop)

    # ---------------------------------------------------------------- connections

    def _handle(self, conn: socket.socket) -> None:
        stream = conn.makefile("rwb")
        try:
            while (message := read_message(stream)) is not None:
                op = message.pop("op", "")
                handler: Callable[..., dict[str, Any]] | None = getattr(self, f"op_{op}", None)
                try:
                    if handler is None:
                        raise LoopOSError(f"unknown operation {op!r}")
                    reply = {"ok": True, **handler(**message)}
                except LoopOSError as exc:
                    reply = {"ok": False, "error": str(exc)}
                except TypeError as exc:
                    reply = {"ok": False, "error": f"bad request for {op}: {exc}"}
                except Exception as exc:  # keep the daemon alive; record the bug
                    traceback.print_exc()
                    reply = {"ok": False, "error": f"internal error: {exc}"}
                write_message(stream, reply)
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                stream.close()
            except OSError:  # the worker died; nothing left to flush to
                pass
            conn.close()

    def _get(self, loop_id: str) -> Loop:
        try:
            return self.loops[loop_id]
        except KeyError:
            raise LoopOSError(f"no loop {loop_id!r}") from None

    # ---------------------------------------------------------------- operator operations

    def op_ping(self) -> dict[str, Any]:
        return {"pid": os.getpid(), "loops": len(self.loops)}

    def op_run(self, spec: dict[str, Any]) -> dict[str, Any]:
        spec = {**DEFAULTS, **{k: v for k, v in spec.items() if v is not None}}
        for key in ("goal", "until", "workspace"):
            if not isinstance(spec.get(key), str) or not spec[key].strip():
                raise LoopOSError(f"{key} is required")
        workspace = Path(spec["workspace"]).expanduser().resolve()
        if not workspace.is_dir():
            raise LoopOSError(f"workspace {workspace} is not a directory")
        if workspace == Path.home() or workspace == Path("/"):
            raise LoopOSError("refusing to use your home or root directory as a workspace")
        spec["workspace"] = str(workspace)
        if not 0 <= int(spec["priority"]) <= 10:
            raise LoopOSError("priority must be between 0 and 10")
        if spec["provider"] == "scripted":
            if not Path(spec.get("script") or "").is_file():
                raise LoopOSError("the scripted provider needs --script pointing to a JSON file")
            spec["model"] = "scripted"
        try:
            shlex.split(spec["until"])
        except ValueError as exc:
            raise LoopOSError(f"cannot parse --until: {exc}") from exc
        try:
            checkpoint.check_size(workspace)
        except checkpoint.CheckpointError as exc:
            raise LoopOSError(str(exc)) from exc
        with self.cond:
            busy = [l.id for l in self.loops.values()
                    if l.status not in TERMINAL and l.spec["workspace"] == spec["workspace"]]
            if busy:
                raise LoopOSError(f"loop {busy[0]} is already working in {workspace}")
            loop_id = self.store.next_loop_id()
            directory = paths.loop_dir(self.root, loop_id)
            (directory / "spec.json").write_text(json.dumps(spec, indent=2))
            loop = Loop(loop_id, spec)
            self.loops[loop_id] = loop
            self._event(loop, "created", f"Loop created: {spec['goal']!r}, done when `{spec['until']}` exits 0.")
            self._spawn(loop)
        return {"id": loop_id}

    def op_ps(self, all: bool = False) -> dict[str, Any]:
        with self.lock:
            loops = [l.view() for l in self.loops.values() if all or l.status not in TERMINAL]
        return {"loops": loops, "decision": self.scheduler.last_decision}

    def op_show(self, loop: str) -> dict[str, Any]:
        with self.lock:
            item = self._get(loop)
            return {"loop": item.view(), "spec": item.spec, "check": item.check, "hint": item.hint}

    def op_logs(self, loop: str | None = None, after: int = 0, limit: int = 200) -> dict[str, Any]:
        if loop:
            self._get(loop)
        return {"events": self.store.events(loop, after, limit)}

    def op_pause(self, loop: str) -> dict[str, Any]:
        with self.cond:
            item = self._get(loop)
            if item.status in TERMINAL or item.status == "paused":
                raise LoopOSError(f"loop {loop} is {item.status}")
            if item.holding_model:
                # Never freeze a loop mid-model-call: it would block the shared model for everyone.
                item.pause_requested = True
                self._event(item, "pause", "Pause requested; the loop stops when its model turn ends.")
                return {"pending": True}
            self._pause_now(item, "Paused by operator.")
        return {"pending": False}

    def op_resume(self, loop: str) -> dict[str, Any]:
        with self.cond:
            item = self._get(loop)
            if item.status != "paused":
                raise LoopOSError(f"loop {loop} is {item.status}, not paused")
            item.status = "running"
            item.note = ""
            item.last_activity = time.monotonic()
            if item.process is None or item.process.poll() is not None:
                self._spawn(item)
            else:
                self._signal(item, signal.SIGCONT)
            self._event(item, "resume", "Resumed.")
            self._dispatch()
        return {}

    def op_kill(self, loop: str) -> dict[str, Any]:
        with self.cond:
            item = self._get(loop)
            if item.status in TERMINAL:
                raise LoopOSError(f"loop {loop} already finished ({item.status})")
            self._terminate(item, "stopped", "Stopped by operator.", restore=False)
            if item.process and item.process.poll() is None:
                self._signal(item, signal.SIGKILL)
        return {}

    def op_shutdown(self) -> dict[str, Any]:
        self.stopping.set()
        return {}

    # ---------------------------------------------------------------- worker operations

    def op_hello(self, loop: str, pid: int) -> dict[str, Any]:
        item = self._get(loop)
        item.last_activity = time.monotonic()
        if not item.check:
            # Baseline: the starting state is the first checkpoint, so a loop never ends worse than it began.
            check = self._run_check(item)
            snapshot = self._checkpoint_dir(item)
            checkpoint.snapshot(Path(item.spec["workspace"]), snapshot)
            with self.cond:
                item.check = check
                item.score = item.best_score = monitor.score(check["exit_code"], check["output"])
                item.best_check = check
                self._event(item, "baseline", f"Baseline done check exited {check['exit_code']} "
                                              f"(score {item.score:.2f}). Saved checkpoint 0.")
                self._persist(item)
        return {"check": item.check, "score": item.score, "listing": self._listing(item), "hint": item.hint}

    def op_acquire(self, loop: str) -> dict[str, Any]:
        with self.cond:
            item = self._get(loop)
            self.scheduler.request(loop, int(item.spec["priority"]))
            item.waiting_model = True
            self._dispatch()
            while loop not in self.scheduler.holders:
                if item.status in TERMINAL or self.stopping.is_set():
                    self.scheduler.forget(loop)
                    item.waiting_model = False
                    raise LoopOSError(f"loop {loop} is {item.status}")
                self.cond.wait(0.5)
            item.waiting_model = False
            item.holding_model = True
            item.last_activity = time.monotonic()
        return {}

    def op_release(self, loop: str) -> dict[str, Any]:
        with self.cond:
            item = self._get(loop)
            self.scheduler.release(loop)
            item.holding_model = False
            item.last_activity = time.monotonic()
            if item.pause_requested and item.status == "running":
                # The worker is blocked waiting for this reply: a safe point to freeze it.
                self._pause_now(item, "Paused at the end of its model turn.")
            self._dispatch()
        return {}

    def op_model_error(self, loop: str, error: str) -> dict[str, Any]:
        with self.cond:
            item = self._get(loop)
            if item.status == "running":
                self._pause_now(item, f"Model unavailable: {error}")
                item.note = f"model unavailable; fix it, then: loopos resume {loop}"
                self._persist(item)
        return {}

    def op_syscall(self, loop: str, action: dict[str, Any]) -> dict[str, Any]:
        item = self._get(loop)
        if item.status in TERMINAL:
            return {"result": {"ok": False, "refused": True, "output": f"Refused: loop is {item.status}"}}
        item.last_activity = time.monotonic()
        result = syscalls.execute(action, syscalls.Policy.from_spec(item.spec))
        item.last_activity = time.monotonic()
        outcome = "refused" if result.get("refused") else ("ok" if result["ok"] else "failed")
        self._event(item, "syscall", f"{syscalls.fingerprint_text(action)} -> {outcome}",
                    {"action": {k: v for k, v in action.items() if k not in {"content", "replace"}},
                     "outcome": outcome, "output": result.get("output", "")[:300]})
        return {"result": result}

    def op_iteration(self, loop: str, iteration: int, action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        item = self._get(loop)
        workspace = Path(item.spec["workspace"])
        if item.status in TERMINAL:
            return self._verdict(item, "stop")
        name = action.get("action")
        check = self._run_check(item) if name in syscalls.MUTATING or name == "finish" else item.check
        current = monitor.score(check["exit_code"], check["output"])
        improved = current > item.best_score
        if improved:
            checkpoint.snapshot(workspace, self._checkpoint_dir(item))
        with self.cond:
            item.iteration = iteration
            item.check = check
            item.score = current
            item.last_activity = time.monotonic()
            item.hint = ""
            item.history = (item.history + [{
                "action": monitor.fingerprint({k: v for k, v in action.items() if k != "reason"}),
                "outcome": monitor.fingerprint(result.get("output", "")),
                "check": monitor.fingerprint(check["output"]),
            }])[-20:]
            if improved:
                item.best_score, item.best_iteration, item.best_check, item.since_best = current, iteration, check, 0
                self._event(item, "checkpoint", f"New best score {current:.2f} at iteration {iteration}; checkpoint saved.")
            else:
                item.since_best += 1
            if item.status in TERMINAL:  # killed while this iteration was running
                return self._verdict(item, "stop")

            if check["exit_code"] == 0:
                self._terminate(item, "done", f"Done check passed after {iteration} iterations.", restore=False)
                return self._verdict(item, "stop", event="done")
            if iteration >= int(item.spec["max_iters"]):
                self._terminate(item, "budget", f"Iteration budget ({item.spec['max_iters']}) used up.")
                return self._verdict(item, "stop", event="budget")
            if item.active_seconds >= float(item.spec["max_minutes"]) * 60:
                self._terminate(item, "budget", f"Time budget ({item.spec['max_minutes']} min) used up.")
                return self._verdict(item, "stop", event="budget")

            verdict = monitor.assess(item.history, item.since_best, self.rules)
            if verdict.kind != "ok":
                if item.recoveries >= self.rules.max_recoveries:
                    self._terminate(item, "gave_up", f"Gave up: {verdict.reason} "
                                                     f"(after {item.recoveries} recoveries).")
                    return self._verdict(item, "stop", event="gave_up")
                item.recoveries += 1
                self._restore_best(item)
                item.history = []
                item.since_best = 0
                item.hint = (f"You were stuck: {verdict.reason}. The workspace was restored to the best "
                             f"checkpoint (iteration {item.best_iteration}, score {item.best_score:.2f}). "
                             "Try a different approach.")
                self._event(item, verdict.kind, f"{verdict.kind.capitalize()} detected: {verdict.reason}. "
                                                f"Restored checkpoint from iteration {item.best_iteration} "
                                                f"(recovery {item.recoveries}/{self.rules.max_recoveries}).")
                self._persist(item)
                return self._verdict(item, "continue", event=verdict.kind)
            if name == "finish":
                item.hint = "You asked to finish, but the done check still fails. Keep working."
            elif result.get("refused"):
                item.hint = "Your last action was refused. Read the reason and adjust."
            self._persist(item)
            return self._verdict(item, "continue")

    # ---------------------------------------------------------------- internals

    def _verdict(self, item: Loop, directive: str, event: str = "") -> dict[str, Any]:
        return {"directive": directive, "status": item.status, "score": item.score, "check": item.check,
                "listing": self._listing(item), "hint": item.hint, "event": event}

    def _event(self, item: Loop | None, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.store.event(item.id if item else None, kind, message, data)

    def _persist(self, item: Loop) -> None:
        self.store.save_loop(item.id, item.spec, item.status, item.info())

    def _checkpoint_dir(self, item: Loop) -> Path:
        return paths.loop_dir(self.root, item.id) / "checkpoints" / "best"

    def _listing(self, item: Loop) -> str:
        files = [str(p) for p in checkpoint.tracked_files(Path(item.spec["workspace"]))]
        extra = f"\n[... {len(files) - 150} more files]" if len(files) > 150 else ""
        return "\n".join(files[:150]) + extra if files else "(empty workspace)"

    def _run_check(self, item: Loop) -> dict[str, Any]:
        policy = syscalls.Policy.from_spec(item.spec)
        code, output = syscalls.run_command(shlex.split(item.spec["until"]), policy,
                                            timeout=int(item.spec["check_timeout"]), enforce_allowlist=False)
        return {"exit_code": code, "output": output}

    def _restore_best(self, item: Loop) -> None:
        checkpoint.restore(self._checkpoint_dir(item), Path(item.spec["workspace"]))
        item.check = item.best_check
        item.score = item.best_score

    def _terminate(self, item: Loop, status: str, reason: str, *, restore: bool = True) -> None:
        """Move a loop to a final state. Unsuccessful endings leave the workspace at the best checkpoint."""
        if restore and item.best_score > item.score and self._checkpoint_dir(item).is_dir():
            self._restore_best(item)
            reason += f" Workspace restored to the best checkpoint (iteration {item.best_iteration}, score {item.best_score:.2f})."
        item.status = status
        item.note = reason
        item.waiting_model = item.holding_model = item.pause_requested = False
        self.scheduler.forget(item.id)
        self._event(item, status, reason)
        self._persist(item)
        self._dispatch()

    def _pause_now(self, item: Loop, reason: str) -> None:
        item.pause_requested = False
        item.status = "paused"
        item.note = reason
        if item.process and item.process.poll() is None:
            self._signal(item, signal.SIGSTOP)
        self._event(item, "pause", reason)
        self._persist(item)
        self._dispatch()

    def _dispatch(self) -> None:
        """Hand free model turns to the best waiting loops. Caller holds the lock."""
        granted = self.scheduler.grant(lambda loop_id: self.loops[loop_id].status == "running")
        decision = self.scheduler.last_decision
        for loop_id in granted:
            if decision and len(decision["candidates"]) > 1:
                rows = ", ".join(f"{c['loop']}={c['base']}+{c['aging_bonus']}" for c in decision["candidates"])
                self._event(self.loops[loop_id], "schedule",
                            f"Got the model turn (priority + aging bonus: {rows}).", decision)
        if granted:
            self.cond.notify_all()

    def _signal(self, item: Loop, sig: int) -> None:
        try:
            os.killpg(item.process.pid, sig)
        except (ProcessLookupError, AttributeError):
            pass

    def _spawn(self, item: Loop) -> None:
        directory = paths.loop_dir(self.root, item.id)
        argv = sandbox.command([sys.executable, "-m", "loopos.worker", "--loop", item.id, "--home", str(self.root)],
                               memory_mb=1024, niceness=max(0, 10 - int(item.spec["priority"])),
                               writable=[str(directory)])
        env = dict(os.environ)
        env["PYTHONPATH"] = PACKAGE_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        with open(directory / "worker.log", "ab") as log:
            item.process = subprocess.Popen(argv, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        item.status = "running"
        item.last_activity = time.monotonic()
        self._event(item, "spawn", f"Worker process started (pid {item.process.pid}).")
        self._persist(item)

    def _crashed(self, item: Loop, reason: str) -> None:
        self.scheduler.forget(item.id)
        item.waiting_model = item.holding_model = False
        item.process = None
        item.restarts += 1
        if item.restarts > self.max_restarts:
            self._terminate(item, "failed", f"{reason}; restart limit ({self.max_restarts}) reached.")
            return
        self._event(item, "restart", f"{reason}; restarting from the last saved iteration "
                                     f"(restart {item.restarts}/{self.max_restarts}).")
        self._spawn(item)
        self._dispatch()

    def _monitor_loop(self) -> None:
        last = time.monotonic()
        while not self.stopping.wait(self.tick):
            now = time.monotonic()
            with self.cond:
                for item in list(self.loops.values()):
                    if item.status == "running":
                        item.active_seconds += now - last
                    if item.process is None or item.status in TERMINAL | {"paused"}:
                        if item.process is not None and item.process.poll() is not None:
                            item.process = None
                        continue
                    code = item.process.poll()
                    if code is not None:
                        self._crashed(item, f"Worker exited unexpectedly (code {code})")
                    elif not item.waiting_model and now - item.last_activity > self.hang_timeout:
                        self._signal(item, signal.SIGKILL)
                        item.process.wait(timeout=5)
                        self._crashed(item, f"Worker hung (no progress for {int(now - item.last_activity)}s)")
                    elif item.active_seconds > float(item.spec["max_minutes"]) * 60 + 60:
                        # The iteration boundary normally enforces this; this catches loops that never get there.
                        self._signal(item, signal.SIGKILL)
                        self._terminate(item, "budget", f"Time budget ({item.spec['max_minutes']} min) exceeded.")
            last = now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loopos.supervisor")
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--hang-timeout", type=float, default=900)
    args = parser.parse_args(argv)
    root = args.home or paths.home()
    supervisor = Supervisor(root, hang_timeout=args.hang_timeout)
    signal.signal(signal.SIGTERM, lambda *_: supervisor.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: supervisor.stopping.set())
    try:
        supervisor.serve()
    except LoopOSError as exc:
        print(f"loopos supervisor: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
