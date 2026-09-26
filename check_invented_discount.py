"""Does the widened number guard still block a genuinely invented discount?

The guard now allows numbers that appear in the fact pack's approved performance
string, not just the draft. This checks the safety property still holds: a 40%
off offer that appears in neither must still be rejected.

Run:  python check_invented_discount.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.composition import compose  # noqa: E402
from engine.nim_polish import build_fact_pack, validate  # noqa: E402

CATEGORY = {"slug": "dentists", "name": "Dentists",
            "voice": {"tone": "clinical", "vocab_taboo": ["cheap"]}}
MERCHANT = {
    "id": "m1", "category_slug": "dentists",
    "identity": {"name": "Dr. Meera's Dental Clinic", "owner_first_name": "Meera",
                 "locality": "Koramangala", "languages": ["en"]},
    "performance": {"views": 1200, "calls": 40, "delta_7d": {"views_pct": -0.2}},
    "offers": [{"title": "Free scaling", "status": "active"}],
}
TRIGGER = {"id": "t1", "kind": "perf_dip", "urgency": 3,
           "payload": {"deadline_iso": "2026-12-15"}}

base = compose(CATEGORY, MERCHANT, TRIGGER, None, now="2026-04-26T10:30:00Z")
pack = build_fact_pack(base, CATEGORY, MERCHANT, TRIGGER, None)
draft = base["body"]
print("pack performance:", pack.get("performance"))
print("pack active_offer:", pack.get("active_offer"))
print("pack deadline:", pack.get("deadline"))
print("draft:", draft, "\n")

# A merchant whose active offer carries a real price, to prove a price that the
# fact pack supplies is usable in a rewrite.
priced_merchant = dict(MERCHANT)
priced_merchant["offers"] = [{"title": "Scaling @ 299", "status": "active"}]
priced_base = compose(CATEGORY, priced_merchant, TRIGGER, None, now="2026-04-26T10:30:00Z")
priced_pack = build_fact_pack(priced_base, CATEGORY, priced_merchant, TRIGGER, None)
priced_draft = priced_base["body"]
print("priced pack active_offer:", priced_pack.get("active_offer"), "\n")

cases = [
    ("invented 40% discount", draft[:-1] + " Save 40% off today?"),
    ("invented 99 price", draft[:-1] + " Book for Rs 99 today?"),
    ("changed the 20% to 80%", draft.replace("20%", "80%")),
    ("dropped a real number", draft.replace("20%", "")),
    ("real pack counts (1200/40)", draft[:-1] + " You have 1200 views and 40 calls. Next step?"),
    ("pack count as discount 40%", draft[:-1] + " Take 40% off this week?"),
    ("real pack deadline", draft[:-1] + " Please act by 2026-12-15. Next step?"),
    ("price not in pack", draft[:-1] + " Listed at 299 as before. Next step?"),
    ("invented far date", draft[:-1] + " Valid until 2027-01-01. Next step?"),
    ("legit reword", draft.replace("Want me to walk you through",
                                   "Would you like me to walk you through")),
]

# Bare counts and dates from the pack are allowed. A percentage or a price is
# only allowed when the pack actually states it in that form, so a discount
# cannot borrow a call count.
expect_accept = {
    "invented 40% discount": False,
    "invented 99 price": False,
    "changed the 20% to 80%": False,
    "dropped a real number": False,
    "real pack counts (1200/40)": True,
    "pack count as discount 40%": False,
    "real pack deadline": True,
    "price not in pack": False,
    "invented far date": False,
    "legit reword": True,
}

failures = 0
for label, candidate in cases:
    ok, reason = validate(candidate, draft, base, pack)
    want = expect_accept[label]
    status = "PASS" if ok == want else "FAIL"
    if status == "FAIL":
        failures += 1
    print(f"  [{status}] {label:<32} accepted={ok} (want {want})  ({reason})")

# Positive control: a price the fact pack really supplies must be usable.
priced_ok, priced_reason = validate(
    priced_draft[:-1] + " Our listed scaling session is 299. Next step?",
    priced_draft, priced_base, priced_pack)
status = "PASS" if priced_ok else "FAIL"
if status == "FAIL":
    failures += 1
print(f"  [{status}] {'pack-supplied price allowed':<32} accepted={priced_ok}  ({priced_reason})")

total = len(cases) + 1
print(f"\n{total - failures}/{total} behaved as required")
raise SystemExit(1 if failures else 0)
