"""Workspace snapshots: the supervisor saves the best state and restores it on livelock or give-up."""
from __future__ import annotations

import shutil
from pathlib import Path

from .syscalls import IGNORED_DIRS

MAX_FILES = 5_000
MAX_BYTES = 100 * 1024 * 1024


class CheckpointError(Exception):
    pass


def tracked_files(root: Path) -> list[Path]:
    """Files LoopOS manages, relative to root. Ignored dirs (.git, caches, venvs) are left alone."""
    files: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        for entry in directory.iterdir():
            if entry.is_symlink():
                files.append(entry.relative_to(root))
            elif entry.is_dir():
                if entry.name not in IGNORED_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                files.append(entry.relative_to(root))
    return sorted(files)


def check_size(root: Path) -> None:
    files = tracked_files(root)
    total = sum((root / f).lstat().st_size for f in files)
    if len(files) > MAX_FILES or total > MAX_BYTES:
        raise CheckpointError(
            f"workspace has {len(files)} files / {total // 1024 // 1024} MB; "
            f"the limit is {MAX_FILES} files / {MAX_BYTES // 1024 // 1024} MB. Point --workspace at a smaller folder."
        )


def snapshot(workspace: Path, destination: Path) -> None:
    """Atomically replace `destination` with a copy of the workspace's tracked files."""
    staging = destination.with_name(destination.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for relative in tracked_files(workspace):
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / relative, target, follow_symlinks=False)
    shutil.rmtree(destination, ignore_errors=True)
    staging.rename(destination)


def restore(source: Path, workspace: Path) -> None:
    """Make the workspace's tracked files identical to the snapshot."""
    if not source.is_dir():
        raise CheckpointError(f"no checkpoint at {source}")
    wanted = set(tracked_files(source))
    for relative in tracked_files(workspace):
        if relative not in wanted:
            (workspace / relative).unlink()
    for relative in wanted:
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.is_file():
            target.unlink()
        shutil.copy2(source / relative, target, follow_symlinks=False)
