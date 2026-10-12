from __future__ import annotations

import json

import pytest

from loopos import config
from loopos.cli import build_parser
from loopos.project import detect_check


def test_settings_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOPOS_MODEL", raising=False)
    assert config.resolve(tmp_path, "model") == ("qwen2.5-coder:3b", "default")
    config.set_value(tmp_path, "model", "big:7b")
    assert config.resolve(tmp_path, "model") == ("big:7b", "config")
    monkeypatch.setenv("LOOPOS_MODEL", "env:1b")
    assert config.resolve(tmp_path, "model") == ("env:1b", "env")
    assert config.resolve(tmp_path, "model", "flag:3b") == ("flag:3b", "flag")
    config.set_value(tmp_path, "model", "default")
    assert "model" not in json.loads((tmp_path / "config.json").read_text())


def test_settings_are_validated(tmp_path):
    assert config.set_value(tmp_path, "priority", "8") == 8
    with pytest.raises(config.ConfigError):
        config.set_value(tmp_path, "priority", "high")
    with pytest.raises(config.ConfigError):
        config.set_value(tmp_path, "provider", "magic")
    with pytest.raises(config.ConfigError):
        config.set_value(tmp_path, "colour", "green")


@pytest.mark.parametrize("files, check", [
    ({"test_app.py": ""}, "pytest -q"),
    ({"pkg/tests/conftest.py": ""}, "pytest -q"),
    ({"package.json": '{"scripts": {"test": "jest"}}'}, "npm test"),
    ({"package.json": '{"scripts": {}}'}, None),
    ({"Makefile": "build:\n\tcc x.c\ntest:\n\t./run\n"}, "make test"),
    ({"Cargo.toml": ""}, "cargo test"),
    ({"go.mod": ""}, "go test ./..."),
    ({"README.md": ""}, None),
])
def test_detect_check(tmp_path, files, check):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    assert detect_check(tmp_path) == check


def test_short_command_forms_parse():
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.words == [] and args.until is None and args.model is None
    args = parser.parse_args(["run", "myproject", "fix the login bug", "-n", "5"])
    assert args.words == ["myproject", "fix the login bug"] and args.iters == 5
    assert parser.parse_args(["tasks", "project"]).count == 10
    assert parser.parse_args(["export"]).out == "loopos-dataset.jsonl"
    assert parser.parse_args(["stop", "l1"]).command == "stop"
