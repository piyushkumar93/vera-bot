"""Per-process send and conversation state."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from threading import RLock
from typing import Any


def normalize_message(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


class RuntimeState:
    def __init__(self) -> None:
        self._lock = RLock()
        self.sent_suppression_keys: set[str] = set()
        self.sent_conversations: set[str] = set()
        self.sent_bodies: dict[str, set[str]] = defaultdict(set)
        self.conversation_history: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.auto_reply_counts: Counter[tuple[str, str]] = Counter()
        self.merchant_opt_out: set[str] = set()
        self.customer_opt_outs: set[str] = set()

    def has_suppressed(self, key: str) -> bool:
        with self._lock:
            return key in self.sent_suppression_keys

    def has_body(self, recipient_key: str, body: str) -> bool:
        with self._lock:
            return normalize_message(body) in self.sent_bodies.get(recipient_key, set())

    def record_action(self, key: str, conversation_id: str, merchant_id: str, customer_id: str | None, body: str) -> None:
        with self._lock:
            if key:
                self.sent_suppression_keys.add(key)
            self.sent_conversations.add(conversation_id)
            recipient_key = customer_id or merchant_id
            self.sent_bodies[recipient_key].add(normalize_message(body))
            self.conversation_history[conversation_id].append({"from": "vera", "body": body})

    def record_reply(self, conversation_id: str, role: str, message: str) -> int:
        with self._lock:
            self.conversation_history[conversation_id].append({"from": role, "body": message})
            normalized = normalize_message(message)
            history = self.conversation_history[conversation_id]
            return sum(1 for turn in history if turn["from"] == role and normalize_message(turn["body"]) == normalized)

    def count_auto_reply(self, merchant_id: str, message: str) -> int:
        key = (merchant_id, normalize_message(message))
        with self._lock:
            self.auto_reply_counts[key] += 1
            return self.auto_reply_counts[key]

    def is_opted_out(self, merchant_id: str) -> bool:
        with self._lock:
            return merchant_id in self.merchant_opt_out

    def is_customer_opted_out(self, customer_id: str) -> bool:
        with self._lock:
            return customer_id in self.customer_opt_outs

    def opt_out(self, merchant_id: str) -> None:
        with self._lock:
            self.merchant_opt_out.add(merchant_id)

    def customer_opt_out(self, customer_id: str) -> None:
        with self._lock:
            if customer_id:
                self.customer_opt_outs.add(customer_id)

    def clear(self) -> None:
        with self._lock:
            self.sent_suppression_keys.clear()
            self.sent_conversations.clear()
            self.sent_bodies.clear()
            self.conversation_history.clear()
            self.auto_reply_counts.clear()
            self.merchant_opt_out.clear()
            self.customer_opt_outs.clear()
