"""The worker: one OS process per loop, repeating think -> act -> report until told to stop.

    python -m loopos.worker --loop l1 --home ~/.local/state/loopos

It asks the supervisor for a model turn, asks the model for one action, asks the supervisor to
perform it (a syscall), then reports the iteration. The supervisor runs the done check and answers
with a directive. State is saved after every iteration so a crashed worker resumes where it left off.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import paths, prompt
from .model import Provider, ProviderError, make_provider
from .protocol import Connection, LoopOSError
from .syscalls import fingerprint_text

MODEL_RETRIES = 3


def parse_action(reply: str) -> tuple[dict[str, Any] | None, str]:
    """Extract the JSON action from a model reply. Small models sometimes wrap it in prose or fences."""
    text = reply.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None, "reply was not a JSON object"
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            return None, f"reply was not valid JSON ({exc.msg})"
    if not isinstance(value, dict):
        return None, "reply must be a JSON object"
    return value, ""


def ask_model(conn: Connection, loop_id: str, provider: Provider, messages: list[dict[str, str]]) -> str:
    """One model call inside a scheduled turn. Persistent failures pause the loop for the operator."""
    failures = 0
    while True:
        conn.request("acquire", loop=loop_id)
        try:
            return provider.complete(messages)
        except ProviderError as exc:
            failures += 1
            error = str(exc)
        finally:
            conn.request("release", loop=loop_id)
        if failures >= MODEL_RETRIES:
            # The supervisor pauses this process; execution continues here after `loopos resume`.
            conn.request("model_error", loop=loop_id, error=error)
            failures = 0
        else:
            time.sleep(2 ** failures)


def save_json(path: Path, value: Any) -> None:
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(value))
    staging.replace(path)


def run(loop_id: str, root: Path) -> int:
    directory = paths.loop_dir(root, loop_id)
    spec = json.loads((directory / "spec.json").read_text())
    state_path = directory / "state.json"
    state: dict[str, Any] = (json.loads(state_path.read_text()) if state_path.exists()
                             else {"iteration": 0, "model_calls": 0, "steps": []})
    with Connection(paths.socket_path(root)) as conn:
        hello = conn.request("hello", loop=loop_id, pid=os.getpid())
        state.update(check=hello["check"], listing=hello["listing"], hint=hello.get("hint", ""))
        state.setdefault("score", hello["score"])
        provider = make_provider(spec, state["model_calls"])
        while True:
            iteration = state["iteration"] + 1
            messages = prompt.messages(spec, state)
            reply = ask_model(conn, loop_id, provider, messages)
            state["model_calls"] += 1
            request, error = parse_action(reply)
            if request is None:
                action: dict[str, Any] = {"action": "invalid", "raw": reply[:300]}
                result: dict[str, Any] = {"ok": False, "refused": True,
                                          "output": f"Refused: {error}. Reply with one JSON action."}
            else:
                action = request
                result = conn.request("syscall", loop=loop_id, action=request)["result"]
            score_before = state.get("score", 0.0)
            verdict = conn.request("iteration", loop=loop_id, iteration=iteration, action=action, result=result)

            with open(directory / "trace.jsonl", "a") as trace:
                trace.write(json.dumps({
                    "loop": loop_id, "iteration": iteration, "provider": provider.name, "model": spec.get("model"),
                    "messages": messages + [{"role": "assistant", "content": reply}],
                    "action": action, "result_ok": result.get("ok", False), "refused": result.get("refused", False),
                    "score_before": score_before, "score_after": verdict["score"],
                    "event": verdict.get("event", ""), "status": verdict.get("status", "running"),
                }) + "\n")

            state["steps"] = (state["steps"] + [{
                "iteration": iteration,
                "summary": fingerprint_text(action) if request is not None else "invalid reply",
                "result": result.get("output", ""),
            }])[-20:]
            state.update(iteration=iteration, score=verdict["score"], check=verdict["check"],
                         listing=verdict["listing"], hint=verdict.get("hint", ""),
                         last_result=result.get("output", ""))
            save_json(state_path, state)
            if verdict["directive"] == "stop":
                return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loopos.worker")
    parser.add_argument("--loop", required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.loop, Path(args.home))
    except LoopOSError as exc:
        print(f"worker {args.loop}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
