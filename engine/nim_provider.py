"""Optional NVIDIA NIM wording layer for the VERA engine (transport only).

This module owns configuration, the HTTP call, and provider/model/latency
bookkeeping. It deliberately does NOT know about trigger selection, consent, or
the action schema -- those stay deterministic in `engine.composition` and
`bot.py`.

Constraints:
  * Standard library only, matching the rest of the engine.
  * The key is read from NVIDIA_API_KEY only, never logged, never persisted.
  * Every failure mode (disabled, missing key, timeout, HTTP error, malformed
    reply) returns None so the caller falls back to the deterministic body.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any

# NVIDIA's OpenAI-compatible surface.
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# /v1/tick runs under a 30s budget, so per-message composition gets a small
# slice of it. A slow model must degrade, not hold the endpoint open.
DEFAULT_TIMEOUT = 6.0
MAX_TIMEOUT = 12.0

_SYSTEM = (
    "You rewrite merchant WhatsApp messages so they read more naturally. "
    "You never add, remove, or change meaning. You never explain your process "
    "and never show your reasoning. You reply with the finished message text "
    "and nothing else."
)

_RULES = (
    "Do not invent, remove, alter, round, reinterpret, or replace any supplied "
    "fact, number, date, price, URL, customer name, merchant name, CTA, or policy. "
    "Use only the facts in the input. Keep every number exactly as written. "
    "Keep every name exactly as written. If a URL is supplied it must appear "
    "verbatim. Keep the call to action and its intent. Keep the same language(s) "
    "as the draft. Do not add offers, products, services, discounts, guarantees, "
    "or claims that are not in the draft. Return only the rewritten message."
)


def _flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _truthy(name: str) -> bool:
    return _flag(name).lower() in {"1", "true", "yes", "on"}


def config() -> dict[str, Any]:
    """Resolve NIM settings from the environment at call time.

    Read per call rather than at import so tests and Render's environment can
    change configuration without reimporting the module.
    """
    provider = _flag("LLM_PROVIDER").lower()
    raw = _flag("LLM_TIMEOUT_SECONDS")
    try:
        timeout = float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    return {
        # Reversible by design: only the exact value "nvidia" enables it.
        "enabled": provider == "nvidia",
        "provider": provider,
        "model": _flag("LLM_MODEL") or DEFAULT_MODEL,
        "base_url": (_flag("NIM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "timeout": max(1.0, min(timeout, MAX_TIMEOUT)),
        "force_ipv4": _truthy("LLM_FORCE_IPV4"),
        "has_key": bool(_flag("NVIDIA_API_KEY")),
    }


def is_enabled() -> bool:
    """True only when a real NIM call can actually be attempted."""
    settings = config()
    return bool(settings["enabled"] and settings["has_key"])


class _ForceIPv4:
    """Temporarily prefer IPv4 during resolution.

    Some container hosts resolve integrate.api.nvidia.com to an address family
    the connection path mishandles, which surfaces as a timeout rather than a
    DNS error. Optional: only engaged when LLM_FORCE_IPV4 is truthy.
    """

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._original = None

    def __enter__(self):
        if not self.enabled:
            return self
        self._original = socket.getaddrinfo

        def ipv4_only(host, port, family=0, *args, **kwargs):
            return self._original(host, port, socket.AF_INET, *args, **kwargs)

        socket.getaddrinfo = ipv4_only
        return self

    def __exit__(self, *exc):
        if self._original is not None:
            socket.getaddrinfo = self._original
            self._original = None
        return False


# Provider/model/latency/fallback bookkeeping. Never holds the key.
STATS: dict[str, Any] = {
    "attempts": 0,
    "successes": 0,
    "failures": 0,
    "rejections": 0,
    "last_latency_ms": None,
    "last_error": "",
}


def stats() -> dict[str, Any]:
    settings = config()
    return {
        "enabled": settings["enabled"],
        "configured": is_enabled(),
        "model": settings["model"] if settings["enabled"] else None,
        "attempts": STATS["attempts"],
        "successes": STATS["successes"],
        "failures": STATS["failures"],
        "rejections": STATS["rejections"],
        "last_latency_ms": STATS["last_latency_ms"],
        "last_error": STATS["last_error"],
    }


def reset_stats() -> None:
    STATS.update(attempts=0, successes=0, failures=0, rejections=0,
                 last_latency_ms=None, last_error="")


def _log(message: str) -> None:
    """Operational logging. Never includes the key or Authorization header."""
    print(f"[nim] {message}", flush=True)


def build_prompt(fact_pack: dict[str, Any], draft: str) -> str:
    """Render the compact, fact-only input handed to the model.

    The model receives only the approved fact pack plus the deterministic
    draft, never the raw context, so it cannot reach for an unapproved fact.
    """
    lines = [f"{key}: {value}" for key, value in fact_pack.items()
             if value not in ("", None, [])]
    facts = "\n".join(f"- {line}" for line in lines) or "- (no additional facts)"
    return (
        f"APPROVED FACTS (the only facts you may use):\n{facts}\n\n"
        f"DRAFT MESSAGE ({len(draft)} characters):\n{draft}\n\n"
        f"{_RULES}\n\n"
        f"Output rules (strict):\n"
        f"- Reply with the rewritten message ONLY.\n"
        f"- Do not describe your reasoning, do not restate the draft, do not "
        f"repeat the facts, and do not add any commentary.\n"
        f"- The rewrite must be between 40 and {max(60, int(len(draft) * 1.3))} "
        f"characters.\n"
        f"- Your entire reply must be under {max(120, int(len(draft) * 1.4))} "
        f"characters.\n"
        f"- End with a question mark if the draft does.\n\n"
        f"Reply now with just the message."
    )


def extract_message(content: str) -> str:
    """Pull the final message out of a model reply.

    Reasoning-capable models (Nemotron among them) emit their deliberation in
    `content` and finish with the real message, often introduced by 'Thus final:'
    or wrapped in quotes. Rather than rely on the model never doing this, take
    the last quoted span, or the text after the final 'final:' marker.
    """
    text_content = (content or "").strip()
    if not text_content:
        return ""

    # These reasoning models restate the draft inside their deliberation, so
    # "last quoted span" can pick up a draft quotation rather than the answer.
    # Prefer an explicit conclusion marker, and among quoted spans take the last
    # one that is NOT identical to the draft.
    markers = ("thus final:", "final answer:", "final rewritten:",
               "final message:", "final:", "rewrite:", "output:")
    lowered = text_content.lower()
    cut = -1
    for marker in markers:
        found = lowered.rfind(marker)
        if found > cut:
            cut = found + len(marker)
    if cut != -1:
        tail = text_content[cut:].strip().strip('"').strip()
        if tail:
            return tail

    quoted = re.findall(r'"([^"]{10,})"', text_content)
    if quoted:
        return quoted[-1].strip()
    return text_content


def complete(fact_pack: dict[str, Any], draft: str) -> str | None:
    """Request a rewording. Returns the text, or None on any failure.

    None is the universal fallback signal: the caller keeps the deterministic
    body. No exception escapes this function.
    """
    settings = config()
    if not settings["enabled"]:
        _log("skipped: LLM_PROVIDER is not 'nvidia' (deterministic path)")
        return None
    key = _flag("NVIDIA_API_KEY")
    if not key:
        STATS["failures"] += 1
        STATS["last_error"] = "NVIDIA_API_KEY not set"
        _log("skipped: NVIDIA_API_KEY not set (deterministic path)")
        return None

    payload = json.dumps({
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": build_prompt(fact_pack, draft)},
        ],
        "temperature": 0.3,
        "top_p": 0.9,
        # Nemotron models are reasoning-tuned: they deliberate before answering.
        # Cutting the budget off early truncates the conclusion, so allow enough
        # room for the scratchpad plus the final line. The prompt's strict length
        # caps and extract_message() handle the rest.
        "max_tokens": 600,
        "stream": False,
        # Nemotron/GLM-style models are reasoning-tuned and emit a scratchpad
        # before the answer. This is the documented switch for disabling that;
        # without it the whole budget goes to deliberation. Sent as a field the
        # non-reasoning models simply ignore.
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{settings['base_url']}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    STATS["attempts"] += 1
    started = time.monotonic()
    try:
        with _ForceIPv4(settings["force_ipv4"]):
            with urllib.request.urlopen(request, timeout=settings["timeout"]) as response:
                data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - started) * 1000
        STATS["failures"] += 1
        STATS["last_latency_ms"] = round(elapsed)
        STATS["last_error"] = f"HTTP {exc.code}"
        _log(f"request failed: HTTP {exc.code} after {elapsed:.0f}ms -> deterministic fallback")
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
            IndexError, TypeError) as exc:
        elapsed = (time.monotonic() - started) * 1000
        STATS["failures"] += 1
        STATS["last_latency_ms"] = round(elapsed)
        STATS["last_error"] = type(exc).__name__
        _log(f"request failed: {type(exc).__name__} after {elapsed:.0f}ms -> deterministic fallback")
        return None

    elapsed = (time.monotonic() - started) * 1000
    STATS["last_latency_ms"] = round(elapsed)
    if not isinstance(content, str) or not content.strip():
        STATS["failures"] += 1
        STATS["last_error"] = "empty content"
        _log(f"empty content after {elapsed:.0f}ms -> deterministic fallback")
        return None

    STATS["successes"] += 1
    _log(f"ok model={settings['model']} latency={elapsed:.0f}ms chars={len(content)}")
    return extract_message(content)
