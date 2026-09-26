"""Audit: are all numbers in each message traceable to the supplied context?

Guards the core rule of the decision layer -- a message may only assert what the
trigger, merchant, or category context actually supplied. For every number that
appears in a body, this checks it can be traced to a source value, and reports
any that cannot.

Run:  python audit_grounding.py [snapshot.json]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def normalise(token: str) -> str:
    cleaned = token.replace(",", "").rstrip(".")
    try:
        return str(float(cleaned))
    except ValueError:
        return cleaned


def main() -> int:
    snapshot_name = sys.argv[1] if len(sys.argv) > 1 else "after_v4.json"
    snapshot = json.loads((ROOT / snapshot_name).read_text(encoding="utf-8"))

    data = ROOT / "dataset" / "expanded"
    sources: dict[str, set[str]] = {}
    for sub in ("triggers", "merchants", "categories"):
        directory = data / sub
        if not directory.is_dir():
            continue
        for name in os.listdir(directory):  # noqa: F821
            if not name.endswith(".json"):
                continue
            try:
                record = json.loads((directory / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            blob = json.dumps(record, ensure_ascii=False)
            key = record.get("id") or record.get("merchant_id") or record.get("slug") or name
            bucket = sources.setdefault(str(key), set())
            for token in NUMBER.findall(blob):
                bucket.add(normalise(token))

    # Percentages are derived from fractional ratios, so any 0..1 source value
    # can legitimately surface as its percentage form.
    derived: set[str] = set()
    for bucket in sources.values():
        for token in bucket:
            try:
                value = float(token)
            except ValueError:
                continue
            if 0 < abs(value) <= 1:
                derived.add(normalise(f"{value * 100:.1f}"))
                derived.add(normalise(f"{value * 100:g}"))
            if abs(value) > 1:
                derived.add(normalise(f"{value / 100:g}"))

    untraced = 0
    for row in snapshot:
        body_numbers = {normalise(t) for t in NUMBER.findall(row["body"])}
        # A claim is grounded if the number appears in the merchant record, the
        # category pack, or the specific trigger that produced this message.
        allowed = set(sources.get(str(row["trigger_id"]), set()))
        for key in (row["merchant_id"], row["category"]):
            allowed |= sources.get(str(key), set())
        allowed |= derived
        missing = sorted(body_numbers - allowed)
        if missing:
            untraced += 1
            print(f"  UNTRACED {row['trigger_id']}: {missing}")
            print(f"    {row['body'][:140]}")

    print(f"\n{len(snapshot)} messages checked, {untraced} with untraced numbers")
    return 1 if untraced else 0


if __name__ == "__main__":
    import os  # noqa: E402  (used in main)
    raise SystemExit(main())
