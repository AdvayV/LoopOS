"""Collect verified, explicitly scripted teaching traces from planted bugs.

These are oracle demonstrations, not evidence of a model solving tasks. Training and
validation are split by task by the dataset exporter. Held-out bug tasks stay separate.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

from loopos import dataset, tasks
from loopos.client import request
from loopos.supervisor import Supervisor, TERMINAL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--per-project", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.per_project <= 20:
        parser.error("--per-project must be between 1 and 20")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = out / "state"
    root.mkdir()
    check = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    supervisor = Supervisor(root, tick=0.1)
    thread = threading.Thread(target=supervisor.serve, daemon=True)
    thread.start()
    if not supervisor.ready.wait(5):
        raise RuntimeError("teaching supervisor could not start")
    examples = Path(__file__).resolve().parents[1] / "examples"
    solved = []
    try:
        for source in sorted(examples.iterdir()):
            if not source.is_dir():
                continue
            folder = out / f"{source.name}-tasks"
            manifest = tasks.make(source, folder, count=args.per_project, check=check,
                                  holdout=2, holdout_out=out / f"{source.name}-holdout")
            print(f"{source.name}: {len(manifest)} teaching tasks; separate held-out bugs", flush=True)
            for index, task in enumerate(manifest):
                workspace = folder / task["name"]
                mutation = task["mutation"]
                path = mutation["path"]
                original = (source / path).read_text()
                broken = (workspace / path).read_text()
                old_line = broken.splitlines(keepends=True)[mutation["line"] - 1]
                new_line = original.splitlines(keepends=True)[mutation["line"] - 1]
                # The final repair is either a precise edit or an original source replacement.
                repair = ({"action": "edit_file", "path": path, "find": old_line, "replace": new_line,
                           "reason": "Restore the original expression on the failing line."} if index % 2 == 0
                          else {"action": "write_file", "path": path, "content": original,
                                "reason": "Restore the verified source implementation."})
                actions = [
                    {"action": "list_dir", "path": ".", "reason": "Locate the project files."},
                    {"action": "read_file", "path": path, "reason": "Inspect the failing implementation."},
                    {"action": "run", "command": "python -m pytest -q -p no:cacheprovider",
                     "reason": "Read the failing tests before repairing the source."},
                    repair,
                ]
                script = out / f"{source.name}-{task['name']}-oracle.json"
                script.write_text(json.dumps(actions))
                loop_id = request(root, "run", spec={"workspace": str(workspace), "goal": task["goal"],
                                                    "until": check, "provider": "scripted", "script": str(script),
                                                    "max_iters": len(actions) + 1, "max_minutes": 2})["id"]
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    state = request(root, "show", loop=loop_id)["loop"]
                    if state["status"] in TERMINAL:
                        break
                    time.sleep(0.1)
                else:
                    request(root, "kill", loop=loop_id)
                    raise RuntimeError(f"teaching task {loop_id} exceeded its deadline")
                if state["status"] != "done":
                    raise RuntimeError(f"oracle repair failed for {source.name}/{task['name']}: {state['status']}")
                solved.append({"project": source.name, "task": task["name"], "loop": loop_id})
                print(f"  {source.name}/{task['name']}: verified done ({len(solved)} completed)", flush=True)
        counts = dataset.export(root, out / "loopos-dataset.jsonl", val_fraction=0.15)
        report = {"source": "scripted oracle demonstrations", "model_quality_claim": False,
                  "tasks": solved, "dataset": counts}
        (out / "collection.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["dataset"]), flush=True)
        return 0 if counts["written"] and counts["validation"] else 1
    finally:
        supervisor.stopping.set()
        thread.join(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
