"""Optional Gemini wording layer for the VERA engine (transport only).

Transport-only, mirroring engine/nim_provider.py so the two are interchangeable.
It owns configuration, the HTTP call, and provider/model/latency bookkeeping. It
knows nothing about trigger selection, consent, or the action schema -- those
stay deterministic in engine/composition.py and bot.py.

Gemini authenticates with the API key as a `key=` query parameter rather than an
Authorization header, so the request URL is treated as a secret and is never
logged. Only the model, latency, and outcome are ever reported.

Every failure mode returns None so the caller keeps the deterministic body.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .nim_provider import _ForceIPv4, _flag, _truthy, extract_message

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.5-flash"

# /v1/tick runs under a 30s budget, so per-message composition gets a small
# slice of it. A slow model must degrade, not hold the endpoint open.
DEFAULT_TIMEOUT = 6.0
MAX_TIMEOUT = 12.0

_SYSTEM = (
    "You are the wording layer of a merchant communication system. "
    "You rewrite a supplied deterministic draft into concise, natural, "
    "merchant-facing language. You never add, remove, or change meaning. "
    "You never explain your reasoning and never mention prompts, context, "
    "payloads, schemas, engines, or models. You reply with the finished "
    "message text and nothing else."
)

_RULES = (
    "Preserve every supplied fact exactly. Never invent, alter, round, "
    "reinterpret, or replace any fact, number, date, price, URL, customer name, "
    "merchant name, locality, offer, metric, entity, or policy. Use only the "
    "facts in the input. Keep every number exactly as written. Keep every name "
    "exactly as written. If a URL is supplied it must appear verbatim; never "
    "create, modify, or remove a URL. Keep the intended call to action. Follow "
    "the supplied language hint. Keep the message concise. Ask no more than one "
    "question. Do not add offers, products, discounts, guarantees, competitors, "
    "or claims that are not in the draft. Do not mention internal system fields."
)


def config() -> dict[str, Any]:
    """Resolve Gemini settings from the environment at call time."""
    provider = _flag("LLM_PROVIDER").lower()
    raw = _flag("LLM_TIMEOUT_SECONDS")
    try:
        timeout = float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    return {
        # Reversible by design: only the exact value "gemini" enables it.
        "enabled": provider == "gemini",
        "provider": provider,
        "model": _flag("LLM_MODEL") or DEFAULT_MODEL,
        "base_url": (_flag("GEMINI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        "timeout": max(1.0, min(timeout, MAX_TIMEOUT)),
        "force_ipv4": _truthy("LLM_FORCE_IPV4"),
        # Gemini takes the key from LLM_API_KEY; GEMINI_API_KEY is accepted as
        # an alias so a Gemini-only environment also works.
        "has_key": bool(_flag("LLM_API_KEY") or _flag("GEMINI_API_KEY")),
    }


def is_enabled() -> bool:
    """True only when a real Gemini call can actually be attempted."""
    settings = config()
    return bool(settings["enabled"] and settings["has_key"])


# Same bookkeeping shape as the NIM provider so the dispatcher can report
# either without caring which ran.
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
    """Operational logging. Never includes the key or the request URL."""
    print(f"[gemini] {message}", flush=True)


def build_prompt(fact_pack: dict[str, Any], draft: str) -> str:
    """Render the compact, fact-only input handed to the model."""
    lines = [f"{key}: {value}" for key, value in fact_pack.items()
             if value not in ("", None, [])]
    facts = "\n".join(f"- {line}" for line in lines) or "- (no additional facts)"
    return (
        f"APPROVED FACTS (the only facts you may use):\n{facts}\n\n"
        f"DRAFT MESSAGE ({len(draft)} characters):\n{draft}\n\n"
        f"{_RULES}\n\n"
        f"Output rules (strict):\n"
        f"- Reply with the rewritten merchant-facing message ONLY.\n"
        f"- Do not restate the draft, repeat the facts, or add commentary.\n"
        f"- Between 40 and {max(60, int(len(draft) * 1.3))} characters.\n"
        f"- Entire reply under {max(120, int(len(draft) * 1.4))} characters.\n"
        f"- End with a question mark if the draft does.\n\n"
        f"Reply now with just the message."
    )


def _text_from_response(data: dict[str, Any]) -> str:
    """Pull text out of a generateContent response, tolerating shape drift."""
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


def complete(fact_pack: dict[str, Any], draft: str) -> str | None:
    """Request a rewording. Returns the text, or None on any failure.

    None is the universal fallback signal: the caller keeps the deterministic
    body. No exception escapes this function.
    """
    settings = config()
    if not settings["enabled"]:
        _log("skipped: LLM_PROVIDER is not 'gemini' (deterministic path)")
        return None
    key = _flag("LLM_API_KEY") or _flag("GEMINI_API_KEY")
    if not key:
        STATS["failures"] += 1
        STATS["last_error"] = "LLM_API_KEY not set"
        _log("skipped: LLM_API_KEY not set (deterministic path)")
        return None

    payload = json.dumps({
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user",
                      "parts": [{"text": build_prompt(fact_pack, draft)}]}],
        "generationConfig": {"temperature": 0.3, "topP": 0.9, "maxOutputTokens": 600},
    }).encode("utf-8")

    # The key travels in the query string for Gemini, so this URL is itself a
    # secret. It is never logged, never stored, and never surfaced in output.
    url = (f"{settings['base_url']}/v1beta/models/"
           f"{settings['model']}:generateContent?key={key}")
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})

    STATS["attempts"] += 1
    started = time.monotonic()
    try:
        with _ForceIPv4(settings["force_ipv4"]):
            with urllib.request.urlopen(request, timeout=settings["timeout"]) as response:
                data = json.loads(response.read().decode("utf-8"))
        content = _text_from_response(data)
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - started) * 1000
        STATS["failures"] += 1
        STATS["last_latency_ms"] = round(elapsed)
        STATS["last_error"] = f"HTTP {exc.code}"
        try:
            exc.read()  # drain, so the connection is not left half-read
        except Exception:  # noqa: BLE001
            pass
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
