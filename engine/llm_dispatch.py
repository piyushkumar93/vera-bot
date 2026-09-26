"""Provider dispatcher for the optional wording layer.

Selects a transport from LLM_PROVIDER and exposes one uniform surface to the
rest of the engine. Callers (engine/nim_polish.py, bot.py) never branch on
which vendor is in use, and adding a provider means adding one module plus one
entry here.

`LLM_PROVIDER` values other than a supported name - including empty - mean the
wording layer is off and the engine is fully deterministic.
"""

from __future__ import annotations

import os
from types import ModuleType

from . import gemini_provider, nim_provider

PROVIDERS: dict[str, ModuleType] = {
    "gemini": gemini_provider,
    "nvidia": nim_provider,
}


def active_name() -> str:
    """Name of the configured provider, whether or not it is usable."""
    return (os.environ.get("LLM_PROVIDER") or "").strip().lower()


def get_provider() -> ModuleType | None:
    """The provider module for the current LLM_PROVIDER, or None if off.

    None is returned for an unset or unsupported provider, which is the signal
    that composition must stay entirely deterministic.
    """
    return PROVIDERS.get(active_name())


def is_enabled() -> bool:
    """True only when a real provider call can actually be attempted."""
    provider = get_provider()
    return bool(provider and provider.is_enabled())


def complete(fact_pack: dict, draft: str) -> str | None:
    """Delegate to the active provider; None when off or on any failure."""
    provider = get_provider()
    if provider is None:
        return None
    return provider.complete(fact_pack, draft)


def stats() -> dict:
    """Reporting for the active provider, or an inert report when off."""
    provider = get_provider()
    if provider is None:
        return {"enabled": False, "configured": False, "model": None,
                "attempts": 0, "successes": 0, "failures": 0,
                "rejections": 0, "last_latency_ms": None, "last_error": ""}
    return provider.stats()


def reset_stats() -> None:
    provider = get_provider()
    if provider is not None:
        provider.reset_stats()
