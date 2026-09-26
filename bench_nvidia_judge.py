"""Benchmark NVIDIA judge candidates on a realistically sized scoring call.

The availability probe used a one-word prompt, which a 550B MoE model answers
fast. A real judgment prompt is far longer and can produce ~1500 tokens, so this
measures end-to-end latency and whether the reply is parseable judge JSON.

Reports latency and validity per model so the judge model is chosen on
evidence rather than on catalog size.

Usage:  python bench_nvidia_judge.py
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "nvidia_bench.txt"
BASE = "https://integrate.api.nvidia.com/v1"

MODELS = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
)
TIMEOUT = 240

# Roughly the shape and size of a real judgment prompt: merchant context, a
# customer-facing message, and a strict JSON output contract.
PROMPT = (
    "You are a strict evaluator of WhatsApp messages sent by small-business "
    "merchants to their customers on MagicPin. Judge the message a merchant "
    "would actually be happy to send, and judge it as a customer would receive "
    "it in a chat thread.\n\n"
    "Merchant context: Dr. Meera's Dental Clinic, a dental clinic in Koramangala, "
    "Bengaluru. The patient has an upcoming root canal treatment scheduled and has "
    "not yet confirmed attendance. The clinic wants to reduce no-shows.\n\n"
    "Merchant message: \"Hi Dr. Meera, just a reminder about your upcoming dental "
    "treatment. Please reply YES to confirm your appointment, or let us know if "
    "you need to reschedule.\"\n\n"
    "Score each dimension from 0 to 10:\n"
    "  specificity      - uses concrete, verifiable detail rather than vague filler\n"
    "  category_fit     - appropriate for a dental clinic and this treatment\n"
    "  merchant_fit     - reflects this merchant's real name and goal\n"
    "  decision_quality - the underlying send/skip decision was correct\n"
    "  engagement       - likely to get a useful reply from the customer\n"
    "Total is the sum of the five dimensions, 0 to 50.\n\n"
    "Reply with ONLY a JSON object, no prose, no markdown fence:\n"
    '{"specificity":0,"category_fit":0,"merchant_fit":0,'
    '"decision_quality":0,"engagement":0,"reasoning":"one short sentence"}'
)
SYSTEM = "You are a precise evaluator. You always reply with valid JSON only."


def key() -> str:
    path = ROOT / ".env"
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))
    return (os.environ.get("LLM_API_KEY") or os.environ.get("NVIDIA_API_KEY")
            or os.environ.get("GEMINI_API_KEY") or "").strip()


def call(token: str, model: str) -> tuple[str, float]:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": PROMPT}],
        "temperature": 0.2,
        "top_p": 1,
        "max_tokens": 1500,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    start = time.time()
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"], time.time() - start


def evaluate(raw: str) -> str:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return "INVALID (no JSON)"
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as exc:
        return f"INVALID (bad JSON: {exc})"
    dims = ("specificity", "category_fit", "merchant_fit",
            "decision_quality", "engagement")
    if not all(d in parsed for d in dims):
        return f"PARTIAL (missing {[d for d in dims if d not in parsed]})"
    return f"VALID total={sum(int(parsed[d]) for d in dims)}/50"


def main() -> int:
    global ROOT_KEY
    ROOT_KEY = key()
    with OUT.open("w", encoding="utf-8") as log:
        log.write(f"key prefix={ROOT_KEY[:6]!r} length={len(ROOT_KEY)}\n\n")
        for model in MODELS:
            try:
                raw, seconds = call(ROOT_KEY, model)
                verdict = evaluate(raw)
                log.write(f"  {model:44} {seconds:6.1f}s  {verdict}\n")
                log.write(f"      raw head: {raw[:220]!r}\n")
            except urllib.error.HTTPError as exc:
                detail = " ".join(exc.read().decode("utf-8", "replace").split())
                log.write(f"  {model:44} HTTP {exc.code} {detail[:90]}\n")
            except Exception as exc:  # noqa: BLE001
                log.write(f"  {model:44} {type(exc).__name__} {str(exc)[:70]}\n")
            log.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
