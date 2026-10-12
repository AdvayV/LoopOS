"""`loopos`: start loops, watch them, and control them."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import dataset, paths, tasks
from .client import daemon_running, request, start_daemon
from .model import check_ollama
from .protocol import LoopOSError
from .supervisor import DEFAULTS, TERMINAL


# ------------------------------------------------------------------ formatting

def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def format_event(event: dict[str, Any]) -> str:
    return f"{clock(event['ts'])}  {event['loop_id'] or '-':<4} {event['kind']:<10} {event['message']}"


def state_label(loop: dict[str, Any]) -> str:
    if loop["status"] == "running" and loop["holding_model"]:
        return "thinking"
    if loop["status"] == "running" and loop["waiting_model"]:
        return "waiting"
    return loop["status"]


def table(loops: list[dict[str, Any]]) -> str:
    if not loops:
        return "No loops. Start one with: loopos run --goal \"...\" --until \"...\""
    rows = [("ID", "STATE", "PRI", "ITER", "SCORE", "BEST", "GOAL", "NOTE")]
    for loop in loops:
        rows.append((loop["id"], state_label(loop), str(loop["priority"]), f"{loop['iteration']}/{loop['max_iters']}",
                     f"{loop['score']:.2f}", f"{loop['best_score']:.2f}", loop["goal"][:38], loop["note"][:60]))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) + "  " + row[-1] for row in rows)


# ------------------------------------------------------------------ commands

def cmd_daemon(args: argparse.Namespace) -> int:
    root = args.root
    if args.action == "start":
        start_daemon(root)
        print(f"Supervisor running (pid {request(root, 'ping')['pid']}).")
    elif args.action == "stop":
        if not daemon_running(root):
            print("Supervisor is not running.")
            return 0
        request(root, "shutdown")
        for _ in range(100):
            if not daemon_running(root):
                break
            time.sleep(0.1)
        print("Supervisor stopped. Unfinished loops resume when it starts again.")
    else:
        if daemon_running(root):
            info = request(root, "ping")
            print(f"Supervisor running (pid {info['pid']}, {info['loops']} loops). State: {root}")
        else:
            print(f"Supervisor not running. State: {root}")
            return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    spec = {
        "goal": args.goal, "until": args.until, "workspace": str(Path(args.workspace).resolve()),
        "priority": args.priority, "max_iters": args.max_iters, "max_minutes": args.max_minutes,
        "provider": args.provider, "model": args.model, "ollama_url": args.ollama_url,
        "script": str(Path(args.script).resolve()) if args.script else None,
        "allow": sorted(set(DEFAULTS["allow"]) | set(args.allow or [])),
    }
    if args.provider == "ollama" and not args.no_model_check:
        problem = check_ollama(args.ollama_url, args.model)
        if problem:
            raise LoopOSError(problem)
    start_daemon(args.root)
    loop_id = request(args.root, "run", spec=spec)["id"]
    print(f"Started loop {loop_id}. Watch: loopos logs {loop_id} -f   Status: loopos ps")
    if args.follow:
        return follow(args.root, loop_id)
    return 0


def follow(root: Path, loop_id: str | None, after: int = 0) -> int:
    try:
        while True:
            events = request(root, "logs", loop=loop_id, after=after)["events"]
            for event in events:
                print(format_event(event), flush=True)
                after = event["seq"]
            if loop_id and request(root, "show", loop=loop_id)["loop"]["status"] in TERMINAL and not events:
                status = request(root, "show", loop=loop_id)["loop"]
                print(f"\nLoop {loop_id} finished: {status['status']}. {status['note']}")
                return 0 if status["status"] == "done" else 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDetached; the loop keeps running.")
        return 0


def cmd_ps(args: argparse.Namespace) -> int:
    print(table(request(args.root, "ps", all=args.all)["loops"]))
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    try:
        while True:
            state = request(args.root, "ps", all=True)
            events = request(args.root, "logs", limit=12)["events"]
            output = [f"LoopOS  {datetime.now():%H:%M:%S}  (Ctrl+C to exit)", "", table(state["loops"])]
            decision = state.get("decision")
            if decision:
                rows = ", ".join(f"{c['loop']}: {c['base']}+{c['aging_bonus']}={c['score']}" for c in decision["candidates"])
                output += ["", f"Last model turn -> {decision['selected']}   ({rows})"]
            output += ["", "Recent events:"] + [format_event(e) for e in events]
            sys.stdout.write("\033[H\033[2J" + "\n".join(output) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_show(args: argparse.Namespace) -> int:
    info = request(args.root, "show", loop=args.id)
    loop, spec = info["loop"], info["spec"]
    print(f"Loop {loop['id']}: {loop['status']}  (pid {loop['pid'] or '-'})")
    print(f"  goal       {spec['goal']}")
    print(f"  done when  `{spec['until']}` exits 0")
    print(f"  workspace  {spec['workspace']}")
    print(f"  model      {spec['provider']}:{spec.get('model')}  priority {spec['priority']}")
    print(f"  progress   iteration {loop['iteration']}/{spec['max_iters']}, score {loop['score']:.2f}, "
          f"best {loop['best_score']:.2f}, {loop['active_seconds']}s active")
    print(f"  watchdog   {loop['restarts']} restarts, {loop['recoveries']} recoveries")
    if loop["note"]:
        print(f"  note       {loop['note']}")
    if info["hint"]:
        print(f"  hint       {info['hint']}")
    check = info.get("check") or {}
    if check:
        print(f"\nLatest done check (exit {check['exit_code']}):\n{check['output'][-1500:]}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    if args.follow:
        return follow(args.root, args.id)
    for event in request(args.root, "logs", loop=args.id, limit=args.lines)["events"]:
        print(format_event(event))
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    reply = request(args.root, args.command, loop=args.id)
    if reply.get("pending"):
        print(f"{args.id}: pause requested; it stops when its current model turn ends.")
    else:
        print(f"{args.id}: {args.command} ok")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    counts = dataset.export(args.root, Path(args.out), args.mode)
    print(f"Read {counts['read']} iterations; wrote {counts['written']} examples to {args.out} "
          f"({counts['duplicates']} duplicates skipped).")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    manifest = tasks.make(Path(args.source), Path(args.out), args.count, args.until, args.seed)
    for task in manifest:
        print(f"{task['name']}  {task['bug']}")
    print(f"\nWrote {len(manifest)} tasks to {args.out} (manifest: {Path(args.out) / 'tasks.json'}).")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    spec = {"provider": args.provider, "model": args.model, "ollama_url": args.ollama_url,
            "max_iters": args.max_iters, "priority": args.priority,
            "script": str(Path(args.script).resolve()) if args.script else None}
    if args.provider == "ollama":
        problem = check_ollama(args.ollama_url, args.model)
        if problem:
            raise LoopOSError(problem)
    summary = tasks.bench(args.root, Path(args.tasks), parallel=args.parallel, spec=spec)
    rows = [("TASK", "LOOP", "RESULT", "ITER", "SECS", "BUG")]
    for r in summary["results"]:
        result = "solved" if r["solved"] else ("edited tests" if r["tampered_tests"] else r["status"])
        rows.append((r["task"], r["loop"], result, str(r["iterations"]), str(r["seconds"]), r["bug"]))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    print("\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)) + "  " + row[-1] for row in rows))
    mean = summary["mean_iterations_when_solved"]
    print(f"\nSolved {summary['solved']}/{summary['tasks']} ({summary['success_rate']:.0%})"
          + (f", {mean} iterations on average" if mean is not None else "")
          + f". Details: {summary['run_dir']}/results.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loopos", description="Run agent loops as supervised processes until a done check passes.")
    parser.add_argument("--home", help="state directory (default: $LOOPOS_HOME or ~/.local/state/loopos)")
    commands = parser.add_subparsers(dest="command", required=True)

    daemon = commands.add_parser("daemon", help="start, stop or check the supervisor")
    daemon.add_argument("action", choices=["start", "stop", "status"])
    daemon.set_defaults(func=cmd_daemon)

    run = commands.add_parser("run", help="start a loop")
    run.add_argument("--goal", required=True, help="what the loop should achieve")
    run.add_argument("--until", required=True, help="done check command; the loop ends when it exits 0")
    run.add_argument("--workspace", default=".", help="folder the loop may read and change (default: .)")
    run.add_argument("--priority", type=int, default=DEFAULTS["priority"], help="0-10, higher gets the model first")
    run.add_argument("--max-iters", type=int, default=DEFAULTS["max_iters"])
    run.add_argument("--max-minutes", type=float, default=DEFAULTS["max_minutes"])
    run.add_argument("--provider", choices=["ollama", "scripted"], default=DEFAULTS["provider"])
    run.add_argument("--model", default=os.environ.get("LOOPOS_MODEL", DEFAULTS["model"]))
    run.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST_URL", DEFAULTS["ollama_url"]))
    run.add_argument("--script", help="JSON list of replies for --provider scripted")
    run.add_argument("--allow", action="append", metavar="PROGRAM", help="extra program the loop may run")
    run.add_argument("--no-model-check", action="store_true", help="start even if the model is unreachable now")
    run.add_argument("-f", "--follow", action="store_true", help="stream events until the loop finishes")
    run.set_defaults(func=cmd_run)

    ps = commands.add_parser("ps", help="list loops")
    ps.add_argument("-a", "--all", action="store_true", help="include finished loops")
    ps.set_defaults(func=cmd_ps)

    top = commands.add_parser("top", help="live view of loops and scheduler decisions")
    top.add_argument("--interval", type=float, default=2)
    top.set_defaults(func=cmd_top)

    show = commands.add_parser("show", help="details of one loop")
    show.add_argument("id")
    show.set_defaults(func=cmd_show)

    logs = commands.add_parser("logs", help="event log (all loops, or one)")
    logs.add_argument("id", nargs="?")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.set_defaults(func=cmd_logs)

    for name, text in (("pause", "freeze a loop (SIGSTOP) at a safe point"),
                       ("resume", "continue a paused loop (SIGCONT)"), ("kill", "stop a loop for good")):
        control = commands.add_parser(name, help=text)
        control.add_argument("id")
        control.set_defaults(func=cmd_control)

    task_cmd = commands.add_parser("tasks", help="generate practice tasks by planting bugs in a passing project")
    task_sub = task_cmd.add_subparsers(dest="tasks_action", required=True)
    make = task_sub.add_parser("make", help="plant one bug per task")
    make.add_argument("--from", dest="source", required=True, help="project whose tests pass")
    make.add_argument("--out", required=True, help="folder for task-NNN/ and tasks.json")
    make.add_argument("--count", type=int, default=10)
    make.add_argument("--until", default=tasks.DEFAULT_CHECK, help="check command that must pass before mutation")
    make.add_argument("--seed", type=int, default=0)
    make.set_defaults(func=cmd_tasks)

    bench = commands.add_parser("bench", help="run a loop on every task and report the success rate")
    bench.add_argument("tasks", help="folder created by `loopos tasks make`")
    bench.add_argument("--parallel", type=int, default=2, help="loops running at once (they share the model)")
    bench.add_argument("--max-iters", type=int, default=20)
    bench.add_argument("--priority", type=int, default=DEFAULTS["priority"])
    bench.add_argument("--provider", choices=["ollama", "scripted"], default=DEFAULTS["provider"])
    bench.add_argument("--model", default=os.environ.get("LOOPOS_MODEL", DEFAULTS["model"]))
    bench.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST_URL", DEFAULTS["ollama_url"]))
    bench.add_argument("--script", help="JSON replies for --provider scripted")
    bench.set_defaults(func=cmd_bench)

    export = commands.add_parser("export", help="write a fine-tuning dataset from loop traces")
    export.add_argument("--out", default="loopos-dataset.jsonl")
    export.add_argument("--mode", choices=["success", "all"], default="success")
    export.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.root = Path(args.home).expanduser().resolve() if args.home else paths.home()
    args.root.mkdir(parents=True, exist_ok=True)
    try:
        return args.func(args)
    except LoopOSError as exc:
        hint = " Start it with: loopos daemon start" if "not running" in str(exc) else ""
        print(f"loopos: {exc}.{hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
