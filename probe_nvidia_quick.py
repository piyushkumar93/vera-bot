"""Probe NVIDIA NIM model availability with a short timeout, one line at a time.

Tests a shortlist of catalog models with a small token budget and a 30s timeout,
appending each result to nvidia_quick.txt as it completes so a slow or dead
model cannot stall the whole sweep. Never prints the key.

Usage:  python probe_nvidia_quick.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "nvidia_quick.txt"
BASE = "https://integrate.api.nvidia.com/v1"

# Models present in the live catalog that are strong instruction followers.
# Names come from the catalog listing rather than memory, because the
# model-specific NIM deployments are being retired.
SHORTLIST = (
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-nano-3-30b-a3b",
    "z-ai/glm-5.3",
    "z-ai/glm-5.3-flash",
    "moonshotai/kimi-k3",
    "openai/gpt-oss-20b",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "mistralai/mistral-nemotron",
    "google/gemma-4-31b-it",
    "mistralai/mistral-large-2-instruct",
)
TIMEOUT = 30


def key() -> str:
    path = ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))
    return (os.environ.get("NVIDIA_API_KEY") or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("LLM_API_KEY") or "").strip()


def probe(token: str, model: str) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        "temperature": 0.2,
        "max_tokens": 20,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
            reply = data["choices"][0]["message"]["content"].strip().replace("\n", " ")
            return f"OK  {reply[:30]}"
    except urllib.error.HTTPError as exc:
        raw = " ".join(exc.read().decode("utf-8", "replace").split())
        if exc.code == 410:
            return "410 GONE (retired deployment)"
        if exc.code == 404:
            return "404 not available"
        return f"HTTP {exc.code} {raw[:70]}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__} {str(exc)[:60]}"


def main() -> int:
    token = key()
    with OUT.open("w", encoding="utf-8") as log:
        log.write(f"key prefix={token[:6]!r} length={len(token)}\n\n")
        for model in SHORTLIST:
            log.write(f"  {model:46} {probe(token, model)}\n")
            log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
