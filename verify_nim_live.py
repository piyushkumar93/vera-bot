"""Real NVIDIA NIM verification: one direct call, one through the bot.

Uses the live NVIDIA_API_KEY from the environment. The key is never printed and
never written to disk; only a length/prefix-free boolean is reported.

Run:  python verify_nim_live.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import nim_provider  # noqa: E402
from engine.composition import compose  # noqa: E402
from engine.nim_polish import build_fact_pack, polish, validate  # noqa: E402

CATEGORY = {
    "slug": "dentists", "name": "Dentists",
    "voice": {"tone": "clinical, peer-to-peer", "vocab_taboo": ["cheap"]},
    "digest": [],
}
MERCHANT = {
    "id": "m_live", "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic", "owner_first_name": "Meera",
                 "locality": "Koramangala", "languages": ["en"]},
    "performance": {"views": 1200, "calls": 40, "delta_7d": {"views_pct": -0.2}},
    "offers": [{"title": "Free scaling", "status": "active"}],
}
TRIGGER = {"id": "t_live", "kind": "perf_dip", "urgency": 3,
           "payload": {"deadline_iso": "2026-12-15"}}


def load_key() -> str:
    """Read NVIDIA_API_KEY from the environment, or .env as a fallback."""
    if not os.environ.get("NVIDIA_API_KEY"):
        env_path = ROOT / ".env"
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("NVIDIA_API_KEY="):
                    os.environ["NVIDIA_API_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
                elif line.startswith("LLM_API_KEY="):
                    os.environ.setdefault("NVIDIA_API_KEY",
                                          line.split("=", 1)[1].strip().strip("\"'"))
    return (os.environ.get("NVIDIA_API_KEY") or "").strip()


def main() -> int:
    key = load_key()
    if not key:
        print("NVIDIA_API_KEY not found in the environment or .env")
        return 1

    os.environ["LLM_PROVIDER"] = "nvidia"
    os.environ.setdefault("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    os.environ.setdefault("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    os.environ["LLM_TIMEOUT_SECONDS"] = "25"
    os.environ["LLM_FORCE_IPV4"] = "1"

    settings = nim_provider.config()
    print("=" * 72)
    print("Configuration")
    print("=" * 72)
    print(f"  LLM_PROVIDER  : {settings['provider']}")
    print(f"  LLM_MODEL     : {settings['model']}")
    print(f"  NIM_BASE_URL  : {settings['base_url']}")
    print(f"  timeout       : {settings['timeout']}s")
    print(f"  force_ipv4    : {settings['force_ipv4']}")
    print(f"  key present   : {settings['has_key']} (never printed)")

    base = compose(CATEGORY, MERCHANT, TRIGGER, None, now="2026-04-26T10:30:00Z")
    pack = build_fact_pack(base, CATEGORY, MERCHANT, TRIGGER, None)
    print(f"\n  deterministic draft:\n    {base['body']}")
    print(f"  fact pack keys     : {sorted(pack)}")

    print("\n" + "=" * 72)
    print("Test 1 - direct NIM request via engine.nim_provider")
    print("=" * 72)
    started = time.monotonic()
    direct = nim_provider.complete(pack, base["body"])
    direct_ms = int((time.monotonic() - started) * 1000)
    if direct is None:
        print(f"  NIM_DIRECT_TEST: FAIL  ({nim_provider.STATS['last_error']})")
    else:
        print(f"  NIM_DIRECT_TEST: PASS  ({direct_ms}ms)")
        print(f"  FULL REPLY ({len(direct)} chars):\n---\n{direct}\n---")
        ok, reason = validate(direct, base["body"], base, pack)
        print(f"  validation: {'accepted' if ok else 'rejected'} ({reason})")

    print("\n" + "=" * 72)
    print("Test 2 - through the real bot path (polish)")
    print("=" * 72)
    nim_provider.reset_stats()
    started = time.monotonic()
    result = polish(base, CATEGORY, MERCHANT, TRIGGER, None)
    bot_ms = int((time.monotonic() - started) * 1000)
    used_nim = result["body"] != base["body"]
    print(f"  BOT_NIM_PATH     : {'PASS' if used_nim else 'FALLBACK'}")
    print(f"  latency          : {bot_ms}ms")
    print(f"  FINAL_BODY_FROM_NIM: {'YES' if used_nim else 'NO'}")
    print(f"  stats            : {nim_provider.stats()}")
    print(f"\n  final body:\n    {result['body']}")
    print(f"\n  non-body fields preserved: "
          f"{all(result[f] == base[f] for f in ('cta', 'send_as', 'template_name', 'suppression_key'))}")

    print("\n" + "=" * 72)
    print("Test 3 - poisoned config: NIM enabled, invalid key, dead endpoint")
    print("=" * 72)
    nim_provider.reset_stats()
    saved = {k: os.environ.get(k) for k in
             ("NIM_BASE_URL", "NVIDIA_API_KEY", "LLM_TIMEOUT_SECONDS")}
    os.environ["NIM_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["NVIDIA_API_KEY"] = "nvapi-INTENTIONALLY_INVALID_KEY"
    os.environ["LLM_TIMEOUT_SECONDS"] = "3"
    poisoned = polish(base, CATEGORY, MERCHANT, TRIGGER, None)
    print(f"  attempts made    : {nim_provider.STATS['attempts']}")
    print(f"  error            : {nim_provider.STATS['last_error']}")
    print(f"  body == draft    : {poisoned['body'] == base['body']}")
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
