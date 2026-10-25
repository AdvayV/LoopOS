"""The syscall table: every action a model may request, and the checks the supervisor applies.

A worker never touches the workspace itself. It asks the supervisor to perform an action, just as a
program asks the kernel; the supervisor validates the request against the loop's policy and either
performs it or refuses with a reason the model can read.
"""
from __future__ import annotations

import codecs
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import sandbox

ACTIONS = {
    "list_dir": {"path"},
    "read_file": {"path"},
    "write_file": {"path", "content"},
    "edit_file": {"path", "find", "replace"},
    "run": {"command"},
    "finish": set(),
}
MUTATING = {"write_file", "edit_file", "run"}
IGNORED_DIRS = {".git", ".loopos", "__pycache__", ".venv", "venv", "node_modules",
                ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "dist", "build"}
PROTECTED_DIRS = {".git", ".loopos"}
DEFAULT_ALLOW = ("python", "python3", "pytest", "ls", "cat", "head", "tail", "grep",
                 "wc", "find", "diff", "echo", "true", "sort", "uniq")

# Schema for structured model output (Ollama `format`), mirroring ACTIONS.
ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": sorted(ACTIONS)},
        "reason": {"type": "string"},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "find": {"type": "string"},
        "replace": {"type": "string"},
        "command": {"type": "string"},
    },
    "required": ["action", "reason"],
}


class SyscallError(Exception):
    """A refused request. The message is shown to the model."""


@dataclass
class Policy:
    workspace: Path
    allow: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_ALLOW))
    command_timeout: int = 120
    cpu_seconds: int = 120
    memory_mb: int = 2048
    niceness: int = 0
    read_limit: int = 20_000
    write_limit: int = 200_000
    output_limit: int = 4_000
    confine_writes: bool = True
    network: bool = False  # TCP connect/listen for commands and the done check

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> "Policy":
        return cls(
            workspace=Path(spec["workspace"]).resolve(),
            allow=frozenset(spec.get("allow") or DEFAULT_ALLOW),
            command_timeout=spec.get("command_timeout", 120),
            memory_mb=spec.get("memory_mb", 2048),
            network=bool(spec.get("network", False)),
            # Higher-priority loops keep a lower niceness for their commands.
            niceness=max(0, 10 - int(spec.get("priority", 5))),
        )


def validate(request: Any) -> dict[str, Any]:
    """Normalize a model's action, or raise SyscallError explaining what is wrong."""
    if not isinstance(request, dict):
        raise SyscallError("an action must be a JSON object")
    name = request.get("action")
    if not isinstance(name, str) or name not in ACTIONS:
        raise SyscallError(f"unknown action {name!r}; use one of: {', '.join(sorted(ACTIONS))}")
    action: dict[str, Any] = {"action": name, "reason": str(request.get("reason", ""))[:500]}
    for key in sorted(ACTIONS[name]):
        value = request.get(key, "." if key == "path" and name == "list_dir" else None)
        if not isinstance(value, str):
            raise SyscallError(f"{name} needs a string field {key!r}")
        if key in {"path", "command", "find"} and not value.strip():
            raise SyscallError(f"{name}: {key!r} must not be empty")
        if key in {"path", "command"} and "\0" in value:
            raise SyscallError(f"{name}: {key!r} must not contain NUL characters")
        action[key] = value
    return action


def fingerprint_text(action: dict[str, Any]) -> str:
    """A compact one-line description, used in logs and prompts."""
    name = action.get("action")
    if name == "run":
        return f"run `{action.get('command')}`"
    if name in {"read_file", "write_file", "edit_file", "list_dir"}:
        return f"{name} {action.get('path')}"
    return str(name)


def truncate(text: str, limit: int) -> str:
    """Keep the tail: errors and test summaries come last."""
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n" + text[-limit:]


def resolve_path(policy: Policy, relative: str, *, for_write: bool = False) -> Path:
    try:
        candidate = (policy.workspace / relative).resolve()
    except (OSError, RuntimeError) as exc:
        raise SyscallError(f"cannot resolve path {relative!r}: {exc}") from exc
    if candidate != policy.workspace and policy.workspace not in candidate.parents:
        raise SyscallError(f"path {relative!r} is outside the workspace")
    parts = candidate.relative_to(policy.workspace).parts
    protected = next((part for part in parts if part in PROTECTED_DIRS), None)
    if for_write and protected:
        raise SyscallError(f"{protected}/ is protected and cannot be modified")
    return candidate


class Jobs:
    """Commands currently running for one loop, so the supervisor can signal them with its worker.

    While `paused` is set, a command's timeout does not advance.
    """

    def __init__(self) -> None:
        self._processes: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self.paused = False

    def add(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.add(process)

    def discard(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def signal(self, sig: int) -> None:
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            _signal_group(process, sig)

    def __len__(self) -> int:
        with self._lock:
            return len(self._processes)


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_command(argv: list[str], policy: Policy, *, timeout: int | None = None,
                enforce_allowlist: bool = True, jobs: Jobs | None = None) -> tuple[int, str]:
    """Run argv in the workspace through the sandbox launcher. Returns (exit code, output tail).

    The command gets its own process group; when it finishes or times out, the whole group is
    killed, so background children cannot outlive it.
    """
    if not argv:
        raise SyscallError("empty command")
    program = os.path.basename(argv[0])
    if enforce_allowlist and program not in policy.allow:
        raise SyscallError(f"command {program!r} is not allowed; allowed: {', '.join(sorted(policy.allow))}")
    writable = [str(policy.workspace), tempfile.gettempdir(), "/dev"] if policy.confine_writes else []
    wrapped = sandbox.command(argv, cpu_seconds=policy.cpu_seconds, memory_mb=policy.memory_mb,
                              niceness=policy.niceness, writable=writable, network=policy.network)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TERM") if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # `python`/`pytest` resolve to the project's .venv if it has one, else to LoopOS's own interpreter.
    bins = [policy.workspace / ".venv" / "bin", Path(sys.executable).parent]
    env["PATH"] = os.pathsep.join([str(b) for b in bins if b.is_dir()] + [env.get("PATH", "")])
    limit = float(timeout or policy.command_timeout)
    process = subprocess.Popen(wrapped, cwd=policy.workspace, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    if jobs is not None:
        jobs.add(process)
    # Read output on a thread and wait for the command itself: a background child holding the pipe
    # open must not keep the syscall waiting.
    tail, total_chars = "", 0

    def read_output() -> None:
        nonlocal tail, total_chars
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = process.stdout.read1(65536)
            decoded = decoder.decode(chunk, final=not chunk)
            total_chars += len(decoded)
            tail = (tail + decoded)[-policy.output_limit:]
            if not chunk:
                break

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    timed_out = False
    try:
        remaining, last = limit, time.monotonic()
        while True:
            try:
                process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if not (jobs is not None and jobs.paused):
                    remaining -= now - last
                last = now
                if remaining <= 0:
                    timed_out = True
                    _signal_group(process, signal.SIGKILL)
                    process.wait()
                    break
    finally:
        _signal_group(process, signal.SIGKILL)
        process.wait()
        if jobs is not None:
            jobs.discard(process)
        reader.join(timeout=5)
        if not reader.is_alive():
            process.stdout.close()
    if timed_out:
        message = f"\n[killed after {limit:g}s timeout]"
        total_chars += len(message)
        tail = (tail + message)[-policy.output_limit:]
    text = (f"[... {total_chars - len(tail)} earlier characters omitted ...]\n"
            if total_chars > len(tail) else "") + tail
    return (124 if timed_out else process.returncode), text


def _list_dir(policy: Policy, path: Path) -> str:
    if not path.is_dir():
        raise SyscallError("not a directory")
    entries = []
    for entry in sorted(path.iterdir()):
        if entry.name in IGNORED_DIRS:
            continue
        entries.append(entry.name + ("/" if entry.is_dir() else ""))
    shown = entries[:200]
    suffix = f"\n[... {len(entries) - 200} more entries]" if len(entries) > 200 else ""
    return "\n".join(shown) + suffix if shown else "(empty directory)"


def execute(request: Any, policy: Policy, jobs: Jobs | None = None) -> dict[str, Any]:
    """Perform one validated action. Never raises for a bad request: refusals become results."""
    try:
        action = validate(request)
        name = action["action"]
        if name == "finish":
            return {"ok": True, "output": "Finish requested; the supervisor will verify with the done check."}
        if name == "run":
            try:
                argv = shlex.split(action["command"])
            except ValueError as exc:
                raise SyscallError(f"could not parse command: {exc}") from exc
            code, output = run_command(argv, policy, jobs=jobs)
            return {"ok": code == 0, "exit_code": code, "output": output or "(no output)"}
        path = resolve_path(policy, action["path"], for_write=name in {"write_file", "edit_file"})
        if name == "list_dir":
            return {"ok": True, "output": _list_dir(policy, path)}
        if name == "read_file":
            if not path.is_file():
                raise SyscallError(f"no such file: {action['path']}")
            with path.open(errors="replace") as source:
                text = source.read(policy.read_limit + 1)
            if len(text) > policy.read_limit:
                text = text[: policy.read_limit] + "\n[... truncated, more characters omitted]"
            return {"ok": True, "output": text}
        if name == "write_file":
            if len(action["content"]) > policy.write_limit:
                raise SyscallError(f"content exceeds {policy.write_limit} characters")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(action["content"])
            return {"ok": True, "output": f"wrote {len(action['content'])} characters to {action['path']}"}
        # edit_file: replace exactly one occurrence so edits are unambiguous.
        if not path.is_file():
            raise SyscallError(f"no such file: {action['path']}")
        with path.open() as source:
            text = source.read(policy.write_limit + 1)
        if len(text) > policy.write_limit:
            raise SyscallError(f"file exceeds {policy.write_limit} characters")
        count = text.count(action["find"])
        if count != 1:
            raise SyscallError(f"`find` text occurs {count} times in {action['path']}; it must occur exactly once")
        updated = text.replace(action["find"], action["replace"], 1)
        if len(updated) > policy.write_limit:
            raise SyscallError(f"edited content exceeds {policy.write_limit} characters")
        path.write_text(updated)
        return {"ok": True, "output": f"edited {action['path']}"}
    except SyscallError as exc:
        return {"ok": False, "refused": True, "output": f"Refused: {exc}"}
    except OSError as exc:
        return {"ok": False, "output": f"Error: {exc.strerror or exc}"}
    except (UnicodeError, ValueError) as exc:
        return {"ok": False, "refused": True, "output": f"Refused: {exc}"}
