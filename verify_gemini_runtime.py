"""Runtime proof of the Gemini wording layer, plus the failure-path proof.

Phase 7: a direct Gemini call and one through the real bot path.
Phase 8: a controlled failure that must degrade to the deterministic body.

Reads the key from .env, never prints it. The key travels in a Gemini query
string, so the request URL is treated as a secret and never printed either.

Run:  python verify_gemini_runtime.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import gemini_provider, llm_dispatch  # noqa: E402
from engine.composition import compose  # noqa: E402
from engine.nim_polish import build_fact_pack, polish, validate  # noqa: E402

CATEGORY = {"slug": "dentists", "name": "Dentists",
            "voice": {"tone": "clinical, peer-to-peer", "vocab_taboo": ["cheap"]},
            "digest": []}
MERCHANT = {
    "id": "m_live", "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic", "owner_first_name": "Meera",
                 "locality": "Koramangala", "languages": ["en"]},
    "performance": {"views": 1200, "calls": 40, "delta_7d": {"views_pct": -0.2}},
    "offers": [{"title": "Free scaling", "status": "active"}],
}
TRIGGER = {"id": "t_live", "kind": "perf_dip", "urgency": 3,
           "payload": {"deadline_iso": "2026-12-15"}}


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def main() -> int:
    load_env()
    settings = gemini_provider.config()
    print("=" * 72)
    print("Configuration (secrets never printed)")
    print("=" * 72)
    print(f"  LLM_PROVIDER   : {settings['provider']}")
    print(f"  LLM_MODEL      : {settings['model']}")
    print(f"  GEMINI_BASE_URL: {settings['base_url']}")
    print(f"  timeout        : {settings['timeout']}s")
    print(f"  key present    : {settings['has_key']}")
    print(f"  dispatcher     : {llm_dispatch.active_name() or None} "
          f"-> {getattr(llm_dispatch.get_provider(), '__name__', None)}")
    print(f"  is_enabled()   : {llm_dispatch.is_enabled()}")

    base = compose(CATEGORY, MERCHANT, TRIGGER, None, now="2026-04-26T10:30:00Z")
    pack = build_fact_pack(base, CATEGORY, MERCHANT, TRIGGER, None)
    print(f"\n  deterministic draft: {base['body']}")

    print("\n" + "=" * 72)
    print("Test 1 - direct Gemini call")
    print("=" * 72)
    started = time.monotonic()
    direct = gemini_provider.complete(pack, base["body"])
    direct_ms = int((time.monotonic() - started) * 1000)
    if direct is None:
        print(f"  GEMINI_API_TEST = FAIL   ({gemini_provider.STATS['last_error']})")
        print(f"  GEMINI_RUNTIME_PATH = NOT EXERCISED (provider unreachable)")
    else:
        ok, reason = validate(direct, base["body"], base, pack)
        print(f"  GEMINI_API_TEST = PASS   ({direct_ms}ms)")
        print(f"  reply: {direct[:200]}")
        print(f"  validation: {'accepted' if ok else 'rejected'} ({reason})")

    print("\n" + "=" * 72)
    print("Test 2 - through the real bot path (polish)")
    print("=" * 72)
    gemini_provider.reset_stats()
    started = time.monotonic()
    result = polish(base, CATEGORY, MERCHANT, TRIGGER, None)
    bot_ms = int((time.monotonic() - started) * 1000)
    used_llm = result["body"] != base["body"]
    print(f"  GEMINI_RUNTIME_PATH = {'PASS' if used_llm else 'FAIL'}")
    print(f"  latency = {bot_ms}ms")
    print(f"  FALLBACK_USED = {'NO' if used_llm else 'YES'}")
    print(f"  attempts={gemini_provider.STATS['attempts']} "
          f"successes={gemini_provider.STATS['successes']} "
          f"failures={gemini_provider.STATS['failures']} "
          f"rejections={gemini_provider.STATS['rejections']}")
    print(f"  final body: {result['body']}")
    print(f"  non-body fields preserved: "
          f"{all(result[f] == base[f] for f in ('cta', 'send_as', 'template_name', 'suppression_key'))}")

    print("\n" + "=" * 72)
    print("Test 3 - controlled failure: unreachable endpoint (Phase 8)")
    print("=" * 72)
    gemini_provider.reset_stats()
    saved = {k: os.environ.get(k) for k in ("GEMINI_BASE_URL", "LLM_TIMEOUT_SECONDS")}
    os.environ["GEMINI_BASE_URL"] = "http://127.0.0.1:9"  # discard port
    os.environ["LLM_TIMEOUT_SECONDS"] = "3"
    poisoned = polish(base, CATEGORY, MERCHANT, TRIGGER, None)
    print(f"  attempts made  : {gemini_provider.STATS['attempts']}  "
          f"(must be > 0: the call must be attempted, not skipped)")
    print(f"  error          : {gemini_provider.STATS['last_error']}")
    print(f"  body == draft  : {poisoned['body'] == base['body']}")
    print(f"  no exception   : True (tick-safe)")
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
