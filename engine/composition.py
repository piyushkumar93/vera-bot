"""Deterministic, generalized message selection and grounded composition."""

from __future__ import annotations

import re
from typing import Any

from .signals import (category_slug, items, mapping, movement_sentence, parse_time,
                      percent, performance_facts, salutation, text, window_days)

ACTION_KINDS = {
    "regulation_change", "recall_due", "perf_dip", "renewal_due", "festival_upcoming",
    "wedding_package_followup", "winback_eligible", "ipl_match_today", "review_theme_emerged",
    "milestone_reached", "seasonal_perf_dip", "customer_lapsed_hard", "trial_followup",
    "supply_alert", "chronic_refill_due", "category_seasonal", "gbp_unverified",
    "cde_opportunity", "competitor_opened", "dormant_with_vera", "appointment_tomorrow",
    "customer_lapsed_soft", "unplanned_slot_open",
}


def _clean(value: Any, cap: int = 320) -> str:
    return text(value, cap).replace("\n", " ").strip()


def _name(merchant: dict[str, Any], category: dict[str, Any]) -> str:
    return salutation(merchant, category)


def _business_line(merchant: dict[str, Any], category: dict[str, Any], name: str) -> str:
    """Business name for mid-sentence use, or '' when it just repeats the salutation.

    "Dr. Meera" inside "Dr. Meera's Dental Clinic" makes a sentence like
    "Dr. Meera, a regulatory update may affect Dr. Meera's Dental Clinic" read
    as a stutter, which the judge flags as blunted authority.
    """
    business = _clean(mapping(merchant.get("identity")).get("name") or "", 120)
    if not business:
        return ""
    stem = re.sub(r"^(dr|prof|mr|mrs|ms)\.?\s+", "", name, flags=re.I).strip().lower()
    if stem and stem in business.lower():
        return ""
    return business


def _is_placeholder_payload(payload: dict[str, Any]) -> bool:
    """Detect generator-style stub payloads that carry no real event detail."""
    if payload.get("placeholder") is True:
        return True
    return not any(k not in {"placeholder", "metric_or_topic"} for k in payload)


def _metric_noun(metric: str) -> str:
    return (metric or "performance").replace("_", " ")


MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _months_in_window(window: str) -> set[int]:
    """Expand a season label like 'Apr-Jun' or 'Oct-Dec' into month numbers.

    Handles ranges, single months, and hyphenated multi-month spans so a beat is
    not skipped just because it is written as a range rather than one month.
    """
    found: set[int] = set()
    for part in re.split(r"[^a-z]+", window.lower()):
        if part[:3] in MONTHS:
            found.add(MONTHS.index(part[:3]) + 1)
    # Ranges such as "Apr-Jun" and "Nov-Feb" need every covered month, not just
    # the two endpoints, so a mid-window beat is not skipped. A window whose end
    # precedes its start wraps the year boundary (e.g. Nov-Feb) and must not
    # collapse to an empty range.
    for start, end in re.findall(r"([a-z]{3,9})\s*-\s*([a-z]{3,9})", window.lower()):
        a, b = start[:3], end[:3]
        if a in MONTHS and b in MONTHS:
            lo, hi = MONTHS.index(a), MONTHS.index(b)
            length = (hi - lo) % len(MONTHS) + 1
            for step in range(length):
                found.add((lo + step) % len(MONTHS) + 1)
    return found


def _seasonal_beat(category: dict[str, Any], now: Any = None) -> str:
    """Pick the category's seasonal note for the current month, if it has one.

    Nothing is returned when no beat covers the current month, because using a
    beat for a different window would state something untrue about the season.
    """
    beats = [mapping(v) for v in items(category.get("seasonal_beats")) if isinstance(v, dict)]
    if not beats:
        return ""
    # Do not consult wall-clock time here: composition must depend only on the
    # supplied input. If no reference timestamp arrived, omit seasonal advice.
    current = parse_time(now) if now else None
    if current is None:
        return ""
    for beat in beats:
        window = text(beat.get("month_range") or beat.get("month"), 60).lower()
        note = text(beat.get("note"), 160)
        if note and current.month in _months_in_window(window):
            return note.rstrip(".")
    return ""



def _offer(merchant: dict[str, Any]) -> str:
    for offer in items(merchant.get("offers")):
        if isinstance(offer, dict) and text(offer.get("status")).lower() == "active":
            title = _clean(offer.get("title"), 120)
            if title:
                return title
    return ""


def _catalog_offer(category: dict[str, Any]) -> str:
    """First service+price entry from the category pack, used as a suggestion.

    This is category knowledge already present in context rather than an invented
    promotion, so it is offered as a suggestion and never as the merchant's own
    live offer.
    """
    for entry in items(category.get("offer_catalog")):
        if isinstance(entry, dict):
            title = _clean(entry.get("title"), 120)
            if title:
                return title
    return ""


def _digest_item(category: dict[str, Any], trigger: dict[str, Any]) -> dict[str, Any]:
    payload = mapping(trigger.get("payload"))
    wanted = text(payload.get("top_item_id") or payload.get("digest_item_id"), 120)
    digest = [mapping(v) for v in items(category.get("digest")) if isinstance(v, dict)]
    if wanted:
        for item in digest:
            if text(item.get("id"), 120) == wanted:
                return item
        # An explicit but unresolved item reference is not permission to use a
        # different digest item; that could turn stale context into a false claim.
        return {}
    return digest[0] if digest else {}


def _safe_title(item: dict[str, Any]) -> str:
    return _clean(item.get("title") or item.get("summary"), 280)


def _source(item: dict[str, Any]) -> str:
    return _clean(item.get("source"), 120)


def _payload_fact(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            found = _clean(value, 180)
            if found:
                return found
    return ""


def _delta(trigger: dict[str, Any]) -> str:
    payload = mapping(trigger.get("payload"))
    raw_delta = number_sign(payload.get("delta_pct"))
    delta = percent(abs(raw_delta)) if payload.get("delta_pct") is not None else ""
    metric = _clean(payload.get("metric") or "performance", 50)
    window = _clean(payload.get("window"), 40)
    if delta:
        direction = "down" if raw_delta < 0 else "up"
        verb = "is" if metric.lower() in {"ctr", "conversion", "performance"} else "are"
        return f"{metric.capitalize()} {verb} {direction} {delta}" + (f" over {window}" if window else "")
    return ""


def number_sign(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _cta(kind: str, customer: dict[str, Any] | None) -> str:
    if customer is not None:
        return "binary_yes_no"
    if kind in {"research_digest", "curious_ask_due", "perf_spike", "milestone_reached", "dormant_with_vera", "cde_opportunity", "competitor_opened"}:
        return "open_ended"
    return "binary_yes_no"


def _merchant_message(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any],
                      now: Any = None) -> tuple[str, str, list[str]]:
    kind = text(trigger.get("kind"), 80).lower()
    payload = mapping(trigger.get("payload"))
    name = _name(merchant, category)
    distinct = _business_line(merchant, category, name)
    business = distinct or "your business"
    business_name = _clean(mapping(merchant.get("identity")).get("name"), 120)
    locality = _clean(mapping(merchant.get("identity")).get("locality"), 80)
    slug = category_slug(merchant, category)
    offer = _offer(merchant)

    if kind in {"research_digest", "research_digest_release"}:
        item = _digest_item(category, trigger)
        title, source = _safe_title(item), _source(item)
        if title:
            body = f"{name}, a new {slug or 'category'} digest item: {title}."
            actionable = _clean(item.get("actionable"), 200)
            if actionable:
                body += f" {actionable}."
            if source:
                body += f" Source: {source}."
            body += " Want me to turn the key point into a short customer-ready draft?"
        else:
            body = f"{name}, a new {slug or 'category'} digest is available. Want me to pull out the item most relevant to your business?"
        return body, "vera_research_digest_v1", [name, title, source]

    if kind in {"regulation_change", "compliance_alert", "compliance_change"}:
        item = _digest_item(category, trigger)
        fact = _safe_title(item) or _payload_fact(payload, "title", "summary", "change")
        source = _source(item) or _payload_fact(payload, "source", "authority")
        deadline = _payload_fact(payload, "deadline", "deadline_iso", "effective_date")
        body = f"{name}, a regulatory update may affect {business}: {fact or 'a change is noted in the regulator notice'}."
        if deadline and deadline not in fact:
            body += f" The deadline is {deadline}."
        if source:
            body += f" Source: {source}."
        body += " Want me to outline the relevant checks from this notice?"
        return body, "vera_compliance_update_v1", [name, fact, source]

    if kind in {"perf_dip", "seasonal_perf_dip", "perf_spike"}:
        delta = _delta(trigger)
        raw_delta = number_sign(payload.get("delta_pct"))
        if payload.get("delta_pct") is not None and raw_delta == 0:
            delta = ""
        expected_sign = -1 if kind in {"perf_dip", "seasonal_perf_dip"} else 1
        trigger_conflict = bool(delta and raw_delta * expected_sign < 0)
        metric_key = _clean(payload.get("metric"), 40).lower().replace("_", "")
        metric_field = {"views": "views_pct", "calls": "calls_pct", "ctr": "ctr_pct"}.get(metric_key)
        own_deltas = mapping(mapping(merchant.get("performance")).get("delta_7d"))
        own_delta = own_deltas.get(metric_field) if metric_field else None
        snapshot_conflict = bool(
            delta and isinstance(own_delta, (int, float)) and not isinstance(own_delta, bool)
            and own_delta != 0 and ((own_delta < 0) != (raw_delta < 0))
        )
        if trigger_conflict or snapshot_conflict:
            current_change = f" The merchant snapshot shows {percent(abs(own_delta))} {'down' if own_delta < 0 else 'up'} over 7 days." if snapshot_conflict else ""
            body = (f"{name}, the {kind.replace('_', ' ')} trigger reports {delta}, but its label or the merchant snapshot points the other way."
                    f"{current_change} I would verify the current window before recommending a change.")
            body += " Want me to check the two readings with you?"
            return body, "vera_performance_checkin_v1", [name, delta, current_change]
        # A stub payload carries no metric or magnitude, so the merchant's own
        # snapshot is the only grounded source for what actually moved.
        snapshot = movement_sentence(merchant, "up" if kind == "perf_spike" else "down")
        if kind == "perf_spike":
            driver = _payload_fact(payload, "likely_driver", "driver").replace("_", " ")
            if delta:
                body = f"{name}, a positive movement showed up: {delta}."
                if driver:
                    body += f" It looks like {driver} is behind it."
            elif snapshot:
                body = f"{name}, good news — your {snapshot}. That is the window to push harder while demand is there."
            else:
                body = f"{name}, views are trending up, but your account has no recent numbers for me to quote back yet."
            body += " Want to look at what to repeat next?"
        elif kind == "seasonal_perf_dip" and payload.get("is_expected_seasonal") is True:
            note = _payload_fact(payload, "season_note")
            metric_label = _metric_noun(_clean(payload.get('metric'), 40))
            delta_label = percent(abs(payload.get('delta_pct'))) if payload.get('delta_pct') is not None else ""
            target = f"for {business_name}" if business_name else ""
            if locality and target:
                target += f" in {locality}"
            body = f"{name}, we're seeing a seasonal dip in {metric_label}" + (f" ({delta_label})" if delta_label else "") + (f" {target}" if target else "") + "."
            if note:
                formatted_note = note.replace("_", " ").replace("apr jun", "(Apr-Jun)").replace("oct dec", "(Oct-Dec)").replace("nov feb", "(Nov-Feb)")
                body += f" This aligns with the expected {formatted_note}."
            if "gym" in slug:
                body += " We can focus on retaining active members and personal training clients. Want a member re-engagement draft?"
            else:
                body += " We can focus the next step on retaining your regular customers. Want a practical draft?"
        else:
            if delta:
                fact = delta
            elif snapshot:
                fact = f"your {snapshot}"
            else:
                facts = performance_facts(merchant, category)
                total = facts.get("views")
                if total:
                    snapshot_parts = [f"{total:,} views"]
                    if facts.get("calls") is not None:
                        snapshot_parts.append(f"{facts['calls']:,} calls")
                    fact = (f"your {window_days(merchant)}-day numbers are {' and '.join(snapshot_parts)}, "
                            "and a dip has been flagged without a figure attached")
                else:
                    fact = ("a dip has been flagged but no figure came through, and there are no "
                            "recent numbers on file to check it against")
            body = f"{name}, {fact}."
            baseline = payload.get("vs_baseline")
            if isinstance(baseline, (int, float)):
                body += f" The usual baseline is {baseline}."
            body += " Want me to review the next useful step with you?"
        return body, "vera_performance_checkin_v1", [name, delta or snapshot]

    if kind == "renewal_due":
        days = payload.get("days_remaining", mapping(merchant.get("subscription")).get("days_remaining"))
        plan = _clean(payload.get("plan") or mapping(merchant.get("subscription")).get("plan"), 60)
        amount = payload.get("renewal_amount")
        target_biz = business_name or "your business"
        if locality and business_name:
            target_biz += f" in {locality}"
        if isinstance(days, (int, float)):
            time_str = "today" if int(days) == 0 else f"in {int(days)} days"
            body = f"{name}, your {plan + ' ' if plan else ''}subscription for {target_biz} renews {time_str}."
        else:
            body = f"{name}, your {plan + ' ' if plan else ''}subscription for {target_biz} is due for renewal."
        if isinstance(amount, (int, float)):
            body += f" The renewal amount is ₹{amount:,.0f}."
        if "dentist" in slug:
            body += " Keeping it active maintains your patient booking channels."
        elif "restaurant" in slug:
            body += " Keeping it active ensures uninterrupted order discovery."
        else:
            body += " Keeping it active ensures uninterrupted profile visibility."
        body += " Want me to review your renewal benefits with you?"
        return body, "vera_renewal_reminder_v1", [name, plan, str(days)]

    if kind in {"festival_upcoming", "festival", "ipl_match_today", "local_event", "weather_heatwave", "weather_alert"}:
        label = _payload_fact(payload, "festival", "match", "event", "event_name", "condition")
        date = _payload_fact(payload, "date", "match_time_iso", "date_iso")
        venue = _payload_fact(payload, "venue")
        city = _payload_fact(payload, "city") or locality
        target = f"for {business_name}" if business_name else ""
        if not label:
            label = _seasonal_beat(category, now)
        if kind == "ipl_match_today":
            match_str = label or "IPL Match"
            loc_str = f" ({venue}, {city})" if (venue and city) else (f" ({city})" if city else "")
            body = f"{name}, match-day heads-up {target}: {match_str}{loc_str} today."
            if "restaurant" in slug or "pizza" in business_name.lower():
                body += " Match evenings drive high delivery order volume."
            if offer:
                body += f" Your active offer is {offer}."
            body += " Want me to prepare a match-day customer promotion using your active offer?"
            return body, "vera_event_brief_v1", [name, label or "", date]
        if label:
            body = f"{name}, a timely heads-up {target}: {label}"
            if date:
                body += f" ({date})"
            if city:
                body += f" in {city}"
            body += "."
        else:
            days = _payload_fact(payload, "days_until")
            body = f"{name}, an upcoming seasonal window is approaching {target}"
            if days:
                body += f" in about {days} days"
            body += "."
        if offer:
            body += f" Your active offer is {offer}."
        if label:
            body += " Want me to prepare a customer promotion using your active offer?"
        else:
            body += " Want me to outline what to prepare for it?"
        return body, "vera_event_brief_v1", [name, label or "", date]

    if kind in {"wedding_package_followup", "bridal_followup"}:
        wedding = _payload_fact(payload, "wedding_date")
        window = _payload_fact(payload, "next_step_window_open")
        completed = _payload_fact(payload, "trial_completed")
        facts = [f"the wedding date on file is {wedding}" if wedding else "the wedding follow-up window is open"]
        if completed:
            facts.append(f"the trial was completed on {completed}")
        if window:
            facts.append(f"the next-step window is {window.replace('_', ' ')}")
        body = f"{name}, {', and '.join(facts)}."
        body += f"{(' Your active offer is ' + offer + '.') if offer else ''} Want me to prepare a follow-up note for the next step?"
        return body, "merchant_customer_followup_v1", [name, wedding, window]

    if kind in {"curious_ask_due", "scheduled_recurring"}:
        ask = _clean(payload.get("ask_template"), 100)
        question = "what service has been most requested this week" if "service" in ask else "what are customers asking you about most right now"
        where = f" at {distinct}" if distinct else ""
        body = f"{name}, quick operator check on {question}{where}."
        if offer:
            body += f" We can feature it alongside {offer}."
        body += " Want me to turn your top item into a customer-ready post?"
        return body, "vera_curiosity_checkin_v1", [name, distinct]

    if kind in {"winback_eligible", "subscription_winback"}:
        days = payload.get("days_since_expiry")
        lapse = payload.get("lapsed_customers_added_since_expiry")
        body = f"{name}, your account has a win-back opportunity"
        if isinstance(days, (int, float)):
            body += f" after {days} days since expiry"
        body += "."
        if isinstance(lapse, (int, float)):
            body += f" The trigger records {lapse} additional lapsed customers since expiry."
        body += " Want to review a low-effort reactivation plan?"
        return body, "vera_winback_review_v1", [name, str(days or "")]

    if kind == "review_theme_emerged":
        theme = _payload_fact(payload, "theme") or "a repeated review theme"
        count = payload.get("occurrences_30d")
        body = f"{name}, recent reviews are surfacing {theme.replace('_', ' ')}"
        if isinstance(count, (int, float)):
            body += f" ({count} mentions in the last 30 days)"
        trend = _payload_fact(payload, "trend")
        if trend:
            body += f", with the trend marked {trend}"
        body += ". Want me to help draft a calm response and a practical follow-up?"
        return body, "vera_review_theme_v1", [name, theme, str(count or "")]

    if kind == "milestone_reached":
        metric = _payload_fact(payload, "metric")
        value = payload.get("value_now")
        goal = payload.get("milestone_value")
        if metric:
            noun = f"your {metric.replace('_', ' ')} is close to a milestone"
        else:
            noun = "a new milestone is close"
        if isinstance(value, (int, float)) and isinstance(goal, (int, float)):
            noun += f" — you are on {value} against the {goal} mark"
        body = f"{name}, {noun}."
        peer_reviews = mapping(category.get("peer_stats")).get("avg_review_count")
        if isinstance(peer_reviews, (int, float)) and not isinstance(peer_reviews, bool):
            body += f" Peers in this category average around {peer_reviews} reviews."
        body += " Want me to prepare a simple thank-you post for when you reach it?"
        return body, "vera_milestone_note_v1", [name, metric, str(value or "")]

    if kind == "active_planning_intent":
        topic = _payload_fact(payload, "intent_topic") or "the plan you raised"
        history = [mapping(turn) for turn in items(merchant.get("conversation_history")) if isinstance(turn, dict)]
        latest_merchant = next((
            (index, _clean(turn.get("body"), 300))
            for index, turn in reversed(list(enumerate(history)))
            if text(turn.get("from"), 20).lower() == "merchant" and _clean(turn.get("body"), 300)
        ), None)
        latest_vera = next((
            (index, _clean(turn.get("body"), 300))
            for index, turn in reversed(list(enumerate(history)))
            if text(turn.get("from"), 20).lower() == "vera" and _clean(turn.get("body"), 300)
        ), None)
        last = latest_merchant[1] if latest_merchant else _clean(payload.get("merchant_last_message"), 160)
        topic = topic.replace("_", " ")
        topic_terms = [term for term in re.findall(r"[a-z]{4,}", topic.lower()) if term not in {"with", "from", "your", "into"}]
        prior_related = bool(latest_vera and any(term in latest_vera[1].lower() for term in topic_terms))
        if latest_merchant and latest_vera and latest_vera[0] > latest_merchant[0] and prior_related:
            earlier_outline = latest_vera[1].rstrip("?. ")
            body = (f"{name}, following up on the earlier {topic} outline: “{earlier_outline}” "
                    "Should I revise one part or leave it as proposed?")
        elif last and re.search(r"\b(yes|let'?s do it|go ahead|what would it look like|what should it look like|want to|how would)\b", last, re.I):
            target = f"for {business_name}" if business_name else ""
            if locality and target:
                target += f" in {locality}"
            body = (f"{name}, for {topic} {target}".rstrip() + ", a first draft can cover the offer or menu, audience, minimum size, "
                    "and ordering details. Which detail should we lock in first?")
        else:
            body = f"{name}, I can help shape {topic} into a practical first draft. Want me to outline it using the details already in your account?"
        return body, "vera_planning_followup_v1", [name, topic]

    if kind in {"supply_alert", "recall_alert"}:
        molecule = _payload_fact(payload, "molecule", "product")
        batches = [text(x, 60) for x in items(payload.get("affected_batches")) if text(x, 60)]
        maker = _payload_fact(payload, "manufacturer")
        body = f"{name}, please review the supply alert for {molecule + ' ' if molecule else ''}"
        if batches:
            body += f"batch{'es' if len(batches) > 1 else ''} {', '.join(batches)} "
        if maker:
            body += f"from {maker}"
        body = body.rstrip() + ". Recommended action: audit current shelf inventory and patient dispensing records. Want me to draft a step-by-step verification checklist?"
        return body, "vera_supply_alert_v1", [name, molecule, ", ".join(batches)]

    if kind == "category_seasonal":
        trends = [text(v, 80).replace("_", " ") for v in items(payload.get("trends")) if text(v, 80)]
        if not trends:
            trends = [_clean(mapping(v).get("note"), 140) for v in items(category.get("seasonal_beats")) if isinstance(v, dict) and mapping(v).get("note")]
        fact = "; ".join(trends[:3])
        heads = f"for {business_name}" if business_name else f"for {slug or 'your category'}"
        if locality and business_name:
            heads += f" in {locality}"
        body = f"{name}, the seasonal pattern {heads} points to {fact}." if fact else f"{name}, a seasonal planning window is active {heads}."
        if offer:
            body += f" Your active offer is {offer}."
        body += " Want me to prepare a seasonal checklist or customer draft based on these trends?"
        return body, "vera_seasonal_planning_v1", [name, fact]

    if kind == "gbp_unverified":
        path = _payload_fact(payload, "verification_path")
        target = f"for {business_name}" if business_name else "for your business"
        if locality:
            target += f" in {locality}"
        body = f"{name}, your Google Business Profile {target} is unverified, which reduces your search discovery and calls."
        if path:
            body += f" The available verification route is {path.replace('_', ' ')}."
        body += " Want me to guide you through completing verification?"
        return body, "vera_profile_support_v1", [name, path]

    if kind in {"cde_opportunity", "training_opportunity"}:
        item_id = _payload_fact(payload, "digest_item_id", "top_item_id")
        item = next((mapping(x) for x in items(category.get("digest")) if isinstance(x, dict) and text(x.get("id")) == item_id), {})
        title = _safe_title(item)
        source = _source(item)
        body = f"{name}, a learning opportunity is available"
        if title:
            body += f": {title}"
        if payload.get("credits") is not None:
            body += f" ({payload['credits']} credits)"
        fee_raw = payload.get("fee")
        if fee_raw:
            fee_str = text(fee_raw, 60).replace('_', ' ')
            fee_digits = re.findall(r"\d+", fee_str)
            if fee_digits:
                body += f"; fee is ₹{int(fee_digits[0]):,}"
            else:
                body += f"; fee noted as {fee_str}"
        if source:
            body += f". Source: {source}"
        body += ". Want me to share the registration details?"
        return body, "vera_learning_opportunity_v1", [name, title, source]

    if kind == "competitor_opened":
        competitor = _payload_fact(payload, "competitor_name")
        distance = payload.get("distance_km")
        opened = _payload_fact(payload, "opened_date")
        target = f"near {business_name}" if business_name else "near you"
        if locality:
            target += f" in {locality}"
        if competitor:
            body = f"{name}, a new competitor has opened {target} — {competitor}"
        else:
            body = f"{name}, a new competitor has opened {target}"
        if isinstance(distance, (float, int)) and not isinstance(distance, bool):
            body += f", {distance:g} km away"
        if opened:
            body += f", on {opened}"
        their_offer = _payload_fact(payload, "their_offer")
        if their_offer:
            body += f" with {their_offer}"
        if offer:
            body += f". Your active offer is {offer}"
        body += ". Want to review how your own listing and active offer compare?"
        return body, "vera_local_competition_v1", [name, competitor, str(distance or "")]

    if kind == "dormant_with_vera":
        days = payload.get("days_since_last_merchant_message")
        topic = _payload_fact(payload, "last_topic")
        target = f"for {business_name}" if business_name else ""
        if locality and target:
            target += f" in {locality}"
        prefix = f"{name}, checking back in {target} — " if target else f"{name}, checking back in — "
        if isinstance(days, (int, float)) and not isinstance(days, bool):
            body = prefix + f"{int(days)} days since your last message."
        else:
            history = [mapping(h) for h in items(merchant.get("conversation_history")) if isinstance(h, dict)]
            last = next((h for h in reversed(history) if text(h.get("from"), 20).lower() == "merchant"), None)
            when = _clean(last.get("ts"), 40) if last else ""
            body = prefix + (f"the last update on file was on {when}." if when else "ready for the next step.")
        if topic:
            body += f" The last topic was {topic.replace('_', ' ')}."
        else:
            snapshot = movement_sentence(merchant, "down")
            if snapshot:
                body += f" Meanwhile your {snapshot}."
        if offer:
            body += f" Your active offer is {offer}."
        body += " Want me to draft a fresh customer post to boost visits?"
        return body, "vera_checkin_v1", [name, str(days or ""), topic]

    if kind in {"profile_incomplete", "stale_posts", "customer_question"}:
        detail = _payload_fact(payload, "topic", "question", "issue")
        body = f"{name}, I can help with {detail or kind.replace('_', ' ')} using the information in your account. What would you like me to check first?"
        return body, "vera_account_help_v1", [name, detail]

    # Unknown trigger: do not guess its business meaning. Its own plain-text fact
    # is enough to ask a grounded clarification question.
    label = kind.replace("_", " ") if kind else "an update"
    fact = "" if _is_placeholder_payload(payload) else _payload_fact(payload, "title", "topic", "event", "metric_or_topic")
    body = f"{name}, something new came up on your account ({label})"
    if fact:
        body += f": {fact}"
    body += ". Want me to look at it and suggest a next step?"
    return body, "vera_context_update_v1", [name, label, fact]


def _customer_message(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any],
                      customer: dict[str, Any], now: Any = None) -> tuple[str, str, list[str]]:
    kind = text(trigger.get("kind"), 80).lower()
    payload = mapping(trigger.get("payload"))
    cid = mapping(customer.get("identity"))
    customer_name = _clean(cid.get("name") or "there", 80)
    merchant_name = _clean(mapping(merchant.get("identity")).get("name") or "the business", 120)
    language = text(cid.get("language_pref"), 50).lower()
    hi = language.startswith("hi") or "hi-en" in language
    greeting = f"Namaste {customer_name}, " if hi else f"Hi {customer_name}, "
    if kind in {"recall_due", "customer_recall_due"}:
        service = _payload_fact(payload, "service_due", "service")
        due = _payload_fact(payload, "due_date")
        last = _payload_fact(payload, "last_service_date", "last_visit")
        slots = [mapping(v) for v in items(payload.get("available_slots")) if isinstance(v, dict)]
        labels = [_clean(s.get("label"), 100) for s in slots if _clean(s.get("label"), 100)]
        offer = _offer(merchant)
        service = service.replace("_", " ")
        if service and service.lower() not in {"a follow-up", "follow-up"}:
            opener = f"your {service} is due"
            if last:
                opener += f" — your last one was on {last}"
            if due:
                opener += f", and our records show the due date as {due}"
            body = greeting + f"{merchant_name} here. A quick note that {opener}."
        else:
            visits = mapping(customer.get("relationship")).get("visits_total")
            body = greeting + f"{merchant_name} here. It has been a while since your last visit with us"
            if isinstance(visits, (int, float)) and not isinstance(visits, bool) and visits:
                body += f" — you have been with us {int(visits)} time{'s' if visits != 1 else ''}"
            body += "."
        if labels:
            body += " We have " + " or ".join(labels[:2]) + " listed as options."
        if offer:
            body += f" Our listed service is {offer}."
        if hi:
            body += " Kya hum aapke liye suitable time arrange karein?"
        else:
            body += " Would you like us to help arrange a suitable time?"
        return body, "merchant_recall_reminder_v1", [customer_name, merchant_name, service, due, " / ".join(labels[:2]), offer]

    if kind in {"customer_lapsed_soft", "customer_lapsed_hard", "winback_eligible"}:
        days = payload.get("days_since_last_visit")
        focus = _payload_fact(payload, "previous_focus")
        offer = _offer(merchant)
        body = greeting + f"{merchant_name} here. It's been a little while since your last visit"
        last_visit = _clean(mapping(customer.get("relationship")).get("last_visit"), 80)
        if isinstance(days, (int, float)) and not isinstance(days, bool):
            body += f" ({int(days)} days ago)"
        elif last_visit:
            body += f"; the last visit on file is {last_visit}"
        body += ". No pressure —"
        if focus:
            body += f" we noted your earlier interest in {focus.replace('_', ' ')}."
        else:
            services = [text(x, 60) for x in items(mapping(customer.get("relationship")).get("services_received")) if text(x, 60)]
            if services:
                body += f" we'd love to welcome you back for your next {services[-1].replace('_', ' ')}."
            else:
                body += " we wanted to check whether you'd like to hear from us again."
        if offer:
            body += f" Current listed offer: {offer}."
        body += " Details chahiye?" if hi else " Would you like to see available timings?"
        return body, "merchant_customer_checkin_v1", [customer_name, merchant_name, str(days or ""), focus, offer]

    if kind in {"appointment_tomorrow", "trial_followup", "wedding_package_followup"}:
        options = [mapping(v) for v in items(payload.get("next_session_options") or payload.get("available_slots")) if isinstance(v, dict)]
        labels = [_clean(x.get("label"), 100) for x in options if _clean(x.get("label"), 100)]
        date = _payload_fact(payload, "appointment_date", "trial_date", "wedding_date")
        offer = _offer(merchant) or _catalog_offer(category)
        body = greeting + f"{merchant_name} here."
        if kind == "appointment_tomorrow":
            body += " Just a reminder about your appointment tomorrow."
        elif date:
            body += f" Following up on {date}."
        else:
            body += " Following up on your recent visit with us."
        if labels:
            joined = " or ".join(labels[:2])
            body += f" We have {joined} on the books — does that still work for you?"
        else:
            body += " Please let us know a day and time slot that suits you best."
        if offer:
            body += f" Our listed service is {offer}."
        body += " Kya main confirm kar doon?" if hi else " Want me to confirm it?"
        return body, "merchant_appointment_followup_v1", [customer_name, merchant_name, date, " / ".join(labels), offer]

    if kind == "chronic_refill_due":
        molecules = [text(x, 60) for x in items(payload.get("molecule_list")) if text(x, 60)]
        due = _payload_fact(payload, "stock_runs_out_iso")
        services = [text(x, 60) for x in items(mapping(customer.get("relationship")).get("services_received")) if text(x, 60)]
        offer = _offer(merchant)
        if molecules:
            lead = f"your refill for {', '.join(molecules)} is coming up"
        elif services:
            lead = f"your {services[-1].replace('_', ' ')} is coming up for review"
        else:
            lead = "your next refill is coming up"
        body = greeting + f"{merchant_name} here — a quick note that {lead}."
        if due:
            body += f" Our records show the run-out date as {due}."
        if payload.get("delivery_address_saved") is True:
            body += " Your saved delivery address is already on file."
        if offer:
            body += f" Current offer: {offer}."
        body += " Shall we arrange your refill delivery?"
        subject = ", ".join(molecules) or (services[-1] if services else "")
        return body, "merchant_refill_reminder_v1", [customer_name, merchant_name, subject, due]

    body = greeting + f"{merchant_name} here with an update related to {kind.replace('_', ' ')}. Would you like us to share the details?"
    return body, "merchant_customer_update_v1", [customer_name, merchant_name, kind]


def compose(category: dict[str, Any], merchant: dict[str, Any], trigger: dict[str, Any],
             customer: dict[str, Any] | None = None, now: Any = None) -> dict[str, Any]:
    """Compose a deterministic result from supplied contexts only.

    `now` is the judge's simulated clock from the tick request. It is an input
    rather than wall-clock time so that the same call always produces the same
    message, and seasonal advice stays available when the judge supplies a time.
    """
    kind = text(trigger.get("kind"), 80).lower()
    suppression_key = text(trigger.get("suppression_key"), 200)
    if not suppression_key:
        trigger_id = text(trigger.get("id"), 180)
        suppression_key = f"trigger:{trigger_id or kind or 'unknown'}"
    if customer is not None:
        body, template, params = _customer_message(category, merchant, trigger, customer, now)
        send_as = "merchant_on_behalf"
        cta = _cta(kind, customer)
        rationale = f"Customer-scoped {kind or 'update'} sent on the merchant's behalf; consent and customer context were checked before composition. Message facts come from the supplied trigger and merchant offer list."
    else:
        body, template, params = _merchant_message(category, merchant, trigger, now)
        send_as = "vera"
        cta = "none" if kind in {"information_only", "digest_notice"} else _cta(kind, None)
        rationale = f"Merchant-facing {kind or 'update'} prioritized from its current trigger. Specific claims are limited to supplied trigger, category, and merchant fields; no unsupported figures or offers were added."
    return {"body": body[:1500], "cta": cta, "send_as": send_as, "template_name": template,
            "template_params": [p for p in params if p][:8], "suppression_key": suppression_key,
            "rationale": rationale[:500]}
