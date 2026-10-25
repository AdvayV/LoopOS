"""Verify training cannot silently retry, duplicate itself, or exceed its deadline."""
import json
import signal
import subprocess
import sys
from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / "train/run_once.py"


def command(out, code, seconds="5"):
    return [sys.executable, str(RUNNER), "--out", str(out), "--max-seconds", seconds,
            "--update-seconds", "0.1", "--", sys.executable, "-u", "-c", code]


def test_one_attempt_saved_results_and_no_automatic_restart(tmp_path):
    cmd = command(tmp_path, "print('loss: 0.25')")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    status = json.loads((tmp_path / "training-status.json").read_text())
    assert status["state"] == "succeeded" and status["attempts"] == 1
    assert "loss: 0.25" in (tmp_path / "training.log").read_text()
    assert subprocess.run(cmd, capture_output=True, timeout=10).returncode == 2


def test_deadline_updates_and_duplicate_lock(tmp_path):
    cmd = command(tmp_path, "import time; print('step 1'); time.sleep(20)", "1")
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    try:
        assert "starting" in process.stdout.readline()
        assert "running" in process.stdout.readline()
        duplicate = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        assert duplicate.returncode == 2 and "duplicate" in duplicate.stderr
        output = process.communicate(timeout=10)[0]
        assert process.returncode == 124 and "step 1" in output
        status = json.loads((tmp_path / "training-status.json").read_text())
        assert status["state"] == "timed_out" and status["elapsed_seconds"] < 5
        assert len(output.splitlines()) > 2
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_failure_does_not_retry_and_sigterm_cleans_up(tmp_path):
    failed = subprocess.run(command(tmp_path / "failed", "raise SystemExit(7)"), capture_output=True, timeout=5)
    assert failed.returncode == 7
    process = subprocess.Popen(command(tmp_path / "interrupted", "import time; time.sleep(20)"),
                               stdout=subprocess.PIPE, text=True)
    try:
        process.stdout.readline()
        process.stdout.readline()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        assert process.returncode == 130
        status = json.loads((tmp_path / "interrupted/training-status.json").read_text())
        assert status["state"] == "interrupted"
        assert not Path(f"/proc/{status['pid']}").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_invalid_deadlines_are_rejected(tmp_path):
    for value in ("0", "-1", "nan", "inf"):
        assert subprocess.run(command(tmp_path, "pass", value), capture_output=True, timeout=5).returncode == 2


def test_export_excludes_premature_finish_even_from_solved_loops():
    from loopos.dataset import keep

    row = {"refused": False, "action": {"action": "finish"}, "score_before": 0.5,
           "score_after": 0.5, "event": ""}
    assert not keep(row, "done", "success")
    assert keep(row | {"score_after": 1.0, "event": "done"}, "done", "success")
