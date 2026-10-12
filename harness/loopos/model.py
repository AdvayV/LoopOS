"""Model providers. The worker only needs `complete(messages) -> str` (a JSON action)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .syscalls import ACTION_SCHEMA

DEFAULT_URLS = {"ollama": "http://localhost:11434", "openai": "http://localhost:8080/v1"}
SERVE_HINT = "or open the VM with `loopos-vm up` then `loopos-vm tunnel`"


class ProviderError(Exception):
    pass


class Provider(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...


def _http_json(url: str, body: dict[str, Any] | None = None, timeout: float = 600,
               headers: dict[str, str] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ProviderError(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from exc
    except ValueError as exc:
        raise ProviderError(f"{url} did not return JSON") from exc


class OllamaProvider:
    """A model served by Ollama, locally or through an SSH tunnel to the VM."""

    name = "ollama"

    def __init__(self, model: str, url: str = DEFAULT_URLS["ollama"], timeout: float = 600,
                 temperature: float = 0.2, context: int = 8192) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.options = {"temperature": temperature, "num_ctx": context}

    def complete(self, messages: list[dict[str, str]]) -> str:
        reply = _http_json(f"{self.url}/api/chat", {"model": self.model, "messages": messages, "stream": False,
                                                     "format": ACTION_SCHEMA, "options": self.options}, self.timeout)
        try:
            return reply["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"unexpected Ollama response: {str(reply)[:200]}") from exc


class OpenAIProvider:
    """Any OpenAI-compatible chat endpoint: llama.cpp's llama-server, vLLM, LM Studio.

    An API key, if the server needs one, is read from $LOOPOS_API_KEY (never stored in loop specs).
    """

    name = "openai"

    def __init__(self, model: str, url: str = DEFAULT_URLS["openai"], timeout: float = 600,
                 temperature: float = 0.2) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        key = os.environ.get("LOOPOS_API_KEY")
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}

    def complete(self, messages: list[dict[str, str]]) -> str:
        reply = _http_json(f"{self.url}/chat/completions", {
            "model": self.model, "messages": messages, "temperature": self.temperature,
            "response_format": {"type": "json_schema", "json_schema": {"name": "action", "schema": ACTION_SCHEMA}},
        }, self.timeout, self.headers)
        try:
            return reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response: {str(reply)[:200]}") from exc


def check_model(provider: str, url: str | None, model: str, timeout: float = 5) -> str:
    """Return "" if the server at `url` serves `model`, else a message saying how to fix it."""
    url = (url or DEFAULT_URLS[provider]).rstrip("/")
    try:
        if provider == "ollama":
            names = {m.get("name", "") for m in _http_json(f"{url}/api/tags", timeout=timeout).get("models", [])}
        else:
            key = os.environ.get("LOOPOS_API_KEY")
            listing = _http_json(f"{url}/models", timeout=timeout,
                                 headers={"Authorization": f"Bearer {key}"} if key else None)
            names = {m.get("id", "") for m in listing.get("data", [])}
    except ProviderError as exc:
        start = "start it with `ollama serve`" if provider == "ollama" else "start llama-server or vLLM"
        return f"{exc}. Is the model server running? Locally, {start}; {SERVE_HINT}"
    wanted = model if provider != "ollama" or ":" in model else f"{model}:latest"
    if wanted not in names:
        available = ", ".join(sorted(names)) or "none"
        fix = f" Pull it with `ollama pull {model}`." if provider == "ollama" else ""
        return f"model {model!r} is not served at {url} (available: {available}).{fix}"
    return ""


class ScriptedProvider:
    """Replays replies from a JSON list: for tests and demos without a model.

    After the list runs out, the last reply repeats (useful for exercising the livelock watchdog).
    `start` skips replies already used, so a restarted worker continues where it left off.
    `delay` simulates model thinking time, so loops visibly compete for model turns.
    """

    name = "scripted"

    def __init__(self, replies: list[Any], start: int = 0, delay: float = 0.0) -> None:
        if not replies:
            raise ProviderError("scripted provider needs at least one reply")
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls = start
        self.delay = delay

    @classmethod
    def from_file(cls, path: str | Path, start: int = 0, delay: float = 0.0) -> "ScriptedProvider":
        return cls(json.loads(Path(path).read_text()), start, delay)

    def complete(self, messages: list[dict[str, str]]) -> str:
        time.sleep(self.delay)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def make_provider(spec: dict[str, Any], calls_made: int = 0) -> Provider:
    kind = spec.get("provider", "ollama")
    if kind == "scripted":
        return ScriptedProvider.from_file(spec["script"], start=calls_made, delay=float(spec.get("script_delay") or 0))
    if kind not in DEFAULT_URLS:
        raise ProviderError(f"unknown provider {kind!r}")
    cls = OllamaProvider if kind == "ollama" else OpenAIProvider
    return cls(spec["model"], spec.get("model_url") or DEFAULT_URLS[kind], timeout=spec.get("model_timeout", 600))
