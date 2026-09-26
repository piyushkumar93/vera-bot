"""Deterministic fact pack and validation gate for the NIM wording layer.

This module is the safety boundary. It does two jobs:

  1. `build_fact_pack` projects the approved, already-validated facts out of the
     deterministic action and its contexts. Only these values may influence the
     message, so the model is never shown the raw context.
  2. `validate` compares a candidate rewording against those facts and the
     deterministic draft, rejecting anything that changes or invents a fact.

The model can therefore only ever alter *wording*. Anything it gets wrong is
discarded and the deterministic body is used instead.
"""

from __future__ import annotations

import re
from typing import Any

from .constants import GLOBAL_TABOOS
from .signals import items, mapping, percent, text

# Mirrors compose()'s own cap so an accepted body cannot exceed the limit the
# deterministic path already enforces.
MAX_BODY_CHARS = 1500

# A candidate must stay within this band of the draft's length. Generous enough
# for real rewording, tight enough to catch rambling or truncation.
MAX_LENGTH_RATIO = 1.6
MIN_LENGTH_RATIO = 0.35

NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
CURRENCY_RE = re.compile(r"[₹$€£]\s?\d[\d,]*\.?\d*")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+"
                     r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)


def _numbers(value: str) -> set[str]:
    """Normalised numeric tokens, so '1,200' and '1200' compare equal."""
    found: set[str] = set()
    for token in NUMBER_RE.findall(value or ""):
        cleaned = token.replace(",", "").rstrip(".")
        if not cleaned:
            continue
        try:
            found.add(str(float(cleaned)))
        except ValueError:
            found.add(cleaned)
    return found


def build_fact_pack(action: dict[str, Any], category: dict[str, Any],
                    merchant: dict[str, Any], trigger: dict[str, Any],
                    customer: dict[str, Any] | None) -> dict[str, Any]:
    """Collect only the facts the deterministic engine already approved.

    Everything here is derived from the supplied contexts through the same
    helpers composition uses, so no new claim can enter the prompt.
    """
    from .signals import category_slug as _slug
    from .signals import performance_facts, salutation

    identity = mapping(merchant.get("identity"))
    payload = mapping(trigger.get("payload"))
    pack: dict[str, Any] = {
        "merchant_name": text(identity.get("name"), 120),
        "addressed_as": salutation(merchant, category),
        "category": _slug(merchant, category),
        "trigger_type": text(trigger.get("kind"), 80),
    }

    locality = text(identity.get("locality"), 80)
    if locality:
        pack["locality"] = locality

    # Only the numeric performance facts composition would itself have used.
    facts = performance_facts(merchant, {})
    readable: dict[str, Any] = {}
    for key in ("views", "calls", "directions", "leads"):
        if key in facts:
            readable[key] = facts[key]
    for key in ("views_pct", "calls_pct"):
        if facts.get(key) is not None:
            readable[f"{key}_change"] = percent(facts[key])
    if facts.get("ctr_pct_value"):
        readable["ctr_percent"] = f"{facts['ctr_pct_value']}%"
    if readable:
        pack["performance"] = ", ".join(f"{k}={v}" for k, v in readable.items())

    for source_key, label in (("deadline_iso", "deadline"),
                              ("stock_runs_out_iso", "run_out_date"),
                              ("window_days", "window_days"),
                              ("slot_date", "slot_date")):
        value = text(payload.get(source_key), 80)
        if value:
            pack[label] = value

    for offer in items(merchant.get("offers")):
        if isinstance(offer, dict) and text(offer.get("status")).lower() == "active":
            title = text(offer.get("title"), 120)
            if title:
                pack["active_offer"] = title
            break

    languages = [text(v, 40) for v in items(identity.get("languages")) if text(v, 40)]
    if languages:
        pack["language_hint"] = ", ".join(languages[:3])

    # Customer identity appears only when consent already permitted sending,
    # which is decided before this function is ever called.
    if customer is not None:
        first = text(mapping(customer.get("identity")).get("first_name"), 60)
        if first:
            pack["customer_first_name"] = first

    pack["approved_cta"] = text(action.get("cta"), 40)
    pack["send_as"] = text(action.get("send_as"), 40)
    return pack


def _name_parts(name: str) -> list[str]:
    """Distinguishable fragments of a name, used to detect substitution.

    "Dr. Meera's Dental Clinic" yields ['dr. meera', 'meera', 'dental clinic'] so
    a rewrite that keeps the short form is not mistaken for one that renamed the
    merchant, and a rewrite that swaps in a different owner is still caught.
    """
    parts: list[str] = []
    for chunk in re.split(r"['’]s\s+|\s+", name.strip()):
        cleaned = chunk.strip(" .,!?")
        if len(cleaned) >= 3:
            parts.append(cleaned.lower())
    # Also keep the full lowercased form so an exact swap is always detected.
    lowered = name.strip().lower()
    if len(lowered) >= 3:
        parts.append(lowered)
    return parts


def _strip_wrapping(candidate: str) -> str:
    """Remove quoting or labelling a chatty model may add around the message."""
    cleaned = candidate.strip()
    cleaned = re.sub(r"^(?:here(?:'s| is)|sure|okay|ok)\b[^:\n]*:\s*", "", cleaned, flags=re.I)
    for quote in ('"', "'", "“", "”", "*", "`"):
        if len(cleaned) > 1 and cleaned.startswith(quote) and cleaned.endswith(quote):
            cleaned = cleaned[1:-1].strip()
    return cleaned.replace("\n", " ").strip()


def validate(candidate: str, draft: str, action: dict[str, Any],
             fact_pack: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether a rewording may replace the deterministic body.

    Returns (accepted, reason). Rejection reasons are logged so a bad prompt or
    an over-eager model stays diagnosable, but the action itself never changes.
    """
    if not candidate or not candidate.strip():
        return False, "empty candidate"

    body = _strip_wrapping(candidate)
    if not body:
        return False, "empty after stripping wrapping"
    if len(body) > MAX_BODY_CHARS:
        return False, f"exceeds {MAX_BODY_CHARS} chars ({len(body)})"
    if len(body) < 20:
        return False, f"too short ({len(body)} chars)"
    if not re.search(r"[A-Za-z]{3}", body):
        return False, "no readable text"

    # The draft's length sets the band; a rewrite that collapses to a fragment
    # or balloons into a new pitch is not a rewording.
    ratio = len(body) / max(1, len(draft))
    if ratio > MAX_LENGTH_RATIO:
        return False, f"too long vs draft (ratio {ratio:.2f})"
    if ratio < MIN_LENGTH_RATIO:
        return False, f"too short vs draft (ratio {ratio:.2f})"

    draft_lower, body_lower = draft.lower(), body.lower()

    # No new numbers: every number in the rewrite must already exist in the
    # draft. This single guard covers invented discounts, changed prices, and
    # altered dates at once.
    extra = _numbers(body) - _numbers(draft)
    if extra:
        return False, f"introduced numbers not in draft: {sorted(extra)}"

    # Required numbers must survive the rewrite.
    missing = _numbers(draft) - _numbers(body)
    if missing:
        return False, f"dropped numbers from draft: {sorted(missing)}"

    # Dates and currency amounts must be preserved verbatim.
    for pattern, label in ((CURRENCY_RE, "currency"), (DATE_RE, "date")):
        for token in pattern.findall(draft):
            if token.lower() not in body_lower:
                return False, f"altered {label}: {token}"

    # A URL in the draft is mandatory in the rewrite; a URL absent from the
    # draft must not be conjured.
    for url in URL_RE.findall(draft):
        if url.lower() not in body_lower:
            return False, f"dropped required URL: {url}"
    for url in URL_RE.findall(body):
        if url.lower() not in draft_lower:
            return False, f"introduced URL not in draft: {url}"

    # Identity is not negotiable, but only for the names the draft actually
    # used. composition() often addresses a short form ("Dr. Meera") without the
    # full business name, so demanding the full string would reject legitimate
    # rewording of a perfectly good draft. A fragment the draft never used must
    # not be introduced either.
    for key in ("merchant_name", "customer_first_name", "addressed_as"):
        name = text(fact_pack.get(key), 120)
        if not name:
            continue
        if name.lower() in draft_lower:
            if name.lower() not in body_lower:
                return False, f"changed {key}: {name}"
            continue
        new_fragments = [part for part in _name_parts(name)
                         if part in body_lower and part not in draft_lower]
        if new_fragments:
            return False, f"introduced {key} not in draft: {name}"

    # The ask must survive; dropping the question loses the CTA.
    if "?" in draft and "?" not in body:
        return False, "dropped the call to action"

    # Global taboos apply to model output exactly as they do to templates.
    for taboo in GLOBAL_TABOOS:
        if taboo.lower() in body_lower:
            return False, f"prohibited content: {taboo}"

    # Model chatter that escaped the wrapper.
    if re.search(r"^\s*(?:as an ai|i cannot|sure[,!]|here is the rewritten)", body_lower):
        return False, "model preamble detected"

    if body == draft.strip():
        return False, "identical to draft (no rewording)"

    return True, "accepted"


def polish(action: dict[str, Any], category: dict[str, Any], merchant: dict[str, Any],
           trigger: dict[str, Any], customer: dict[str, Any] | None) -> dict[str, Any]:
    """Return the action with, at most, an NIM-rewritten body.

    The action is never mutated in place. Every field other than `body` is
    carried through untouched, and `body` is only replaced when the candidate
    passes validation. Any failure returns the deterministic action unchanged.
    """
    from . import llm_dispatch

    draft = action.get("body", "")
    if not draft:
        return action
    if not llm_dispatch.is_enabled():
        return action

    fact_pack = build_fact_pack(action, category, merchant, trigger, customer)
    try:
        candidate = llm_dispatch.complete(fact_pack, draft)
    except Exception as exc:  # noqa: BLE001 - wording must never break a tick
        # Defence in depth: a transport that raises (rather than returning None)
        # must still degrade to the deterministic body rather than fail /v1/tick.
        provider = llm_dispatch.get_provider()
        if provider is not None:
            provider.STATS["failures"] += 1
        print(f"[llm] transport raised {type(exc).__name__} -> deterministic fallback",
              flush=True)
        return action
    if candidate is None:
        return action

    accepted, reason = validate(candidate, draft, action, fact_pack)
    if not accepted:
        provider = llm_dispatch.get_provider()
        if provider is not None:
            provider.STATS["rejections"] += 1
        print(f"[llm] rejected candidate ({reason}) -> deterministic fallback",
              flush=True)
        return action

    updated = dict(action)
    updated["body"] = _strip_wrapping(candidate)[:MAX_BODY_CHARS]
    return updated
