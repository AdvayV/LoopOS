"""Newline-delimited JSON over a Unix socket: one request line, one response line."""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, BinaryIO


class LoopOSError(Exception):
    """An error reported by the supervisor, or a failure to reach it."""


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    stream.flush()


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    line = stream.readline()
    if not line:
        return None
    return json.loads(line)


class Connection:
    """A client connection to the supervisor. Workers keep one open for their lifetime."""

    def __init__(self, path: Path, timeout: float | None = None) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        try:
            self.socket.connect(str(path))
        except OSError as exc:
            self.socket.close()
            raise LoopOSError(f"supervisor is not running ({exc.strerror or exc})") from exc
        self.stream = self.socket.makefile("rwb")

    def request(self, op: str, **fields: Any) -> dict[str, Any]:
        write_message(self.stream, {"op": op, **fields})
        reply = read_message(self.stream)
        if reply is None:
            raise LoopOSError("supervisor closed the connection")
        if not reply.get("ok"):
            raise LoopOSError(reply.get("error", "unknown error"))
        return reply

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.socket.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
