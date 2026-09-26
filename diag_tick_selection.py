"""Which submission triggers survive the per-merchant one-action-per-tick rule? (diagnostic)"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bot import _priority  # noqa: E402

DATA = Path("dataset/expanded")
rows = [json.loads(line) for line in
        Path("submission.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
wanted = {r["trigger_id"] for r in rows}

triggers = {}
for name in os.listdir(DATA / "triggers"):
    record = json.loads((DATA / "triggers" / name).read_text(encoding="utf-8"))
    if record.get("id") in wanted:
        triggers[record["id"]] = record

NOW = "2026-04-26T10:30:00Z"

# Group the way the tick does, then keep the top-priority trigger per merchant.
by_merchant: dict[str, list] = {}
for tid, trigger in triggers.items():
    by_merchant.setdefault(trigger.get("merchant_id"), []).append(trigger)

kept, dropped = [], []
for merchant_id, group in by_merchant.items():
    ranked = sorted(group, key=lambda t: _priority(t, NOW), reverse=True)
    kept.append(ranked[0]["id"])
    dropped.extend(t["id"] for t in ranked[1:])

print(f"submission triggers : {len(rows)}")
print(f"merchants involved  : {len(by_merchant)}")
print(f"one action/merchant : {len(kept)}  <- what the judge can score")
print(f"suppressed by rule  : {len(dropped)}")
print("\nsuppressed (a higher-priority trigger for the same merchant won):")
for tid in sorted(dropped):
    print("   ", tid)
print("\nscored (highest priority per merchant):")
for tid in sorted(kept):
    print("   ", tid)
