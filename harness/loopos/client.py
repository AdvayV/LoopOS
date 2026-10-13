"""Talking to the supervisor from the CLI and other tools."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import paths
from .protocol import Connection, LoopOSError
from .supervisor import PACKAGE_ROOT


def connect(root: Path) -> Connection:
    return Connection(paths.socket_path(root))


def request(root: Path, op: str, **fields: Any) -> dict[str, Any]:
    with connect(root) as conn:
        return conn.request(op, **fields)


def daemon_running(root: Path) -> bool:
    try:
        request(root, "ping")
        return True
    except LoopOSError:
        return False


def start_daemon(root: Path) -> None:
    if daemon_running(root):
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = PACKAGE_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    with open(paths.daemon_log(root), "ab") as log:
        subprocess.Popen([sys.executable, "-m", "loopos.supervisor", "--home", str(root)], cwd=root, env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        if daemon_running(root):
            return
        time.sleep(0.1)
    raise LoopOSError(f"supervisor did not start; see {paths.daemon_log(root)}")
