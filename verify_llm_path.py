"""End-to-end proof of whether the deployed app path calls NVIDIA NIM.

The premise under test: does `LLM_PROVIDER=nvidia` cause bot.py/compose() to
issue NVIDIA NIM requests? This script runs the real HTTP service with a
deliberately invalid key and a black-hole NIM base URL, then drives a real
context push + tick.

If the app genuinely called NIM, the tick would fail or fall back. If the
decision path is deterministic and LLM-free, the message is produced
identically with the LLM env vars set, absent, or poisoned.

Run:  python verify_llm_path.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("VERIFY_PORT", "8161"))
BASE = f"http://127.0.0.1:{PORT}"

# A key that cannot possibly authenticate, and an unroutable base URL. Any real
# call would raise; a deterministic path never notices either.
POISON = {
    "LLM_PROVIDER": "nvidia",
    "LLM_MODEL": "nvidia/nemotron-3-super-120b-a12b",
    "NIM_BASE_URL": "http://127.0.0.1:9/v1",          # discard port
    "NVIDIA_API_KEY": "nvapi-DELIBERATELY_INVALID_KEY_FOR_PROBE",
    "OPENAI_API_KEY": "sk-DELIBERATELY_INVALID",
    "LLM_API_KEY": "DELIBERATELY_INVALID",
}

CATEGORY = {
    "id": "dentists", "name": "Dentists", "slug": "dentists",
    "voice": {"tone": "clinical, peer-to-peer", "vocab_taboo": ["cheap", "discount"],
              "vocab_preferred": ["clinical", "appointment", "treatment"]},
    "digest": [{"id": "radiograph", "title": "Radiograph dose limits",
                "source": "DCI circular", "summary": "New dose limits"}],
}
MERCHANT = {
    "id": "m_probe", "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic",
                 "owner_first_name": "Meera", "locality": "Koramangala",
                 "languages": ["en"]},
    "performance": {"views": 0, "calls": 0, "ctr": 0.0},
    "signals": [], "offers": [],
}
TRIGGER = {
    "id": "t_probe", "merchant_id": "m_probe", "kind": "regulatory_update",
    "urgency": 4, "scope": "merchant", "customer_id": None,
    "expires_at": "2099-12-15", "eligible": True,
    "payload": {"deadline_iso": "2026-12-15", "top_item_id": "radiograph",
                "source": "DCI circular 2026-11-04"},
}


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def run_once(label: str, env_overrides: dict[str, str]) -> dict:
    env = dict(os.environ)
    env.update(env_overrides)
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "bot.py"), "--port", str(PORT)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT))
    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                get("/v1/healthz")
                break
            except (urllib.error.URLError, OSError):
                if server.poll() is not None:
                    print(f"[{label}] server exited early:\n"
                          f"{server.stdout.read().decode('utf-8', 'replace')}")
                    raise SystemExit(1)
                time.sleep(0.3)
        else:
            print(f"[{label}] server never became healthy")
            raise SystemExit(1)

        meta = get("/v1/metadata")
        post("/v1/context", {"scope": "category", "context_id": "dentists",
                             "version": 1, "payload": CATEGORY})
        post("/v1/context", {"scope": "merchant", "context_id": "m_probe",
                             "version": 1, "payload": MERCHANT})
        post("/v1/context", {"scope": "trigger", "context_id": "t_probe",
                             "version": 1, "payload": TRIGGER})
        tick = post("/v1/tick", {"now": "2026-04-26T10:30:00Z",
                                 "available_triggers": ["t_probe"]})
        actions = tick.get("actions") or []
        body = actions[0]["body"] if actions else ""
        print(f"\n[{label}]")
        print(f"  metadata.model : {meta.get('model')}")
        print(f"  actions        : {len(actions)}")
        print(f"  body           : {body[:150]}")
        return {"model": meta.get("model"), "body": body, "count": len(actions)}
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def main() -> int:
    print("=" * 70)
    print("Does the deployed app path call NVIDIA NIM?")
    print("=" * 70)

    poisoned = run_once("LLM env set to nvidia + INVALID key + unroutable URL", POISON)
    clean = run_once("LLM env vars completely absent", {})

    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    same = poisoned["body"] == clean["body"] and poisoned["count"] == clean["count"]
    print(f"identical output with and without LLM env vars : {same}")
    print(f"reported model                                 : {poisoned['model']}")
    print(f"actions produced under poisoned config         : {poisoned['count']}")
    if same and poisoned["count"] > 0:
        print("\nCONCLUSION: the decision path made NO network call.")
        print("A real NIM call would have failed against the invalid key and")
        print("unroutable URL. The message is produced locally and identically,")
        print("so 'deterministic-rules-v1' is an ACCURATE description.")
        return 0
    print("\nCONCLUSION: output differed - the LLM path may be active. Investigate.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
