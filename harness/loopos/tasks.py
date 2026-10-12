"""Practice tasks: plant one realistic bug in a passing project, then benchmark loops on fixing it.

    loopos tasks examples/inventory -n 10 --holdout 3     -> inventory-tasks/ and inventory-holdout/
    loopos bench inventory-tasks -j 2

`make` keeps only mutations the tests catch. The manifest (tasks.json) records each bug and a hash of
every test file; it lives next to the task folders, never inside them, so a loop cannot read it.
A held-out set uses different mutations from the same project, for measuring a model that was
trained on the main set. `bench` copies each task to a scratch folder, runs a loop on it, and
flags loops that edited tests.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import random
import shlex
import shutil
import time
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import checkpoint
from .client import request, start_daemon
from .syscalls import Policy, run_command

DEFAULT_CHECK = "python -m pytest -q -p no:cacheprovider"
GOAL = ("The test suite fails. Find and fix the bug in the source code so that `{check}` passes. "
        "Do not modify the tests.")

_BINARY = {ast.Add: ("+", "-"), ast.Sub: ("-", "+"), ast.Mult: ("*", "+"), ast.FloorDiv: ("//", "/"),
           ast.Mod: ("%", "//")}
_COMPARE = {ast.Lt: ("<", "<="), ast.LtE: ("<=", "<"), ast.Gt: (">", ">="), ast.GtE: (">=", ">"),
            ast.Eq: ("==", "!="), ast.NotEq: ("!=", "==")}
_BOOLEAN = {ast.And: ("and", "or"), ast.Or: ("or", "and")}


@dataclass(frozen=True)
class Mutation:
    path: str
    line: int
    col: int      # character offset of the replaced token
    old: str
    new: str

    def describe(self) -> str:
        return f"{self.path}:{self.line}: `{self.old}` -> `{self.new}`"

    def apply(self, source: str) -> str:
        lines = source.splitlines(keepends=True)
        text = lines[self.line - 1]
        assert text[self.col:self.col + len(self.old)] == self.old, "source changed since analysis"
        lines[self.line - 1] = text[:self.col] + self.new + text[self.col + len(self.old):]
        return "".join(lines)


def is_test_file(relative: Path) -> bool:
    return (relative.name.startswith("test_") or relative.name.endswith("_test.py")
            or relative.name == "conftest.py" or "tests" in relative.parts)


def _token_between(tokens: list[tokenize.TokenInfo], start: tuple[int, int], end: tuple[int, int],
                   text: str) -> tokenize.TokenInfo | None:
    found = [t for t in tokens if t.start >= start and t.end <= end and t.string == text]
    return found[0] if len(found) == 1 else None


def mutations(source: str, path: str) -> list[Mutation]:
    """Every single-token mutation of `source` this module knows how to make."""
    if not source.isascii():  # ast offsets are bytes, tokenize offsets characters; keep them equal
        return []
    tree = ast.parse(source)
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    found: list[Mutation] = []

    def add(token: tokenize.TokenInfo | None, new: str) -> None:
        if token is not None:
            found.append(Mutation(path, token.start[0], token.start[1], token.string, new))

    def span(left: ast.AST, right: ast.AST) -> tuple[tuple[int, int], tuple[int, int]]:
        return (left.end_lineno, left.end_col_offset), (right.lineno, right.col_offset)

    annotations = {id(n) for node in ast.walk(tree) for attr in ("annotation", "returns")
                   if (ann := getattr(node, attr, None)) is not None for n in ast.walk(ann)}
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            old, new = _BINARY[type(node.op)]
            add(_token_between(tokens, *span(node.left, node.right), old), new)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
            old, new = _COMPARE[type(node.ops[0])]
            add(_token_between(tokens, *span(node.left, node.comparators[0]), old), new)
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            old, new = _BOOLEAN[type(node.op)]
            add(_token_between(tokens, *span(node.values[0], node.values[1]), old), new)
        elif (isinstance(node, ast.Constant) and type(node.value) is int and id(node) not in annotations
              and 0 <= node.value < 10_000):
            start = (node.lineno, node.col_offset)
            add(_token_between(tokens, start, (node.end_lineno, node.end_col_offset), str(node.value)),
                str(node.value + 1))
    return sorted(set(found), key=lambda m: (m.path, m.line, m.col))


def _test_hashes(project: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256((project / p).read_bytes()).hexdigest()
            for p in checkpoint.tracked_files(project) if is_test_file(p)}


def _check(project: Path, check: str) -> int:
    code, _ = run_command(shlex.split(check), Policy(workspace=project.resolve()), timeout=120,
                          enforce_allowlist=False)
    return code


def make(source: Path, out: Path, count: int, check: str = DEFAULT_CHECK, seed: int = 0,
         holdout: int = 0, holdout_out: Path | None = None) -> list[dict[str, Any]]:
    """Write `count` tasks to `out` and, if asked, `holdout` further tasks (different bugs) to
    `holdout_out`. Returns the manifest of `out`."""
    source = source.resolve()
    if _check(source, check) != 0:
        raise ValueError(f"`{check}` must pass in {source} before bugs are planted")
    pool = [m for relative in checkpoint.tracked_files(source)
            if relative.suffix == ".py" and not is_test_file(relative)
            for m in mutations((source / relative).read_text(), str(relative))]
    random.Random(seed).shuffle(pool)
    targets = [(out, count)] + ([(holdout_out or out.with_name(out.name + "-holdout"), holdout)] if holdout else [])
    manifests: list[list[dict[str, Any]]] = []
    pool_iter = iter(pool)
    for folder, wanted in targets:
        folder.mkdir(parents=True, exist_ok=True)
        manifest: list[dict[str, Any]] = []
        for mutation in pool_iter:
            if len(manifest) >= wanted:
                break
            name = f"task-{len(manifest) + 1:03d}"
            task = folder / name
            shutil.rmtree(task, ignore_errors=True)
            shutil.copytree(source, task, ignore=shutil.ignore_patterns(*checkpoint.IGNORED_DIRS))
            target = task / mutation.path
            target.write_text(mutation.apply(target.read_text()))
            if _check(task, check) == 0:  # the tests do not notice this mutation; useless as a task
                shutil.rmtree(task)
                continue
            manifest.append({"name": name, "goal": GOAL.format(check=check), "until": check,
                             "bug": mutation.describe(), "mutation": asdict(mutation), "tests": _test_hashes(task)})
        (folder / "tasks.json").write_text(json.dumps(manifest, indent=2))
        manifests.append(manifest)
    return manifests[0]


def bench(root: Path, tasks_dirs: Path | list[Path], *, parallel: int = 2, spec: dict[str, Any] | None = None,
          poll: float = 1.0, progress: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Run a loop on every task in the given folders (at most `parallel` at once) and summarize.

    `progress(kind, info)` is called with "start" ({task, loop}), "finish" (a result row) and, every
    poll, "tick" ({running: [task names]}), so callers can show results as they arrive.
    """
    report = progress or (lambda kind, info: None)
    dirs = [tasks_dirs] if isinstance(tasks_dirs, Path) else list(tasks_dirs)
    manifest = []
    for directory in dirs:
        for task in json.loads((directory / "tasks.json").read_text()):
            label = task["name"] if len(dirs) == 1 else f"{directory.name}/{task['name']}"
            manifest.append({**task, "label": label, "source": directory / task["name"]})
    run_dir = root / "bench" / time.strftime("%Y%m%d-%H%M%S")
    start_daemon(root)
    pending, running, results = list(manifest), {}, []
    while pending or running:
        while pending and len(running) < parallel:
            task = pending.pop(0)
            workspace = run_dir / task["label"]
            shutil.copytree(task["source"], workspace)
            loop_spec = {**(spec or {}), "goal": task["goal"], "until": task["until"], "workspace": str(workspace)}
            loop_id = request(root, "run", spec=loop_spec)["id"]
            running[loop_id] = (task, workspace, time.monotonic())
            report("start", {"task": task["label"], "loop": loop_id})
        time.sleep(poll)
        report("tick", {"running": [task["label"] for task, _, _ in running.values()]})
        states = {loop["id"]: loop for loop in request(root, "ps", all=True)["loops"]}
        for loop_id in [i for i in running if states[i]["status"] in {"done", "stopped", "gave_up", "budget", "failed"}]:
            task, workspace, started = running.pop(loop_id)
            loop = states[loop_id]
            tampered = sorted(p for p, digest in task["tests"].items()
                              if not (workspace / p).is_file()
                              or hashlib.sha256((workspace / p).read_bytes()).hexdigest() != digest)
            solved = loop["status"] == "done" and not tampered
            results.append({"task": task["label"], "loop": loop_id, "status": loop["status"], "solved": solved,
                            "tampered_tests": tampered, "iterations": loop["iteration"],
                            "seconds": round(time.monotonic() - started), "bug": task["bug"]})
            report("finish", results[-1])
    results.sort(key=lambda r: r["task"])
    solved = [r for r in results if r["solved"]]
    summary = {
        "tasks": len(results), "solved": len(solved),
        "success_rate": round(len(solved) / len(results), 3) if results else 0.0,
        "mean_iterations_when_solved": round(sum(r["iterations"] for r in solved) / len(solved), 1) if solved else None,
        "results": results, "spec": spec or {}, "run_dir": str(run_dir),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results.json").write_text(json.dumps(summary, indent=2))
    return summary
