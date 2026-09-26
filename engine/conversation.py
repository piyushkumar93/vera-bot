"""Deterministic, short-horizon reply handling."""

from __future__ import annotations

import re
from typing import Any

from .signals import mapping, text
from .state import RuntimeState

AUTO_PHRASES = (
    "thank you for contacting", "thank you for your message", "our team will respond",
    "we will get back to you", "automated assistant", "your message is important to us",
    "office hours", "we are currently unavailable", "we have received your message",
)
STOP_PHRASES = ("stop messaging", "stop sending", "don't message", "do not message", "unsubscribe", "leave me alone", "useless spam", "don't contact", "do not contact", "remove me", "report spam")
DECLINE_PHRASES = ("not interested", "no thanks", "no thank you", "don't need", "do not need", "not for me")
DEFER_PHRASES = ("not now", "maybe later", "ask me later", "busy right now", "another time", "next week")
YES_PATTERN = re.compile(r"\b(yes|yeah|yep|ok|okay|sure|go ahead|let'?s do it|do it|proceed|what'?s next|next step|send it|please start|haan)\b", re.I)
QUESTION_PATTERN = re.compile(r"\b(what|why|how|when|where|which|who|can you|could you|is there|does it)\b|\?", re.I)


def is_auto_reply(message: str) -> bool:
    normalized = " ".join(message.lower().split())
    return any(phrase in normalized for phrase in AUTO_PHRASES)


def respond(body: dict[str, Any], state: RuntimeState) -> dict[str, Any]:
    conversation_id = text(body.get("conversation_id"), 180) or "unknown"
    merchant_id = text(body.get("merchant_id"), 180)
    customer_id = text(body.get("customer_id"), 180)
    role = text(body.get("from_role"), 30).lower() or "merchant"
    message = text(body.get("message"), 4000)
    lower = message.lower()
    repeated = state.record_reply(conversation_id, role, message)

    if role == "merchant" and is_auto_reply(message):
        count = state.count_auto_reply(merchant_id, message)
        if count >= 3:
            return {"action": "end", "rationale": "The same canned auto-reply has repeated; closing so Vera does not spend turns on an unattended line."}
        return {"action": "wait", "wait_seconds": 14400 if count == 1 else 86400,
                "rationale": "Detected a likely WhatsApp auto-reply. Waiting for a person to take over before continuing."}

    if role == "merchant" and (lower.strip() == "stop" or any(phrase in lower for phrase in STOP_PHRASES)):
        state.opt_out(merchant_id)
        return {"action": "end", "rationale": "The merchant asked Vera to stop or declined; ending the conversation and suppressing further merchant outreach."}

    if role == "customer" and (lower.strip() == "stop" or any(phrase in lower for phrase in STOP_PHRASES)):
        state.customer_opt_out(customer_id)
        return {"action": "end", "rationale": "The customer asked Vera to stop; ending and suppressing further customer messages for this customer."}

    if role in {"merchant", "customer"} and any(phrase in lower for phrase in DECLINE_PHRASES):
        return {"action": "end", "rationale": "The recipient declined this recommendation; closing this conversation without treating a topic-specific decline as a permanent opt-out."}

    if role == "merchant" and any(phrase in lower for phrase in DEFER_PHRASES):
        return {"action": "wait", "wait_seconds": 86400,
                "rationale": "The merchant asked to defer; pausing this conversation for a day before considering a follow-up."}

    if role == "merchant" and YES_PATTERN.search(message):
        # Commitment is a state transition: move to the concrete next step and
        # do not ask another discovery question.
        return {"action": "send", "body": "Great — proceeding with the next step. I’ll prepare the draft using the details already shared and show it here for your review.",
                "cta": "open_ended", "rationale": "The merchant expressed clear intent, so the reply moves directly to execution without another qualification question."}

    if role == "merchant" and ("gst" in lower or "unrelated" in lower):
        return {"action": "send", "body": "I can’t help file GST returns, so please work with your CA for that. I can continue with the Vera task we were discussing when you’re ready.",
                "cta": "open_ended", "rationale": "Politely redirects an out-of-scope request to the original merchant-support task."}

    if role == "merchant" and QUESTION_PATTERN.search(message):
        return {"action": "send", "body": "I can clarify that using the details in the current context. Which part of the recommendation should I explain first?",
                "cta": "open_ended", "rationale": "The merchant asked a question; Vera asks which point needs clarification instead of guessing at missing details."}

    if not message:
        return {"action": "wait", "wait_seconds": 1800, "rationale": "No usable reply text was supplied; waiting before taking another step."}
    if repeated > 1:
        return {"action": "wait", "wait_seconds": 3600, "rationale": "The same reply repeated; pausing to avoid an unhelpful loop."}
    return {"action": "send", "body": "Thanks, understood. I’ll keep the next step focused on the request already in this conversation and use only the details you’ve shared.",
            "cta": "open_ended", "rationale": "Acknowledges the reply without inventing missing account details or changing the conversation goal."}
