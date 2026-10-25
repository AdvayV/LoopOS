"""Three public forms: loopos <syscall>, loopos check_status, loopos show results."""
from __future__ import annotations

import argparse
import json
import shlex
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import paths, syscalls
from .calls import Calls


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loopos", usage="loopos [--home DIR] <syscall> [arguments]\n"
                                    "       loopos [--home DIR] check_status [ID]\n"
                                    "       loopos [--home DIR] show results [ID]",
                                    description=__doc__)
    parser.add_argument("--home", help="state directory (or $LOOPOS_HOME)")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for name in syscalls.ACTIONS:
        command = commands.add_parser(name, help=f"execute the {name} syscall")
        command.add_argument("-w", "--workspace", type=Path, default=Path.cwd())
        command.add_argument("--timeout", type=positive_int, default=120, help="command timeout in seconds")
        if name == "finish":
            command.add_argument("--until", required=True, help="command that must pass to verify completion")
        elif name == "run":
            command.add_argument("argv", nargs=argparse.REMAINDER, help="quoted command, or program and arguments")
        else:
            command.add_argument("path", **({"nargs": "?", "default": "."} if name == "list_dir" else {}))
            for field in ("content",) if name == "write_file" else ("find", "replace") if name == "edit_file" else ():
                command.add_argument(field)
    commands.add_parser("check_status", help="status of the latest call, or a call ID").add_argument(
        "id", nargs="?", type=positive_int)
    show = commands.add_parser("show", help="show the actual saved result of a call")
    show.add_argument("results", choices=["results"])
    show.add_argument("id", nargs="?", type=positive_int)
    return parser


def describe(record: dict) -> str:
    return f"Call {record['id']}: {record['action']['action']} — {record['status']}"


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def perform(args: argparse.Namespace, calls: Calls) -> int:
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    action = {"action": args.command}
    for field in syscalls.ACTIONS[args.command]:
        if field != "command":
            action[field] = getattr(args, field)
    if args.command == "run":
        words = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        action["command"] = words[0] if len(words) == 1 else shlex.join(words)
    if args.command == "finish":
        action["until"] = args.until
    call_id = calls.start(action, workspace)
    policy = syscalls.Policy(workspace=workspace, command_timeout=args.timeout)
    jobs = syscalls.Jobs()

    def interrupted(_sig, _frame):
        raise KeyboardInterrupt

    old_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        print(f"Call {call_id}: {args.command} — running", flush=True)
        if args.command == "finish":
            result = syscalls.execute({"action": "run", "command": args.until}, policy, jobs)
            result["output"] = ("Done check passed.\n" if result["ok"] else "Done check did not pass.\n") + result["output"]
        else:
            result = syscalls.execute(action, policy, jobs)
        calls.finish(call_id, result)
    except KeyboardInterrupt:
        jobs.signal(signal.SIGKILL)
        calls.finish(call_id, {"ok": False, "exit_code": 130,
                              "output": "Execution interrupted; workspace changes may be partial."}, "interrupted")
        print(f"Call {call_id}: interrupted. Use loopos show results {call_id}.", flush=True)
        return 130
    finally:
        signal.signal(signal.SIGTERM, old_handler)
    print(f"{describe(calls.get(call_id))}. Use loopos show results {call_id}.", flush=True)
    if result.get("refused"):
        return 2
    code = result.get("exit_code", 0 if result["ok"] else 1)
    return 128 - code if code < 0 else code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    calls = None
    try:
        root = Path(args.home).expanduser().resolve() if args.home else paths.home()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        calls = Calls(root)
        if args.command in syscalls.ACTIONS:
            return perform(args, calls)
        record = calls.get(args.id)
        if record is None:
            print(f"No call {args.id}." if args.id else "No syscalls have been executed.")
            return 1
        print(describe(record))
        print(f"Workspace: {record['workspace']}")
        print(f"Started: {timestamp(record['started'])}")
        if record["ended"] is not None:
            print(f"Finished: {timestamp(record['ended'])}")
            print(f"Duration: {max(0, record['ended'] - record['started']):.3f}s")
        if args.command == "show":
            print("Request: " + json.dumps(record["action"], ensure_ascii=False))
            result = record["result"]
            if result is None:
                print("Result is pending; the call is still running.")
                return 1
            if "exit_code" in result:
                print(f"Exit code: {result['exit_code']}")
            output = result["output"]
            print("Output:")
            print(output if output else "(empty output)", end="" if output.endswith("\n") else "\n")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"loopos: {exc}", file=sys.stderr)
        return 1
    finally:
        if calls is not None:
            calls.close()


if __name__ == "__main__":
    raise SystemExit(main())
