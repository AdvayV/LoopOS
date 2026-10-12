"""Guess a project's done check, so `loopos run` works without --until."""
from __future__ import annotations

import json
from pathlib import Path

from .checkpoint import tracked_files


def detect_check(workspace: Path) -> str | None:
    """The command that tells whether this project works, or None if it cannot be guessed."""
    files = {str(p) for p in tracked_files(workspace)} if workspace.is_dir() else set()
    names = {Path(f).name for f in files}
    if (any(n.startswith("test_") and n.endswith(".py") or n.endswith("_test.py") for n in names)
            or "pytest.ini" in names or "conftest.py" in names):
        return "pytest -q"
    package = workspace / "package.json"
    if package.is_file():
        try:
            if json.loads(package.read_text()).get("scripts", {}).get("test"):
                return "npm test"
        except ValueError:
            pass
    makefile = workspace / "Makefile"
    if makefile.is_file() and any(line.startswith("test:") for line in makefile.read_text().splitlines()):
        return "make test"
    if (workspace / "Cargo.toml").is_file():
        return "cargo test"
    if (workspace / "go.mod").is_file():
        return "go test ./..."
    return None
