from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import pytest

from loopos import checkpoint, monitor, sandbox
from loopos.scheduler import ModelScheduler
from loopos.syscalls import Jobs, Policy, SyscallError, execute, run_command, validate
from loopos.worker import parse_action


# ---------------------------------------------------------------- syscalls

@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    (tmp_path / "a.txt").write_text("hello world\nhello again\n")
    (tmp_path / ".git").mkdir()
    return Policy(workspace=tmp_path.resolve(), allow=frozenset({"echo", "python", Path(sys.executable).name}))


def test_validate_rejects_unknown_and_missing_fields():
    with pytest.raises(SyscallError, match="unknown action"):
        validate({"action": "rm"})
    with pytest.raises(SyscallError, match="needs a string field 'find'"):
        validate({"action": "edit_file", "path": "a", "replace": "b"})
    assert validate({"action": "list_dir"})["path"] == "."


def test_paths_cannot_escape_or_touch_git(policy):
    assert execute({"action": "read_file", "path": "../etc/passwd"}, policy)["refused"]
    assert execute({"action": "read_file", "path": "/etc/passwd"}, policy)["refused"]
    result = execute({"action": "write_file", "path": ".git/config", "content": "x"}, policy)
    assert result["refused"] and "protected" in result["output"]


def test_edit_requires_exactly_one_match(policy):
    result = execute({"action": "edit_file", "path": "a.txt", "find": "hello", "replace": "bye"}, policy)
    assert result["refused"] and "2 times" in result["output"]
    assert execute({"action": "edit_file", "path": "a.txt", "find": "world", "replace": "there"}, policy)["ok"]
    assert (policy.workspace / "a.txt").read_text().startswith("hello there")


def test_run_enforces_allowlist_and_returns_output(policy):
    assert "not allowed" in execute({"action": "run", "command": "rm -rf ."}, policy)["output"]
    result = execute({"action": "run", "command": "echo hi there"}, policy)
    assert result["ok"] and result["output"].strip() == "hi there"
    assert execute({"action": "run", "command": "echo 'unterminated"}, policy)["refused"]


def test_long_output_keeps_the_tail(policy):
    policy.output_limit = 100
    code, output = run_command([sys.executable, "-c", "print('x' * 500 + 'END')"], policy)
    assert code == 0 and output.rstrip().endswith("END") and "omitted" in output


@pytest.mark.skipif(sandbox.landlock_abi() < 1, reason="Landlock not available")
def test_commands_cannot_write_outside_the_workspace(policy, tmp_path_factory, monkeypatch):
    # Commands may also write to the temp dir; point it elsewhere so `outside` really is outside.
    monkeypatch.setattr(tempfile, "tempdir", str(policy.workspace / "tmp"))
    outside = tmp_path_factory.mktemp("outside")
    script = f"open({str(outside / 'x')!r}, 'w').write('x')"
    code, output = run_command([sys.executable, "-c", script], policy)
    assert code != 0 and "Permission denied" in output
    assert not (outside / "x").exists()
    code, _ = run_command([sys.executable, "-c", "open('inside.txt', 'w').write('ok')"], policy)
    assert code == 0 and (policy.workspace / "inside.txt").read_text() == "ok"


def test_command_timeout(policy):
    code, output = run_command([sys.executable, "-c", "import time; time.sleep(5)"], policy, timeout=1)
    assert code == 124 and "timeout" in output


# ---------------------------------------------------------------- monitor

def test_score_reads_pytest_summaries():
    assert monitor.score(0, "anything") == 1.0
    assert monitor.score(1, "3 failed, 1 passed in 0.10s") == 0.25
    assert monitor.score(2, "1 error in 0.1s") == 0.0
    assert monitor.score(1, "no summary") == 0.0
    assert monitor.score(1, "1 failed, 99999 passed") < 1.0


def entry(action: str, outcome: str = "o", check: str = "c") -> dict:
    return {"action": action, "outcome": outcome, "check": check}


def test_assess_detects_repeats_cycles_and_stalls():
    rules = monitor.Rules(repeat_limit=3, cycle_window=4, stall_limit=5)
    assert monitor.assess([entry("a")] * 3, 0, rules).kind == "livelock"
    assert monitor.assess([entry("a"), entry("a", "x"), entry("a")], 0, rules).kind == "ok"
    assert monitor.assess([entry("a"), entry("b")] * 2, 0, rules).kind == "livelock"
    assert monitor.assess([entry("a"), entry("b", check="d")] * 2, 0, rules).kind == "ok"
    assert monitor.assess([entry("a"), entry("b"), entry("c")], 5, rules).kind == "stalled"


def test_fingerprint_ignores_timings():
    assert monitor.fingerprint("1 failed in 0.12s") == monitor.fingerprint("1 failed in 3.40s")


# ---------------------------------------------------------------- scheduler

def test_scheduler_prefers_priority_then_ages_waiters():
    scheduler = ModelScheduler(slots=1, aging=2)
    for loop, priority in (("low", 1), ("high", 5)):
        scheduler.request(loop, priority)
    assert scheduler.grant() == ["high"]
    granted = []
    # "high" keeps coming back; "low" gains +1 every 2 turns it waits and eventually wins.
    for _ in range(12):
        scheduler.release(granted[-1] if granted else "high")
        scheduler.request("high", 5)
        granted += scheduler.grant()
        if granted[-1] == "low":
            break
    assert granted[-1] == "low"
    assert len(granted) == 8  # after waiting 8 turns: 1 + 8 // 2 = 5 ties with high; the longer waiter wins


def test_scheduler_skips_ineligible_and_forgets():
    scheduler = ModelScheduler()
    scheduler.request("a", 9)
    scheduler.request("b", 1)
    assert scheduler.grant(lambda loop: loop != "a") == ["b"]
    scheduler.forget("b")
    assert scheduler.grant() == ["a"]
    assert scheduler.last_decision["selected"] == "a"


# ---------------------------------------------------------------- checkpoints

def test_snapshot_and_restore_round_trip(tmp_path):
    ws, snap = tmp_path / "ws", tmp_path / "snap"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "a.py").write_text("v1")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref")
    checkpoint.snapshot(ws, snap)
    assert not (snap / ".git").exists()
    (ws / "pkg" / "a.py").write_text("v2")
    (ws / "new.py").write_text("junk")
    checkpoint.restore(snap, ws)
    assert (ws / "pkg" / "a.py").read_text() == "v1"
    assert not (ws / "new.py").exists()
    assert (ws / ".git" / "HEAD").read_text() == "ref"


# ---------------------------------------------------------------- worker parsing

@pytest.mark.parametrize("reply", [
    '{"action": "finish", "reason": "ok"}',
    'Sure!\n```json\n{"action": "finish", "reason": "ok"}\n```',
])
def test_parse_action_accepts_wrapped_json(reply):
    action, error = parse_action(reply)
    assert error == "" and action["action"] == "finish"


def test_parse_action_reports_bad_replies():
    assert parse_action("no json here")[1]
    assert parse_action("[1, 2]")[1]


def test_command_group_is_cleaned_up(policy):
    code, output = run_command(["sh", "-c", "sleep 30 & echo $!"], policy, enforce_allowlist=False)
    pid = int(output.split()[-1])
    time.sleep(0.2)
    assert code == 0
    assert not Path(f"/proc/{pid}").exists() or Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"


def test_paused_jobs_do_not_time_out(policy):
    jobs = Jobs()
    jobs.paused = True
    started = time.monotonic()
    code, _ = run_command([sys.executable, "-c", "import time; time.sleep(1.5)"], policy, timeout=1, jobs=jobs)
    assert code == 0 and time.monotonic() - started >= 1.5 and len(jobs) == 0


def test_check_ollama_explains_problems():
    from loopos.model import check_ollama
    assert "cannot reach Ollama" in check_ollama("http://127.0.0.1:9", "m", timeout=1)
