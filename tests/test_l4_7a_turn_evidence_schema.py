"""L4.7A — TurnEvidence schema and provenance contract.

Schema only: nothing here changes ConversationEngine, the prompt, the model or any runtime
path. The schema is proven against the full L4.7E corpus, so the later shadow UNDERSTAND
pass (L4.7B) has a contract that demonstrably represents real customer language.

SCHEMA-01  all 162 corpus cases parse/validate
SCHEMA-02  unknown stays unknown — no invented defaults, no faked confidence
SCHEMA-03  multiple evidence types coexist in one burst
SCHEMA-04  vehicle make/model/year/category representation, incl. model-only and supersession
SCHEMA-05  location role separation (inspection vs customer origin vs seller)
SCHEMA-06  ordered scheduling alternatives, primary and fallback never collapsed
SCHEMA-07  ambiguity preserves alternatives and picks no winner
SCHEMA-08  conflict preserves both sides
SCHEMA-09  provenance preserved (source ids, spans, interpreter, versions, reconstruction)
SCHEMA-10  schema version serialized; deterministic round-trip; version guard
SCHEMA-11  reconciliation disposition lives outside the interpretation and is append-only
SCHEMA-12  no business authority and no runtime import dependency
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.schemas.turn_evidence import (  # noqa: E402
    SCHEMA_VERSION,
    AcceptanceEvidence,
    AcceptanceSignal,
    Alternative,
    AmbiguityNote,
    BurstReconstruction,
    ConflictNote,
    CorrectionEvidence,
    CorrectionRelation,
    EvidenceStatus,
    FaqIntentEvidence,
    LocationEvidence,
    LocationRole,
    Provenance,
    ReconciliationLog,
    ReconciliationRecord,
    ReconciliationStatus,
    SchedulingPriority,
    SchedulingRequestEvidence,
    ServiceIntentEvidence,
    ServiceIntentKind,
    SourceKind,
    SourceSpan,
    TurnEvidence,
    TurnRef,
    VehicleEvidence,
)
from semantic_corpus.corpus_mapping import (  # noqa: E402
    corpus_case_to_turn_evidence,
    split_vehicle,
    turn_evidence_to_harness_items,
)
from semantic_corpus.evaluation import evaluate_case, load_corpus  # noqa: E402

CORPUS = load_corpus()


# ── SCHEMA-01 / SCHEMA-12(corpus) ─────────────────────────────────────────────

class TestCorpusCompatibility(unittest.TestCase):

    def test_schema_01_every_corpus_case_is_representable(self):
        for case in CORPUS:
            with self.subTest(case=case["id"]):
                evidence = corpus_case_to_turn_evidence(case)
                self.assertIsInstance(evidence, TurnEvidence)
                self.assertEqual(evidence.schema_version, SCHEMA_VERSION)

    def test_schema_01b_no_meaning_is_dropped(self):
        """Every expected corpus field reappears after mapping through the schema."""
        for case in CORPUS:
            expected_fields = {i["field"] for i in case["expected_turn_evidence"]}
            produced = {i["field"] for i in
                        turn_evidence_to_harness_items(corpus_case_to_turn_evidence(case))}
            # vehicle_superseded is carried as a vehicle mention flagged is_superseded
            expected_fields.discard("vehicle_superseded")
            missing = expected_fields - produced
            self.assertFalse(missing, f"{case['id']} lost {missing}")

    def test_schema_01c_round_trip_scores_clean_on_the_harness(self):
        """Mapping corpus truth → schema → harness reproduces the corpus exactly."""
        for case in CORPUS:
            with self.subTest(case=case["id"]):
                evidence = corpus_case_to_turn_evidence(case)
                produced = {"turn_evidence": turn_evidence_to_harness_items(evidence)}
                result = evaluate_case(case, produced)
                self.assertEqual(result.false_positives, 0, result.notes)
                self.assertEqual(result.false_negatives, 0, result.notes)
                self.assertEqual(result.unsupported_inferences, [], result.notes)

    def test_unknown_corpus_field_fails_loudly(self):
        bogus = dict(CORPUS[0])
        bogus = json.loads(json.dumps(bogus))
        bogus["expected_turn_evidence"] = [
            {"field": "invented_concept", "value": 1, "status": "CONFIRMED"}]
        with self.assertRaises(ValueError):
            corpus_case_to_turn_evidence(bogus)


# ── SCHEMA-02 ─────────────────────────────────────────────────────────────────

class TestUnknownStaysUnknown(unittest.TestCase):

    def test_schema_02_defaults_are_absent_not_invented(self):
        item = VehicleEvidence()
        self.assertIsNone(item.value)
        self.assertIsNone(item.make)
        self.assertIsNone(item.model)
        self.assertIsNone(item.year)
        self.assertIsNone(item.confidence, "confidence must never be faked")
        self.assertIsNone(item.category_suggestion)
        self.assertEqual(item.status, EvidenceStatus.PROPOSED)

    def test_schema_02b_empty_turn_is_legitimate(self):
        empty = TurnEvidence()
        self.assertTrue(empty.is_empty())
        self.assertEqual(empty.refs(), ())

    def test_schema_02c_unresolved_items_are_not_resolved(self):
        ambiguous = VehicleEvidence(status=EvidenceStatus.AMBIGUOUS)
        self.assertFalse(ambiguous.resolved)
        confirmed = VehicleEvidence(value="Peugeot 2008", status=EvidenceStatus.CONFIRMED)
        self.assertTrue(confirmed.resolved)


# ── SCHEMA-03 ─────────────────────────────────────────────────────────────────

class TestCoexistence(unittest.TestCase):

    def test_schema_03_faq_and_business_evidence_coexist(self):
        """The L4.6 FAQ-bypass class: an FAQ must never erase vehicle/location evidence."""
        case = next(c for c in CORPUS if c["id"] == "WILD-B-01")
        evidence = corpus_case_to_turn_evidence(case)
        self.assertTrue(evidence.faq_intents)
        self.assertTrue(evidence.vehicle_mentions)
        self.assertTrue(evidence.service_intents)
        self.assertGreaterEqual(len(evidence.refs()), 5)

    def test_schema_03b_all_categories_can_be_present_at_once(self):
        turn = TurnEvidence(
            service_intents=(ServiceIntentEvidence(kind=ServiceIntentKind.INSPECTION,
                                                   value="PREPURCHASE_INSPECTION"),),
            vehicle_mentions=(VehicleEvidence(make="Ford", model="Focus", year=2017),),
            location_mentions=(LocationEvidence(locality="Quilmes",
                                                role=LocationRole.INSPECTION_LOCATION.value),),
            faq_intents=(FaqIntentEvidence(topic="payment"),),
            acceptance=AcceptanceEvidence(signal=AcceptanceSignal.ACCEPT, value=True),
            scheduling_requests=(SchedulingRequestEvidence(day_expression="jueves",
                                                           time="13:00"),),
            corrections=(CorrectionEvidence(relation=CorrectionRelation.CORRECT_EXISTING),),
        )
        self.assertEqual(len(turn.refs()), 7)
        self.assertFalse(turn.is_empty())


# ── SCHEMA-04 ─────────────────────────────────────────────────────────────────

class TestVehicleEvidence(unittest.TestCase):

    def test_schema_04_model_and_year(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-B-01")
        vehicle = corpus_case_to_turn_evidence(case).vehicle_mentions[0]
        self.assertEqual(vehicle.make, "Peugeot")
        self.assertEqual(vehicle.model, "2008")
        self.assertEqual(vehicle.year, 2014)
        self.assertEqual(vehicle.status, EvidenceStatus.CONFIRMED)

    def test_schema_04b_model_only_mention(self):
        case = next(c for c in CORPUS if c["id"] == "REAL-003")
        vehicle = corpus_case_to_turn_evidence(case).vehicle_mentions[0]
        self.assertEqual(vehicle.model, "Fox")
        self.assertIsNone(vehicle.year, "no year was stated")
        self.assertEqual(vehicle.year_status, EvidenceStatus.AMBIGUOUS)
        self.assertEqual(vehicle.status, EvidenceStatus.PROPOSED)

    def test_schema_04c_multiple_mentions_and_supersession(self):
        case = next(c for c in CORPUS if c["id"] == "SYN-CORR-01")   # Ford Ka → Ford Kuga
        evidence = corpus_case_to_turn_evidence(case)
        self.assertEqual(len(evidence.vehicle_mentions), 2)
        superseded = [v for v in evidence.vehicle_mentions if v.is_superseded]
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0].value, "Ford Ka")
        self.assertEqual([v.mention_index for v in evidence.vehicle_mentions], [0, 1])

    def test_schema_04d_category_is_a_suggestion_and_alternatives_survive(self):
        vehicle = VehicleEvidence(
            value="2008", model="2008", category_suggestion="SUV_4X4_DEPORTIVO",
            status=EvidenceStatus.AMBIGUOUS,
            alternatives=(Alternative(value="Peugeot 2008", reason="catalog model"),
                          Alternative(value=2008, reason="manufacturing year")))
        self.assertEqual(len(vehicle.alternatives), 2)
        self.assertFalse(vehicle.resolved, "a suggestion is not canonical")

    def test_split_vehicle_helper(self):
        self.assertEqual(split_vehicle("Peugeot 2008"), ("Peugeot", "2008"))
        self.assertEqual(split_vehicle("Volkswagen Gol Trend"), ("Volkswagen", "Gol Trend"))
        self.assertEqual(split_vehicle(None), (None, None))


# ── SCHEMA-05 ─────────────────────────────────────────────────────────────────

class TestLocationRoles(unittest.TestCase):

    def test_schema_05_inspection_and_origin_are_separate(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-B-02")
        locations = corpus_case_to_turn_evidence(case).location_mentions
        by_role = {loc.role: loc.locality for loc in locations}
        self.assertEqual(by_role[LocationRole.INSPECTION_LOCATION.value], "Berazategui")
        self.assertEqual(by_role[LocationRole.CUSTOMER_ORIGIN.value], "Tigre")

    def test_schema_05b_role_does_not_depend_on_field_order(self):
        """LOC-04 states the origin first; roles must still be assigned correctly."""
        case = next(c for c in CORPUS if c["id"] == "SYN-LOC-04")
        locations = corpus_case_to_turn_evidence(case).location_mentions
        by_role = {loc.role: loc.locality for loc in locations}
        self.assertEqual(by_role[LocationRole.INSPECTION_LOCATION.value], "Berazategui")
        self.assertEqual(by_role[LocationRole.CUSTOMER_ORIGIN.value], "Tigre")

    def test_schema_05c_all_four_roles_exist(self):
        self.assertEqual(
            {r.value for r in LocationRole},
            {"INSPECTION_LOCATION", "CUSTOMER_ORIGIN", "SELLER_LOCATION",
             "UNKNOWN_LOCATION_ROLE"})

    def test_schema_05d_role_is_mandatory_on_location_evidence(self):
        self.assertEqual(LocationEvidence().role, LocationRole.UNKNOWN_LOCATION_ROLE.value)


# ── SCHEMA-06 ─────────────────────────────────────────────────────────────────

class TestOrderedScheduling(unittest.TestCase):

    def test_schema_06_primary_and_fallback_are_not_collapsed(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-A-04")   # "Mñ 15hs? O nose jueves"
        requests = corpus_case_to_turn_evidence(case).scheduling_requests
        self.assertEqual(len(requests), 2)
        primary, fallback = requests
        self.assertEqual(primary.priority, SchedulingPriority.PRIMARY)
        self.assertEqual(primary.day_expression, "TOMORROW")
        self.assertEqual(primary.time, "15:00")
        self.assertEqual(fallback.priority, SchedulingPriority.FALLBACK)
        self.assertEqual(fallback.day_expression, "THURSDAY")
        self.assertIsNone(fallback.time, "the fallback time must stay open")
        self.assertTrue(fallback.flexible_time)

    def test_schema_06b_time_never_migrates_between_branches(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-A-04")
        requests = corpus_case_to_turn_evidence(case).scheduling_requests
        times = [r.time for r in requests]
        self.assertEqual(times, ["15:00", None])

    def test_schema_06c_resolved_date_is_optional(self):
        request = SchedulingRequestEvidence(day_expression="mñ", time="15:00")
        self.assertIsNone(request.resolved_date,
                          "date arithmetic belongs to deterministic reconciliation")


# ── SCHEMA-06b acceptance / hesitation ────────────────────────────────────────

class TestAcceptanceSignals(unittest.TestCase):

    def test_acceptance_signals_are_distinct(self):
        # turn-evidence/1.1 (L4.7B.2 Phase E) adds FUTURE_INTENT: "te aviso cuando lo
        # compre" is neither acceptance nor rejection, and reading it as ACCEPT was a
        # measured L4.7B.1 disagreement. Additive; every 1.0 signal is unchanged.
        self.assertEqual(
            {s.value for s in AcceptanceSignal},
            {"ACCEPT", "REJECT", "HESITATE", "FUTURE_INTENT", "QUESTION_ONLY", "UNKNOWN"})

    def test_accept_case(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-A-03")
        self.assertEqual(corpus_case_to_turn_evidence(case).acceptance.signal,
                         AcceptanceSignal.ACCEPT)

    def test_hesitation_is_not_acceptance(self):
        case = next(c for c in CORPUS if c["id"] == "SYN-REJ-02")   # "Lo voy a pensar"
        evidence = corpus_case_to_turn_evidence(case)
        self.assertEqual(evidence.acceptance.signal, AcceptanceSignal.HESITATE)
        self.assertNotEqual(evidence.acceptance.signal, AcceptanceSignal.ACCEPT)


# ── SCHEMA-07 correction / replacement ────────────────────────────────────────

class TestCorrectionRelations(unittest.TestCase):

    def test_relations_available(self):
        self.assertEqual(
            {r.value for r in CorrectionRelation},
            {"CORRECT_EXISTING", "REPLACE_CANDIDATE", "SWITCH_TO_PRIOR_CANDIDATE",
             "ADD_SECOND_CANDIDATE", "UNKNOWN_RELATION"})

    def test_replacement_records_both_sides(self):
        case = next(c for c in CORPUS if c["id"] == "SYN-CORR-01")
        correction = corpus_case_to_turn_evidence(case).corrections[0]
        self.assertEqual(correction.relation, CorrectionRelation.REPLACE_CANDIDATE)
        self.assertEqual(correction.from_value, "Ford Ka")
        self.assertEqual(correction.to_value, "Ford Kuga")


# ── SCHEMA-07/08 ambiguity and conflict ───────────────────────────────────────

class TestAmbiguityAndConflict(unittest.TestCase):

    def test_schema_07_ambiguity_preserved_without_a_winner(self):
        case = next(c for c in CORPUS if c["id"] == "REAL-001")
        evidence = corpus_case_to_turn_evidence(case)
        self.assertTrue(evidence.ambiguities)
        fields = {a.field for a in evidence.ambiguities}
        self.assertIn("vehicle", fields)
        vehicle = next(v for v in evidence.vehicle_mentions)
        self.assertEqual(vehicle.status, EvidenceStatus.AMBIGUOUS)
        self.assertIsNone(vehicle.value, "an ambiguous item must carry no chosen value")

    def test_schema_07b_alternatives_survive(self):
        note = AmbiguityNote(field="vehicle", reason="model vs year",
                             alternatives=(Alternative(value="Peugeot 2008"),
                                           Alternative(value=2008)))
        self.assertEqual(len(note.alternatives), 2)

    def test_schema_08_conflict_preserves_both_sides(self):
        conflict = ConflictNote(
            field="inspection_location",
            sides=(Alternative(value="Berazategui", reason="stated first"),
                   Alternative(value="Quilmes", reason="stated later, contradicts")),
            reason="two locations, no resolving context")
        turn = TurnEvidence(conflicts=(conflict,))
        self.assertEqual(len(turn.conflicts[0].sides), 2)
        self.assertFalse(turn.is_empty())

    def test_schema_08b_conflict_status_is_unresolved(self):
        item = LocationEvidence(status=EvidenceStatus.CONFLICT, locality=None)
        self.assertFalse(item.resolved)


# ── SCHEMA-09 provenance ──────────────────────────────────────────────────────

class TestProvenance(unittest.TestCase):

    def test_schema_09_item_provenance_fields(self):
        prov = Provenance(
            source_kind=SourceKind.SEMANTIC, interpreter="semantic:gpt-4o-mini",
            model_version="2026-09-01", source_message_ids=("wamid.A", "wamid.B"),
            spans=(SourceSpan(message_id="wamid.A", start=6, end=22, excerpt="un 2008 del 2014"),))
        item = VehicleEvidence(value="Peugeot 2008", provenance=prov)
        self.assertEqual(item.provenance.schema_version, SCHEMA_VERSION)
        self.assertEqual(item.provenance.source_message_ids, ("wamid.A", "wamid.B"))
        self.assertEqual(item.provenance.spans[0].excerpt, "un 2008 del 2014")

    def test_schema_09b_turn_records_burst_reconstruction_method(self):
        """L4.7E found burst boundaries are only PARTIAL — the method must be recorded."""
        turn = TurnEvidence(turn=TurnRef(
            thread_id=2037, ordered_message_ids=("wamid.1", "wamid.2"),
            reconstruction=BurstReconstruction.REPLAY_CHRONOLOGICAL))
        self.assertEqual(turn.turn.reconstruction, BurstReconstruction.REPLAY_CHRONOLOGICAL)
        self.assertEqual(turn.turn.ordered_message_ids, ("wamid.1", "wamid.2"))

    def test_schema_09c_corpus_mapping_carries_provenance(self):
        evidence = corpus_case_to_turn_evidence(
            next(c for c in CORPUS if c["id"] == "WILD-A-04"))
        for _ref, item in evidence.iter_items():
            self.assertEqual(item.provenance.source_kind, SourceKind.SEMANTIC)
            self.assertTrue(item.provenance.source_message_ids)
        self.assertEqual(evidence.turn.reconstruction, BurstReconstruction.CORPUS_FIXTURE)

    def test_schema_09d_refs_are_stable_and_addressable(self):
        evidence = corpus_case_to_turn_evidence(
            next(c for c in CORPUS if c["id"] == "WILD-B-02"))
        refs = evidence.refs()
        self.assertIn("location_mentions[0]", refs)
        self.assertIsNotNone(evidence.item_at("location_mentions[0]"))
        self.assertIsNone(evidence.item_at("location_mentions[99]"))


# ── SCHEMA-10 serialization ───────────────────────────────────────────────────

class TestSerialization(unittest.TestCase):

    def test_schema_10_version_is_serialized(self):
        payload = json.loads(TurnEvidence().to_canonical_json())
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)

    def test_schema_10b_serialization_is_deterministic(self):
        evidence = corpus_case_to_turn_evidence(
            next(c for c in CORPUS if c["id"] == "WILD-A-04"))
        self.assertEqual(evidence.to_canonical_json(), evidence.to_canonical_json())
        keys = list(json.loads(evidence.to_canonical_json()).keys())
        self.assertEqual(keys, sorted(keys), "canonical JSON sorts keys")

    def test_schema_10c_round_trip_is_lossless(self):
        for cid in ("WILD-A-04", "WILD-B-01", "WILD-B-02", "REAL-004", "SYN-CORR-01"):
            with self.subTest(case=cid):
                original = corpus_case_to_turn_evidence(
                    next(c for c in CORPUS if c["id"] == cid))
                restored = TurnEvidence.from_json(original.to_canonical_json())
                self.assertEqual(restored.to_canonical_json(), original.to_canonical_json())

    def test_schema_10d_incompatible_major_version_is_rejected(self):
        payload = json.loads(TurnEvidence().to_canonical_json())
        payload["schema_version"] = "turn-evidence/2.0"
        with self.assertRaises(ValueError):
            TurnEvidence.from_json(payload)
        payload["schema_version"] = "something-else/1.0"
        with self.assertRaises(ValueError):
            TurnEvidence.from_json(payload)

    def test_schema_10e_unknown_fields_are_rejected(self):
        payload = json.loads(TurnEvidence().to_canonical_json())
        payload["free_for_all"] = {"anything": True}
        with self.assertRaises(Exception):
            TurnEvidence.from_json(payload)


# ── SCHEMA-11 reconciliation boundary ─────────────────────────────────────────

class TestReconciliationBoundary(unittest.TestCase):

    def test_schema_11_disposition_is_not_part_of_interpretation(self):
        fields = set(TurnEvidence.model_fields)
        for forbidden in ("reconciliation", "reconciliation_status", "canonical_state",
                          "accepted", "applied"):
            self.assertNotIn(forbidden, fields)
        item_fields = set(VehicleEvidence.model_fields)
        self.assertNotIn("reconciliation_status", item_fields)

    def test_schema_11b_interpretation_is_immutable(self):
        evidence = corpus_case_to_turn_evidence(
            next(c for c in CORPUS if c["id"] == "WILD-B-02"))
        with self.assertRaises(Exception):
            evidence.location_mentions[0].locality = "Tigre"      # type: ignore[misc]
        with self.assertRaises(Exception):
            evidence.interpreter = "someone else"                 # type: ignore[misc]

    def test_schema_11c_log_is_append_only_and_references_items(self):
        evidence = corpus_case_to_turn_evidence(
            next(c for c in CORPUS if c["id"] == "WILD-B-02"))
        ref = evidence.refs()[0]
        log = ReconciliationLog()
        log2 = log.append(ReconciliationRecord(
            evidence_ref=ref, status=ReconciliationStatus.ACCEPTED,
            decided_by="reconciler:zone_authority", canonical_value="Berazategui"))
        self.assertEqual(len(log.records), 0, "append returns a new log; nothing mutates")
        self.assertEqual(len(log2.records), 1)
        self.assertEqual(log2.for_ref(ref)[0].status, ReconciliationStatus.ACCEPTED)

    def test_schema_11d_all_dispositions_available(self):
        self.assertEqual(
            {s.value for s in ReconciliationStatus},
            {"ACCEPTED", "REJECTED", "DEFERRED", "NEEDS_CLARIFICATION",
             "CONFLICT_UNRESOLVED", "SUPERSEDED"})


# ── SCHEMA-12 no business authority ───────────────────────────────────────────

class TestNoBusinessAuthority(unittest.TestCase):

    SCHEMA_FILE = ROOT / "backend" / "app" / "schemas" / "turn_evidence.py"

    def test_schema_12_no_runtime_or_db_imports(self):
        """Inspect the import graph, not substrings — the schema must import nothing heavy."""
        import ast
        tree = ast.parse(self.SCHEMA_FILE.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertTrue(imported <= {"__future__", "json", "enum", "typing", "pydantic"},
                        f"unexpected imports: {sorted(imported)}")
        source = self.SCHEMA_FILE.read_text(encoding="utf-8")
        for forbidden in ("conversation_engine", "sqlalchemy", "app.db", "SessionLocal",
                          "PricingService", "ScheduleService", "OutboundSafetyGate",
                          "BookingFlowService", "openai."):
            self.assertNotIn(forbidden, source,
                             f"TurnEvidence must not reference {forbidden}")

    def test_schema_12b_no_mutating_verbs_in_the_contract(self):
        source = self.SCHEMA_FILE.read_text(encoding="utf-8")
        for forbidden in ("db.add(", "db.commit(", "session.", "INSERT ", "UPDATE "):
            self.assertNotIn(forbidden, source)

    def test_schema_12c_schema_cannot_reach_models(self):
        """Importing the schema must not pull in ORM models or engine machinery."""
        import importlib
        before = set(sys.modules)
        importlib.reload(importlib.import_module("app.schemas.turn_evidence"))
        newly = set(sys.modules) - before
        for name in newly:
            self.assertNotIn("conversation_engine", name)
            self.assertNotIn("app.models", name)

    def test_schema_12d_evidence_carries_no_canonical_write_path(self):
        """The typed evidence exposes values and provenance only — no apply/commit API."""
        for model in (TurnEvidence, VehicleEvidence, LocationEvidence,
                      SchedulingRequestEvidence, AcceptanceEvidence):
            for attr in dir(model):
                self.assertNotIn(attr, {"apply", "commit", "save", "persist", "write"},
                                 f"{model.__name__} exposes {attr}")


if __name__ == "__main__":
    unittest.main()
