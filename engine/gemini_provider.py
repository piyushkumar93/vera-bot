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
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .nim_provider import _ForceIPv4, _flag, _truthy, extract_message

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-3.5-flash"

# Gemini's free tier rate-limits per minute and answers a burst with HTTP 429
# rather than serving the request, which silently costs a share of the wording
# layer. Measured on this key: 22 calls at 2.7s spacing all succeeded, while an
# unpaced burst failed. Calls are therefore spaced by this minimum gap, which
# sustains ~22 requests/minute with headroom. It is a floor, not a fixed delay:
# the first call of a burst proceeds immediately and an idle process never
# waits.
# Sampling temperature for the wording pass. The business decision is already
# fixed deterministically, so this only governs how freely the model may vary
# phrasing. Measured against 0.8: raising it did not reduce the rate at which
# the model echoes the draft (38% vs 33% once provider failures are excluded), so
# it stays low and deterministic. Raise it only if echoing becomes a problem.
TEMPERATURE = 0.3

MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL_SECONDS") or 2.7)

# Serialises the pacing check and the sleep so two concurrent ticks cannot both
# decide the previous call was "long enough ago" and fire at once.
_PACE_LOCK = threading.Lock()
_LAST_CALL = 0.0


def _pace() -> None:
    """Block until MIN_INTERVAL has passed since the previous Gemini call."""
    global _LAST_CALL
    with _PACE_LOCK:
        wait = _LAST_CALL + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL = time.monotonic()

# /v1/tick runs under a 30s budget, so per-message composition gets a small
# slice of it. A slow model must degrade, not hold the endpoint open.
# polish() may sample twice (see MAX_POLISH_ATTEMPTS), so the worst case for the
# wording layer is 2 x MAX_TIMEOUT = 24s, which still fits inside a 30s tick.
DEFAULT_TIMEOUT = 6.0
MAX_TIMEOUT = 12.0

# The whole retry/failover sequence shares ONE wall-clock budget equal to
# `timeout`, divided across the planned attempts. This keeps the "never wait
# indefinitely" guarantee intact: a tick cannot spend more on the wording layer
# than LLM_TIMEOUT_SECONDS allows, however many attempts run. The budget is
# enforced in total rather than per attempt, so a slow model degrades instead of
# being masked by a longer timeout.
#
# Backoff is applied only after an explicit 429 (rate limit), and is capped so
# it can never stall a tick. Moving to a *different* model does not back off:
# each model has its own quota pool, so a sibling is tried immediately.
MAX_BACKOFF = 1.5

# Sibling flash models, tried in order when the configured model cannot serve
# the request. Overload and quota are reported per-model, not per-key, so a
# sibling is the correct remedy for exactly the failures a single model reports.
# This stays a transport-level concern and does not alter the deterministic core.
DEFAULT_FALLBACK_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
)

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


# Statuses worth a second try on a *different* attempt or model. 429/500/502/503
# are overload or quota conditions that clear on their own; a timeout means the
# slot was lost, not that the answer was wrong. 400/401/403/404 are decisions
# about this exact request (bad payload, bad key, denied project, retired
# model) and are identical on every retry, so they fail fast instead of burning
# the budget to reach a guaranteed-same result.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _fallback_models(model: str) -> tuple[str, ...]:
    """Ordered sibling models to try after the configured one.

    A single misconfigured or overloaded model must not disable the whole
    wording layer, so a small fixed ladder of siblings is tried in turn. The
    primary is always attempted first; siblings only ever rescue a failure the
    primary cannot recover from. LLM_FALLBACK_MODELS overrides the default.
    """
    raw = _flag("LLM_FALLBACK_MODELS")
    if raw.strip():
        candidates = [part.strip() for part in raw.replace(";", ",").split(",")]
    else:
        candidates = list(DEFAULT_FALLBACK_MODELS)
    # Never retry the primary as its own fallback, and never repeat a sibling.
    ordered: list[str] = []
    for name in [model, *candidates]:
        if name and name != model and name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def _redact(text: str, key: str) -> str:
    """Strip the credential out of anything about to be logged or recorded.

    Gemini carries the key in the query string, so the key can leak into a
    request URL, and some error bodies echo the URL back. Every diagnostic
    string passes through here, and the key is replaced before the text is
    stored in STATS or printed.
    """
    if not text:
        return ""
    cleaned = text.replace(key, "<redacted>") if key else text
    # Belt and braces: catch the key even if it was URL-encoded or truncated.
    return re.sub(r"(key=)[^&\s\"']+", r"\1<redacted>", cleaned)


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
        "base_url": (
            _flag("GEMINI_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/"),
        "timeout": max(1.0, min(timeout, MAX_TIMEOUT)),
        "force_ipv4": _truthy("LLM_FORCE_IPV4"),
        "fallback_models": _fallback_models(
            _flag("LLM_MODEL") or DEFAULT_MODEL),

        # Gemini takes the key from LLM_API_KEY; GEMINI_API_KEY is accepted as
        # an alias so a Gemini-only environment also works.
        "has_key": bool(
            _flag("LLM_API_KEY") or _flag("GEMINI_API_KEY")
        ),
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
    STATS.update(
        attempts=0,
        successes=0,
        failures=0,
        rejections=0,
        last_latency_ms=None,
        last_error="",
    )


def _log(message: str) -> None:
    """Operational logging. Never includes the key or the request URL."""
    print(f"[gemini] {message}", flush=True)


def build_prompt(fact_pack: dict[str, Any], draft: str) -> str:
    """Render the compact, fact-only input handed to the model."""
    lines = [
        f"{key}: {value}"
        for key, value in fact_pack.items()
        if value not in ("", None, [])
    ]

    facts = (
        "\n".join(f"- {line}" for line in lines)
        or "- (no additional facts)"
    )

    return (
        f"APPROVED FACTS (the only facts you may use):\n"
        f"{facts}\n\n"
        f"DRAFT MESSAGE ({len(draft)} characters):\n"
        f"{draft}\n\n"
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

    parts = (
        (candidates[0].get("content") or {}).get("parts")
        or []
    )

    return "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict)
    )


def complete(fact_pack: dict[str, Any], draft: str) -> str | None:
    """Request a rewording.

    Returns the generated text, or None on any failure.

    None is the universal fallback signal: the caller keeps the deterministic
    body. No exception escapes this function.
    """
    settings = config()

    if not settings["enabled"]:
        _log(
            "skipped: LLM_PROVIDER is not 'gemini' "
            "(deterministic path)"
        )
        return None

    key = _flag("LLM_API_KEY") or _flag("GEMINI_API_KEY")

    if not key:
        STATS["failures"] += 1
        STATS["last_error"] = "LLM_API_KEY not set"

        _log(
            "skipped: LLM_API_KEY not set "
            "(deterministic path)"
        )
        return None

    payload = json.dumps(
        {
            "systemInstruction": {
                "parts": [
                    {
                        "text": _SYSTEM
                    }
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": build_prompt(
                                fact_pack,
                                draft,
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": TEMPERATURE,
                "topP": 0.9,
                "maxOutputTokens": 600,
            },
        }
    ).encode("utf-8")

    # One wall-clock budget covers every attempt and every fallback model, so
    # retrying can never make a tick slower than the configured timeout.
    budget = float(settings["timeout"])
    started = time.monotonic()
    deadline = started + budget

    # The configured model is always tried first. Siblings are only reached
    # when the primary reports a condition that cannot resolve on a retry.
    ladder = [settings["model"], *settings["fallback_models"]]
    per_attempt = max(1.0, budget / len(ladder))

    STATS["attempts"] += 1

    last_error = "unknown"
    for index, model in enumerate(ladder):
        remaining = deadline - time.monotonic()

        # Budget exhausted: stop rather than start an attempt that cannot
        # finish. The deterministic body is already the outcome.
        if remaining <= 0.5 and index > 0:
            _log(
                f"budget exhausted after {index} attempt(s) "
                f"-> deterministic fallback"
            )
            break

        # Back off only when rate-limited. A sibling model is a separate quota
        # pool, so trying it immediately is both faster and more likely to work.
        if index and "429" in last_error:
            pause = min(MAX_BACKOFF, max(0.0, remaining - per_attempt))
            if pause > 0:
                time.sleep(pause)

        timeout = max(0.5, min(per_attempt, deadline - time.monotonic()))
        text, error, retryable = _attempt(settings, key, model, payload, timeout)

        if text:
            # extract_message() is applied exactly as before, so a reply that
            # trails deliberation or quoting still resolves to the message.
            text = extract_message(text)
            if not isinstance(text, str) or not text.strip():
                last_error = f"empty content model={model}"
                break
            elapsed = (time.monotonic() - started) * 1000
            STATS["last_latency_ms"] = round(elapsed)
            STATS["successes"] += 1
            note = "" if model == settings["model"] else f" (via {model})"
            _log(
                f"ok model={model}{note} "
                f"latency={elapsed:.0f}ms chars={len(text)}"
            )
            return text

        last_error = error

        if "HTTP 429" in last_error:
            # A 429 is a project/billing quota limit, not a per-model capacity
            # blip, so no sibling model will do better. Continuing the ladder
            # would spend the entire budget on requests that cannot succeed and
            # then report a misleading timeout. Stop, and surface the real
            # cause so the operator can see quota rather than "fell back".
            _log(f"quota exhausted ({last_error}) -> deterministic fallback")
            break

        if not retryable:
            # A decision about this request (400/401/403/404) is identical on
            # every model, so stop instead of spending the rest of the budget.
            _log(f"unrecoverable: {error} -> deterministic fallback")
            break

    elapsed = (time.monotonic() - started) * 1000
    STATS["failures"] += 1
    STATS["last_latency_ms"] = round(elapsed)
    STATS["last_error"] = _redact(last_error, key)

    _log(
        f"request failed: {last_error} after {elapsed:.0f}ms "
        f"-> deterministic fallback"
    )

    return None


def _attempt(settings: dict[str, Any], key: str, model: str,
             payload: bytes, timeout: float) -> tuple[str | None, str, bool]:
    """Make one generateContent call.

    Returns (text, error_label, retryable). Exactly one of text and error_label
    is meaningful; retryable says whether another attempt could plausibly
    succeed. Never raises, and never returns the key or the request URL.
    """
    # The key travels in the query string for Gemini, so this URL is itself a
    # secret. It is never logged, never stored, and never surfaced in output.
    url = (
        f"{settings['base_url']}/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        # Pacing happens before the socket timeout starts being meaningful, and
        # is intentionally outside the request budget: a rate-limit wait is not
        # the model being slow, and charging it to the deadline would turn a
        # healthy provider into a spurious timeout.
        _pace()
        with _ForceIPv4(settings["force_ipv4"]):
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")

        data = json.loads(raw)

    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""

        # Redacted before it reaches the log or STATS: an error body can echo
        # the request URL, and the URL carries the key.
        detail = _redact(" ".join(body.split())[:300], key)
        label = f"HTTP {exc.code} model={model}"
        _log(f"{label} -> {detail}")
        return None, label, exc.code in RETRYABLE_STATUS

    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # URLError wraps DNS, refused, reset and TLS failures; TimeoutError is
        # an OSError subclass on 3.10+, so a stalled read lands here too.
        reason = getattr(exc, "reason", None)
        name = type(reason).__name__ if reason is not None else type(exc).__name__
        label = f"{name} model={model}"
        _log(f"{label} -> deterministic fallback")
        # A dead connection or a dropped slot is worth one more try elsewhere.
        return None, label, True

    except (ValueError, KeyError, IndexError, TypeError) as exc:
        # Malformed JSON or an unexpected envelope shape.
        label = f"{type(exc).__name__} model={model}"
        _log(f"malformed response: {label}")
        return None, label, True

    if not isinstance(data, dict):
        return None, "malformed response", True

    # Missing candidates, a safety block, or an empty finishReason all land
    # here as empty text.
    text = _text_from_response(data)

    if not isinstance(text, str) or not text.strip():
        label = f"empty content model={model}"
        # Missing candidates, a safety block, and a truncated finishReason all
        # land here. The reason is diagnostic only, so it is read defensively.
        candidates = data.get("candidates") or []
        first = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
        finish = first.get("finishReason")
        if finish:
            label += f" ({_redact(str(finish), key)})"
        _log(f"{label} -> deterministic fallback")
        return None, label, True

    return text, "", False
