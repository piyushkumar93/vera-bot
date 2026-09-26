"""Normalize untrusted context dictionaries and derive safe signal values."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def text(value: Any, limit: int = 400) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    result = str(value).strip()
    return result[:limit]


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percent(value: Any) -> str:
    n = number(value)
    if n is None:
        return ""
    # Trigger deltas are fractional ratios; tolerate explicit whole percentages.
    shown = n * 100 if abs(n) <= 1 else n
    rendered = f"{shown:.1f}".rstrip("0").rstrip(".")
    return rendered + "%"


def parse_time(value: Any) -> datetime | None:
    raw = text(value, 80)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def trigger_expired(trigger: dict[str, Any], now: Any) -> bool:
    expiry = parse_time(trigger.get("expires_at"))
    current = parse_time(now)
    return bool(expiry and current and expiry <= current)


def owner_name(merchant: dict[str, Any]) -> str:
    identity = mapping(merchant.get("identity"))
    owner = text(identity.get("owner_first_name"), 80)
    if owner:
        return owner
    name = text(identity.get("name"), 120)
    # Preserve professional title when it is part of the supplied name.
    return name.split("'s")[0] if "'s" in name else name


def category_slug(merchant: dict[str, Any], category: dict[str, Any]) -> str:
    return text(merchant.get("category_slug") or category.get("slug"), 80).lower()


# Titles implied by the category voice, so a dentist is addressed the way a
# colleague would address them instead of by bare first name.
PROFESSIONAL_TITLE_HINTS = ("dentist", "clinic", "dental", "doctor", "physio", "ayurveda", "homeo")


def salutation(merchant: dict[str, Any], category: dict[str, Any]) -> str:
    """Resolve how to address this merchant from identity plus category voice."""
    identity = mapping(merchant.get("identity"))
    owner = text(identity.get("owner_first_name"), 80)
    business = text(identity.get("name"), 120)
    if not owner:
        owner = business.split("'s")[0].strip() if "'s" in business else business
    if not owner:
        return "there"
    # A supplied honorific is authoritative; never double it up.
    if owner.lower().startswith(("dr.", "dr ", "prof.", "mr.", "mrs.", "ms.")):
        return owner
    slug = category_slug(merchant, category)
    voice = mapping(category.get("voice"))
    examples = [text(v, 60) for v in items(voice.get("salutation_examples"))]
    haystack = f"{slug} {business}".lower()
    if any(hint in haystack for hint in PROFESSIONAL_TITLE_HINTS) or any(
        "dr" in e.lower().split() for e in examples
    ):
        return f"Dr. {owner}"
    return owner


def performance_facts(merchant: dict[str, Any], category: dict[str, Any]) -> dict[str, Any]:
    """Read the merchant's own snapshot so messages can cite real numbers.

    Every value is copied from the supplied MerchantContext; absent fields are
    left out rather than estimated.
    """
    performance = mapping(merchant.get("performance"))
    delta = mapping(performance.get("delta_7d"))
    facts: dict[str, Any] = {}
    for field, key in (("views", "views"), ("calls", "calls"), ("directions", "directions"), ("leads", "leads")):
        value = performance.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            facts[key] = int(value)
    for field, key in (("views_pct", "views_pct"), ("calls_pct", "calls_pct"), ("ctr_pct", "ctr_pct")):
        value = delta.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            facts[key] = float(value)
    ctr = performance.get("ctr")
    if isinstance(ctr, (int, float)) and not isinstance(ctr, bool) and ctr > 0:
        facts["ctr_pct_value"] = round(float(ctr) * 100, 1)
    peer = mapping(category.get("peer_stats"))
    peer_ctr = peer.get("avg_ctr")
    if isinstance(peer_ctr, (int, float)) and not isinstance(peer_ctr, bool) and peer_ctr > 0:
        facts["peer_ctr_pct"] = round(float(peer_ctr) * 100, 1)
    return facts


def movement_sentence(merchant: dict[str, Any], direction: str) -> str:
    """Describe the merchant's own 7-day movement in plain, cited language."""
    facts = performance_facts(merchant, {})
    parts: list[str] = []
    for key, label in (("views_pct", "views"), ("calls_pct", "calls")):
        value = facts.get(key)
        if value is None or value == 0:
            continue
        if (value < 0) != (direction == "down"):
            continue
        parts.append(f"{label} {percent(abs(value))} {'lower' if direction == 'down' else 'higher'}")
    if not parts:
        return ""
    if len(parts) == 1:
        return f"7-day {parts[0]}"
    return f"7-day {parts[0]} and {parts[1]}"


def window_days(merchant: dict[str, Any]) -> int:
    value = mapping(merchant.get("performance")).get("window_days")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 30



def consent_allows(customer: dict[str, Any], trigger: dict[str, Any]) -> bool:
    """Require affirmative opt-in for the trigger's purpose; missing is denied."""
    preferences = mapping(customer.get("preferences"))
    consent = mapping(customer.get("consent"))
    if preferences.get("reminder_opt_in") is False:
        return False
    channel = text(preferences.get("channel"), 80).lower()
    if channel in {"none", "none_recorded", "", "unknown"}:
        return False
    if not text(consent.get("opted_in_at"), 80):
        return False
    scopes = {text(v, 80).lower() for v in items(consent.get("scope")) if text(v, 80)}
    if not scopes:
        return False
    kind = text(trigger.get("kind"), 80).lower()
    purpose = {
        "recall_due": {"recall_reminders"},
        "customer_lapsed_soft": {"recall_reminders", "promotional_offers"},
        "customer_lapsed_hard": {"winback_offers", "promotional_offers"},
        "winback_eligible": {"winback_offers", "promotional_offers"},
        "appointment_tomorrow": {"appointment_reminders"},
        "wedding_package_followup": {"bridal_package_followup", "appointment_reminders"},
        "trial_followup": {"appointment_reminders", "program_updates", "kids_program_updates"},
        "chronic_refill_due": {"refill_reminders"},
        "customer_recall_due": {"recall_reminders"},
    }.get(kind, set())
    return bool(purpose.intersection(scopes))

