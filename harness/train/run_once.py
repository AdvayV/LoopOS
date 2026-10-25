"""Run one training command with a deadline, durable progress, and no automatic retries."""
from __future__ import annotations

import argparse
import codecs
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return seconds


def run_once(command: list[str], out: Path, max_seconds: float, update_seconds: float,
             restart: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=True)
    status_file = out / "training-status.json"
    with (out / "training.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A job already owns this output directory; no duplicate was started.", file=sys.stderr)
            return 2
        if status_file.exists() and not restart:
            print("This job was already attempted. Inspect training-status.json; "
                  "use --restart only for an intentional new attempt.", file=sys.stderr)
            return 2
        started = time.monotonic()
        status = {"state": "starting", "started": datetime.now(timezone.utc).isoformat(),
                  "command": command, "deadline_seconds": max_seconds, "pid": None,
                  "attempts": 1, "elapsed_seconds": 0, "last_output": ""}

        def publish(state: str, code: int | None = None):
            status.update(state=state, elapsed_seconds=round(time.monotonic() - started, 1),
                          updated=datetime.now(timezone.utc).isoformat())
            if code is not None:
                status["exit_code"] = code
            temporary = status_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(status, indent=2) + "\n")
            temporary.replace(status_file)
            last_line = status["last_output"].splitlines()[-1:] or ["waiting for training output"]
            print(f"[{status['updated']}] {state}: {status['elapsed_seconds']}s / "
                  f"{max_seconds:g}s; {last_line[0][-240:]}", flush=True)

        def interrupted(_signum, _frame):
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, interrupted)
        process = None
        reader = None
        code, state = 1, "failed"
        try:
            publish("starting")
            with (out / "training.log").open("ab" if restart else "wb", buffering=0) as log:
                def capture():
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    while chunk := process.stdout.read1(65536):
                        log.write(chunk)
                        status["last_output"] = (status["last_output"] + decoder.decode(chunk))[-2000:]

                try:
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                    status["pid"] = process.pid
                    reader = threading.Thread(target=capture, daemon=True)
                    reader.start()
                    publish("running")
                    while process.poll() is None:
                        remaining = max_seconds - (time.monotonic() - started)
                        if remaining <= 0:
                            code, state = 124, "timed_out"
                            break
                        try:
                            process.wait(timeout=min(update_seconds, remaining))
                        except subprocess.TimeoutExpired:
                            publish("running")
                    else:
                        code = process.returncode
                        state = "succeeded" if code == 0 else "failed"
                finally:
                    # Clean up the group even if the command exits with live background children.
                    if process is not None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=10)
                        if reader is not None:
                            reader.join(timeout=5)
                        if reader is None or not reader.is_alive():
                            process.stdout.close()
        except KeyboardInterrupt:
            code, state = 130, "interrupted"
        except OSError as exc:
            status["last_output"] = str(exc)
        finally:
            signal.signal(signal.SIGTERM, previous)
            publish(state, code)
        return 128 - code if code < 0 else code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="job status, log, and lock directory")
    parser.add_argument("--max-seconds", type=positive_seconds, default=7200)
    parser.add_argument("--update-seconds", type=positive_seconds, default=30)
    parser.add_argument("--restart", action="store_true", help="explicitly allow a second attempt")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a training command after --")
    if args.update_seconds > 60:
        parser.error("--update-seconds must be at most 60")
    return run_once(command, args.out.resolve(), args.max_seconds, args.update_seconds, args.restart)


if __name__ == "__main__":
    raise SystemExit(main())
