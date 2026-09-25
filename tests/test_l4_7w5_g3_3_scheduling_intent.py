"""L4.7W5-GATE3-G3-3 — an availability enquiry finally has somewhere to live.

G3-2 proved the semantic contract could not represent "the customer is asking what
appointment availability exists". `SchedulingRequestEvidence` models a PROPOSED APPOINTMENT
and defines a row with no day and no time as the empty row, so the meaning was destroyed
rather than carried — and prompt rule S7 told the model, in as many words, not to emit one.
The only time-shaped slot left in the vocabulary was `faq_topic: business_hours`, which is
where the live message went.

This milestone adds the representation and traces it. It grants NO authority: the claim
reaches no reconciler, queries no service, writes no state and sends nothing. The trace must
say so honestly.

Test oracles here are literal expectations and the authored corpus file. Where a production
helper is exercised it is the SUBJECT of the assertion, never the source of the expected
value — the R1 and R2 reviews both failed on suites that let production compute their own
expectations.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas import turn_evidence as te                      # noqa: E402
from app.schemas.turn_evidence import (                          # noqa: E402
    BurstReconstruction,
    EvidenceStatus,
    SchedulingIntentEvidence,
    SchedulingIntentKind,
    SchedulingRequestEvidence,
    TurnEvidence,
)
from app.services import semantic_interpreter as si               # noqa: E402
from app.services.semantic_interpreter import SemanticTurnInterpreter  # noqa: E402

CORPUS = ROOT / "tests/semantic_corpus/scheduling_intent.jsonl"
#: The exact live text, held once. Evidence of meaning — never a matching rule.
LIVE_TEXT = "Que horarios tienen disponiblees"
#: Synthetic ids, shaped like real ones. A short placeholder slips past the masking regex
#: (`wamid\.[A-Za-z0-9+/=_-]{4,}`) and would have proved the sweep works when it had not run.
SYNTHETIC_WAMIDS = ("wamid.SYNTHETICCASEONE==", "wamid.SYNTHETICCASETWO==")


def corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def interpret(payload: dict, *, texts=(LIVE_TEXT,)) -> TurnEvidence:
    """Run the real parser over a model payload. No provider call, no network."""
    engine = SemanticTurnInterpreter.__new__(SemanticTurnInterpreter)
    return SemanticTurnInterpreter._to_turn_evidence(
        engine, payload, model="test-model", thread_id=2053, burst_id="burst-1",
        message_ids=SYNTHETIC_WAMIDS,
        reconstruction=BurstReconstruction.LIVE_DEBOUNCE, texts=texts)


AVAILABILITY_PAYLOAD = {"scheduling_intents": [
    {"kind": "AVAILABILITY_ENQUIRY", "status": "PROPOSED"}]}


class Representation(unittest.TestCase):
    """1, 2, 5, 6 — the meaning exists, and the neighbouring meanings did not move."""

    def test_g33_01_the_live_message_is_representable_as_an_availability_enquiry(self):
        evidence = interpret(AVAILABILITY_PAYLOAD).without_empty_items()
        self.assertEqual(len(evidence.scheduling_intents), 1)
        item = evidence.scheduling_intents[0]
        self.assertEqual(item.kind, SchedulingIntentKind.AVAILABILITY_ENQUIRY)
        self.assertEqual(item.field, "scheduling_intent")
        self.assertEqual(item.value, "AVAILABILITY_ENQUIRY")
        self.assertEqual(item.status, EvidenceStatus.PROPOSED)

    def test_g33_02_no_date_or_time_can_exist_on_the_type_at_all(self):
        """Structural, not guarded: there is nowhere to write an appointment."""
        fields = set(SchedulingIntentEvidence.model_fields)
        for forbidden in ("day_expression", "resolved_date", "time", "flexible_time",
                          "rank", "priority", "date", "slot", "slots"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, fields)

    def test_g33_03_it_is_not_a_faq_topic_at_the_contract_level(self):
        evidence = interpret(AVAILABILITY_PAYLOAD).without_empty_items()
        self.assertEqual(evidence.faq_intents, ())
        self.assertEqual(evidence.scheduling_requests, ())

    def test_g33_04_a_business_hours_question_is_still_a_faq_topic(self):
        evidence = interpret({"faq_topics": ["business_hours"]}).without_empty_items()
        self.assertEqual(len(evidence.faq_intents), 1)
        self.assertEqual(evidence.faq_intents[0].topic, "business_hours")
        self.assertEqual(evidence.scheduling_intents, ())

    def test_g33_05_a_concrete_proposal_is_still_a_scheduling_request(self):
        evidence = interpret({"scheduling_requests": [
            {"day_expression": "THURSDAY", "time": "15:00", "status": "CONFIRMED"}]})
        evidence = evidence.without_empty_items()
        self.assertEqual(len(evidence.scheduling_requests), 1)
        self.assertEqual(evidence.scheduling_requests[0].day_expression, "THURSDAY")
        self.assertEqual(evidence.scheduling_requests[0].time, "15:00")
        self.assertEqual(evidence.scheduling_intents, (), "a proposal is not an enquiry")

    def test_g33_06_a_slot_rejection_stays_in_its_own_family(self):
        evidence = interpret({"acceptance": {"signal": "REJECT", "status": "CONFIRMED"}})
        evidence = evidence.without_empty_items()
        self.assertIsNotNone(evidence.acceptance)
        self.assertEqual(evidence.acceptance.signal.value, "REJECT")
        self.assertEqual(evidence.scheduling_intents, ())

    def test_g33_07_an_enquiry_and_a_proposal_can_coexist_without_colliding(self):
        evidence = interpret({
            "scheduling_intents": [{"kind": "AVAILABILITY_ENQUIRY", "status": "CONFIRMED"}],
            "scheduling_requests": [{"day_expression": "SATURDAY", "status": "CONFIRMED"}]})
        self.assertEqual(evidence.refs(),
                         ("scheduling_requests[0]", "scheduling_intents[0]"))

    def test_g33_08_an_ambiguous_reading_is_representable_without_being_confirmed(self):
        evidence = interpret({"scheduling_intents": [
            {"kind": "AVAILABILITY_ENQUIRY", "status": "AMBIGUOUS", "reason": "sin contexto"}]})
        item = evidence.scheduling_intents[0]
        self.assertEqual(item.status, EvidenceStatus.AMBIGUOUS)
        self.assertIn(item.status, te.UNRESOLVED_STATUSES)


class FailsClosed(unittest.TestCase):
    """7, 8 — malformed output produces absence, never a coerced value."""

    MALFORMED = (
        ("unknown kind", [{"kind": "WHENEVER"}]),
        ("no kind at all", [{"status": "CONFIRMED"}]),
        ("not an object", ["AVAILABILITY_ENQUIRY"]),
        ("kind is a number", [{"kind": 7}]),
        ("kind is null", [{"kind": None}]),
        ("empty string kind", [{"kind": ""}]),
        ("collection is null", None),
        ("collection is a string", "AVAILABILITY_ENQUIRY"),
    )

    def test_g33_09_malformed_intents_are_dropped_not_coerced(self):
        for label, payload in self.MALFORMED:
            with self.subTest(case=label):
                self.assertEqual(interpret({"scheduling_intents": payload}).scheduling_intents,
                                 (), f"{label} produced an intent")

    def test_g33_10_a_smuggled_date_or_time_is_refused_by_the_contract(self):
        for field, value in (("resolved_date", "2026-09-26"), ("time", "13:00"),
                             ("day_expression", "SATURDAY"), ("slot", "13:00")):
            with self.subTest(field=field):
                with self.assertRaises(Exception):
                    SchedulingIntentEvidence(kind=SchedulingIntentKind.AVAILABILITY_ENQUIRY,
                                             **{field: value})

    def test_g33_11_the_parser_ignores_smuggled_fields_entirely(self):
        evidence = interpret({"scheduling_intents": [{
            "kind": "AVAILABILITY_ENQUIRY", "resolved_date": "2026-09-26",
            "time": "13:00", "day_expression": "SATURDAY"}]})
        dumped = evidence.scheduling_intents[0].model_dump()
        for forbidden in ("resolved_date", "time", "day_expression"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, dumped)
        self.assertNotIn("2026-09-26", json.dumps(dumped))
        self.assertNotIn("13:00", json.dumps(dumped))

    def test_g33_12_sanitisation_keeps_the_valid_item_and_drops_the_empty_one(self):
        evidence = te.TurnEvidence(scheduling_intents=(
            te.SchedulingIntentEvidence(kind=te.SchedulingIntentKind.AVAILABILITY_ENQUIRY,
                                        value="AVAILABILITY_ENQUIRY"),
            te.SchedulingIntentEvidence()))
        self.assertEqual(len(evidence.scheduling_intents), 2)
        self.assertEqual(len(evidence.without_empty_items().scheduling_intents), 1)


class ProvenanceAndBurst(unittest.TestCase):
    """9, 10 — who said it, in which burst, in what order."""

    def setUp(self):
        self.evidence = interpret(AVAILABILITY_PAYLOAD)
        self.item = self.evidence.scheduling_intents[0]

    def test_g33_13_provenance_names_the_interpreter_and_its_version(self):
        self.assertEqual(self.item.provenance.interpreter,
                         "semantic:understand:understand/1.19")
        self.assertEqual(self.item.provenance.source_kind.value, "SEMANTIC")
        self.assertEqual(self.item.provenance.model_version, "test-model")

    def test_g33_14_ordered_source_message_ids_survive_on_the_item(self):
        self.assertEqual(self.item.provenance.source_message_ids,
                         ("wamid.SYNTHETICCASEONE==", "wamid.SYNTHETICCASETWO=="))

    def test_g33_15_burst_reconstruction_is_exact(self):
        self.assertEqual(self.evidence.turn.ordered_message_ids,
                         ("wamid.SYNTHETICCASEONE==", "wamid.SYNTHETICCASETWO=="))
        self.assertEqual(self.evidence.turn.burst_id, "burst-1")
        self.assertEqual(self.evidence.turn.thread_id, 2053)
        self.assertEqual(self.evidence.turn.reconstruction,
                         BurstReconstruction.LIVE_DEBOUNCE)


class TraceAndInspector(unittest.TestCase):
    """11, 12, 13 — persisted, displayed, and honestly labelled NOT ROUTED."""

    def semantic_payload(self) -> dict:
        from app.services.hybrid_trace import semantic_evidence_from
        evidence = interpret(AVAILABILITY_PAYLOAD).without_empty_items()
        result = type("R", (), {"ok": True, "evidence": evidence, "model": "test-model",
                                "prompt_version": "understand/1.19",
                                "schema_version": "turn-evidence/1.3",
                                "latency_ms": 10, "total_tokens": 20})()
        provider = type("P", (), {"ready": True, "get": lambda self, t: result})()
        import dataclasses
        return dataclasses.asdict(semantic_evidence_from(provider, dispatch="async"))

    def test_g33_16_the_trace_records_the_claim_family(self):
        payload = self.semantic_payload()
        self.assertEqual(payload["status"], "OK")
        self.assertIn("scheduling_intents", payload["produced_claims"])

    def test_g33_17_the_trace_carries_the_item_and_its_provenance(self):
        payload = self.semantic_payload()
        item = payload["evidence"]["scheduling_intents"][0]
        self.assertEqual(item["kind"], "AVAILABILITY_ENQUIRY")
        self.assertEqual(item["provenance"]["source_kind"], "SEMANTIC")
        self.assertEqual(item["provenance"]["interpreter"],
                         "semantic:understand:understand/1.19")

    def test_g33_18_the_inspector_displays_it_as_semantic_evidence(self):
        from app.ui.hybrid_trace_view import _semantic_card
        card = _semantic_card(self.semantic_payload())
        self.assertIn("scheduling_intents", card)
        self.assertIn("AVAILABILITY_ENQUIRY", card)

    def test_g33_18b_the_rendered_page_masks_the_wamids_the_new_family_carries(self):
        """Masking is a PAGE-level sweep, because provenance ids ride inside the evidence
        JSON. Asserted on the real surface an operator sees, not on the card alone."""
        from app.ui.hybrid_trace_view import render_turn_trace_page
        page = render_turn_trace_page({
            "captured": True, "turn_id": "burst-1",
            "trace": {"trace_version": "hybrid-decision-trace/1.3", "turn_id": "burst-1",
                      "thread_id": 2053, "message_count": 1,
                      "ordered_message_ids": ["wamid.SYNTHETICCASEONE==", "wamid.SYNTHETICCASETWO=="],
                      "message_timestamps": ["2026-09-24T12:27:07+00:00"],
                      "semantic": self.semantic_payload(), "ce_evidence": [],
                      "reconciliation": [], "canonical_before": {}, "canonical_after": {},
                      "badges": [], "result_kind": "BLOCKED"}}, "burst-1")
        self.assertIn("scheduling_intents", page)
        self.assertIn("AVAILABILITY_ENQUIRY", page)
        self.assertNotIn("wamid.SYNTHETICCASEONE==", page, "raw WAMIDs must stay masked")
        self.assertNotIn("wamid.SYNTHETICCASETWO==", page)
        self.assertIn("wamid⋯", page,
                      "the masked fingerprint must be shown instead")

    def test_g33_19_the_trace_reports_it_as_not_routed(self):
        """Semantic produced a claim and no reconciler received it. That is NOT_ROUTED."""
        from app.services.hybrid_trace import classify_row, not_routed_for
        self.assertEqual(classify_row((), semantic_available=True), "NOT_ROUTED")
        self.assertEqual(not_routed_for((), True), ("SEMANTIC",))

    def test_g33_20_the_family_is_consumed_only_by_the_g3_4_authority_site(self):
        """G3-3 asserted the engine did not mention the family at all, so routing could not
        drift in unnoticed. G3-4 is the milestone authorised to route it, so the guard now
        pins WHERE it may be read: the availability-authority decision, and nowhere that
        writes state. An unapproved consumer still fails here.
        """
        import ast, inspect
        from app.services import conversation_engine as engine_mod
        readers = set()
        source = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.unparse(node)
            if "AVAILABILITY_ENQUIRY" in body or "scheduling_intents" in body:
                readers.add(node.name)
        # G3-4-R2 made `_semantic_reading` the turn's single read; the named projection
        # remains. Any OTHER consumer still fails this guard.
        self.assertEqual(readers, {"_semantic_reading"},
                         "exactly one reader touches the family — the R2 single read")


class NoAuthority(unittest.TestCase):
    """14, 15, 16 — evidence only: no state, no booking, no extra model call."""

    def new_source(self) -> str:
        import inspect
        return (inspect.getsource(te.SchedulingIntentEvidence)
                + inspect.getsource(te.SchedulingIntentKind))

    def test_g33_21_the_representation_touches_no_orm_or_service(self):
        """Checked on the imports and calls, not on prose: the module docstring may name a
        service in order to say the schema must never call one."""
        module = (BACKEND / "app/schemas/turn_evidence.py").read_text(encoding="utf-8")
        imports = [l for l in module.splitlines()
                   if l.startswith(("import ", "from ")) and "__future__" not in l]
        self.assertEqual(imports, ["import json", "from enum import Enum",
                                   "from typing import Any, Iterator, Optional",
                                   "from pydantic import BaseModel, ConfigDict, Field"])
        for forbidden in ("ScheduleService(", "OutboundSafetyGate(", ".commit()",
                          "Session(", "session."):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, module)

    def test_g33_22_the_new_type_carries_no_booking_or_state_field(self):
        """Asserted on the FIELDS, not on prose: the docstring says the type cannot create
        a booking, and a text scan would fail on the word that says so."""
        fields = set(SchedulingIntentEvidence.model_fields)
        for forbidden in ("revision", "revision_id", "booking", "booked", "flow",
                          "flow_booking_token", "active_requested_date", "last_stage",
                          "needs_human", "stage", "state"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, fields)
        self.assertEqual(fields - set(te.EvidenceItem.model_fields), {"kind"},
                         "the type adds exactly one field to the base contract")

    def test_g33_23_no_provider_call_was_added(self):
        interpreter = (BACKEND / "app/services/semantic_interpreter.py").read_text(encoding="utf-8")
        engine = (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"self\._transport\(", interpreter)), 1)
        self.assertEqual(len(re.findall(r"self\._call_openai\(", engine)), 2)
        self.assertEqual(len(re.findall(r"self\._ai_invoked = True", engine)), 1)

    def test_g33_24_parsing_is_a_pure_function_of_the_payload(self):
        first = interpret(AVAILABILITY_PAYLOAD).model_dump(mode="json")
        second = interpret(AVAILABILITY_PAYLOAD).model_dump(mode="json")
        self.assertEqual(first, second)


class ContractVersioning(unittest.TestCase):
    """17 — additive, and existing consumers are untouched."""

    def test_g33_25_the_schema_version_was_bumped(self):
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")
        self.assertEqual(si.PROMPT_VERSION, "understand/1.19")

    def test_g33_26_scheduling_request_evidence_is_unchanged(self):
        self.assertEqual(SchedulingRequestEvidence()._MEANINGFUL_FIELDS,
                         ("day_expression", "resolved_date", "time"))
        self.assertTrue(SchedulingRequestEvidence(flexible_time=True).is_semantically_empty())

    def test_g33_27_a_1_2_shaped_record_still_validates(self):
        """Nothing that validated before may stop validating: the change is additive."""
        # Every type is resolved through the live module: another suite reloads this module,
        # and a class captured at import time would no longer be the one TurnEvidence expects.
        legacy = te.TurnEvidence(
            schema_version="turn-evidence/1.2",
            faq_intents=(te.FaqIntentEvidence(topic="business_hours", value="business_hours"),),
            scheduling_requests=(te.SchedulingRequestEvidence(day_expression="THURSDAY"),))
        self.assertEqual(legacy.scheduling_intents, ())
        self.assertEqual(legacy.schema_version, "turn-evidence/1.2")
        self.assertEqual(len(legacy.refs()), 2)

    def test_g33_28_the_new_collection_is_iterated_and_sanitised(self):
        collections = TurnEvidence()._COLLECTIONS      # pydantic wraps it on the class
        self.assertIn("scheduling_intents", collections)
        self.assertGreater(collections.index("scheduling_intents"),
                           collections.index("scheduling_requests"),
                           "ref ordering must stay stable for existing families")
        self.assertEqual(collections[:5], (
            "service_intents", "vehicle_mentions", "location_mentions", "faq_intents",
            "scheduling_requests"), "existing families must keep their positions")


class Corpus(unittest.TestCase):
    """18 — the capability corpus, its provenance, and the general corpus unharmed."""

    def test_g33_29_the_live_case_is_present_with_full_provenance(self):
        live = [c for c in corpus() if c["origin"] == "live"]
        self.assertEqual(len(live), 1)
        case = live[0]
        self.assertEqual(case["id"], "SI-L01")
        self.assertEqual(case["text"], LIVE_TEXT)
        self.assertEqual(case["expected_intent"], "AVAILABILITY_ENQUIRY")
        self.assertIsNone(case["expected_faq_topic"])
        self.assertFalse(case["expects_day"])
        self.assertFalse(case["expects_time"])
        for key in ("milestone", "captured_at", "message_id", "thread_id", "burst_id",
                    "trace_id", "deployment_sha", "text_md5", "input_hash"):
            with self.subTest(key=key):
                self.assertIn(key, case["provenance"])

    def test_g33_30_the_recorded_md5_matches_the_recorded_text(self):
        import hashlib
        case = [c for c in corpus() if c["id"] == "SI-L01"][0]
        self.assertEqual(hashlib.md5(case["text"].encode("utf-8")).hexdigest(),
                         case["provenance"]["text_md5"])

    def test_g33_31_real_and_authored_provenance_are_distinguishable(self):
        for case in corpus():
            with self.subTest(case=case["id"]):
                self.assertIn(case["origin"], ("live", "authored"))
                self.assertEqual("provenance" in case, case["origin"] == "live")

    def test_g33_32_every_required_semantic_boundary_has_a_case(self):
        groups = {c["group"] for c in corpus()}
        self.assertEqual(groups, {
            "availability_enquiry_clean", "availability_enquiry_noisy", "business_hours",
            "concrete_proposal", "offered_slot_rejection", "duration",
            "time_without_request", "ambiguous"})

    def test_g33_33_no_availability_case_expects_a_day_or_a_time(self):
        for case in corpus():
            if case["expected_intent"] == "AVAILABILITY_ENQUIRY":
                with self.subTest(case=case["id"]):
                    self.assertFalse(case["expects_day"])
                    self.assertFalse(case["expects_time"])

    def test_g33_34_no_production_module_reads_this_corpus(self):
        for path in (BACKEND / "app").rglob("*.py"):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("scheduling_intent.jsonl", body)
                self.assertNotIn("disponiblees", body)

    def test_g33_35_the_general_corpus_integrity_is_untouched(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from semantic_corpus.integrity import audit_corpus
        self.assertEqual(audit_corpus(), [], "the general corpus instrument must stay clean")


class PromptContract(unittest.TestCase):
    """The S7 correction: the safety purpose kept, the meaning no longer destroyed."""

    def prompt(self) -> str:
        return si._SYSTEM_PROMPT

    def test_g33_36_s7_still_forbids_inventing_an_appointment(self):
        self.assertIn("NUNCA inventes un scheduling_requests", self.prompt())

    def test_g33_37_s7_now_redirects_instead_of_destroying(self):
        self.assertIn("scheduling_intents", self.prompt())
        self.assertIn("AVAILABILITY_ENQUIRY", self.prompt())

    def test_g33_38_the_business_hours_distinction_is_stated(self):
        self.assertIn("business_hours", self.prompt())
        self.assertIn("AGENDA", self.prompt())

    def test_g33_39_the_vocabulary_is_enumerated_not_the_phrasings(self):
        self.assertEqual(si.SCHEDULING_INTENT_VALUES, ("AVAILABILITY_ENQUIRY",))

    def test_g33_40_the_live_customer_text_is_not_in_the_prompt(self):
        self.assertNotIn(LIVE_TEXT, self.prompt())
        self.assertNotIn("disponiblees", self.prompt())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
