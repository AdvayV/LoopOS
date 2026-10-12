"""Where the supervisor keeps its socket, database and per-loop files."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def home() -> Path:
    root = Path(os.environ.get("LOOPOS_HOME") or Path.home() / ".local" / "state" / "loopos")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def socket_path(root: Path) -> Path:
    path = root / "loopos.sock"
    # AF_UNIX paths are limited to ~108 bytes; fall back to a short, per-home path in /tmp.
    if len(str(path)) > 100:
        digest = hashlib.sha256(str(root).encode()).hexdigest()[:12]
        path = Path("/tmp") / f"loopos-{os.getuid()}-{digest}.sock"
    return path


def database(root: Path) -> Path:
    return root / "loopos.db"


def loop_dir(root: Path, loop_id: str) -> Path:
    path = root / "loops" / loop_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def daemon_log(root: Path) -> Path:
    return root / "supervisor.log"
