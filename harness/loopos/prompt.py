"""What the worker shows the model each iteration. One system + one user message per turn,
which is also the shape of a training example."""
from __future__ import annotations

from typing import Any

SYSTEM = """You are a LoopOS worker. You reach a goal by taking ONE action per turn.
Reply with a single JSON object and nothing else:
  {"action": "list_dir",   "path": ".", "reason": "..."}
  {"action": "read_file",  "path": "src/app.py", "reason": "..."}
  {"action": "edit_file",  "path": "src/app.py", "find": "exact old text", "replace": "new text", "reason": "..."}
  {"action": "write_file", "path": "notes.md", "content": "full file text", "reason": "..."}
  {"action": "run",        "command": "pytest -q", "reason": "..."}
  {"action": "finish",     "reason": "why the goal is met"}
Rules:
- Paths are relative to the workspace. You cannot leave it or modify .git/.
- `edit_file` replaces text that must occur exactly once; read the file first and copy it exactly.
- Commands run without a shell (no pipes, redirects or &&), only allowed programs work, and there is no network.
- The supervisor runs the done check after every change. The goal is met only when it passes.
- If the supervisor says you are repeating yourself, change approach instead of retrying."""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " ..."


def user_message(spec: dict[str, Any], state: dict[str, Any]) -> str:
    check = state.get("check") or {}
    lines = [
        f"GOAL: {spec['goal']}",
        f"DONE CHECK: `{spec['until']}` (must exit 0)",
        f"ITERATION: {state.get('iteration', 0) + 1} of {spec['max_iters']}",
        f"ALLOWED COMMANDS: {', '.join(sorted(spec.get('allow') or []))}",
        "",
        "WORKSPACE FILES:",
        state.get("listing") or "(unknown)",
        "",
        f"LATEST DONE CHECK (exit {check.get('exit_code', '?')}):",
        _clip(check.get("output", "(not run yet)"), 2500),
    ]
    steps = state.get("steps", [])[-6:]
    if steps:
        lines += ["", "RECENT STEPS (oldest first):"]
        for step in steps:
            lines.append(f"#{step['iteration']} {step['summary']} -> {_clip(step['result'], 600)}")
    last = state.get("last_result")
    if last:
        lines += ["", "FULL RESULT OF YOUR LAST ACTION:", _clip(last, 6000)]
    if state.get("hint"):
        lines += ["", f"SUPERVISOR: {state['hint']}"]
    lines += ["", "Reply with your next action as JSON."]
    return "\n".join(lines)


def messages(spec: dict[str, Any], state: dict[str, Any]) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_message(spec, state)}]
