"""End-to-end check of the live HTTP service with NIM enabled.

Starts nothing itself: point it at a running `python bot.py`. Pushes a real
context, ticks, and reports whether the NIM wording reached the response and
whether /v1/metadata updated to reflect it.

Run:  python verify_bot_e2e.py [base_url]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8131"

CATEGORY = {
    "id": "dentists", "name": "Dentists", "slug": "dentists",
    "voice": {"tone": "clinical, peer-to-peer", "vocab_taboo": ["cheap"]},
    "digest": [],
}
MERCHANT = {
    "id": "m_e2e", "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic", "owner_first_name": "Meera",
                 "locality": "Koramangala", "languages": ["en"]},
    "performance": {"views": 1200, "calls": 40, "delta_7d": {"views_pct": -0.2}},
    "signals": [], "offers": [{"title": "Free scaling", "status": "active"}],
}
TRIGGER = {"id": "t_e2e", "merchant_id": "m_e2e", "kind": "perf_dip",
           "urgency": 3, "scope": "merchant", "customer_id": None,
           "expires_at": "2099-01-01", "eligible": True,
           "payload": {"deadline_iso": "2026-12-15"}}


def call(method: str, path: str, payload=None, allow_conflict: bool = False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if allow_conflict and exc.code == 409:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        else:
            raise
    return body, int((time.monotonic() - started) * 1000)


def main() -> int:
    print("=" * 70)
    print(f"healthz   : {call('GET', '/v1/healthz')[0]['status']}")

    before = call("GET", "/v1/metadata")[0]
    print(f"metadata  : model={before['model']}")
    print(f"            llm={before.get('llm')}")

    for scope, cid, payload in (("category", "dentists", CATEGORY),
                                ("merchant", "m_e2e", MERCHANT),
                                ("trigger", "t_e2e", TRIGGER)):
        result, _ = call("POST", "/v1/context",
                         {"scope": scope, "context_id": cid, "version": 1,
                          "payload": payload}, allow_conflict=True)
        # A repeat run re-pushes version 1, which the context store correctly
        # rejects as a conflict. That is the versioning contract working, not a
        # failure, so treat it as already-applied.
        accepted = result.get("accepted", result.get("status") == "already_applied")
        print(f"context   : {scope} accepted={accepted} {result.get('reason', '')}")

    tick, tick_ms = call("POST", "/v1/tick",
                         {"now": "2026-04-26T10:30:00Z",
                          "available_triggers": ["t_e2e"]})
    actions = tick.get("actions") or []
    print(f"\ntick latency : {tick_ms}ms   actions={len(actions)}")
    if actions:
        action = actions[0]
        print(f"tick body    : {action['body']}")
        print(f"tick cta     : {action['cta']}   send_as={action['send_as']}")

    reply, _ = call("POST", "/v1/reply", {
        "conversation_id": "conv_m_e2e_merchant_t_e2e", "merchant_id": "m_e2e",
        "from_role": "merchant", "message": "Yes, please do that."})
    print(f"\nreply action : {reply.get('action')}")

    after = call("GET", "/v1/metadata")[0]
    print(f"\nmetadata after: model={after['model']}")
    print(f"              llm={after.get('llm')}")

    # Latency spread across repeated ticks, which is what the 30s budget cares
    # about: the average is easy, the tail is what can break an endpoint.
    samples: list[int] = []
    for i in range(12):
        _, ms = call("POST", "/v1/tick",
                     {"now": "2026-04-26T10:30:00Z", "available_triggers": ["t_e2e"]})
        samples.append(ms)
    samples_sorted = sorted(samples)
    print(f"\nlatency over {len(samples)} ticks: "
          f"avg={sum(samples) // len(samples)}ms  "
          f"min={samples_sorted[0]}ms  worst={samples_sorted[-1]}ms")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
