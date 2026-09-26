"""Deterministic message snapshot for before/after comparison.

Composes every row of the current submission through the real engine and writes
the bodies to a JSON file, so a change to composition can be compared message by
message without involving the judge or the network.

Run:  python snapshot_messages.py before.json
      python snapshot_messages.py after.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.composition import compose  # noqa: E402

DATA = ROOT / "dataset" / "expanded"
NOW = "2026-04-26T10:30:00Z"


def load_dir(sub: str, key: str) -> dict:
    out = {}
    path = DATA / sub
    if not path.is_dir():
        return out
    for name in os.listdir(path):
        if not name.endswith(".json"):
            continue
        try:
            record = json.loads((path / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            out[record.get(key, name)] = record
    return out


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "snapshot.json"

    merchants = load_dir("merchants", "merchant_id")
    triggers = load_dir("triggers", "id")
    customers = load_dir("customers", "id")
    categories = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                  for p in (DATA / "categories").glob("*.json")}

    rows = [json.loads(line) for line in
            (ROOT / "submission.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]

    snapshot = []
    for row in rows:
        merchant = merchants.get(row.get("merchant_id"), {})
        trigger = triggers.get(row.get("trigger_id"), {})
        customer = customers.get(row.get("customer_id")) if row.get("customer_id") else None
        slug = merchant.get("category_slug", "")
        category = categories.get(slug, {})
        action = compose(category, merchant, trigger, customer, now=NOW)
        snapshot.append({
            "trigger_id": row.get("trigger_id"),
            "merchant_id": row.get("merchant_id"),
            "category": slug,
            "body": action["body"],
            "cta": action["cta"],
            "template": action["template_name"],
        })

    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    print(f"wrote {len(snapshot)} messages -> {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
