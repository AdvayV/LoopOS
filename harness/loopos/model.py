"""Model providers. The worker only needs `complete(messages) -> str` (a JSON action)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .syscalls import ACTION_SCHEMA


class ProviderError(Exception):
    pass


class Provider(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...


class OllamaProvider:
    """A model served by Ollama, locally or through an SSH tunnel to the GPU VM."""

    name = "ollama"

    def __init__(self, model: str, url: str = "http://localhost:11434", timeout: float = 600,
                 temperature: float = 0.2, context: int = 8192) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.options = {"temperature": temperature, "num_ctx": context}

    def complete(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "stream": False,
                           "format": ACTION_SCHEMA, "options": self.options}).encode()
        request = urllib.request.Request(f"{self.url}/api/chat", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise ProviderError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"cannot reach Ollama at {self.url}: {getattr(exc, 'reason', exc)}") from exc
        try:
            return reply["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected Ollama response: {str(reply)[:200]}") from exc


def check_ollama(url: str, model: str, timeout: float = 5) -> str:
    """Return "" if Ollama at `url` serves `model`, else a message saying how to fix it."""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as response:
            names = {m.get("name", "") for m in json.load(response).get("models", [])}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return (f"cannot reach Ollama at {url} ({getattr(exc, 'reason', exc)}). Start it with `ollama serve`, "
                "or open the GPU VM with `loopos-vm up` then `loopos-vm tunnel`")
    wanted = model if ":" in model else f"{model}:latest"
    if wanted not in names:
        available = ", ".join(sorted(names)) or "none"
        return f"model {model!r} is not on {url} (available: {available}). Pull it with `ollama pull {model}`"
    return ""


class ScriptedProvider:
    """Replays replies from a JSON list: for tests and demos without a model.

    After the list runs out, the last reply repeats (useful for exercising the livelock watchdog).
    `start` skips replies already used, so a restarted worker continues where it left off.
    """

    name = "scripted"

    def __init__(self, replies: list[Any], start: int = 0) -> None:
        if not replies:
            raise ProviderError("scripted provider needs at least one reply")
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls = start

    @classmethod
    def from_file(cls, path: str | Path, start: int = 0) -> "ScriptedProvider":
        return cls(json.loads(Path(path).read_text()), start)

    def complete(self, messages: list[dict[str, str]]) -> str:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def make_provider(spec: dict[str, Any], calls_made: int = 0) -> Provider:
    kind = spec.get("provider", "ollama")
    if kind == "ollama":
        return OllamaProvider(spec.get("model", "qwen2.5-coder:7b"),
                              spec.get("ollama_url", "http://localhost:11434"),
                              timeout=spec.get("model_timeout", 600))
    if kind == "scripted":
        return ScriptedProvider.from_file(spec["script"], start=calls_made)
    raise ProviderError(f"unknown provider {kind!r}")
