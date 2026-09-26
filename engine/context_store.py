"""Versioned in-memory context storage and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

SCOPES = ("category", "merchant", "customer", "trigger")


@dataclass(frozen=True)
class ContextRecord:
    version: int
    payload: dict[str, Any]


class ContextStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ContextRecord] = {}
        self._lock = RLock()

    def put(self, scope: str, context_id: str, version: int, payload: dict[str, Any]) -> tuple[bool, int | None]:
        if scope not in SCOPES:
            raise ValueError("invalid_scope")
        if not context_id or not isinstance(context_id, str):
            raise ValueError("invalid_context_id")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("invalid_version")
        if not isinstance(payload, dict):
            raise ValueError("invalid_payload")
        key = (scope, context_id)
        with self._lock:
            current = self._records.get(key)
            if current and version <= current.version:
                return False, current.version
            self._records[key] = ContextRecord(version, payload)
        return True, None

    def get(self, scope: str, context_id: str | None) -> dict[str, Any] | None:
        if not context_id:
            return None
        with self._lock:
            record = self._records.get((scope, context_id))
            return record.payload if record else None

    def counts(self) -> dict[str, int]:
        with self._lock:
            counts = {scope: 0 for scope in SCOPES}
            for scope, _ in self._records:
                counts[scope] += 1
            return counts

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

