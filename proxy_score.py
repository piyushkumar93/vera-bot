"""Deterministic proxy scorer for the five judge rubric dimensions.

The official judge (judge_simulator.py) requires an LLM provider key, so this
measures the same five dimensions from verifiable properties of each message
instead. It is a LOCAL STAND-IN, not the official score.

Dimensions mirror the judge system prompt in judge_simulator.LLMScorer:
  specificity         verifiable numbers, dates, prices, citations
  category_fit        category vocabulary and voice, no category taboos
  merchant_fit        merchant name/business, own metrics and own offers
  decision_quality    trigger facts used, no internal jargon, no contradiction
  engagement          one clear ask, low effort, lever present

Each check is a boolean or bounded count over the message and its contexts, so
repeated runs on identical inputs give identical scores.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

NUMBER_RE = re.compile(r"\d")
URL_RE = re.compile(r"https?://", re.I)
DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
    re.I,
)
CITATION_RE = re.compile(
    r"\b(source|jida|dci|ida|icmr|cdsco|gst council|circular|calendar|aggregate|bulletin|release)\b",
    re.I,
)
PRICE_RE = re.compile(r"[₹$]\s?\d|\d+\s?%")
MULTI_CTA_RE = re.compile(r"\b(reply (yes|no)|\bYES\b.*\bNO\b|1 for .*2 for)\b", re.I)
JARGON_RE = re.compile(r"\b(trigger|payload|context|jsonl|api|endpoint|tick|idempot|schema)\b", re.I)
HEDGE_RE = re.compile(r"\b(quotes no figure|no 7-day numbers|do not have|don't have|not in this context)\b", re.I)
PROMO_RE = re.compile(r"\b(guaranteed|100% safe|miracle|best in city|shred in 7 days|viral)\b", re.I)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_contexts() -> dict[str, Any]:
    """Load category/merchant/customer/trigger records keyed by 'kind:id'."""
    out: dict[str, Any] = {}
    for path in (ROOT / "dataset/categories").glob("*.json"):
        value = _read(path)
        out[f"category:{value['slug']}"] = value
    for name, key in (("dataset/expanded/merchants", "merchant_id"),
                      ("dataset/expanded/customers", "customer_id"),
                      ("dataset/expanded/triggers", "id")):
        directory = ROOT / name
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            value = _read(path)
            out[f"{path.parent.name.rstrip('s')}:{value[key]}"] = value
    for name, container, key in (("dataset/merchants_seed.json", "merchants", "merchant_id"),
                                 ("dataset/customers_seed.json", "customers", "customer_id"),
                                 ("dataset/triggers_seed.json", "triggers", "id")):
        path = ROOT / name
        if path.exists():
            for value in _read(path)[container]:
                out.setdefault(f"{container.rstrip('s')}:{value[key]}", value)
    return out


def _digits(text: str) -> int:
    return len(re.findall(r"\d+", text))


def score_specificity(body: str) -> tuple[int, str]:
    """Reward grounded, checkable facts: figures, prices, dates, citations."""
    if not body.strip():
        return 0, "no message emitted"
    score, notes = 2, []
    digits = _digits(body)
    if digits:
        score += min(3, digits)
        notes.append(f"{digits} numeric token(s)")
    if PRICE_RE.search(body):
        score += 2
        notes.append("price/percentage")
    if DATE_RE.search(body):
        score += 2
        notes.append("date reference")
    if CITATION_RE.search(body):
        score += 2
        notes.append("source citation")
    if URL_RE.search(body):
        score -= 4
        notes.append("URL present")
    if HEDGE_RE.search(body):
        score -= 1
        notes.append("declares a gap")
    return max(0, min(10, score)), ", ".join(notes)


def score_category_fit(body: str, category: dict[str, Any]) -> tuple[int, str]:
    """Reward category vocabulary; penalize this category's declared taboos."""
    if not body.strip():
        return 0, "no message emitted"
    slug = category.get("slug", "")
    voice = category.get("voice", {}) or {}
    lowered = body.lower()
    score, notes = 4, []
    allowed = [str(v).lower() for v in (voice.get("vocab_allowed") or []) if len(str(v)) > 3]
    hits = [v for v in allowed if v in lowered]
    if hits:
        score += min(3, len(hits))
        notes.append(f"category term(s): {', '.join(hits[:3])}")
    taboos = [str(v).lower() for v in (voice.get("vocab_taboo") or []) if str(v).strip()]
    violated = [t for t in taboos if t in lowered]
    if violated:
        score -= 4
        notes.append(f"taboo violation: {violated[0]}")
    if PROMO_RE.search(lowered):
        score -= 3
        notes.append("promotional taboo")
    if "dr. " in lowered and slug == "dentists":
        score += 2
        notes.append("clinical honorific")
    return max(0, min(10, score)), ", ".join(notes) or "neutral operator tone"


def score_merchant_fit(body: str, merchant: dict[str, Any]) -> tuple[int, str]:
    """Reward personalization to this merchant's identity, metrics, and offers."""
    if not body.strip():
        return 0, "no message emitted"
    identity = merchant.get("identity", {}) or {}
    performance = merchant.get("performance", {}) or {}
    lowered = body.lower()
    score, notes = 2, []
    owner = str(identity.get("owner_first_name") or "").lower()
    if owner and owner in lowered:
        score += 3
        notes.append("owner first name")
    business = str(identity.get("name") or "").lower()
    if business and business in lowered:
        score += 2
        notes.append("business name")
    locality = str(identity.get("locality") or "").lower()
    if locality and locality in lowered:
        score += 1
        notes.append("locality")
    for field in ("views", "calls", "directions", "leads"):
        value = performance.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 \
                and f"{int(value):,}" in body:
            score += 2
            notes.append(f"own {field} figure")
            break
    delta = performance.get("delta_7d") or {}
    for key, label in (("views_pct", "views"), ("calls_pct", "calls")):
        value = delta.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            if f"{abs(round(value * 100, 1)):g}%" in body:
                score += 2
                notes.append(f"own {label} 7-day delta")
                break
    for offer in merchant.get("offers") or []:
        if isinstance(offer, dict) and offer.get("status") == "active" \
                and str(offer.get("title", "")).lower() in lowered:
            score += 2
            notes.append("active offer")
            break
    return max(0, min(10, score)), ", ".join(notes) or "no merchant-specific anchor"


def _payload_echoes(body: str, payload: dict[str, Any]) -> int:
    """Count concrete payload values that actually appear in the message."""
    lowered = body.lower()
    echoes = 0
    for key, value in payload.items():
        if key in {"placeholder", "metric_or_topic"} or value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if str(value) in body:
                echoes += 1
        elif isinstance(value, str) and len(value) > 3:
            if value.lower() in lowered:
                echoes += 1
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and str(item.get("label", "")).lower() in lowered:
                    echoes += 1
                elif isinstance(item, str) and len(item) > 3 and item.lower() in lowered:
                    echoes += 1
    return echoes


def score_decision_quality(body: str, trigger: dict[str, Any]) -> tuple[int, str]:
    """Reward using the trigger's own facts; penalize jargon and hollow copy."""
    if not body.strip():
        return 0, "no message emitted"
    payload = trigger.get("payload", {}) or {}
    lowered = body.lower()
    score, notes = 3, []
    kind = str(trigger.get("kind") or "")
    if kind:
        notes.append(f"kind={kind}")
    echoes = _payload_echoes(body, payload)
    if echoes:
        score += min(4, echoes * 2)
        notes.append(f"{echoes} payload fact(s) used")
    if JARGON_RE.search(lowered):
        score -= 3
        notes.append("internal jargon leaked")
    if HEDGE_RE.search(body):
        score -= 1
        notes.append("declares a gap")
    if len(body.strip()) < 40:
        score -= 2
        notes.append("very short")
    return max(0, min(10, score)), ", ".join(notes) or "generic trigger handling"


def score_engagement(body: str, cta: str) -> tuple[int, str]:
    """Reward one clear, low-effort ask plus a concrete hook."""
    if not body.strip():
        return 0, "no message emitted"
    lowered = body.lower()
    score, notes = 2, []
    questions = body.count("?")
    if questions == 1:
        score += 3
        notes.append("single question")
    elif questions > 1:
        score -= 2
        notes.append(f"{questions} questions")
    if cta in {"open_ended", "binary_yes_no"}:
        score += 2
        notes.append(f"cta={cta}")
    if re.search(r"\b(want me to|shall i|want to|should i|do you want)\b", lowered):
        score += 2
        notes.append("concrete next step")
    if MULTI_CTA_RE.search(body):
        score -= 3
        notes.append("multiple CTAs")
    if re.search(r"\b(want to know|curious|full list)\b", lowered):
        score += 1
        notes.append("curiosity hook")
    if re.search(r"\b(no pressure|happy to|just say|reply yes|whenever)\b", lowered):
        score += 1
        notes.append("friction reducer")
    return max(0, min(10, score)), ", ".join(notes) or "no clear ask"


DIMENSIONS: tuple[str, ...] = ("specificity", "category_fit", "merchant_fit",
                               "decision_quality", "engagement")


def score_row(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Score one submitted row against its own contexts."""
    body = row.get("body", "")
    merchant = ctx.get(f"merchant:{row.get('merchant_id')}", {})
    category = ctx.get(f"category:{merchant.get('category_slug')}", {})
    trigger = ctx.get(f"trigger:{row.get('trigger_id')}", {})
    values, reasons = {}, {}
    checks = (
        ("specificity", lambda: score_specificity(body)),
        ("category_fit", lambda: score_category_fit(body, category)),
        ("merchant_fit", lambda: score_merchant_fit(body, merchant)),
        ("decision_quality", lambda: score_decision_quality(body, trigger)),
        ("engagement", lambda: score_engagement(body, row.get("cta", "none"))),
    )
    for name, check in checks:
        values[name], reasons[name] = check()
    return {"scores": values, "reasons": reasons, "total": sum(values.values())}


def load_rows() -> list[dict[str, Any]]:
    path = ROOT / "submission.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    ctx, rows = load_contexts(), load_rows()
    totals = {d: 0 for d in DIMENSIONS}
    emitted = 0
    header = f"{'id':5} {'kind':26} " + " ".join(f"{d[:9]:>9}" for d in DIMENSIONS) + "   total"
    print(header)
    print("-" * len(header))
    for row in rows:
        result = score_row(row, ctx)
        trigger = ctx.get(f"trigger:{row.get('trigger_id')}", {})
        sent = bool(row.get("body", "").strip())
        if sent:
            emitted += 1
            for d in DIMENSIONS:
                totals[d] += result["scores"][d]
        cells = " ".join(f"{result['scores'][d]:9}" for d in DIMENSIONS)
        mark = "" if sent else "   (suppressed: consent)"
        print(f"{row['test_id']:5} {str(trigger.get('kind'))[:26]:26} {cells}"
              f"   {result['total']:3}/50{mark}")
    print("-" * len(header))
    n = max(1, emitted)
    print(f"\nemitted {emitted}/{len(rows)} messages; averages over emitted only")
    for d in DIMENSIONS:
        print(f"  {d:16} {totals[d] / n:5.2f} / 10")
    overall = sum(totals.values()) / n
    print(f"  {'TOTAL':16} {overall:5.2f} / 50  ({overall * 2:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
