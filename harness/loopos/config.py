"""Remembered defaults, so everyday commands stay short.

    loopos config                       show settings and where each comes from
    loopos config model qwen2.5-coder:7b
    loopos config model default         back to the built-in value

Precedence: command-line flag > environment variable > config file > built-in default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# key: (built-in default, environment variable, type)
SETTINGS: dict[str, tuple[Any, str | None, type]] = {
    "provider": ("ollama", "LOOPOS_PROVIDER", str),
    "model": ("qwen2.5-coder:3b", "LOOPOS_MODEL", str),
    "url": (None, "LOOPOS_MODEL_URL", str),
    "priority": (5, "LOOPOS_PRIORITY", int),
    "iters": (30, None, int),
}
PROVIDERS = ("ollama", "openai", "scripted")


class ConfigError(ValueError):
    pass


def _path(root: Path) -> Path:
    return root / "config.json"


def load(root: Path) -> dict[str, Any]:
    try:
        return json.loads(_path(root).read_text())
    except (OSError, ValueError):
        return {}


def set_value(root: Path, key: str, value: str) -> Any:
    if key not in SETTINGS:
        raise ConfigError(f"unknown setting {key!r}; settings: {', '.join(SETTINGS)}")
    data = load(root)
    if value == "default":
        data.pop(key, None)
        stored = None
    else:
        kind = SETTINGS[key][2]
        try:
            stored = kind(value)
        except ValueError as exc:
            raise ConfigError(f"{key} must be a {kind.__name__}") from exc
        if key == "provider" and stored not in PROVIDERS:
            raise ConfigError(f"provider must be one of: {', '.join(PROVIDERS)}")
        data[key] = stored
    _path(root).write_text(json.dumps(data, indent=2))
    return stored


def resolve(root: Path, key: str, flag: Any = None) -> tuple[Any, str]:
    """(value, source) where source is "flag", "env", "config" or "default"."""
    default, env, kind = SETTINGS[key]
    if flag is not None:
        return flag, "flag"
    if env and os.environ.get(env):
        return kind(os.environ[env]), "env"
    data = load(root)
    if key in data:
        return data[key], "config"
    return default, "default"
