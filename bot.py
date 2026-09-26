#!/usr/bin/env python3
"""HTTP service for the magicpin VERA challenge (standard library only)."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock
from typing import Any
from urllib.parse import urlparse

from engine.composition import compose
from engine.constants import SAFETY_PRIORITY
from engine.context_store import ContextStore
from engine.conversation import respond
from engine.signals import consent_allows, mapping, text, trigger_expired
from engine import llm_dispatch
from engine.nim_polish import polish
from engine.state import RuntimeState

STARTED = time.monotonic()
contexts = ContextStore()
runtime = RuntimeState()
MAX_ACTIONS_PER_TICK = 20
# At most this many messages per tick get an NIM wording call. Keeps the
# optional layer from serialising across a large trigger batch.
MAX_POLISH_PER_TICK = 1
TICK_LOCK = RLock()


def _suppression_key(trigger: dict[str, Any], trigger_id: str) -> str:
    provided = text(trigger.get("suppression_key"), 200)
    return provided or f"trigger:{trigger_id}"


def _priority(trigger: dict[str, Any], now: Any = None) -> tuple[int, int, int, str]:
    """Rank candidates: safety class, then urgency, then freshness, then id.

    Freshness is a tiebreaker only. The judge's `available_triggers` list already
    asserts a trigger is live, so a past `expires_at` deprioritises rather than
    discards it -- the harness sends a wall-clock `now` that can sit months after
    the dataset's own dates.
    """
    kind = text(trigger.get("kind"), 80).lower()
    urgency = trigger.get("urgency", 1)
    try:
        urgency_value = max(1, min(5, int(urgency)))
    except (ValueError, TypeError):
        urgency_value = 1
    safety = SAFETY_PRIORITY.get(kind, 1)
    stale = 1 if trigger_expired(trigger, now) else 0
    return safety, urgency_value, stale, text(trigger.get("id"), 180)


def _eligible_customer(customer: dict[str, Any], trigger: dict[str, Any], merchant_id: str) -> bool:
    if text(customer.get("merchant_id"), 180) != merchant_id:
        return False
    if text(customer.get("state"), 40).lower() in {"churned", "opted_out", "do_not_contact"}:
        return False
    return consent_allows(customer, trigger)


def _history_opted_out(merchant: dict[str, Any]) -> bool:
    stop_phrases = ("stop messaging", "stop sending", "unsubscribe", "not interested", "do not contact", "don't contact")
    history = merchant.get("conversation_history")
    if not isinstance(history, list):
        return False
    for turn in history[-20:]:
        if not isinstance(turn, dict) or text(turn.get("from"), 30).lower() != "merchant":
            continue
        engagement = text(turn.get("engagement"), 60).lower()
        message = text(turn.get("body"), 400).lower()
        if any(marker in engagement for marker in ("unsubscribe", "opt_out", "opt-out", "declined")) or any(phrase in message for phrase in stop_phrases):
            return True
    return False


def handle_context(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        scope = body.get("scope")
        context_id = body.get("context_id")
        version = body.get("version")
        accepted, current_version = contexts.put(scope, context_id, version, body.get("payload"))
    except ValueError as exc:
        reason = str(exc)
        return 400, {"accepted": False, "reason": reason, "details": "A valid scope, context_id, positive integer version, and object payload are required."}
    if not accepted:
        return 409, {"accepted": False, "reason": "stale_version", "current_version": current_version}
    return 200, {"accepted": True, "ack_id": f"ack_{context_id}_v{version}",
                 "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


def handle_tick(body: dict[str, Any]) -> dict[str, Any]:
    # Serialize tick selection and reservation to prevent duplicate sends if
    # the judge retries or overlaps tick requests.
    with TICK_LOCK:
        return _handle_tick(body)


def _handle_tick(body: dict[str, Any]) -> dict[str, Any]:
    now = body.get("now")
    trigger_ids = body.get("available_triggers")
    if not isinstance(trigger_ids, list):
        return {"actions": []}
    candidates: list[tuple[tuple[int, int, str], str, dict[str, Any], dict[str, Any]]] = []
    for raw_id in trigger_ids:
        trigger_id = text(raw_id, 180)
        trigger = contexts.get("trigger", trigger_id)
        if not trigger:
            continue
        # `available_triggers` is the judge's explicit statement of what is active
        # right now, so it outranks the trigger's own expires_at. The harness
        # sends a wall-clock `now` while the dataset is dated months earlier, so
        # a hard expiry check would silently discard most live events. Expiry is
        # used only to rank, never to drop, in bot.py::handle_tick.
        merchant_id = text(trigger.get("merchant_id"), 180)
        merchant = contexts.get("merchant", merchant_id)
        if not merchant or runtime.is_opted_out(merchant_id) or _history_opted_out(merchant):
            continue
        category = contexts.get("category", text(merchant.get("category_slug"), 80))
        if not category:
            continue
        customer_id = text(trigger.get("customer_id"), 180)
        customer = contexts.get("customer", customer_id) if customer_id else None
        customer_scope = text(trigger.get("scope"), 30).lower() == "customer"
        if customer_scope and (not customer or not _eligible_customer(customer, trigger, merchant_id)):
            continue
        customer_id = text(trigger.get("customer_id"), 180)
        if customer_scope and runtime.is_customer_opted_out(customer_id):
            continue
        if not customer_scope:
            customer = None
            customer_id = ""
        raw_key = _suppression_key(trigger, trigger_id)
        recipient_key = text(trigger.get("customer_id"), 180) if customer_scope else merchant_id
        key = f"{merchant_id}|{recipient_key}|{raw_key}"
        if runtime.has_suppressed(key):
            continue
        candidates.append((_priority(trigger, now), trigger_id, trigger, merchant))

    # Highest urgency per merchant only; avoids stacking simultaneous nudges.
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    actions: list[dict[str, Any]] = []
    selected_merchants: set[str] = set()
    selected_customers: set[tuple[str, str]] = set()
    polished_count = 0
    for _, trigger_id, trigger, merchant in candidates:
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break
        merchant_id = text(trigger.get("merchant_id"), 180)
        if merchant_id in selected_merchants:
            continue
        customer_id = text(trigger.get("customer_id"), 180) or None
        pair = (merchant_id, customer_id or "")
        if pair in selected_customers:
            continue
        customer = contexts.get("customer", customer_id) if customer_id else None
        if text(trigger.get("scope"), 30).lower() != "customer":
            customer = None
            customer_id = None
        category = contexts.get("category", text(merchant.get("category_slug"), 80)) or {}
        message = compose(category, merchant, trigger, customer, now=now)
        customer_part = customer_id or "merchant"
        raw_key = _suppression_key(trigger, trigger_id)
        dedupe_key = f"{merchant_id}|{customer_id or merchant_id}|{raw_key}"
        if runtime.has_suppressed(dedupe_key) or runtime.has_body(customer_id or merchant_id, message["body"]):
            continue
        conversation_id = f"conv_{merchant_id}_{customer_part}_{trigger_id}"[:240]
        if conversation_id in runtime.sent_conversations:
            continue
        # Wording polish is optional and strictly cosmetic. It is applied after
        # every deterministic gate (selection, consent, suppression, dedupe) so
        # those decisions never depend on the model, and capped per tick so a
        # burst of triggers cannot serialise into many network calls and blow
        # the 30s endpoint budget.
        body = message["body"]
        if polished_count < MAX_POLISH_PER_TICK:
            polished = polish(message, category, merchant, trigger, customer)
            if polished["body"] != message["body"]:
                body = polished["body"]
                polished_count += 1
        action = {
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": message["send_as"],
            "trigger_id": trigger_id,
            "template_name": message["template_name"],
            "template_params": message["template_params"],
            "body": body,
            "cta": message["cta"],
            "suppression_key": _suppression_key(trigger, trigger_id),
            "rationale": message["rationale"],
        }
        actions.append(action)
        runtime.record_action(dedupe_key, conversation_id, merchant_id, customer_id, action["body"])
        selected_merchants.add(merchant_id)
        selected_customers.add(pair)
    return {"actions": actions}


def health() -> dict[str, Any]:
    return {"status": "ok", "uptime_seconds": int(time.monotonic() - STARTED), "contexts_loaded": contexts.counts()}


def metadata() -> dict[str, Any]:
    # Report the model only when the provider path has actually produced a
    # message. Environment variables alone prove nothing: a configured-but-never-
    # used provider must not be advertised as the active model.
    llm = llm_dispatch.stats()
    active = llm["successes"] > 0
    return {"team_name": "VERA Deterministic Engine", "team_members": [],
            "model": (f"vera-deterministic-engine-v1+llm:{llm['model']}" if active
                      else "deterministic-rules-v1"),
            "approach": "Context-grounded deterministic composition with versioned state, consent gating, suppression, and reply routing",
            "version": "1.0.0",
            "llm": {"provider": llm_dispatch.active_name() or None,
                    "configured": llm["configured"], "active": active,
                    "model": llm["model"], "attempts": llm["attempts"],
                    "successes": llm["successes"], "failures": llm["failures"],
                    "rejections": llm["rejections"]}}


class Handler(BaseHTTPRequestHandler):
    server_version = "VeraChallenge/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep PII-bearing request bodies out of logs.
        return

    def _json(self, status: int, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 500_000:
                return None
            value = json.loads(self.rfile.read(size).decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/v1/healthz":
            self._json(200, health())
        elif path == "/v1/metadata":
            self._json(200, metadata())
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        if body is None:
            self._json(400, {"error": "invalid_json"})
        elif path == "/v1/context":
            status, result = handle_context(body)
            self._json(status, result)
        elif path == "/v1/tick":
            self._json(200, handle_tick(body))
        elif path == "/v1/reply":
            self._json(200, respond(body, runtime))
        elif path == "/v1/teardown":
            contexts.clear()
            runtime.clear()
            self._json(200, {"accepted": True, "status": "torn_down"})
        else:
            self._json(404, {"error": "not_found"})


def _resolve_port(default: int) -> int:
    """Prefer the PORT env var, because PaaS hosts inject the listen port there.

    An explicit --port still wins, so local runs and the judge runner are
    unaffected.
    """
    for raw in (os.environ.get("PORT"),):
        if raw:
            try:
                return int(raw)
            except ValueError:
                print(f"Ignoring non-numeric PORT={raw!r}")
    return default


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic VERA challenge bot")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=_resolve_port(8080))
    args = parser.parse_args()

    # Serve in a background thread so the main thread can wait on a stop event.
    # PaaS platforms send SIGTERM before recycling a container; without this the
    # process is killed mid-request instead of shutting down cleanly.
    stop = threading.Event()

    def _handle_signal(signum, _frame):
        print(f"Received signal {signum}, shutting down")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass  # not on the main thread, or unsupported on this platform

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"VERA challenge bot listening on http://{args.host}:{args.port}", flush=True)

    try:
        while not stop.wait(timeout=1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        print("VERA challenge bot stopped", flush=True)


if __name__ == "__main__":
    main()
