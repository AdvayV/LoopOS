"""Integration tests: a real supervisor thread, real worker processes, the scripted provider."""
from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time

import pytest

from loopos import dataset
from loopos.protocol import LoopOSError

from conftest import BUGGY, FIX, SLEEP, start

READ = {"action": "read_file", "path": "calc.py", "reason": "look"}
BREAK = {"action": "write_file", "path": "calc.py", "content": "def add(a, b) return\n", "reason": "oops"}


def process_state(pid: int) -> str:
    with open(f"/proc/{pid}/stat") as stat:
        return stat.read().rsplit(")", 1)[1].split()[0]


def test_loop_reaches_done_and_records_trace(harness, workspace):
    loop_id = harness.run(workspace, [READ, FIX])
    loop = harness.wait(loop_id)
    assert loop.status == "done" and loop.iteration == 2
    assert "return a + b" in (workspace / "calc.py").read_text()
    kinds = [e["kind"] for e in harness.events(loop_id)]
    assert kinds[:3] == ["created", "spawn", "baseline"] and kinds[-1] == "done"
    trace = [json.loads(line) for line in (harness.root / "loops" / loop_id / "trace.jsonl").read_text().splitlines()]
    assert [t["score_before"] for t in trace] == [0.5, 0.5] and trace[-1]["score_after"] == 1.0
    assert trace[0]["messages"][0]["role"] == "system" and trace[0]["messages"][-1]["role"] == "assistant"


def test_refused_action_is_explained_to_the_model(harness, workspace):
    loop_id = harness.run(workspace, [{"action": "read_file", "path": "../../secret", "reason": "x"}, FIX])
    assert harness.wait(loop_id).status == "done"
    syscall = [e for e in harness.events(loop_id) if e["kind"] == "syscall"][0]
    assert syscall["data"]["outcome"] == "refused"
    trace = (harness.root / "loops" / loop_id / "trace.jsonl").read_text().splitlines()
    assert "outside the workspace" in json.loads(trace[1])["messages"][1]["content"]


def test_livelock_recovers_then_gives_up_and_restores_best(harness, workspace):
    loop_id = harness.run(workspace, [BREAK, READ])  # READ repeats forever after the script ends
    loop = harness.wait(loop_id)
    assert loop.status == "gave_up" and loop.recoveries == 2
    assert len([e for e in harness.events(loop_id) if e["kind"] == "livelock"]) == 2
    assert (workspace / "calc.py").read_text() == BUGGY  # the broken write was rolled back to the baseline


def test_iteration_budget(harness, workspace):
    loop_id = harness.run(workspace, [READ, {"action": "list_dir", "path": ".", "reason": "x"}], max_iters=2)
    loop = harness.wait(loop_id)
    assert loop.status == "budget" and "budget" in loop.note


def test_finish_is_verified_not_trusted(harness, workspace):
    loop_id = harness.run(workspace, [{"action": "finish", "reason": "trust me"}, FIX])
    assert harness.wait(loop_id).status == "done"
    trace = (harness.root / "loops" / loop_id / "trace.jsonl").read_text().splitlines()
    assert "still fails" in json.loads(trace[1])["messages"][1]["content"]


def test_crashed_worker_is_restarted_from_saved_state(harness, workspace):
    loop_id = harness.run(workspace, [READ, SLEEP, FIX])
    harness.wait_for_event(loop_id, "syscall")  # READ finished; the worker is now inside the slow step
    time.sleep(0.5)
    os.killpg(harness.supervisor.loops[loop_id].process.pid, signal.SIGKILL)
    loop = harness.wait(loop_id)
    assert loop.status == "done" and loop.restarts == 1
    assert harness.wait_for_event(loop_id, "restart")


def test_pause_freezes_the_process_and_resume_continues(harness, workspace):
    loop_id = harness.run(workspace, [SLEEP, SLEEP, FIX])
    harness.wait_for_event(loop_id, "baseline")
    pid = harness.supervisor.loops[loop_id].process.pid
    harness.request("pause", loop=loop_id)
    loop = harness.wait(loop_id, {"paused"}, timeout=10)
    time.sleep(0.3)
    assert process_state(pid) == "T"  # stopped by SIGSTOP
    with pytest.raises(LoopOSError):
        harness.request("pause", loop=loop_id)
    harness.request("resume", loop=loop_id)
    assert harness.wait(loop_id).status == "done"


def test_kill_stops_the_loop(harness, workspace):
    loop_id = harness.run(workspace, [SLEEP])
    harness.wait_for_event(loop_id, "baseline")
    pid = harness.supervisor.loops[loop_id].process.pid
    harness.request("kill", loop=loop_id)
    assert harness.wait(loop_id).status == "stopped"
    time.sleep(0.5)
    assert not os.path.exists(f"/proc/{pid}") or process_state(pid) == "Z"


def test_one_loop_per_workspace(harness, workspace):
    harness.run(workspace, [SLEEP] * 3 + [FIX])
    with pytest.raises(LoopOSError, match="already working"):
        harness.run(workspace, [FIX])


@pytest.mark.parametrize("harness", [{"hang_timeout": 1.5, "max_restarts": 1}], indirect=True)
def test_hung_model_call_is_killed_and_restarted(harness, workspace):
    # A fake model server that accepts connections and never answers.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held = []
    threading.Thread(target=lambda: [held.append(server.accept()) for _ in range(5)], daemon=True).start()
    url = f"http://127.0.0.1:{server.getsockname()[1]}"
    loop_id = harness.request("run", spec={"goal": "g", "until": "false", "workspace": str(workspace),
                                           "provider": "ollama", "model": "m", "ollama_url": url})["id"]
    loop = harness.wait(loop_id, timeout=20)
    assert loop.status == "failed" and "hung" in loop.note
    server.close()


def test_unreachable_model_pauses_the_loop(harness, workspace):
    port_holder = socket.socket()
    port_holder.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{port_holder.getsockname()[1]}"  # bound but not listening: connection refused
    loop_id = harness.request("run", spec={"goal": "g", "until": "false", "workspace": str(workspace),
                                           "provider": "ollama", "model": "m", "ollama_url": url})["id"]
    loop = harness.wait(loop_id, {"paused"}, timeout=20)
    assert "model unavailable" in loop.note
    harness.request("kill", loop=loop_id)
    port_holder.close()


def test_supervisor_restart_resumes_unfinished_loops(harness, workspace):
    loop_id = harness.run(workspace, [SLEEP, SLEEP, FIX])
    harness.wait_for_event(loop_id, "baseline")
    harness.supervisor.stopping.set()
    time.sleep(1.5)
    supervisor, thread = start(harness.root)
    try:
        harness.supervisor = supervisor
        loop = harness.wait(loop_id)
        assert loop.status == "done"
        assert harness.wait_for_event(loop_id, "recovered")
    finally:
        supervisor.stopping.set()
        thread.join(10)


def test_dataset_export_keeps_useful_steps(harness, workspace, tmp_path):
    harness.wait(harness.run(workspace, [{"action": "read_file", "path": "nope.py", "reason": "x"}, READ, FIX]))
    out = tmp_path / "data.jsonl"
    counts = dataset.export(harness.root, out)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert counts["read"] == 3 and counts["written"] == 2  # the failed read of a missing file is dropped
    assert rows[-1]["meta"]["event"] == "done" and rows[-1]["messages"][-1]["role"] == "assistant"
