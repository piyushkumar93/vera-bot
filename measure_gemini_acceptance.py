"""Measure the live Gemini acceptance rate and why candidates get rejected.

Drives the real polish() path over every submission message with the real
provider, and reports accepted vs rejected plus the reason for each rejection.
The key is read from .env and is never printed, and the request URL is never
printed because Gemini carries the key in the query string.

Run:  python measure_gemini_acceptance.py [runs]
"""

from __future__ import annotations

import collections
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import gemini_provider  # noqa: E402
from engine.composition import compose  # noqa: E402
from engine.nim_polish import build_fact_pack, polish, validate  # noqa: E402

DATA = ROOT / "dataset" / "expanded"
NOW = "2026-04-26T10:30:00Z"


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


def load_dir(sub: str, key: str) -> dict:
    out = {}
    directory = DATA / sub
    if not directory.is_dir():
        return out
    for name in os.listdir(directory):
        if name.endswith(".json"):
            record = json.loads((directory / name).read_text(encoding="utf-8"))
            out[record.get(key, name)] = record
    return out


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    load_env()
    print(f"provider={gemini_provider.config()['provider']} "
          f"model={gemini_provider.config()['model']} "
          f"key_present={gemini_provider.config()['has_key']}")

    merchants = load_dir("merchants", "merchant_id")
    triggers = load_dir("triggers", "id")
    customers = load_dir("customers", "id")
    categories = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                  for p in (DATA / "categories").glob("*.json")}
    rows = [json.loads(line) for line in
            (ROOT / "submission.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    totals = collections.Counter()
    reasons = collections.Counter()

    for run in range(1, runs + 1):
        for row in rows:
            merchant = merchants.get(row["merchant_id"], {})
            trigger = triggers.get(row["trigger_id"], {})
            customer = customers.get(row.get("customer_id")) if row.get("customer_id") else None
            category = categories.get(merchant.get("category_slug", ""), {})
            base = compose(category, merchant, trigger, customer, now=NOW)
            pack = build_fact_pack(base, category, merchant, trigger, customer)

            started = time.monotonic()
            result = polish(base, category, merchant, trigger, customer)
            took = int((time.monotonic() - started) * 1000)
            used_llm = result["body"] != base["body"]

            if used_llm:
                totals["accepted"] += 1
            else:
                # Distinguish "provider gave nothing" from "gave something the
                # validator refused", since only the second is a rejection.
                raw = gemini_provider.complete(pack, base["body"])
                if raw is None:
                    totals["provider_failed"] += 1
                    reasons[f"PROVIDER: {gemini_provider.STATS['last_error']}"] += 1
                elif raw.strip() == base["body"].strip():
                    totals["identical"] += 1
                    reasons["identical to draft"] += 1
                else:
                    totals["rejected"] += 1
                    _, why = validate(raw, base["body"], base, pack)
                    reasons[why.split(":")[0].split("(")[0].strip()] += 1

            if run == runs:
                verdict = "LLM" if used_llm else "FALLBACK"
                print(f"  {row['trigger_id'][:34]:<34} {verdict:<10} {took}ms")

    attempts = sum(totals.values())
    print(f"\nruns={runs} messages={attempts}")
    for key, value in totals.most_common():
        print(f"  {key:<18} {value:>4}  {value / max(1, attempts) * 100:5.1f}%")
    print("\nrejection reasons:")
    for reason, count in reasons.most_common():
        print(f"  {count:>4}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
