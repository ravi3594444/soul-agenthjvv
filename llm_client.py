"""
llm_client.py — Shared LLM backend manager.

Single source of truth for all LLM calls across scorer.py, chat_bot.py,
and crew_agents.py.  Previously each module had its own copy of _init_llm()
and _call_backend(), leading to divergent behavior and triple maintenance.

Usage:
    from llm_client import get_backend, call_llm, LLMBackend

    backend = get_backend()          # auto-detects best available
    response = call_llm("hello")     # one-shot text completion
"""

import os
import time
import requests
from dataclasses import dataclass, field
from typing import Optional

# ── backend configs ──────────────────────────────────────────────────────────

_BACKENDS_CONFIG = [
    # (name, env_var, model, endpoint, min_interval_secs)
    ("groq",        "GROQ_API_KEY",       "llama-3.3-70b-versatile",
     "https://api.groq.com/openai/v1/chat/completions",            0.0),
    ("openrouter",  "OPENROUTER_API_KEY", "meta-llama/llama-4-scout:free",
     "https://openrouter.ai/api/v1/chat/completions",              0.0),
    ("together",    "TOGETHER_API_KEY",   "meta-llama/Llama-4-Scout-17E-16E-Instruct",
     "https://api.together.xyz/v1/chat/completions",               0.0),
    ("gemini",      "GEMINI_API_KEY",     "gemini-2.0-flash",
     None,                                                          0.0),
    ("mistral",     "MISTRAL_API_KEY",    "mistral-large-latest",
     "https://api.mistral.ai/v1/chat/completions",                 2.1),
]


@dataclass
class LLMBackend:
    name: str
    model: str
    key: str
    endpoint: Optional[str]
    min_interval: float = 0.0
    _last_call: float = field(default=0.0, repr=False, compare=False)

    # ── rate limiting ────────────────────────────────────────────────────────

    def throttle(self):
        if self.min_interval > 0:
            elapsed = time.time() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()

    # ── low-level HTTP call ──────────────────────────────────────────────────

    def _post(self, messages: list[dict], max_tokens: int = 300,
              temperature: float = 0.05) -> str:
        """Call the backend. Returns text content or raises."""
        self.throttle()

        if self.name == "gemini":
            # Gemini uses a different wire format
            contents = []
            for m in messages:
                role = "user" if m["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent?key={self.key}",
                json={
                    "contents": contents,
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                    },
                },
                timeout=30,
            )
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return parts[0]["text"] if parts else ""

        # OpenAI-compatible (groq, openrouter, together, mistral)
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/opportunity-engine"

        r = requests.post(
            self.endpoint,
            headers=headers,
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ── public call surface ──────────────────────────────────────────────────

    def complete(self, prompt: str, system: str = "", max_tokens: int = 300,
                 temperature: float = 0.05) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._post(messages, max_tokens=max_tokens, temperature=temperature)

    def chat(self, messages: list[dict], max_tokens: int = 2048,
             temperature: float = 0.7) -> str:
        return self._post(messages, max_tokens=max_tokens, temperature=temperature)


# ── module-level singleton ────────────────────────────────────────────────────

_backend: Optional[LLMBackend] = None
_initialized = False


def _clear_proxies():
    for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)


def _probe(name: str, model: str, key: str, endpoint: Optional[str],
           min_interval: float) -> Optional[LLMBackend]:
    """Return an LLMBackend if the provider responds, else None."""
    if not key:
        return None
    try:
        b = LLMBackend(name=name, model=model, key=key,
                       endpoint=endpoint, min_interval=min_interval)
        b.complete("Reply with just: ok", max_tokens=5)
        return b
    except Exception as e:
        print(f"[llm_client] {name}: probe failed — {e}")
        return None


def init(preference_order: Optional[list[str]] = None) -> bool:
    """
    Probe all configured backends in order and store the first that works.
    Returns True if any backend is available.

    Args:
        preference_order: list of backend names to try, e.g. ["mistral","groq"].
                          Defaults to the order in _BACKENDS_CONFIG.
    """
    global _backend, _initialized
    _clear_proxies()

    config_map = {n: (n, m, e, i) for n, _, m, e, i in _BACKENDS_CONFIG}
    order = preference_order or [n for n, *_ in _BACKENDS_CONFIG]

    for name in order:
        if name not in config_map:
            continue
        _, model, endpoint, min_interval = config_map[name]
        key = os.getenv(
            next(env for n, env, *_ in _BACKENDS_CONFIG if n == name), ""
        )
        b = _probe(name, model, key, endpoint, min_interval)
        if b:
            _backend = b
            _initialized = True
            print(f"[llm_client] using {name} ({model})")
            return True

    print("[llm_client] no LLM backend available — heuristic mode only")
    print("[llm_client] add GROQ_API_KEY, MISTRAL_API_KEY, or GEMINI_API_KEY to .env")
    _initialized = True
    return False


def get_backend() -> Optional[LLMBackend]:
    if not _initialized:
        init()
    return _backend


def call_llm(prompt: str, system: str = "", max_tokens: int = 300,
             temperature: float = 0.05) -> str:
    """Convenience wrapper — raises if no backend is available."""
    b = get_backend()
    if not b:
        raise RuntimeError("No LLM backend configured")
    return b.complete(prompt, system=system, max_tokens=max_tokens,
                      temperature=temperature)


def is_available() -> bool:
    return get_backend() is not None


def backend_name() -> str:
    b = get_backend()
    return b.name if b else "heuristic"
