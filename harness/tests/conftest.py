from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from loopos import monitor
from loopos.protocol import Connection
from loopos.supervisor import TERMINAL, Supervisor
from loopos import paths

BUGGY = "def add(a, b):\n    return a - b\n"
TESTS = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
FIX = {"action": "edit_file", "path": "calc.py", "find": "return a - b", "replace": "return a + b", "reason": "fix"}
SLEEP = {"action": "run", "command": "python -c 'import time; time.sleep(1.5)'", "reason": "slow step"}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY)
    (root / "test_calc.py").write_text(TESTS)
    return root


class Harness:
    def __init__(self, root: Path, supervisor: Supervisor, tmp_path: Path) -> None:
        self.root, self.supervisor, self.tmp_path = root, supervisor, tmp_path

    def request(self, op: str, **fields):
        with Connection(paths.socket_path(self.root)) as conn:
            return conn.request(op, **fields)

    def run(self, workspace: Path, replies: list, **spec) -> str:
        script = self.tmp_path / f"script-{time.monotonic_ns()}.json"
        script.write_text(json.dumps(replies))
        full = {"goal": "make the tests pass", "until": f"{sys.executable} -m pytest -q -p no:cacheprovider",
                "workspace": str(workspace), "provider": "scripted", "script": str(script), **spec}
        return self.request("run", spec=full)["id"]

    def wait(self, loop_id: str, statuses=TERMINAL, timeout: float = 30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            loop = self.supervisor.loops[loop_id]
            if loop.status in statuses:
                return loop
            time.sleep(0.05)
        raise AssertionError(f"{loop_id} still {self.supervisor.loops[loop_id].status}; events: "
                             + "; ".join(e["message"] for e in self.events(loop_id)))

    def wait_for_event(self, loop_id: str, kind: str, timeout: float = 30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [e for e in self.events(loop_id) if e["kind"] == kind]
            if matches:
                return matches
            time.sleep(0.05)
        raise AssertionError(f"no {kind} event for {loop_id}")

    def events(self, loop_id: str):
        return self.supervisor.store.events(loop_id, limit=1000)


def start(root: Path, **options) -> tuple[Supervisor, threading.Thread]:
    supervisor = Supervisor(root, tick=0.1, **options)
    thread = threading.Thread(target=supervisor.serve, daemon=True)
    thread.start()
    assert supervisor.ready.wait(5)
    return supervisor, thread


@pytest.fixture
def harness(tmp_path: Path, request):
    root = tmp_path / "home"
    root.mkdir()
    options = getattr(request, "param", {}) or {}
    supervisor, thread = start(root, rules=monitor.Rules(), **options)
    yield Harness(root, supervisor, tmp_path)
    supervisor.stopping.set()
    thread.join(10)
