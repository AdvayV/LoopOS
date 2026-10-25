"""Exercise the public commands as separate terminal processes, with real syscalls."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from loopos.calls import Calls
from loopos.syscalls import Policy, execute


@pytest.fixture
def terminal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "state"
    root.mkdir()
    env = dict(os.environ, LOOPOS_HOME=str(root), NO_COLOR="1")

    def invoke(*args):
        return subprocess.run([sys.executable, "-m", "loopos", *args], cwd=workspace,
                              env=env, text=True, capture_output=True, timeout=15)

    return invoke, workspace, root, env


def test_every_syscall_and_explicit_results(terminal):
    cli, ws, _, _ = terminal
    assert cli("check_status").returncode == 1
    assert cli("show", "results").returncode == 1
    assert cli("write_file", "a file.txt", "hello 世界\n").returncode == 0
    assert cli("read_file", "a file.txt").returncode == 0
    result = cli("show", "results")
    assert "hello 世界\n" in result.stdout and "read_file" in result.stdout
    assert cli("edit_file", "a file.txt", "hello", "goodbye").returncode == 0
    assert (ws / "a file.txt").read_text() == "goodbye 世界\n"
    assert cli("list_dir").returncode == 0
    assert "a file.txt" in cli("show", "results").stdout
    assert cli("run", "python", "-c", "print('actual output')").returncode == 0
    assert "actual output" in cli("show", "results").stdout
    assert cli("finish", "--until", "true").returncode == 0
    assert "Done check passed" in cli("show", "results").stdout
    assert "succeeded" in cli("check_status").stdout
    assert "hello 世界" in cli("show", "results", "2").stdout


def test_failures_replace_latest_success_and_preserve_exit_code(terminal):
    cli, _, _, _ = terminal
    assert cli("run", "echo good").returncode == 0
    assert cli("read_file", "missing").returncode == 2
    result = cli("show", "results")
    assert "no such file" in result.stdout and "good" not in result.stdout
    assert cli("run", "python", "-c", "import sys; print('failed output'); sys.exit(7)").returncode == 7
    result = cli("show", "results")
    assert "Exit code: 7" in result.stdout and "failed output" in result.stdout
    assert cli("finish", "--until", "python -c 'raise SystemExit(3)'").returncode == 3
    assert "did not pass" in cli("show", "results").stdout


@pytest.mark.parametrize("args", [
    ("ps",), ("watch", "l1"), ("model", "x"), ("daemon", "start"),
    ("show", "l1"), ("finish",), ("read_file",), ("check_status", "0"),
    ("run", "--timeout", "0", "echo hi"),
])
def test_invalid_and_removed_commands(terminal, args):
    cli, _, _, _ = terminal
    assert cli(*args).returncode == 2


def test_refusals_and_quoted_shell_characters(terminal):
    cli, ws, _, _ = terminal
    assert cli("write_file", "../escape", "bad").returncode == 2
    assert cli("write_file", ".git/config", "bad").returncode == 2
    assert cli("run", "rm -rf .").returncode == 2
    assert cli("run", "echo 'unterminated").returncode == 2
    assert cli("run").returncode == 2
    assert cli("run", "echo literal; touch marker").returncode == 0
    assert not (ws / "marker").exists()
    assert "literal; touch marker" in cli("show", "results").stdout


def test_live_status_pending_results_timeout_and_interrupt(terminal):
    cli, ws, _, env = terminal
    command = [sys.executable, "-m", "loopos", "run", "--timeout", "1",
               "python", "-c", "import time; print('started', flush=True); time.sleep(10)"]
    process = subprocess.Popen(command, cwd=ws, env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert "running" in process.stdout.readline()
        assert "running" in cli("check_status").stdout
        assert "pending" in cli("show", "results").stdout
        assert process.wait(timeout=10) == 124
        result = cli("show", "results").stdout
        assert "timeout" in result and "started" in result
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    process = subprocess.Popen(command, cwd=ws, env=env, stdout=subprocess.PIPE, text=True)
    try:
        process.stdout.readline()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 130
        assert "interrupted" in cli("check_status").stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()


def test_abandoned_calls_and_concurrent_completion(terminal):
    _, _, root, _ = terminal
    calls = Calls(root)
    try:
        first = calls.start({"action": "list_dir"}, root)
        second = calls.start({"action": "read_file"}, root)
        calls.finish(second, {"ok": True, "output": "second"})
        calls.finish(first, {"ok": True, "output": "first"})
        assert calls.get()["id"] == second
        abandoned = calls.start({"action": "run"}, root)
        calls.db.execute("UPDATE calls SET token='expired' WHERE id=?", (abandoned,))
        assert calls.get()["status"] == "interrupted"
        assert calls.get(99999) is None
    finally:
        calls.close()


@pytest.mark.parametrize("action", [None, [], {"action": []}, {"action": {}},
    {"action": "read_file", "path": "a\0b"}, {"action": "run", "command": "echo \0"},
    {"action": "write_file", "path": "a", "content": 1},
    {"action": "edit_file", "path": "a", "find": "", "replace": "x"}])
def test_malformed_requests_are_results_not_exceptions(tmp_path, action):
    assert execute(action, Policy(tmp_path))["refused"]


def test_symlinks_binary_files_limits_and_ambiguous_edits(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    policy = Policy(ws, write_limit=10, read_limit=4)
    outside = tmp_path / "outside"
    outside.write_text("outside")
    (ws / "link").symlink_to(outside)
    (ws / "cycle").symlink_to("cycle")
    for name in ("link", "cycle"):
        assert execute({"action": "read_file", "path": name}, policy)["refused"]
    (ws / "a").write_bytes(b"\xff\xfe")
    assert execute({"action": "edit_file", "path": "a", "find": "x", "replace": "y"}, policy)["refused"]
    assert execute({"action": "write_file", "path": "a", "content": "12345678901"}, policy)["refused"]
    (ws / "a").write_text("hi hi")
    assert "truncated" in execute({"action": "read_file", "path": "a"}, policy)["output"]
    assert execute({"action": "edit_file", "path": "a", "find": "hi", "replace": "x"}, policy)["refused"]
    assert execute({"action": "edit_file", "path": "a", "find": "hi hi", "replace": "x" * 11}, policy)["refused"]
    assert (ws / "a").read_text() == "hi hi"


def test_empty_files_directory_errors_and_workspace_flag(terminal):
    cli, ws, _, _ = terminal
    assert cli("write_file", "empty.txt", "").returncode == 0
    assert cli("read_file", "empty.txt").returncode == 0
    assert "(empty output)" in cli("show", "results").stdout
    assert cli("read_file", ".").returncode == 2
    assert cli("list_dir", "empty.txt").returncode == 2
    assert cli("write_file", "empty.txt/child", "x").returncode == 1
    assert "failed" in cli("check_status").stdout
    nested = ws / "nested"
    nested.mkdir()
    assert cli("write_file", "-w", str(nested), "hello.txt", "nested output").returncode == 0
    assert (nested / "hello.txt").read_text() == "nested output"
    assert cli("read_file", "-w", str(ws / "missing"), "hello.txt").returncode == 1
    assert cli("write_file", "notes/.loopos/state", "x").returncode == 2


def test_argument_boundaries_stdout_stderr_and_signal_exit(terminal):
    cli, _, _, _ = terminal
    code = "import sys; print(repr(sys.argv[1:])); print('stderr result', file=sys.stderr)"
    assert cli("run", "--", "python", "-c", code, "", "a b", "$HOME", "--flag").returncode == 0
    result = cli("show", "results").stdout
    assert "['', 'a b', '$HOME', '--flag']" in result
    assert "stderr result" in result
    assert cli("run", "python", "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)").returncode == 143
    assert "Exit code: -15" in cli("show", "results").stdout


def test_exact_output_truncation_counts_and_split_unicode(tmp_path):
    policy = Policy(tmp_path, output_limit=100)
    code = "import os; os.write(1,b'x'*10000); os.write(1,bytes([0xe4])); os.write(1,bytes([0xb8,0x96]))"
    result = execute({"action": "run", "command": __import__('shlex').join([sys.executable, "-c", code])}, policy)
    assert result["ok"]
    assert result["output"] == "[... 9901 earlier characters omitted ...]\n" + "x" * 99 + "世"


def test_corrupt_history_is_a_clear_cli_error(terminal):
    cli, _, root, _ = terminal
    (root / "calls.db").write_text("not a sqlite database")
    result = cli("check_status")
    assert result.returncode == 1
    assert "loopos:" in result.stderr and "Traceback" not in result.stderr


def test_sigkill_executor_is_reported_as_interrupted(terminal):
    cli, ws, _, env = terminal
    process = subprocess.Popen([sys.executable, "-m", "loopos", "run", "--timeout", "1",
                                "python", "-c", "import time; time.sleep(0.1)"],
                               cwd=ws, env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert "running" in process.stdout.readline()
        process.kill()
        process.wait(timeout=5)
        assert "interrupted" in cli("check_status").stdout
        assert "no completed result" in cli("show", "results").stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
