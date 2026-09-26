"""Smoke-test the configured judge provider: does it return parseable JSON?

Run before a scored pass so a provider that answers with prose (or reasoning
preambles) is caught here rather than silently falling back to judge defaults.

Usage:  python probe_provider_json.py
"""

from __future__ import annotations

import run_judge_scored  # noqa: F401  (for load_dotenv)
import judge_simulator as js

PROMPT = (
    "You are scoring a WhatsApp message. Reply with ONLY a JSON object, "
    "no prose and no markdown fence, in exactly this shape:\n"
    '{"score": 7, "subscores": {"relevance": 7, "clarity": 7}, '
    '"reasoning": "one short sentence"}'
)


def main() -> int:
    provider = js.create_provider()
    print(f"provider: {provider.name()}")
    try:
        raw = provider.complete(PROMPT)
    except Exception as exc:  # noqa: BLE001
        print(f"CALL FAILED: {type(exc).__name__}: {str(exc)[:200]}")
        return 1

    print(f"raw ({len(raw)} chars): {raw[:300]!r}")
    # _parse_response is a method; its JSON extraction is the part under test.
    import re
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        print("PARSE FAILED -> judge would score with fallbacks")
        return 1
    import json
    parsed = json.loads(match.group())
    print(f"parsed: {parsed}")
    if "score" not in parsed:
        print("WARN: no 'score' key in parsed JSON")
        return 1
    print("OK: provider returns usable judge JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
