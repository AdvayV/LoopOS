from __future__ import annotations

import io

from loopos import ui
from loopos.cli import ShellError, ShellParser, build_parser


def plain() -> ui.Style:
    return ui.Style(io.StringIO())  # not a terminal: no colour codes


def test_banner_spells_loop_os_with_a_shadow():
    lines = ui.banner(plain()).splitlines()
    assert len(lines) == 7  # 5 glyph rows, 1 shadow row, subtitle
    assert lines[0].startswith("  ██        ████████")
    assert set(lines[5].strip()) == {"░", " "}
    assert "v0.1.0" in lines[-1]


def test_banner_uses_a_yellow_to_green_gradient_on_terminals():
    style = plain()
    style.color = style.truecolor = True
    rows = ui.banner(style).splitlines()
    assert "38;2;242;232;92" in rows[0] and "38;2;122;190;66" in rows[4]


def test_iteration_lines_are_compact_and_show_test_counts_only_when_they_change():
    memo: dict = {}
    style = plain()
    base = {"kind": "baseline", "message": "", "data": {"check_exit": 1, "score": 0.5, "tests": [1, 2]}}
    assert ui.event_lines(style, base, memo=memo) == ["  start  ✗ failing · 1/2 tests"]

    def iteration(n, **data):
        payload = {"n": n, "action": "read_file", "target": "a.py", "outcome": "ok", "score": 0.5,
                   "tests": [1, 2], "improved": False} | data
        return ui.event_lines(style, {"kind": "iteration", "message": "", "data": payload}, memo=memo)[0]

    first = iteration(1)
    assert "✓ read   a.py" in first and "tests" not in first
    refused = iteration(2, outcome="refused", detail="Refused: path '../x' is outside the workspace")
    assert "⊘" in refused and "outside workspace" in refused
    fixed = iteration(3, action="edit_file", score=1.0, tests=[2, 2], improved=True)
    assert "2/2 tests" in fixed and "★ best" in fixed


def test_notes_and_final_lines_use_event_details():
    style = plain()
    livelock = {"kind": "livelock", "message": "long text", "data": {
        "summary": "same action, same result ×3", "restored_iteration": 0, "recovery": 1, "max_recoveries": 2}}
    assert ui.event_lines(style, livelock) == ["       ↺ same action, same result ×3 → rolled back to iteration 0 · 1/2"]
    done = {"kind": "done", "message": "x", "ts": 110.0, "data": {"iterations": 3}}
    assert ui.event_lines(style, done, started=100.0)[-1] == "  ✔ done · check passed · 3 iterations · 10s"
    gave_up = {"kind": "gave_up", "message": "x", "ts": 0, "data": {"summary": "stuck", "recoveries": 2, "iterations": 9}}
    assert ui.event_lines(style, gave_up)[-1] == "  ✖ gave up · stuck, after 2 rollbacks · 9 iterations"
    assert ui.event_lines(style, {"kind": "syscall", "message": "noise"}) == []


def test_short_helpers():
    assert ui.short_refusal("Refused: command 'curl' is not allowed; allowed: cat, ls") == "command not allowed"
    assert ui.short_refusal("Refused: `find` text occurs 2 times in a.py") == "find text ×2"
    assert ui.short_path("/a/b/c/d/e/f/g/h/i/j/k/l", 12) == "/…/k/l"
    assert ui.clip("a   b\nc", 10) == "a b c" and ui.clip("abcdefghij", 5) == "abcd…"


def test_ps_table_lists_state_progress_and_goal():
    loops = [{"id": "l1", "status": "running", "holding_model": True, "waiting_model": False, "priority": 8,
              "iteration": 2, "max_iters": 30, "score": 0.5, "goal": "Fix it"}]
    table = ui.ps_table(plain(), loops).splitlines()
    assert table[0].split() == ["ID", "STATE", "PRI", "ITER", "PROGRESS", "GOAL"]
    assert "● thinking" in table[1] and "2/30" in table[1] and table[1].endswith("Fix it")


def test_shell_parser_reports_errors_in_one_line():
    parser = build_parser(ShellParser)
    try:
        parser.parse_args(["run", "--goal", "x"])
    except ShellError as exc:
        assert "--until" in str(exc) and "run --help" in str(exc)
    else:
        raise AssertionError("expected a ShellError")
