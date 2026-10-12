"""Process launcher that confines itself, then execs the real command.

    python -m loopos.sandbox --cpu 120 --mem 2048 --nice 5 --write /work --write /tmp -- pytest -q

Limits are applied in this fresh process instead of a fork-time preexec_fn, which is unsafe
in the multi-threaded supervisor:
  * RLIMIT_CPU / RLIMIT_AS cap CPU seconds and address space,
  * nice() lowers scheduling priority for low-priority loops,
  * Landlock (Linux >= 5.13) denies file writes outside the --write directories.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import platform
import resource
import struct
import sys

# x86_64 and aarch64 share these generic syscall numbers.
_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# Landlock filesystem rights that modify the filesystem (reads stay unrestricted).
_WRITE_FILE = 1 << 1
_REMOVE_DIR, _REMOVE_FILE = 1 << 4, 1 << 5
_MAKE_ANY = sum(1 << bit for bit in range(6, 13))  # char, dir, reg, sock, fifo, block, sym
_REFER = 1 << 13     # ABI 2
_TRUNCATE = 1 << 14  # ABI 3


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def landlock_abi() -> int:
    """Landlock ABI version supported by the running kernel, or 0 if unavailable."""
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64"}:
        return 0
    version = _libc().syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0), _CREATE_RULESET_VERSION)
    return max(int(version), 0)


def restrict_writes(writable: list[str]) -> bool:
    """Deny filesystem modifications outside `writable` for this process and its children."""
    abi = landlock_abi()
    if abi < 1:
        return False
    handled = _WRITE_FILE | _REMOVE_DIR | _REMOVE_FILE | _MAKE_ANY
    if abi >= 2:
        handled |= _REFER
    if abi >= 3:
        handled |= _TRUNCATE
    libc = _libc()
    attr = ctypes.c_uint64(handled)  # struct landlock_ruleset_attr { __u64 handled_access_fs; ... }
    ruleset = libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(8), 0)
    if ruleset < 0:
        return False
    try:
        for path in writable:
            try:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                # Packed struct landlock_path_beneath_attr { __u64 allowed_access; __s32 parent_fd; }
                rule = ctypes.create_string_buffer(struct.pack("=Qi", handled, fd), 12)
                if libc.syscall(_SYS_ADD_RULE, ruleset, _RULE_PATH_BENEATH, rule, 0) < 0:
                    raise OSError(ctypes.get_errno(), f"landlock_add_rule failed for {path}")
            finally:
                os.close(fd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")
        if libc.syscall(_SYS_RESTRICT_SELF, ruleset, 0) < 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(ruleset)
    return True


def apply_limits(cpu_seconds: int | None, memory_mb: int | None, niceness: int) -> None:
    if cpu_seconds:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    if memory_mb:
        size = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (size, size))
    if niceness > 0:
        os.nice(niceness)


def command(argv: list[str], *, cpu_seconds: int | None = None, memory_mb: int | None = None,
            niceness: int = 0, writable: list[str] | None = None) -> list[str]:
    """Wrap argv so it runs through this launcher with the given limits."""
    # Run this file directly in isolated mode: works without installing the package and ignores
    # modules in the (untrusted) working directory.
    wrapped = [sys.executable, "-I", os.path.abspath(__file__), "--nice", str(niceness)]
    if cpu_seconds:
        wrapped += ["--cpu", str(cpu_seconds)]
    if memory_mb:
        wrapped += ["--mem", str(memory_mb)]
    for path in writable or []:
        wrapped += ["--write", path]
    return wrapped + ["--", *argv]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loopos.sandbox")
    parser.add_argument("--cpu", type=int)
    parser.add_argument("--mem", type=int)
    parser.add_argument("--nice", type=int, default=0)
    parser.add_argument("--write", action="append", default=[])
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    target = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not target:
        parser.error("missing command")
    apply_limits(args.cpu, args.mem, args.nice)
    if args.write and not restrict_writes(args.write):
        print("loopos.sandbox: Landlock unavailable; file writes are not confined", file=sys.stderr)
    try:
        os.execvp(target[0], target)
    except FileNotFoundError:
        print(f"loopos.sandbox: command not found: {target[0]}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
