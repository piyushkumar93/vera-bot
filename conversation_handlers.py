"""Standalone multi-turn handler for the optional replay phase.

Wraps the engine's reply router with a small, self-contained conversation state
so a replay harness can drive a thread without running the HTTP service:

    state = ConversationState(merchant_id="m_001")
    result = respond(state, "Ok lets do it. Whats next?")
    # -> {"action": "send", "body": ..., "cta": ..., "rationale": ...}

Routing is deterministic and identical to `/v1/reply`: canned auto-replies wait
and then close, opt-outs end and mute the merchant permanently, clear commitment
moves straight to execution, and out-of-scope requests are declined politely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from engine.conversation import respond as _respond
from engine.state import RuntimeState


@dataclass
class ConversationState:
    """Minimal replay state: a conversation id plus the turns seen so far."""

    merchant_id: str
    conversation_id: str = ""
    turns: List[Dict[str, Any]] = field(default_factory=list)
    runtime: RuntimeState = field(default_factory=RuntimeState, repr=False)

    def __post_init__(self) -> None:
        if not self.conversation_id:
            self.conversation_id = f"conv_{self.merchant_id or 'unknown'}"

    def record(self, role: str, message: str, turn_number: int | None = None) -> None:
        self.turns.append({"from": role, "body": message, "turn_number": turn_number})

    @property
    def ended(self) -> bool:
        return bool(self.turns) and self.turns[-1].get("action") == "end"


def respond(state: ConversationState, merchant_message: str) -> dict[str, Any]:
    """Given the conversation so far plus the latest message, produce the reply.

    Returns one of the contract's three actions: `send`, `wait`, or `end`.
    """
    if not isinstance(state, ConversationState):
        raise TypeError("state must be a ConversationState")
    turn_number = len(state.turns) + 1
    body = {
        "conversation_id": state.conversation_id,
        "merchant_id": state.merchant_id,
        "customer_id": None,
        "from_role": "merchant",
        "message": merchant_message,
        "turn_number": turn_number,
    }
    result = _respond(body, state.runtime)
    state.turns.append({"from": "merchant", "body": merchant_message,
                        "turn_number": turn_number, "action": result.get("action")})
    return result
