"""Compare NIM models on the one property that matters: clean short output.

Nemotron variants are reasoning-tuned and spend their budget deliberating. This
sends the real rewrite prompt to several catalog models and reports the raw
length, so the wording layer is pinned to a model that actually answers in one
piece.

Run:  python probe_nim_models.py
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
from engine import nim_provider  # noqa: E402
from engine.composition import compose  # noqa: E402
from engine.nim_polish import build_fact_pack, validate  # noqa: E402

MODELS = (
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "z-ai/glm-5.3-flash",
    "moonshotai/kimi-k2.6",
    "writer/palmyra-creative-122b",
    "google/gemma-4-31b-it",
)

CATEGORY = {"slug": "dentists", "name": "Dentists", "voice": {"tone": "clinical"}}
MERCHANT = {
    "id": "m", "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic", "owner_first_name": "Meera",
                 "locality": "Koramangala", "languages": ["en"]},
    "performance": {"views": 1200, "calls": 40, "delta_7d": {"views_pct": -0.2}},
    "offers": [{"title": "Free scaling", "status": "active"}],
}
TRIGGER = {"id": "t", "kind": "perf_dip", "urgency": 3, "payload": {}}


def main() -> int:
    from verify_nim_live import load_key
    key = load_key()
    if not key:
        print("NVIDIA_API_KEY not found")
        return 1
    os.environ.update({"LLM_PROVIDER": "nvidia", "NVIDIA_API_KEY": key,
                       "LLM_TIMEOUT_SECONDS": "25", "LLM_FORCE_IPV4": "1",
                       "NIM_BASE_URL": "https://integrate.api.nvidia.com/v1"})

    base = compose(CATEGORY, MERCHANT, TRIGGER, None, now="2026-04-26T10:30:00Z")
    pack = build_fact_pack(base, CATEGORY, MERCHANT, TRIGGER, None)
    print(f"draft ({len(base['body'])} chars): {base['body']}\n")

    for model in MODELS:
        os.environ["LLM_MODEL"] = model
        started = time.monotonic()
        raw = nim_provider.complete(pack, base["body"])
        ms = int((time.monotonic() - started) * 1000)
        if raw is None:
            print(f"  {model:44} FAIL {nim_provider.STATS['last_error']:>12}  {ms:>6}ms")
            continue
        ok, reason = validate(raw, base["body"], base, pack)
        verdict = "ACCEPT" if ok else f"reject:{reason[:34]}"
        print(f"  {model:44} raw={nim_provider.STATS['last_latency_ms']}ms "
              f"out={len(raw):>4}c  {verdict}")
        print(f"      -> {raw[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
