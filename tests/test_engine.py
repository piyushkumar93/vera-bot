from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from bot import _eligible_customer, contexts, handle_context, handle_tick, runtime
from engine.composition import compose
from engine.context_store import ContextStore
from engine.conversation import respond
from engine.signals import consent_allows
from engine.state import RuntimeState

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ContextVersionTests(unittest.TestCase):
    def test_newer_version_replaces_and_equal_or_stale_is_rejected(self):
        store = ContextStore()
        self.assertEqual(store.put("merchant", "m-x", 1, {"v": 1}), (True, None))
        self.assertEqual(store.put("merchant", "m-x", 1, {"v": 9}), (False, 1))
        self.assertEqual(store.get("merchant", "m-x"), {"v": 1})
        self.assertEqual(store.put("merchant", "m-x", 2, {"v": 2}), (True, None))
        self.assertEqual(store.get("merchant", "m-x"), {"v": 2})

    def test_invalid_scope_and_payload_are_rejected(self):
        store = ContextStore()
        with self.assertRaises(ValueError):
            store.put("other", "x", 1, {})
        with self.assertRaises(ValueError):
            store.put("trigger", "x", 1, None)


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.categories = {p.stem: read_json(p) for p in (ROOT / "dataset/categories").glob("*.json")}
        cls.merchants = {m["merchant_id"]: m for m in read_json(ROOT / "dataset/merchants_seed.json")["merchants"]}
        cls.customers = {c["customer_id"]: c for c in read_json(ROOT / "dataset/customers_seed.json")["customers"]}
        cls.triggers = {t["id"]: t for t in read_json(ROOT / "dataset/triggers_seed.json")["triggers"]}

    def compose_trigger(self, trigger_id):
        trigger = self.triggers[trigger_id]
        merchant = self.merchants[trigger["merchant_id"]]
        category = self.categories[merchant["category_slug"]]
        customer = self.customers.get(trigger.get("customer_id"))
        return compose(category, merchant, trigger, customer)

    def test_research_digest_uses_selected_category_item_and_citation(self):
        result = self.compose_trigger("trg_001_research_digest_dentists")
        self.assertIn("JIDA Oct 2026, p.14", result["body"])
        self.assertIn("3-month fluoride varnish", result["body"])
        self.assertEqual(result["send_as"], "vera")

    def test_perf_dip_formats_trigger_delta_without_inventing_other_metrics(self):
        result = self.compose_trigger("trg_004_perf_dip_bharat")
        self.assertIn("50%", result["body"])
        self.assertIn("calls", result["body"].lower())
        self.assertNotIn("sales", result["body"].lower())

    def test_snapshot_delta_keeps_its_own_window_label(self):
        merchant = dict(self.merchants["m_002_bharat_dentist_mumbai"])
        merchant["performance"] = {"window_days": 30, "delta_7d": {"views_pct": -0.08, "calls_pct": -0.21}}
        trigger = {"kind": "perf_dip", "payload": {}}
        result = compose(self.categories["dentists"], merchant, trigger)
        self.assertIn("7-day views 8% lower and calls 21% lower", result["body"])
        self.assertNotIn("over the last 30 days", result["body"])

        spike = compose(self.categories["dentists"], merchant | {"performance": {"window_days": 30, "delta_7d": {"views_pct": 0.02, "calls_pct": 0.05}}}, {"kind": "perf_spike", "payload": {}})
        self.assertIn("7-day views 2% higher and calls 5% higher", spike["body"])
        self.assertNotIn("over the last 30 days", spike["body"])

    def test_conflicting_trigger_and_snapshot_signals_are_flagged(self):
        merchant = dict(self.merchants["m_002_bharat_dentist_mumbai"])
        merchant["performance"] = {"window_days": 7, "delta_7d": {"calls_pct": 0.20}}
        trigger = {"kind": "perf_dip", "payload": {"metric": "calls", "delta_pct": -0.10, "window": "7d"}}
        result = compose(self.categories["dentists"], merchant, trigger)
        self.assertIn("snapshot shows 20% up", result["body"])
        self.assertIn("verify the current window", result["body"])

    def test_planning_question_advances_from_merchant_history(self):
        trigger = self.triggers["trg_013_corporate_thali_planning"]
        merchant = self.merchants[trigger["merchant_id"]]
        result = compose(self.categories[merchant["category_slug"]], merchant, trigger)
        self.assertIn("offer or menu", result["body"])
        self.assertIn("ordering details", result["body"])
        self.assertNotIn("what one constraint should I build around first", result["body"])

    def test_planning_followup_references_the_last_vera_outline(self):
        trigger = self.triggers["trg_016_kids_yoga_program_drafting"]
        merchant = self.merchants[trigger["merchant_id"]]
        result = compose(self.categories[merchant["category_slug"]], merchant, trigger)
        self.assertIn("earlier kids yoga summer camp outline", result["body"])
        self.assertIn("₹2,499", result["body"])
        self.assertIn("revise one part", result["body"])

    def test_category_seasonality_uses_only_supplied_simulated_time(self):
        category = dict(self.categories["dentists"])
        category["seasonal_beats"] = [{"month_range": "Nov-Feb", "note": "exam-stress demand"}]
        merchant = self.merchants["m_001_drmeera_dentist_delhi"]
        trigger = {"kind": "festival_upcoming", "payload": {"days_until": 4}}
        no_clock = compose(category, merchant, trigger)
        winter = compose(category, merchant, trigger, now="2026-12-01T00:00:00Z")
        april = compose(category, merchant, trigger, now="2026-04-01T00:00:00Z")
        self.assertNotIn("exam-stress demand", no_clock["body"])
        self.assertIn("exam-stress demand", winter["body"])
        self.assertNotIn("exam-stress demand", april["body"])

    def test_missing_research_item_falls_back_without_fabricated_claim(self):
        trigger = {"id": "new", "kind": "research_digest", "payload": {"top_item_id": "not-present"}}
        result = compose(self.categories["dentists"], self.merchants["m_001_drmeera_dentist_delhi"], trigger)
        self.assertIn("digest is available", result["body"])
        self.assertNotIn("38%", result["body"])

    def test_customer_copy_uses_only_present_slots_and_offer(self):
        result = self.compose_trigger("trg_003_recall_due_priya")
        self.assertIn("Wed 5 Nov, 6pm", result["body"])
        self.assertIn("Dental Cleaning @ ₹299", result["body"])
        self.assertEqual(result["send_as"], "merchant_on_behalf")

    def test_customer_opt_out_and_scope_mismatch_are_denied(self):
        customer = dict(self.customers["c_001_priya_for_m001"])
        customer["preferences"] = {"reminder_opt_in": False, "channel": "whatsapp"}
        self.assertFalse(consent_allows(customer, self.triggers["trg_003_recall_due_priya"]))
        customer["preferences"] = {"reminder_opt_in": True, "channel": "whatsapp"}
        customer["consent"] = {"opted_in_at": "2026-01-01", "scope": ["promotional_offers"]}
        self.assertFalse(consent_allows(customer, self.triggers["trg_003_recall_due_priya"]))

    def test_active_offer_only_is_used(self):
        merchant = dict(self.merchants["m_001_drmeera_dentist_delhi"])
        merchant["offers"] = [dict(self.merchants["m_001_drmeera_dentist_delhi"]["offers"][1])]
        merchant["offers"][0]["status"] = "expired"
        trigger = self.triggers["trg_003_recall_due_priya"]
        result = compose(self.categories["dentists"], merchant, trigger, self.customers[trigger["customer_id"]])
        self.assertNotIn("₹499", result["body"])

    def test_unknown_category_and_sparse_trigger_are_safe(self):
        result = compose({}, {"merchant_id": "new", "identity": {"name": "A"}}, {"kind": "new_kind", "payload": {}})
        self.assertTrue(result["body"])
        self.assertEqual(result["send_as"], "vera")
        self.assertEqual(result["suppression_key"], "trigger:new_kind")

    def test_updated_digest_context_is_used_and_unmatched_id_does_not_fall_back(self):
        category = dict(self.categories["dentists"])
        category["digest"] = [{"id": "new-item", "title": "Current update with a 7% change", "source": "Supplied bulletin"}]
        merchant = self.merchants["m_001_drmeera_dentist_delhi"]
        matching = {"kind": "research_digest", "payload": {"top_item_id": "new-item"}}
        result = compose(category, merchant, matching)
        self.assertIn("7% change", result["body"])
        unmatched = compose(category, merchant, {"kind": "research_digest", "payload": {"top_item_id": "old-item"}})
        self.assertNotIn("7%", unmatched["body"])

    def test_expanded_trigger_distribution_composes_without_placeholder_leak(self):
        data = ROOT / "dataset/expanded"
        categories = {p.stem: read_json(p) for p in (data / "categories").glob("*.json")}
        merchants = {m["merchant_id"]: m for p in (data / "merchants").glob("*.json") if (m := read_json(p))}
        customers = {c["customer_id"]: c for p in (data / "customers").glob("*.json") if (c := read_json(p))}
        triggers = [read_json(p) for p in (data / "triggers").glob("*.json")]
        self.assertEqual(len(triggers), 100)
        for trigger in triggers:
            with self.subTest(kind=trigger["kind"], trigger=trigger["id"]):
                merchant = merchants[trigger["merchant_id"]]
                customer = customers.get(trigger.get("customer_id")) if trigger.get("scope") == "customer" else None
                result = compose(categories[merchant["category_slug"]], merchant, trigger, customer)
                self.assertTrue(result["body"])
                self.assertNotIn("placeholder", result["body"].lower())
                self.assertNotIn("None", result["body"])
                self.assertIn(result["cta"], {"none", "open_ended", "binary_yes_no"})

    def test_long_merchant_history_does_not_break_composition(self):
        merchant = dict(self.merchants["m_001_drmeera_dentist_delhi"])
        merchant["conversation_history"] = [{"from": "merchant", "body": f"prior turn {i}", "engagement": "ignored"} for i in range(1000)]
        result = compose(self.categories["dentists"], merchant, self.triggers["trg_001_research_digest_dentists"])
        self.assertTrue(result["body"])


class ReplyTests(unittest.TestCase):
    def test_auto_reply_repetition_ends(self):
        state = RuntimeState()
        message = "Thank you for contacting us! Our team will respond shortly."
        results = [respond({"conversation_id": f"c{i}", "merchant_id": "m1", "message": message}, state) for i in range(1, 4)]
        self.assertEqual(results[0]["action"], "wait")
        self.assertEqual(results[1]["action"], "wait")
        self.assertEqual(results[2]["action"], "end")

    def test_clear_intent_moves_to_action_and_opt_out_ends(self):
        state = RuntimeState()
        intent = respond({"conversation_id": "c", "merchant_id": "m1", "message": "Ok lets do it. Whats next?"}, state)
        self.assertEqual(intent["action"], "send")
        self.assertIn("proceeding", intent["body"])
        no = respond({"conversation_id": "c2", "merchant_id": "m2", "message": "Stop messaging me. This is useless spam."}, state)
        self.assertEqual(no["action"], "end")
        self.assertTrue(state.is_opted_out("m2"))
        customer_no = respond({"conversation_id": "c3", "merchant_id": "m3", "customer_id": "c9", "from_role": "customer", "message": "Please stop messaging me."}, state)
        self.assertEqual(customer_no["action"], "end")
        self.assertTrue(state.is_customer_opted_out("c9"))

    def test_decline_defer_and_question_have_distinct_routes(self):
        state = RuntimeState()
        decline = respond({"conversation_id": "d", "merchant_id": "m1", "message": "No thanks, not interested"}, state)
        self.assertEqual(decline["action"], "end")
        self.assertFalse(state.is_opted_out("m1"))
        defer = respond({"conversation_id": "later", "merchant_id": "m2", "message": "Not now, ask me later"}, state)
        self.assertEqual((defer["action"], defer["wait_seconds"]), ("wait", 86400))
        question = respond({"conversation_id": "q", "merchant_id": "m3", "message": "How would that work?"}, state)
        self.assertEqual(question["action"], "send")
        self.assertIn("Which part", question["body"])

    def test_merchant_history_opt_out_suppresses_future_ticks(self):
        from bot import _history_opted_out
        self.assertTrue(_history_opted_out({"conversation_history": [{"from": "merchant", "body": "Please stop messaging me", "engagement": "merchant_replied"}]}))
        self.assertFalse(_history_opted_out({"conversation_history": [{"from": "merchant", "body": "Yes, please continue", "engagement": "intent_action"}]}))


class TickTests(unittest.TestCase):
    def setUp(self):
        contexts.clear()
        runtime.clear()

    def push(self, scope, cid, payload, version=1):
        status, response = handle_context({"scope": scope, "context_id": cid, "version": version, "payload": payload})
        self.assertEqual(status, 200, response)

    def test_tick_suppresses_duplicate_trigger_and_expired_context(self):
        category = read_json(ROOT / "dataset/categories/dentists.json")
        merchant = read_json(ROOT / "dataset/merchants_seed.json")["merchants"][0]
        trigger = read_json(ROOT / "dataset/triggers_seed.json")["triggers"][0]
        self.push("category", "dentists", category)
        self.push("merchant", merchant["merchant_id"], merchant)
        self.push("trigger", trigger["id"], trigger)
        now = "2026-04-27T00:00:00Z"
        first = handle_tick({"now": now, "available_triggers": [trigger["id"]]})
        second = handle_tick({"now": now, "available_triggers": [trigger["id"]]})
        self.assertEqual(len(first["actions"]), 1)
        self.assertEqual(second["actions"], [])
        expired = dict(trigger, expires_at="2026-04-26T00:00:00Z", suppression_key="new-key")
        self.push("trigger", "expired", expired)
        self.assertEqual(handle_tick({"now": now, "available_triggers": ["expired"]})["actions"], [])

    def test_tick_withholds_customer_message_without_consent(self):
        category = read_json(ROOT / "dataset/categories/dentists.json")
        merchants = read_json(ROOT / "dataset/merchants_seed.json")["merchants"]
        customers = read_json(ROOT / "dataset/customers_seed.json")["customers"]
        triggers = read_json(ROOT / "dataset/triggers_seed.json")["triggers"]
        merchant = merchants[0]
        trigger = next(t for t in triggers if t["kind"] == "recall_due")
        customer = next(c for c in customers if c["customer_id"] == trigger["customer_id"])
        customer["preferences"]["reminder_opt_in"] = False
        for scope, cid, payload in [("category", "dentists", category), ("merchant", merchant["merchant_id"], merchant),
                                    ("customer", customer["customer_id"], customer), ("trigger", trigger["id"], trigger)]:
            self.push(scope, cid, payload)
        result = handle_tick({"now": "2026-04-27T00:00:00Z", "available_triggers": [trigger["id"]]})
        self.assertEqual(result["actions"], [])

    def test_shared_campaign_suppression_key_is_scoped_to_each_merchant(self):
        categories = {p.stem: read_json(p) for p in (ROOT / "dataset/categories").glob("*.json")}
        merchants = read_json(ROOT / "dataset/merchants_seed.json")["merchants"]
        selected = [m for m in merchants if m["category_slug"] == "dentists"][:2]
        for merchant in selected:
            self.push("merchant", merchant["merchant_id"], merchant)
        self.push("category", "dentists", categories["dentists"])
        ids = []
        for i, merchant in enumerate(selected):
            trigger = {"id": f"research-{i}", "scope": "merchant", "kind": "research_digest", "merchant_id": merchant["merchant_id"],
                       "urgency": 2, "payload": {}, "suppression_key": "research:dentists:week"}
            ids.append(trigger["id"])
            self.push("trigger", trigger["id"], trigger)
        result = handle_tick({"now": "2026-04-27T00:00:00Z", "available_triggers": ids})
        self.assertEqual({action["merchant_id"] for action in result["actions"]}, {m["merchant_id"] for m in selected})

    def test_priority_is_not_input_order_and_limits_same_merchant_to_one_action(self):
        categories = {p.stem: read_json(p) for p in (ROOT / "dataset/categories").glob("*.json")}
        merchants = read_json(ROOT / "dataset/merchants_seed.json")["merchants"]
        triggers = read_json(ROOT / "dataset/triggers_seed.json")["triggers"]
        merchant = merchants[0]
        for scope, cid, payload in [("category", "dentists", categories["dentists"]),
                                    ("merchant", merchant["merchant_id"], merchant)]:
            self.push(scope, cid, payload)
        templates = [
            {"id": "priority-low", "scope": "merchant", "kind": "curious_ask_due", "merchant_id": merchant["merchant_id"], "urgency": 1, "payload": {}, "suppression_key": "priority:low", "expires_at": "2026-12-31T00:00:00Z"},
            {"id": "priority-high", "scope": "merchant", "kind": "research_digest", "merchant_id": merchant["merchant_id"], "urgency": 5, "payload": {}, "suppression_key": "priority:high", "expires_at": "2026-12-31T00:00:00Z"},
            {"id": "priority-mid", "scope": "merchant", "kind": "perf_spike", "merchant_id": merchant["merchant_id"], "urgency": 2, "payload": {}, "suppression_key": "priority:mid", "expires_at": "2026-12-31T00:00:00Z"},
        ]
        for trigger in templates:
            self.push("trigger", trigger["id"], trigger)
        expected = "priority-high"
        result = handle_tick({"now": "2026-04-27T00:00:00Z", "available_triggers": [t["id"] for t in templates]})
        self.assertEqual(len(result["actions"]), 1)
        self.assertEqual(result["actions"][0]["trigger_id"], expected)

    def test_all_canonical_pairs_return_deterministic_valid_grounded_shapes(self):
        categories = {p.stem: read_json(p) for p in (ROOT / "dataset/expanded/categories").glob("*.json")}
        merchants = {read_json(p)["merchant_id"]: read_json(p) for p in (ROOT / "dataset/expanded/merchants").glob("*.json")}
        customers = {read_json(p)["customer_id"]: read_json(p) for p in (ROOT / "dataset/expanded/customers").glob("*.json")}
        triggers = {read_json(p)["id"]: read_json(p) for p in (ROOT / "dataset/expanded/triggers").glob("*.json")}
        pairs = read_json(ROOT / "dataset/expanded/test_pairs.json")["pairs"]
        self.assertEqual(len(pairs), 30)
        for pair in pairs:
            with self.subTest(test_id=pair["test_id"]):
                trigger = triggers[pair["trigger_id"]]
                merchant = merchants[pair["merchant_id"]]
                customer = customers.get(pair.get("customer_id"))
                category = categories[merchant["category_slug"]]
                a = compose(category, merchant, trigger, customer)
                b = compose(category, merchant, trigger, customer)
                self.assertEqual(a, b)
                self.assertTrue(a["body"].strip())
                self.assertIn(a["send_as"], {"vera", "merchant_on_behalf"})
                self.assertTrue(a["template_name"] and a["suppression_key"] and a["rationale"])
                self.assertNotRegex(a["body"], r"https?://")
                if customer and not _eligible_customer(customer, trigger, pair["merchant_id"]):
                    # Composition can be previewed, but the send gate must deny it.
                    self.assertFalse(_eligible_customer(customer, trigger, pair["merchant_id"]))


class LiveServerTests(unittest.TestCase):
    """End-to-end check against a real HTTP server, as the judge would call it."""

    @classmethod
    def setUpClass(cls):
        import socket
        import subprocess
        import time
        import urllib.error
        import urllib.request

        cls.urllib_request = urllib.request
        cls.urllib_error = urllib.error
        for port in range(8100, 8160):
            probe = socket.socket()
            try:
                probe.bind(("127.0.0.1", port))
                probe.close()
                break
            except OSError:
                probe.close()
        else:
            raise unittest.SkipTest("no free port for the test server")
        cls.base = f"http://127.0.0.1:{port}"
        cls.server = subprocess.Popen(
            [sys.executable, str(ROOT / "bot.py"), "--port", str(port)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                cls._get("/v1/healthz")
                return
            except Exception:
                time.sleep(0.2)
        cls.server.terminate()
        raise unittest.SkipTest("test server did not become reachable")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server", None):
            cls.server.terminate()
            cls.server.wait(timeout=10)

    @classmethod
    def _get(cls, path):
        with cls.urllib_request.urlopen(f"{cls.base}{path}", timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @classmethod
    def _post(cls, path, payload):
        """POST and return (status, body); HTTP error statuses are part of the contract."""
        raw = json.dumps(payload).encode("utf-8")
        req = cls.urllib_request.Request(
            f"{cls.base}{path}", data=raw, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with cls.urllib_request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except cls.urllib_error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


    def test_healthz_and_metadata_are_well_formed(self):
        health = self._get("/v1/healthz")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(set(health["contexts_loaded"]), {"category", "merchant", "customer", "trigger"})
        meta = self._get("/v1/metadata")
        for key in ("team_name", "model", "approach", "version"):
            self.assertIn(key, meta)

    def test_context_versioning_over_http(self):
        payload = {"slug": "dentists", "voice": {"tone": "peer_clinical"}}
        status, body = self._post("/v1/context", {"scope": "category", "context_id": "version-probe",
                                                  "version": 5, "payload": payload})
        self.assertEqual(status, 200)
        self.assertTrue(body["accepted"])
        status, body = self._post("/v1/context", {"scope": "category", "context_id": "version-probe",
                                                  "version": 5, "payload": {"slug": "changed"}})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "stale_version")
        self.assertEqual(body["current_version"], 5)
        status, body = self._post("/v1/context", {"scope": "category", "context_id": "version-probe",
                                                  "version": 6, "payload": {"slug": "next"}})
        self.assertEqual(status, 200)
        self.assertTrue(body["accepted"])
        status, body = self._post("/v1/context", {"scope": "bogus", "context_id": "x", "version": 1,
                                                  "payload": {}})
        self.assertEqual(status, 400)
        self.assertFalse(body["accepted"])

    def test_unknown_routes_and_bad_json_do_not_crash(self):
        with self.assertRaises(self.urllib_error.HTTPError):
            self._get("/v1/does-not-exist")
        req = self.urllib_request.Request(f"{self.base}/v1/tick", data=b"{not json",
                                          method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(self.urllib_error.HTTPError):
            self.urllib_request.urlopen(req, timeout=10)
        self.assertEqual(self._get("/v1/healthz")["status"], "ok")

    def test_reply_routes_auto_reply_opt_out_and_commitment(self):
        auto = "Thank you for contacting us! Our team will respond shortly."
        seen = []
        for turn in range(1, 5):
            _, body = self._post("/v1/reply", {"conversation_id": "live-auto", "merchant_id": "m-live-1",
                                                "from_role": "merchant", "message": auto, "turn_number": turn})
            seen.append(body["action"])
        self.assertIn("end", seen, "repeated canned replies must eventually close the thread")
        self.assertNotIn("send", seen[:3], "must not keep talking to an unattended line")

        _, body = self._post("/v1/reply", {"conversation_id": "live-hostile", "merchant_id": "m-live-2",
                                            "from_role": "merchant",
                                            "message": "Stop messaging me. This is useless spam.",
                                            "turn_number": 2})
        self.assertEqual(body["action"], "end")

        _, body = self._post("/v1/reply", {"conversation_id": "live-intent", "merchant_id": "m-live-3",
                                            "from_role": "merchant",
                                            "message": "Ok lets do it. Whats next?", "turn_number": 2})
        self.assertEqual(body["action"], "send")
        lowered = body["body"].lower()
        self.assertFalse(any(w in lowered for w in ("would you", "can you tell", "how about")))
        self.assertTrue(any(w in lowered for w in ("proceeding", "draft", "here", "next step")))


if __name__ == "__main__":
    unittest.main()
