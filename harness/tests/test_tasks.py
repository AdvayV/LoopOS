from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loopos import tasks

from conftest import TESTS

GOOD = "def add(a, b):\n    return a + b\n\n\ndef small(n: int) -> bool:\n    return n < 10 and n >= 0\n"
TESTS_SMALL = TESTS + "\nfrom calc import small\n\ndef test_small():\n    assert small(9) and not small(10) and small(0)\n"
CHECK = f"{sys.executable} -m pytest -q -p no:cacheprovider"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "calc.py").write_text(GOOD)
    (root / "test_calc.py").write_text(TESTS_SMALL)
    return root


def test_mutations_cover_operators_comparisons_booleans_and_constants():
    found = {(m.line, m.old, m.new) for m in tasks.mutations(GOOD, "calc.py")}
    assert found == {(2, "+", "-"), (6, "<", "<="), (6, "and", "or"), (6, ">=", ">"), (6, "10", "11"), (6, "0", "1")}
    mutation = next(m for m in tasks.mutations(GOOD, "calc.py") if m.old == "and")
    assert "n < 10 or n >= 0" in mutation.apply(GOOD)


def test_annotations_are_not_mutated():
    assert tasks.mutations("x: list[int] = [1]\n", "a.py") == [tasks.Mutation("a.py", 1, 16, "1", "2")]


def test_make_keeps_only_bugs_the_tests_catch(project, tmp_path):
    out = tmp_path / "tasks"
    manifest = tasks.make(project, out, count=10, check=CHECK)
    # n >= 0 -> n > 0 is caught (small(0)); 10 -> 11 is caught (small(10)); every kept task must fail.
    assert manifest and all(tasks._check(out / t["name"], CHECK) != 0 for t in manifest)
    assert json.loads((out / "tasks.json").read_text()) == manifest
    assert not any((out / t["name"] / "tasks.json").exists() for t in manifest)
    assert set(manifest[0]["tests"]) == {"test_calc.py"}


def test_make_requires_a_passing_project(project, tmp_path):
    (project / "calc.py").write_text(GOOD.replace("a + b", "a - b"))
    with pytest.raises(ValueError, match="must pass"):
        tasks.make(project, tmp_path / "tasks", count=1, check=CHECK)


def test_bench_scores_solutions_and_catches_test_edits(harness, project, tmp_path):
    out = tmp_path / "tasks"
    tasks.make(project, out, count=2, check=CHECK)
    fix = tmp_path / "fix.json"
    fix.write_text(json.dumps([{"action": "write_file", "path": "calc.py", "content": GOOD, "reason": "restore"}]))
    summary = tasks.bench(harness.root, out, parallel=2, spec={"provider": "scripted", "script": str(fix)}, poll=0.2)
    assert summary["solved"] == 2 and summary["success_rate"] == 1.0

    cheat = tmp_path / "cheat.json"
    cheat.write_text(json.dumps([{"action": "write_file", "path": "test_calc.py", "content": "def test_ok():\n    pass\n",
                                  "reason": "make it pass"}]))
    summary = tasks.bench(harness.root, out, parallel=1, spec={"provider": "scripted", "script": str(cheat)}, poll=0.2)
    assert summary["solved"] == 0
    assert all(r["status"] == "done" and r["tampered_tests"] == ["test_calc.py"] for r in summary["results"])


def test_holdout_tasks_use_different_bugs(project, tmp_path):
    out, held = tmp_path / "p-tasks", tmp_path / "p-holdout"
    manifest = tasks.make(project, out, count=2, check=CHECK, holdout=1, holdout_out=held)
    held_manifest = json.loads((held / "tasks.json").read_text())
    assert len(manifest) == 2 and len(held_manifest) == 1
    assert held_manifest[0]["name"] == "task-001" and (held / "task-001" / "calc.py").is_file()
    bugs = {t["bug"] for t in manifest} | {t["bug"] for t in held_manifest}
    assert len(bugs) == 3  # no mutation appears in both sets


def test_bench_runs_several_task_folders(harness, project, tmp_path):
    a, b = tmp_path / "a-tasks", tmp_path / "b-tasks"
    tasks.make(project, a, count=1, check=CHECK)
    tasks.make(project, b, count=1, check=CHECK, seed=1)
    fix = tmp_path / "fix.json"
    fix.write_text(json.dumps([{"action": "write_file", "path": "calc.py", "content": GOOD, "reason": "restore"}]))
    summary = tasks.bench(harness.root, [a, b], parallel=2, spec={"provider": "scripted", "script": str(fix)}, poll=0.2)
    assert sorted(r["task"] for r in summary["results"]) == ["a-tasks/task-001", "b-tasks/task-001"]
    assert summary["solved"] == 2
