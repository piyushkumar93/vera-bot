#!/usr/bin/env python3
"""Build the challenge's one-line-per-pair submission from the expanded data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.composition import compose
from engine.signals import consent_allows, text


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "dataset" / "expanded"
OUTPUT = ROOT / "submission.jsonl"
# The judge advances simulated time from 2026-04-26. Passing it explicitly keeps
# seasonal grounding deterministic instead of depending on wall-clock time.
SIMULATED_NOW = "2026-04-26T10:00:00Z"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_objects(directory: Path, key: str) -> dict[str, dict[str, Any]]:
    objects = {}
    for path in sorted(directory.glob("*.json")):
        value = load(path)
        objects[text(value.get(key), 180)] = value
    return objects


def suppressed_row(pair: dict[str, Any], trigger: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "test_id": pair["test_id"],
        "trigger_id": text(trigger.get("id"), 180),
        "merchant_id": text(pair.get("merchant_id"), 180),
        "customer_id": text(pair.get("customer_id"), 180) or None,
        "body": "",
        "cta": "none",
        "send_as": "merchant_on_behalf",
        "suppression_key": text(trigger.get("suppression_key"), 200)
        or f"trigger:{text(trigger.get('id'), 180) or 'unknown'}",
        "rationale": f"Suppressed: {reason}",
    }


def build_rows() -> list[dict[str, Any]]:
    categories = {path.stem: load(path) for path in sorted((DATA / "categories").glob("*.json"))}
    merchants = load_objects(DATA / "merchants", "merchant_id")
    customers = load_objects(DATA / "customers", "customer_id")
    triggers = load_objects(DATA / "triggers", "id")
    pairs = load(DATA / "test_pairs.json")["pairs"]

    rows = []
    for pair in pairs:
        trigger = triggers[pair["trigger_id"]]
        merchant = merchants[pair["merchant_id"]]
        customer_id = text(pair.get("customer_id"), 180)
        customer_scoped = text(trigger.get("scope"), 30).lower() == "customer"
        customer = customers.get(customer_id) if customer_scoped and customer_id else None
        if customer_scoped and (
            customer is None
            or text(customer.get("merchant_id"), 180) != pair["merchant_id"]
            or text(customer.get("state"), 40).lower() in {"churned", "opted_out", "do_not_contact"}
            or not consent_allows(customer, trigger)
        ):
            rows.append(suppressed_row(pair, trigger, "customer identity, state, channel, or matching consent scope does not authorize this trigger."))
            continue

        category = categories.get(text(merchant.get("category_slug"), 80), {})
        result = compose(category, merchant, trigger, customer, now=SIMULATED_NOW)
        # Match the published submission contract; runtime-only template metadata
        # stays in the /v1/tick response and is not duplicated in this artifact.
        rows.append({
            "test_id": pair["test_id"],
            "trigger_id": text(trigger.get("id"), 180),
            "merchant_id": text(merchant.get("merchant_id"), 180),
            "customer_id": text(customer.get("customer_id"), 180) if customer else None,
            "body": result["body"],
            "cta": result["cta"],
            "send_as": result["send_as"],
            "suppression_key": result["suppression_key"],
            "rationale": result["rationale"],
        })
    return rows


def main() -> None:
    rows = build_rows()
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
