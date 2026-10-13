"""Terminal presentation: the logo, colours, and calm one-line-per-iteration views.

Colours are used only on a terminal, and never when NO_COLOR is set. Truecolor terminals get a smooth
yellow-to-green gradient; others get the nearest 256-colour steps.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, TextIO

# Yellow at the top of the logo, green at the bottom; the shadow is a dark olive.
GRADIENT = [(242, 232, 92), (214, 226, 80), (184, 216, 72), (152, 204, 68), (122, 190, 66)]
GRADIENT_256 = [227, 191, 155, 149, 113]
SHADOW, SHADOW_256 = (61, 84, 34), 58
ACCENT, ACCENT_256 = (184, 216, 72), 149
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# 4x5 pixel glyphs; each pixel is drawn two characters wide so letters keep a square look.
GLYPHS = {
    "L": ["X...", "X...", "X...", "X...", "XXXX"],
    "O": ["XXXX", "X..X", "X..X", "X..X", "XXXX"],
    "P": ["XXXX", "X..X", "XXXX", "X...", "X..."],
    "S": ["XXXX", "X...", "XXXX", "...X", "XXXX"],
    " ": ["..", "..", "..", "..", ".."],
}

FINAL = {"done", "gave_up", "budget", "stopped", "failed"}
VERBS = {"run": "run", "read_file": "read", "edit_file": "edit", "write_file": "write",
         "list_dir": "list", "finish": "finish", "invalid": "reply"}


class Style:
    def __init__(self, stream: TextIO = sys.stdout) -> None:
        self.color = stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.truecolor = os.environ.get("COLORTERM", "").lower() in {"truecolor", "24bit"}
        self.tty = stream.isatty()

    def rgb(self, text: str, rgb: tuple[int, int, int], fallback: int, bold: bool = False) -> str:
        if not self.color:
            return text
        code = f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}" if self.truecolor else f"38;5;{fallback}"
        return f"\033[{'1;' if bold else ''}{code}m{text}\033[0m"

    def sgr(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def accent(self, text: str, bold: bool = False) -> str:
        return self.rgb(text, ACCENT, ACCENT_256, bold)

    def dim(self, text: str) -> str:
        return self.sgr(text, "2")

    def bold(self, text: str) -> str:
        return self.sgr(text, "1")

    def green(self, text: str) -> str:
        return self.sgr(text, "32")

    def red(self, text: str) -> str:
        return self.sgr(text, "31")

    def yellow(self, text: str) -> str:
        return self.sgr(text, "33")

    def cyan(self, text: str) -> str:
        return self.sgr(text, "36")


def width() -> int:
    return max(60, min(shutil.get_terminal_size((100, 24)).columns, 120))


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def short_path(path: str, limit: int = 44) -> str:
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    if len(path) <= limit:
        return path
    parts = path.split("/")
    tail = "/".join(parts[-2:])
    root = "~" if parts[0] == "~" else ""
    return f"{root}/…/{tail}" if len(tail) < limit - 4 else "…" + path[-(limit - 1):]


# ---------------------------------------------------------------- logo

def banner(style: Style, subtitle: str = "agent loops, supervised like processes") -> str:
    word = "LOOP OS"
    columns = sum(len(GLYPHS[ch][0]) + 1 for ch in word) * 2 + 1
    rows = len(GLYPHS["L"]) + 1
    cells: list[list[tuple[str, int]]] = [[(" ", -1)] * columns for _ in range(rows)]  # (char, layer)
    x = 0
    pixels: list[tuple[int, int]] = []
    for ch in word:
        glyph = GLYPHS[ch]
        for r, line in enumerate(glyph):
            for c, mark in enumerate(line):
                if mark == "X":
                    pixels.append((r, x + c * 2))
        x += (len(glyph[0]) + 1) * 2
    for r, c in pixels:  # shadow first, one row down and one column right
        for dc in (1, 2):
            cells[r + 1][c + dc] = ("░", 0)
    for r, c in pixels:
        cells[r][c] = cells[r][c + 1] = ("█", 1)
    lines = []
    for r, row in enumerate(cells):
        out, run, layer = [], "", None
        for char, kind in row + [("", -2)]:  # sentinel flushes the last run
            if kind != layer and run:
                out.append(style.rgb(run, GRADIENT[r], GRADIENT_256[r]) if layer == 1
                           else style.rgb(run, SHADOW, SHADOW_256) if layer == 0 else run)
                run = ""
            run, layer = run + char, kind
        lines.append("  " + "".join(out).rstrip())
    from . import __version__
    lines.append("  " + style.dim(f"{subtitle} · v{__version__}"))
    return "\n".join(lines)


# ---------------------------------------------------------------- loop views

STATE_LABELS = {"gave_up": "gave up", "budget": "budget"}


def state_of(loop: dict[str, Any]) -> str:
    if loop["status"] == "running" and loop.get("holding_model"):
        return "thinking"
    if loop["status"] == "running" and loop.get("waiting_model"):
        return "waiting"
    return loop["status"]


def state_badge(style: Style, state: str) -> str:
    color = {"done": style.green, "thinking": style.accent, "running": style.accent, "waiting": style.yellow,
             "paused": style.yellow, "gave_up": style.red, "failed": style.red, "budget": style.yellow,
             "stopped": style.dim, "starting": style.dim}.get(state, style.dim)
    return color("●") + " " + STATE_LABELS.get(state, state)


def score_bar(style: Style, score: float, cells: int = 8) -> str:
    filled = round(max(0.0, min(score, 1.0)) * cells)
    return style.accent("━" * filled) + style.dim("─" * (cells - filled))


def tests_text(tests: Any) -> str:
    return f"{tests[0]}/{tests[1]} tests" if tests else ""


def ps_table(style: Style, loops: list[dict[str, Any]]) -> str:
    if not loops:
        return style.dim("  No loops yet. Start one with: run --goal \"…\" --until \"pytest -q\"")
    goal_width = max(20, width() - 52)
    head = f"  {'ID':<5}{'STATE':<12}{'PRI':>4}  {'ITER':>6}  {'PROGRESS':<14}{'GOAL'}"
    lines = [style.dim(head)]
    for loop in loops:
        state = state_of(loop)
        pad = 12 - (2 + len(STATE_LABELS.get(state, state)))  # badge is "● label"
        iters = f"{loop['iteration']}/{loop['max_iters']}"
        lines.append("  " + style.accent(loop["id"].ljust(5)) + state_badge(style, state) + " " * max(pad, 1)
                     + f"{loop['priority']:>4}  {iters:>6}  " + score_bar(style, loop["score"])
                     + f" {loop['score']:.2f}  " + clip(loop["goal"], goal_width))
    return "\n".join(lines)


def loop_header(style: Style, loop: dict[str, Any], spec: dict[str, Any]) -> str:
    model = spec.get("model") if spec.get("provider") != "scripted" else "scripted replies"
    return "\n".join([
        f"  {style.accent('◆', bold=True)} {style.accent(loop['id'], bold=True)}  {style.bold(clip(spec['goal'], width() - 10))}",
        "    " + style.dim("until ") + clip(spec["until"], 48) + style.dim("  ·  ") + str(model)
        + style.dim("  ·  priority ") + str(spec["priority"]),
        "    " + style.dim("in    ") + short_path(spec["workspace"], 56),
        "",
    ])


def _icon(style: Style, outcome: str) -> str:
    return {"ok": style.green("✓"), "failed": style.red("✗"), "refused": style.yellow("⊘"),
            "invalid": style.yellow("?")}.get(outcome, " ")


REFUSALS = [(r"outside the workspace", "outside workspace"), (r"is not allowed", "command not allowed"),
            (r"is protected", "protected path"), (r"occurs (\d+) times", r"find text ×\1"),
            (r"no such file", "no such file"), (r"unknown action", "unknown action"),
            (r"needs a string field", "missing field"), (r"could not parse", "unparsable command")]


def short_refusal(detail: str) -> str:
    for pattern, label in REFUSALS:
        match = re.search(pattern, detail)
        if match:
            return match.expand(label)
    return clip(re.sub(r"^Refused:\s*", "", detail), 18)


def event_lines(style: Style, event: dict[str, Any], started: float | None = None,
                memo: dict[str, Any] | None = None) -> list[str]:
    """Calm rendering of one supervisor event; noise (syscalls, spawns, checkpoints) is skipped.

    Pass the same `memo` dict for consecutive events of one loop so repeated details (the test
    count) are only shown when they change.
    """
    kind, data, message = event["kind"], event.get("data") or {}, event["message"]
    memo = {} if memo is None else memo
    if kind == "baseline":
        memo["tests"] = data.get("tests")
        check = style.green("✓ passing") if data.get("check_exit") == 0 else style.red("✗ failing")
        tests = tests_text(data.get("tests"))
        return [f"  {style.dim('start')}  {check}" + (style.dim(f" · {tests}") if tests else "")]
    if kind == "iteration":
        verb = VERBS.get(data.get("action"), str(data.get("action")))
        target_width = min(34, max(16, width() - 76))
        if data["outcome"] == "refused":
            result = style.yellow(short_refusal(data.get("detail", "")))
        elif data["outcome"] == "invalid":
            result = style.yellow("invalid reply")
        elif data.get("action") == "run":
            result = (style.dim if data.get("exit_code") == 0 else style.red)(f"exit {data.get('exit_code')}")
        else:
            result = ""
        visible = len(re.sub(r"\033\[[0-9;]*m", "", result))
        tests = data.get("tests")
        changed = tests and tests != memo.get("tests")
        memo["tests"] = tests or memo.get("tests")
        extra = style.dim(f"  {tests_text(tests)}") if changed else ""
        star = style.accent("  ★ best") if data.get("improved") else ""
        return [f"  {data['n']:>3}  {_icon(style, data['outcome'])} {verb:<6} "
                f"{clip(data.get('target', ''), target_width):<{target_width}}  {result}{' ' * max(22 - visible, 2)}"
                f"{score_bar(style, data['score'])} {data['score']:.2f}{extra}{star}"]
    if kind in {"livelock", "stalled"}:
        if data.get("summary"):
            text = (f"{data['summary']} → rolled back to iteration {data['restored_iteration']}"
                    f" · {data['recovery']}/{data['max_recoveries']}")
        else:
            text = re.sub(r"^(Livelock|Stalled) detected: ", "", message)
        return ["       " + style.yellow("↺ " + clip(text, width() - 10))]
    notes = {"pause": ("⏸", style.yellow), "resume": ("▶", style.accent),
             "restart": ("⟳", style.yellow), "recovered": ("⟳", style.accent)}
    if kind in notes:
        icon, color = notes[kind]
        return ["       " + color(icon + " " + clip(message.rstrip("."), width() - 10))]
    if kind in FINAL:
        parts = []
        if data.get("iterations") is not None:
            parts.append(f"{data['iterations']} iterations")
        if started:
            parts.append(f"{round(event['ts'] - started)}s")
        if kind == "done":
            return ["", "  " + style.green("✔ done") + style.dim(" · " + " · ".join(["check passed"] + parts))]
        if kind == "gave_up":
            why = (f"{data['summary']}, after {data['recoveries']} rollbacks" if data.get("summary")
                   else re.sub(r"^Gave up: ", "", message).rstrip("."))
            return ["", "  " + style.red("✖ gave up") + style.dim(" · " + " · ".join([clip(why, 60)] + parts))]
        label, color = {"failed": ("✖ failed", style.red), "budget": ("◷ budget used", style.yellow),
                        "stopped": ("■ stopped", style.dim)}[kind]
        return ["", "  " + color(label) + style.dim(" · " + " · ".join([clip(message.rstrip("."), 60)] + parts))]
    return []


def status_line(style: Style, loop: dict[str, Any], frame: int) -> str:
    spin = style.accent(SPINNER[frame % len(SPINNER)])
    state = state_of(loop)
    nxt = loop["iteration"] + 1
    text = {"thinking": f"thinking · iteration {nxt}", "waiting": "waiting for a model turn",
            "running": f"working · iteration {nxt}", "starting": "starting"}.get(state)
    if state == "paused":
        return "       " + style.yellow(f"⏸ paused — resume with: resume {loop['id']}")
    return f"       {spin} " + style.dim(text or state)


class LiveLine:
    """A single status line redrawn in place below the streamed output (terminals only)."""

    def __init__(self, style: Style, stream: TextIO = sys.stdout) -> None:
        self.style, self.stream, self.shown = style, stream, False

    def clear(self) -> None:
        if self.shown:
            self.stream.write("\r\033[K")
            self.shown = False

    def draw(self, text: str) -> None:
        if not self.style.tty:
            return
        self.stream.write("\r\033[K" + text)
        self.stream.flush()
        self.shown = True

    def print(self, text: str) -> None:
        self.clear()
        self.stream.write(text + "\n")
        self.stream.flush()


if __name__ == "__main__":
    print(banner(Style()))
