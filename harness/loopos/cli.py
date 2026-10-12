"""`loopos`: start loops, watch them, and control them.

Run `loopos` with no arguments on a terminal for the interactive prompt; every subcommand also works
directly (`loopos run ...`, `loopos ps`, ...).
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, dataset, paths, tasks, ui
from .client import daemon_running, request, start_daemon
from .model import check_model
from .project import detect_check
from .protocol import LoopOSError
from .supervisor import DEFAULTS, TERMINAL

style = ui.Style()


def say(text: str = "") -> None:
    print(text, flush=True)


# ------------------------------------------------------------------ live loop view

def watch(root: Path, loop_id: str, *, live: bool = True) -> int:
    """Print a loop's story one line per iteration, with a spinner for what it is doing now."""
    info = request(root, "show", loop=loop_id)
    say(ui.loop_header(style, info["loop"], info["spec"]))
    line = ui.LiveLine(style)
    after, started, frame, last_poll, memo = 0, None, 0, 0.0, {}
    try:
        while True:
            now = time.monotonic()
            if now - last_poll >= 0.4:
                last_poll = now
                events = request(root, "logs", loop=loop_id, after=after, limit=1000)["events"]
                final = None
                for event in events:
                    after = event["seq"]
                    if event["kind"] == "created":
                        started = event["ts"]
                    for text in ui.event_lines(style, event, started, memo):
                        line.print(text)
                    if event["kind"] in ui.FINAL:
                        final = event["kind"]
                if final:
                    line.clear()
                    return 0 if final == "done" else 1
                loop = request(root, "show", loop=loop_id)["loop"]
                if loop["status"] in TERMINAL and not events:
                    line.clear()
                    say()
                    return 0 if loop["status"] == "done" else 1
                if not live:
                    return 0
            line.draw(ui.status_line(style, loop, frame))
            frame += 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        line.clear()
        say(style.dim(f"\n  detached — {loop_id} keeps running. Watch again with: watch {loop_id}"))
        return 0


# ------------------------------------------------------------------ commands

def cmd_daemon(args: argparse.Namespace) -> int:
    root = args.root
    if args.action == "start":
        start_daemon(root)
        say(f"  {style.green('●')} supervisor running " + style.dim(f"(pid {request(root, 'ping')['pid']})"))
    elif args.action == "stop":
        if not daemon_running(root):
            say(style.dim("  supervisor is not running"))
            return 0
        request(root, "shutdown")
        for _ in range(100):
            if not daemon_running(root):
                break
            time.sleep(0.1)
        say(f"  {style.dim('●')} supervisor stopped " + style.dim("(unfinished loops resume when it starts again)"))
    else:
        if daemon_running(root):
            info = request(root, "ping")
            say(f"  {style.green('●')} supervisor running " + style.dim(f"(pid {info['pid']}, {info['loops']} loops, "
                                                                         f"state in {ui.short_path(str(root))})"))
        else:
            say(f"  {style.dim('●')} supervisor not running " + style.dim(f"(state in {ui.short_path(str(root))})"))
            return 1
    return 0


def model_spec(args: argparse.Namespace) -> dict[str, Any]:
    """Provider settings from flags, environment and `loopos config`; --script implies scripted replies."""
    script = args.script or os.environ.get("LOOPOS_SCRIPT")
    delay = args.delay if args.delay is not None else float(os.environ.get("LOOPOS_SCRIPT_DELAY") or 0)
    provider = "scripted" if script else config.resolve(args.root, "provider", args.provider)[0]
    if provider == "scripted" and not script:
        raise LoopOSError("scripted replies need --script FILE")
    return {"provider": provider, "model": config.resolve(args.root, "model", args.model)[0],
            "model_url": config.resolve(args.root, "url", args.url)[0],
            "script": str(Path(script).resolve()) if script else None, "script_delay": delay or None}


def require_model(spec: dict[str, Any], skip: bool = False) -> None:
    if spec["provider"] == "scripted" or skip:
        return
    problem = check_model(spec["provider"], spec["model_url"], spec["model"])
    if problem:
        raise LoopOSError(problem)


def cmd_run(args: argparse.Namespace) -> int:
    words = list(args.words)
    workspace = Path(args.workspace) if args.workspace else None
    if words and workspace is None and Path(words[0]).is_dir():
        workspace = Path(words.pop(0))  # `loopos run myproject`
    workspace = (workspace or Path(".")).resolve()
    until = args.until or detect_check(workspace)
    if not until:
        raise LoopOSError(f"can't tell how to check {ui.short_path(str(workspace))}; "
                          "say how with -u, e.g. run -u \"make test\"")
    spec = {
        "goal": " ".join(words) or args.goal or f"Make `{until}` pass", "until": until, "workspace": str(workspace),
        "priority": config.resolve(args.root, "priority", args.priority)[0],
        "max_iters": config.resolve(args.root, "iters", args.iters)[0],
        "max_minutes": args.minutes or DEFAULTS["max_minutes"],
        "allow": sorted(set(DEFAULTS["allow"]) | set(args.allow or [])),
        **model_spec(args),
    }
    require_model(spec, args.no_check)
    start_daemon(args.root)
    loop_id = request(args.root, "run", spec=spec)["id"]
    if args.detach or not style.tty:
        say(f"  {style.accent('▸')} started {style.accent(loop_id)}" + style.dim(f"  ·  watch {loop_id}"))
        return 0
    say()
    return watch(args.root, loop_id)


def cmd_watch(args: argparse.Namespace) -> int:
    say()
    return watch(args.root, args.id)


def cmd_ps(args: argparse.Namespace) -> int:
    say(ui.ps_table(style, request(args.root, "ps", all=args.all)["loops"]))
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    try:
        while True:
            state = request(args.root, "ps", all=True)
            loops = state["loops"]
            output = [ui.banner(style, f"live view · {datetime.now():%H:%M:%S} · Ctrl+C to exit"), "",
                      ui.ps_table(style, loops)]
            decision = state.get("decision")
            if decision and len(decision["candidates"]) > 1:
                rows = "  ".join(f"{c['loop']} {c['base']}+{c['aging_bonus']}" for c in decision["candidates"])
                output += ["", style.dim("  last model turn → ") + style.accent(decision["selected"])
                           + style.dim(f"   (priority + aging: {rows})")]
            recent = [e for e in request(args.root, "logs", limit=60)["events"] if e["kind"] != "syscall"][-8:]
            if recent:
                output += ["", style.dim("  recent")]
                for event in recent:
                    lines = [t for t in ui.event_lines(style, event) if t.strip()]
                    for text in lines or ["       " + style.dim(ui.clip(event["message"], ui.width() - 20))]:
                        output.append(style.dim(f"  {event['loop_id'] or '-':<4}") + text)
            sys.stdout.write("\033[H\033[2J" + "\n".join(output) + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_show(args: argparse.Namespace) -> int:
    info = request(args.root, "show", loop=args.id)
    loop, spec = info["loop"], info["spec"]
    say(ui.loop_header(style, loop, spec).rstrip())
    rows = [
        ("state", ui.state_badge(style, ui.state_of(loop)) + style.dim(f"   pid {loop['pid'] or '-'}")),
        ("progress", f"iteration {loop['iteration']}/{spec['max_iters']}   " + ui.score_bar(style, loop["score"])
         + f" {loop['score']:.2f}" + style.dim(f"   best {loop['best_score']:.2f} · {loop['active_seconds']}s active")),
        ("watchdog", f"{loop['restarts']} restarts · {loop['recoveries']} recoveries"),
    ]
    if loop["note"]:
        rows.append(("note", loop["note"]))
    if info["hint"]:
        rows.append(("hint", info["hint"]))
    for label, value in rows:
        say(f"    {style.dim(label.ljust(9))}{value}")
    check = info.get("check") or {}
    if check:
        tail = [l for l in check["output"].strip().splitlines() if l.strip()][-6:]
        say(f"\n    {style.dim('last check')} exit {check['exit_code']}")
        for text in tail:
            say("    " + style.dim("│ ") + ui.clip(text, ui.width() - 8))
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    if args.id and not args.raw:
        say()
        return watch(args.root, args.id, live=args.follow)
    for event in request(args.root, "logs", loop=args.id, limit=args.lines)["events"]:
        stamp = datetime.fromtimestamp(event["ts"]).strftime("%H:%M:%S")
        say(style.dim(stamp) + f"  {style.accent((event['loop_id'] or '-').ljust(4))} "
            + style.dim(event["kind"].ljust(10)) + " " + event["message"])
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    op = "kill" if args.command == "stop" else args.command
    reply = request(args.root, op, loop=args.id)
    if reply.get("pending"):
        say(f"  {style.yellow('⏸')} {args.id} pauses when its current model turn ends")
    else:
        icon = {"pause": style.yellow("⏸"), "resume": style.accent("▶"), "kill": style.red("■")}[op]
        say(f"  {icon} {args.id} " + {"pause": "paused", "resume": "resumed", "kill": "stopped"}[op])
    return 0


def cmd_model(args: argparse.Namespace) -> int:
    if args.name:
        config.set_value(args.root, "model", args.name)
    model, source = config.resolve(args.root, "model")
    provider, _ = config.resolve(args.root, "provider")
    url, _ = config.resolve(args.root, "url")
    say(f"  {style.accent('◆')} {model}" + style.dim(f"  ·  {provider}"
                                                       f"{' at ' + url if url else ''}  ·  {source}"))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.key and args.value is None:
        raise LoopOSError(f"give a value: config {args.key} VALUE  (or VALUE = default)")
    if args.key:
        try:
            config.set_value(args.root, args.key, args.value)
        except config.ConfigError as exc:
            raise LoopOSError(str(exc)) from exc
    for key in config.SETTINGS:
        value, source = config.resolve(args.root, key)
        say(f"  {style.accent(key.ljust(9))}{str(value if value is not None else '-'):<28}" + style.dim(source))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    out = Path(args.out)
    counts = dataset.export(args.root, out, "all" if args.all else "success")
    say(f"  {style.green('✓')} {counts['written']} training examples → {ui.short_path(str(out.resolve()))}"
        + style.dim(f"   (from {counts['read']} iterations, {counts['duplicates']} duplicates skipped)"))
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    out = Path(args.out or f"{source.name}-tasks")
    try:
        manifest = tasks.make(source, out, args.count, args.until, args.seed)
    except ValueError as exc:
        raise LoopOSError(str(exc)) from exc
    for task in manifest:
        say(f"  {style.accent('•')} {task['name']}  " + style.dim(task["bug"]))
    say(f"\n  {style.green('✓')} {len(manifest)} tasks → {ui.short_path(str(out.resolve()))}"
        + style.dim(f"   next: loopos bench {args.out or out.name}"))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    spec = {**model_spec(args), "max_iters": args.iters,
            "priority": config.resolve(args.root, "priority", args.priority)[0]}
    require_model(spec)
    line, frame = ui.LiveLine(style), [0]

    def progress(kind: str, info: dict[str, Any]) -> None:
        if kind == "finish":
            if info["solved"]:
                n = info["iterations"]
                mark, result = style.green("✓"), f"solved · {n} iter{'s' if n != 1 else ''}"
            elif info["tampered_tests"]:
                mark, result = style.red("✗"), "edited the tests"
            else:
                mark, result = style.red("✗"), info["status"].replace("_", " ")
            line.print(f"  {mark} {info['task']}  {result:<16}" + style.dim(f"{info['seconds']:>4}s   {info['bug']}"))
        elif kind == "tick" and info["running"]:
            frame[0] += 1
            line.draw("    " + style.accent(ui.SPINNER[frame[0] % len(ui.SPINNER)]) + " "
                      + style.dim("running " + ", ".join(info["running"])))

    model = "scripted replies" if spec["provider"] == "scripted" else spec["model"]
    say(style.dim(f"  {model}  ·  {args.parallel} at a time\n"))
    summary = tasks.bench(args.root, Path(args.tasks), parallel=args.parallel, spec=spec, poll=0.25, progress=progress)
    line.clear()
    mean = summary["mean_iterations_when_solved"]
    say(f"\n  {style.accent('◆', bold=True)} solved {summary['solved']}/{summary['tasks']} "
        f"({summary['success_rate']:.0%})" + (f" · {mean} iterations on average" if mean is not None else "")
        + style.dim(f"   results.json in {ui.short_path(summary['run_dir'])}"))
    return 0


# ------------------------------------------------------------------ parser and shell

class ShellError(Exception):
    pass


class ShellParser(argparse.ArgumentParser):
    """In the interactive prompt, report mistakes in one line instead of a usage dump."""

    def error(self, message: str) -> None:
        raise ShellError(f"{message}  ·  {self.prog.removeprefix('loopos').strip() or 'command'} --help for options")


def add_model_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("model (defaults come from `loopos model` / `loopos config`)")
    group.add_argument("-m", "--model")
    group.add_argument("--provider", choices=config.PROVIDERS)
    group.add_argument("--url", "--model-url", dest="url", help="model server URL")
    group.add_argument("--script", help="replay JSON replies instead of a model")
    group.add_argument("--delay", "--script-delay", dest="delay", type=float, help="seconds per scripted reply")
    group.add_argument("-p", "--priority", type=int, help="0-10, higher gets the model first")


def build_parser(parser_class: type[argparse.ArgumentParser] = argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser = parser_class(prog="loopos", description="Run agent loops as supervised processes until a done check passes. "
                                                     "Run with no command for the interactive prompt.")
    parser.add_argument("--home", help="state directory (default: $LOOPOS_HOME or ~/.local/state/loopos)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    run = commands.add_parser("run", help="start a loop and watch it: run · run DIR · run \"goal\"")
    run.add_argument("words", nargs="*", metavar="[DIR] [GOAL]",
                     help="folder to work in (default: here) and what to achieve (default: make the check pass)")
    run.add_argument("-w", "--workspace", help="folder to work in")
    run.add_argument("-u", "--until", help="done check command (default: detected, e.g. pytest -q)")
    run.add_argument("--goal", help=argparse.SUPPRESS)
    run.add_argument("-n", "--iters", "--max-iters", dest="iters", type=int, help="iteration budget (default 30)")
    run.add_argument("--minutes", "--max-minutes", dest="minutes", type=float, help="time budget (default 60)")
    run.add_argument("--allow", action="append", metavar="PROGRAM", help="extra program the loop may run")
    run.add_argument("--no-check", "--no-model-check", dest="no_check", action="store_true",
                     help="start even if the model is unreachable now")
    run.add_argument("-d", "--detach", action="store_true", help="start and return immediately")
    add_model_flags(run)
    run.set_defaults(func=cmd_run)

    for name, text, func in (("watch", "follow a loop live", cmd_watch), ("show", "details of a loop", cmd_show)):
        command = commands.add_parser(name, help=text)
        command.add_argument("id")
        command.set_defaults(func=func)

    ps = commands.add_parser("ps", help="list loops (-a: include finished)")
    ps.add_argument("-a", "--all", action="store_true")
    ps.set_defaults(func=cmd_ps)

    top = commands.add_parser("top", help="live view of all loops")
    top.add_argument("--interval", type=float, default=1)
    top.set_defaults(func=cmd_top)

    logs = commands.add_parser("logs", help="a loop's story, or the raw event log")
    logs.add_argument("id", nargs="?")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("--raw", action="store_true", help="every supervisor event, unformatted")
    logs.add_argument("-n", "--lines", type=int, default=50)
    logs.set_defaults(func=cmd_logs)

    for name, text in (("pause", "freeze a loop (SIGSTOP)"), ("resume", "continue a paused loop (SIGCONT)"),
                       ("stop", "stop a loop for good"), ("kill", argparse.SUPPRESS)):
        control = commands.add_parser(name, help=text)
        control.add_argument("id")
        control.set_defaults(func=cmd_control)

    model = commands.add_parser("model", help="show or choose the model: model · model NAME")
    model.add_argument("name", nargs="?")
    model.set_defaults(func=cmd_model)

    settings = commands.add_parser("config", help="show or set defaults: config · config KEY VALUE")
    settings.add_argument("key", nargs="?", choices=list(config.SETTINGS))
    settings.add_argument("value", nargs="?")
    settings.set_defaults(func=cmd_config)

    task_cmd = commands.add_parser("tasks", help="plant bugs in a passing project: tasks DIR")
    task_cmd.add_argument("source", metavar="DIR", help="project whose tests pass")
    task_cmd.add_argument("-o", "--out", help="output folder (default: DIR-tasks)")
    task_cmd.add_argument("-n", "--count", type=int, default=10)
    task_cmd.add_argument("-u", "--until", default=tasks.DEFAULT_CHECK, help="check that must pass before mutation")
    task_cmd.add_argument("--seed", type=int, default=0)
    task_cmd.set_defaults(func=cmd_tasks)

    bench = commands.add_parser("bench", help="score the model on practice tasks: bench DIR")
    bench.add_argument("tasks", metavar="DIR", help="folder made by `loopos tasks`")
    bench.add_argument("-j", "--parallel", type=int, default=2, help="loops at once (default 2)")
    bench.add_argument("-n", "--iters", type=int, default=20, help="iteration budget per task (default 20)")
    add_model_flags(bench)
    bench.set_defaults(func=cmd_bench)

    export = commands.add_parser("export", help="write training data: export [FILE]")
    export.add_argument("out", nargs="?", default="loopos-dataset.jsonl")
    export.add_argument("--all", action="store_true", help="every iteration, not just useful ones")
    export.set_defaults(func=cmd_export)

    daemon = commands.add_parser("daemon", help="supervisor: daemon start|stop|status")
    daemon.add_argument("action", choices=["start", "stop", "status"])
    daemon.set_defaults(func=cmd_daemon)
    return parser


SHELL_HELP = [
    ("run", "start a loop here and watch it"),
    ("run DIR  ·  run \"goal\"", "another folder  ·  with a goal"),
    ("ps  ·  top", "list loops  ·  live view"),
    ("watch ID  ·  show ID  ·  logs ID", "follow  ·  details  ·  story"),
    ("pause ID  ·  resume ID  ·  stop ID", "control a loop"),
    ("model NAME", "choose the model (remembered)"),
    ("tasks DIR  ·  bench DIR", "practice tasks  ·  score the model"),
    ("help  ·  clear  ·  exit", ""),
]


def shell_help() -> None:
    for command, text in SHELL_HELP:
        say(f"  {style.accent(command.ljust(36))}{style.dim(text)}")


def shell(root: Path) -> int:
    """Interactive prompt under the logo; each line is a loopos subcommand."""
    parser = build_parser(ShellParser)
    commands = set(next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices)
    say(ui.banner(style))
    say()
    shell_help()
    try:
        start_daemon(root)  # so ps, logs and run work straight away
    except LoopOSError as exc:
        say(style.red(f"  ✖ {exc}"))
    say()
    try:
        import readline
        history = root / "history"
        try:
            readline.read_history_file(history)
        except OSError:
            pass
    except ImportError:
        readline = None
    # \001 and \002 tell readline the colour codes take no space on screen.
    prompt = ("\001" + "\033[38;5;149m\002" if style.color else "") + "loopos" \
        + ("\001\033[0m\002" if style.color else "") + " ❯ "
    while True:
        try:
            line = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            say()
            break
        if not line:
            continue
        if line in {"exit", "quit", "q"}:
            break
        if line in {"help", "?"}:
            shell_help()
            continue
        if line == "clear":
            sys.stdout.write("\033[H\033[2J")
            say(ui.banner(style))
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            say(style.red(f"  ✖ {exc}"))
            continue
        if tokens[0] == "loopos":
            tokens = tokens[1:]
        if not tokens or tokens[0] not in commands:
            say(style.red(f"  ✖ unknown command {tokens[0] if tokens else ''!r}") + style.dim("  ·  type help"))
            continue
        try:
            args = parser.parse_args(tokens)
        except ShellError as exc:
            say(style.red(f"  ✖ {exc}"))
            continue
        except SystemExit:  # --help was printed
            continue
        if not getattr(args, "func", None):
            shell_help()
            continue
        args.root = root
        try:
            args.func(args)
        except LoopOSError as exc:
            say(style.red(f"  ✖ {exc}"))
        except KeyboardInterrupt:
            say()
        say()
    if readline:
        try:
            readline.write_history_file(root / "history")
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = Path(args.home).expanduser().resolve() if args.home else paths.home()
    args.root.mkdir(parents=True, exist_ok=True)
    if not getattr(args, "func", None):
        if sys.stdin.isatty() and sys.stdout.isatty():
            return shell(args.root)
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except LoopOSError as exc:
        hint = "  Start it with: loopos daemon start" if "not running" in str(exc) else ""
        print(style.red(f"  ✖ {exc}") + style.dim(hint), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
