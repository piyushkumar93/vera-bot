"""Deterministic business decision layer: trigger -> fact -> action -> CTA.

composition.py decides *whether* a message may be sent and which template carries
it. This module decides *what the recommendation actually is*: given a trigger
and the supplied contexts, what happened, why it matters now, what the merchant
should do about it, and what to ask them.

Design constraints, all deliberate:

  * Deterministic. Same contexts in, same decision out. No model is consulted.
  * Grounded. Every fact is read from the supplied trigger/merchant/category
    payload. Nothing is invented: no fabricated discount, revenue, customer
    count, demand level, competitor behaviour, or capability. When a trigger
    carries no usable fact the decision says so instead of guessing.
  * Category-aware. The recommended action is chosen for the merchant's
    category, because "review your listing" is useless to a pharmacy and
    "check your schedule" is useless to a gym.
  * Wording-layer friendly. The output is a short, self-contained instruction
    that the optional Gemini pass can phrase naturally, and that the existing
    validator can still check against the deterministic draft.

The point is to move the *business judgement* in here, deterministically, so the
model is never asked to invent a decision it has no data to support.
"""

from __future__ import annotations

from typing import Any

from .signals import items, mapping, percent, text, window_days



# Category slugs that receive a specialised action, keyed by business family.
# Anything not listed falls back to DEFAULT_ACTION, so a new category still gets
# a usable decision rather than a dead end.
CATEGORY_ACTIONS: dict[str, dict[str, str]] = {
    "restaurants": {
        "visibility": "refresh the listing photos, hours and menu highlights that drive reservations and delivery discovery",
        "demand": "line up the dishes and offers on the listing to match this demand window",
        "review": "reply to the recent review themes and add a review ask to the next dine-in visit",
        "retention": "follow up with the customers who have gone quiet and get the next table booked",
    },
    "gyms": {
        "visibility": "refresh the listing and class timetable so nearby searchers can find current sessions",
        "demand": "match the class and programme slots on the listing to this demand window",
        "review": "reply to the recent review themes and ask for a review at the next session",
        "retention": "follow up with the members who have dropped off and get the next session booked",
    },
    "salons": {
        "visibility": "refresh the listing, services and timings so new bookings can find you",
        "demand": "feature the services matching this demand window in the listing and the next post",
        "review": "reply to the recent review themes and add a review ask to the next appointment",
        "retention": "follow up with the clients who have gone quiet and get the next appointment booked",
    },
    "pharmacies": {
        "visibility": "complete the listing details and availability so nearby searches reach the counter",
        "demand": "check stock and shelf readiness against this demand window before it peaks",
        "review": "work the recent review themes and keep availability and hours accurate",
        "retention": "follow up with the customers who have gone quiet and confirm their supply and repeat visit",
    },
    "dentists": {
        "visibility": "refresh the listing, clinic timings and services so new patient enquiries arrive",
        "demand": "align appointment capacity and listed services with this demand window",
        "review": "reply to the recent review themes and ask for a review after the next visit",
        "retention": "follow up with the customers who are due back and get the next visit in the diary",
    },
}



def _category_actions(slug: str) -> dict[str, str]:
    """Category action table, with the generic set as the fallback."""
    merged = dict(DEFAULT_ACTION)
    merged.update(CATEGORY_ACTIONS.get((slug or "").lower(), {}))
    return merged


def trigger_family(kind: str) -> str:
    """Map a trigger kind onto the business family that drives the action.

    Only the family is inferred here; the actual fact and the recommended
    action still come from the payload and the merchant context. An
    unrecognised kind returns "" so the caller can ask rather than guess.
    """
    k = (kind or "").lower()
    if k in {"perf_dip", "seasonal_perf_dip", "gbp_unverified", "competitor_opened",
             "cde_opportunity"}:
        return "visibility"
    if k in {"perf_spike", "festival_upcoming", "category_seasonal", "ipl_match_today",
             "research_digest", "research_digest_release", "regulation_change",
             "compliance_alert", "compliance_change", "supply_alert", "recall_alert",
             "renewal_due", "curious_ask_due"}:
        return "demand"
    if k in {"review_theme_emerged", "milestone_reached"}:
        return "review"
    # Customer-lifecycle triggers are a retention problem first: the merchant
    # already has the customer, so the action is a follow-up, not more
    # visibility. Recommending a listing refresh here would be a category and
    # intent mismatch.
    if k in {"recall_due", "customer_recall_due", "appointment_tomorrow",
             "trial_followup", "wedding_package_followup", "chronic_refill_due",
             "customer_lapsed_soft", "customer_lapsed_hard", "winback_eligible",
             "unplanned_slot_open", "dormant_with_vera"}:
        return "retention"
    return ""


def merchant_angles(merchant: dict[str, Any], category: dict[str, Any]) -> dict[str, str]:
    """Facts about this merchant that a recommendation can legitimately rest on.

    Every value is read from supplied context. Missing fields are simply absent
    from the result, so the caller never has to invent a stand-in.
    """
    out: dict[str, str] = {}
    performance = mapping(merchant.get("performance"))
    identity = mapping(merchant.get("identity"))

    window = window_days(merchant)
    for key in ("views", "calls", "directions"):
        value = performance.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            out[f"{key}_{window}d"] = f"{int(value):,} {key}"

    aggregate = mapping(merchant.get("customer_aggregate"))
    lapsed = aggregate.get("lapsed_90d_plus")
    if isinstance(lapsed, (int, float)) and not isinstance(lapsed, bool) and lapsed > 0:
        out["lapsed_90d_plus"] = f"{int(lapsed):,}"
    retention = aggregate.get("retention_3mo_pct")
    if isinstance(retention, (int, float)) and not isinstance(retention, bool) and 0 < retention <= 1:
        out["retention_3mo_pct"] = percent(retention)

    locality = text(identity.get("locality"), 80)
    if locality:
        out["locality"] = locality
    return out


def strongest_metric(merchant: dict[str, Any]) -> str:
    """The metric with the largest 7-day movement, as a grounded talking point.

    Used only when a trigger did not name its own metric, so the message can
    still say something concrete instead of "your numbers moved".
    """
    delta = mapping(mapping(merchant.get("performance")).get("delta_7d"))
    best_label, best_value = "", 0.0
    for key, label in (("views_pct", "views"), ("calls_pct", "calls"), ("ctr_pct", "ctr")):
        value = delta.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value == 0:
            continue
        if abs(value) > abs(best_value):
            best_label, best_value = label, float(value)
    if not best_label:
        return ""
    direction = "down" if best_value < 0 else "up"
    return f"{best_label} {percent(abs(best_value))} {direction} over 7 days"


def review_note(merchant: dict[str, Any]) -> str:
    """The most frequent recent review theme, if the context supplies one."""
    themes = [mapping(t) for t in items(merchant.get("review_themes"))]
    named = [t for t in themes if text(t.get("theme"), 60)]
    if not named:
        return ""
    best = max(named, key=lambda t: t.get("occurrences_30d") or 0)
    theme = text(best.get("theme"), 60).replace("_", " ")
    count = best.get("occurrences_30d")
    suffix = ""
    if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
        suffix = f" ({int(count)} mentions in 30 days)"
    return f"the most-mentioned theme is {theme}{suffix}"


def decide(kind: str, payload: dict[str, Any], category: dict[str, Any],
           merchant: dict[str, Any]) -> dict[str, Any]:
    """Produce the structured business decision for one trigger.

    Returns fact / why_now / recommended_action / cta, each a plain string. An
    empty string means "the supplied context does not support this", which the
    caller must treat as permission to ask rather than to assert.
    """
    slug = category_slug_of(merchant, category)
    actions = _category_actions(slug)
    family = trigger_family(kind)
    angles = merchant_angles(merchant, category)

    # --- fact: only what the trigger itself supplies ------------------------
    fact = ""
    for key in ("title", "topic", "event", "change", "summary"):
        value = text(payload.get(key), 180)
        if value:
            fact = value
            break

    # --- why now: this merchant's own grounded position ---------------------
    why_now = ""
    if angles.get("lapsed_90d_plus"):
        why_now = f"you have {angles['lapsed_90d_plus']} customers lapsed 90 days or more"
        if angles.get("retention_3mo_pct"):
            why_now += f", with 3-month retention at {angles['retention_3mo_pct']}"
    else:
        metric = strongest_metric(merchant)
        if metric:
            why_now = f"your {metric}"
        elif angles.get(f"views_{window_days(merchant)}d"):
            why_now = f"your {window_days(merchant)}-day numbers are {angles[f'views_{window_days(merchant)}d']}"

    note = review_note(merchant)
    if note and family == "review":
        why_now = f"{why_now}, and {note}" if why_now else note

    # --- recommended action: category-specific and concrete -----------------
    recommended = actions.get(family) or actions["visibility"]

    # --- CTA: one low-friction question about that specific action ----------
    cta = "Want me to walk you through exactly what to change?"

    return {
        "fact": fact,
        "why_now": why_now,
        "recommended_action": recommended,
        "cta": cta,
        "category_angle": recommended,
        "merchant_angle": why_now,
        "family": family,
        "category": slug,
    }




def category_slug_of(merchant: dict[str, Any], category: dict[str, Any]) -> str:
    return (text(merchant.get("category_slug"), 80)
            or text(category.get("slug"), 80)).lower()

DEFAULT_ACTION = {
    "visibility": "review and refresh the listing details that drive local discovery",
    "demand": "review what is currently listed and line it up with this demand window",
    "review": "review the recent customer feedback themes on the listing",
    "retention": "follow up with the customers who are due back and get the next visit booked",
}
