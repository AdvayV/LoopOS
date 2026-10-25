"""Exercise the installed CLI in a terminal and save a reviewable transcript."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-tty", action="store_true")
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1] / "train/out/terminal-smoke"
    base.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=base))
    workspace = run / "workspace"
    workspace.mkdir()
    env = dict(os.environ, LOOPOS_HOME=str(run / "state"), NO_COLOR="1")
    executable = Path(sys.executable).with_name("loopos")
    steps = []
    terminal = {"stdin_tty": sys.stdin.isatty(), "stdout_tty": sys.stdout.isatty(),
                "term": os.environ.get("TERM"), "term_program": os.environ.get("TERM_PROGRAM")}
    transcript = (run / "transcript.txt").open("w", encoding="utf-8")

    def say(message: str):
        print(message, flush=True)
        transcript.write(message + "\n")
        transcript.flush()

    def invoke(*words: str, expected: int = 0, contains: str | None = None):
        say("\n$ " + shlex.join(["loopos", *words]))
        result = subprocess.run([str(executable), *words], cwd=workspace, env=env,
                                text=True, capture_output=True, timeout=15)
        output = result.stdout + result.stderr
        say(output.rstrip())
        passed = result.returncode == expected and (contains is None or contains in output)
        steps.append({"args": list(words), "exit_code": result.returncode,
                      "expected_exit_code": expected, "output": output, "passed": passed})
        say(f"{'PASS' if passed else 'FAIL'} (exit {result.returncode}, expected {expected})")
        if not passed:
            raise AssertionError(f"Unexpected result: {words}")

    passed = False
    try:
        say("LoopOS: installed CLI terminal verification")
        say("Terminal: " + json.dumps(terminal))
        if args.require_tty and not (terminal["stdin_tty"] and terminal["stdout_tty"]):
            raise AssertionError("This run requires a real terminal")
        invoke("--help", contains="show results")
        invoke("check_status", expected=1, contains="No syscalls")
        invoke("show", "results", expected=1)
        invoke("write_file", "notes/hello 世界.txt", "hello 世界\n")
        invoke("check_status", contains="succeeded")
        invoke("read_file", "notes/hello 世界.txt")
        invoke("show", "results", contains="hello 世界\n")
        invoke("edit_file", "notes/hello 世界.txt", "hello", "goodbye")
        assert (workspace / "notes/hello 世界.txt").read_text() == "goodbye 世界\n"
        invoke("list_dir", "notes")
        invoke("show", "results", contains="hello 世界.txt")
        invoke("run", "python", "-c", "import sys; print('stdout: real result'); print('stderr: real result', file=sys.stderr)")
        invoke("show", "results", contains="stderr: real result")
        invoke("finish", "--until", "python -c 'from pathlib import Path; assert Path(\"notes/hello 世界.txt\").read_text() == \"goodbye 世界\\n\"'")
        invoke("show", "results", contains="Done check passed")
        invoke("show", "results", "2", contains="hello 世界\n")
        invoke("read_file", "missing.txt", expected=2)
        invoke("show", "results", contains="no such file")
        invoke("write_file", "../escape", "blocked", expected=2)
        invoke("show", "results", contains="outside the workspace")
        invoke("write_file", "nested/.git/config", "blocked", expected=2)
        invoke("run", "rm -rf .", expected=2)
        invoke("run", "echo literal; touch marker")
        invoke("show", "results", contains="literal; touch marker")
        assert not (workspace / "marker").exists()
        invoke("run", "python", "-c", "print('failed command result'); raise SystemExit(7)", expected=7)
        invoke("show", "results", contains="Exit code: 7")
        invoke("finish", "--until", "python -c 'raise SystemExit(3)'", expected=3)
        invoke("show", "results", contains="Done check did not pass")
        invoke("run", "--timeout", "1", "python", "-c", "import time; print('before timeout', flush=True); time.sleep(10)", expected=124)
        invoke("show", "results", contains="timeout")
        invoke("check_status", "99999", expected=1)
        invoke("ps", expected=2)
        passed = True
    except Exception as exc:
        say(f"FAILED: {exc}")
    finally:
        report = {"passed": passed, "terminal": terminal, "steps": steps, "directory": str(run)}
        (run / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        (base / "latest.json").write_text(json.dumps({"directory": str(run), "passed": passed}))
        say(f"\n{'PASS' if passed else 'FAIL'}: {len(steps)} CLI checks. Artifacts: {run}")
        transcript.close()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
