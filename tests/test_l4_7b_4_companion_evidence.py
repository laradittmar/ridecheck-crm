"""L4.7B.4 — companion evidence: a fact and the relation that explains it travel together.

L4.7B.3 closed nine of the ten gate lines. The tenth failed in two groups for one reason:
the interpreter emitted the value and dropped its companion — the `corrections[]` entry
beside a corrected value, the process fact beside a stance. These tests pin the contract and
the deterministic derivations that now guarantee it, with a stub transport: no network.

Nothing here grants the shadow any authority. It still proposes; reconciliation still owns
every mutation.

COMP-01  a corrected value carries its correction item
COMP-02  a year correction keeps from/to
COMP-03  a vehicle replacement carries the relation
COMP-04  no antecedent → no invented correction
COMP-05  FUTURE_INTENT and SEARCHING_NOT_READY coexist when both are supported
COMP-06  FUTURE_INTENT alone is valid
COMP-07  SEARCHING_NOT_READY alone is valid
COMP-08  HESITATE stays distinct from FUTURE_INTENT
COMP-09  companion items survive mapper sanitation
COMP-10  no false ACCEPT regression
COMP-11  quote_request stays strict
COMP-12  all 162 corpus cases remain evaluable
"""
from __future__ import annotations

import json
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from app.schemas.turn_evidence import (  # noqa: E402
    AcceptanceSignal,
    AmbiguityNote,
    ConflictNote,
    CorrectionEvidence,
    CorrectionRelation,
    EvidenceStatus,
    TurnEvidence,
    VehicleEvidence,
)
from app.services.semantic_interpreter import (  # noqa: E402
    PROMPT_VERSION,
    READINESS_VALUES,
    SemanticTurnInterpreter,
)
from semantic_corpus.evaluation import load_corpus  # noqa: E402

EMPTY = {"service_intents": [], "vehicles": [], "locations": [], "faq_topics": [],
         "acceptance": None, "scheduling_requests": [], "corrections": [], "identity": [],
         "handoff": None, "ambiguities": [], "conflicts": [], "notes": []}


def interpret(payload, messages=("...",)):
    interp = SemanticTurnInterpreter(
        SimpleNamespace(openai_api_key="sk-stub", openai_chat_model="gpt-4o-mini"),
        transport=lambda msgs, model: (json.dumps({**EMPTY, **payload}, ensure_ascii=False), {}))
    return interp.interpret(list(messages)).evidence


def by_field(evidence):
    return {item.field: item for _ref, item in evidence.iter_items()}


# ── corrections ───────────────────────────────────────────────────────────────

class TestCorrectionCompanion(unittest.TestCase):

    def test_comp_01_corrected_value_carries_its_correction(self):
        """A superseded vehicle IS a replacement: the relation is recorded either way."""
        evidence = interpret({"vehicles": [
            {"make": "Ford", "model": "Kuga", "is_superseded": False, "status": "CONFIRMED"},
            {"make": "Ford", "model": "Ka", "is_superseded": True, "status": "CONFIRMED"}]},
            ["Es un Ford Ka... no, perdón, es un Ford Kuga"])
        self.assertEqual(len(evidence.corrections), 1, "the relation must not be lost")
        correction = evidence.corrections[0]
        self.assertEqual(correction.relation, CorrectionRelation.REPLACE_CANDIDATE)
        self.assertEqual(correction.from_value, "Ford Ka")
        self.assertEqual(correction.to_value, "Ford Kuga")

    def test_comp_02_year_correction_keeps_from_and_to(self):
        evidence = interpret({"corrections": [
            {"relation": "CORRECT_EXISTING", "from_value": 2014, "to_value": 2015,
             "status": "CONFIRMED"}]}, ["Es del 2015 no del 2014"])
        correction = evidence.corrections[0]
        self.assertEqual((correction.from_value, correction.to_value), (2014, 2015))
        # …and the corrected value itself becomes evidence, not only the relation
        self.assertEqual(evidence.vehicle_mentions[0].year, 2015)

    def test_comp_03_replacement_emits_the_relation(self):
        """Even when the discarded car was never named, the replacement happened."""
        evidence = interpret({
            "corrections": [{"relation": "REPLACE_CANDIDATE", "to_value": "Volkswagen Amarok",
                             "status": "CONFIRMED"}],
            "vehicles": [{"make": "Volkswagen", "model": "Amarok", "status": "CONFIRMED"}]},
            ["Cambié de auto, ahora es una Amarok"])
        self.assertEqual(len(evidence.corrections), 1)
        self.assertEqual(evidence.corrections[0].relation,
                         CorrectionRelation.REPLACE_CANDIDATE)

    def test_comp_04_no_antecedent_no_invented_correction(self):
        """A plain first mention must not grow a correction out of nothing."""
        evidence = interpret({"vehicles": [{"make": "Toyota", "model": "Corolla",
                                            "year": 2019, "status": "CONFIRMED"}]},
                             ["Toyota Corolla 2019"])
        self.assertEqual(evidence.corrections, ())
        self.assertEqual(len(evidence.vehicle_mentions), 1)

    def test_a_year_already_present_is_not_duplicated(self):
        evidence = interpret({
            "corrections": [{"relation": "CORRECT_EXISTING", "from_value": 2014,
                             "to_value": 2015, "status": "CONFIRMED"}],
            "vehicles": [{"make": "Ford", "model": "Focus", "year": 2015,
                          "status": "CONFIRMED"}]}, ["el Focus es del 2015, no del 2014"])
        self.assertEqual(len(evidence.vehicle_mentions), 1)


# ── stance and the process fact ───────────────────────────────────────────────

class TestReadinessCompanion(unittest.TestCase):

    def test_comp_05_future_intent_and_searching_coexist(self):
        evidence = interpret({
            "service_intents": [{"kind": "READINESS", "value": "SEARCHING_NOT_READY",
                                 "status": "CONFIRMED"}],
            "acceptance": {"signal": "FUTURE_INTENT", "status": "CONFIRMED"}},
            ["Por ahora estoy buscando un auto, después aviso"])
        fields = by_field(evidence)
        self.assertEqual(fields["readiness"].value, "SEARCHING_NOT_READY")
        self.assertEqual(evidence.acceptance.signal, AcceptanceSignal.FUTURE_INTENT)

    def test_comp_06_future_intent_alone_is_valid(self):
        evidence = interpret({"acceptance": {"signal": "FUTURE_INTENT",
                                             "status": "CONFIRMED"}},
                             ["si me cierra te escribo"])
        self.assertEqual(evidence.acceptance.signal, AcceptanceSignal.FUTURE_INTENT)
        self.assertNotIn("readiness", by_field(evidence),
                         "delay language alone is not a search phase")

    def test_comp_07_searching_alone_is_valid(self):
        evidence = interpret({"service_intents": [{"kind": "READINESS",
                                                   "value": "SEARCHING_NOT_READY",
                                                   "status": "CONFIRMED"}]},
                             ["Aún no elegí el auto"])
        self.assertEqual(by_field(evidence)["readiness"].value, "SEARCHING_NOT_READY")
        self.assertIsNone(evidence.acceptance)

    def test_comp_08_hesitate_is_not_future_intent(self):
        hesitate = interpret({"acceptance": {"signal": "HESITATE", "status": "CONFIRMED"}},
                             ["lo voy a pensar"])
        future = interpret({"acceptance": {"signal": "FUTURE_INTENT", "status": "CONFIRMED"}},
                           ["después te aviso"])
        self.assertEqual(hesitate.acceptance.signal, AcceptanceSignal.HESITATE)
        self.assertEqual(future.acceptance.signal, AcceptanceSignal.FUTURE_INTENT)
        self.assertNotEqual(hesitate.acceptance.signal, future.acceptance.signal)

    def test_readiness_slot_and_array_entry_mean_the_same(self):
        from_slot = interpret({"readiness": {"value": "SEARCHING_NOT_READY",
                                             "status": "CONFIRMED"}}, ["estoy mirando"])
        from_array = interpret({"service_intents": [{"kind": "READINESS",
                                                     "value": "SEARCHING_NOT_READY",
                                                     "status": "CONFIRMED"}]},
                               ["estoy mirando"])
        self.assertEqual(by_field(from_slot)["readiness"].value,
                         by_field(from_array)["readiness"].value)
        self.assertEqual(len([i for _r, i in from_slot.iter_items()
                              if i.field == "readiness"]), 1, "never emitted twice")
        self.assertEqual(READINESS_VALUES, ("SEARCHING_NOT_READY",))


# ── sanitation must not eat the companions ────────────────────────────────────

class TestMapperPreservation(unittest.TestCase):

    def test_comp_09_companion_items_survive_sanitation(self):
        """A relation, an ambiguity, a conflict and a catalog suggestion all carry meaning."""
        relation_only = CorrectionEvidence(relation=CorrectionRelation.REPLACE_CANDIDATE)
        self.assertFalse(relation_only.is_semantically_empty(),
                         "the relation is evidence even with no from/to")
        catalog_only = VehicleEvidence(status=EvidenceStatus.AMBIGUOUS,
                                       catalog_candidate="Volkswagen Fox",
                                       reason="model-only mention")
        self.assertFalse(catalog_only.is_semantically_empty())

        turn = TurnEvidence(
            corrections=(relation_only,),
            vehicle_mentions=(catalog_only,),
            ambiguities=(AmbiguityNote(field="vehicle_year", reason="two numbers"),),
            conflicts=(ConflictNote(field="inspection_location", reason="two localities"),))
        pruned = turn.without_empty_items()
        self.assertEqual(len(pruned.corrections), 1)
        self.assertEqual(len(pruned.vehicle_mentions), 1)
        self.assertEqual(len(pruned.ambiguities), 1, "notes are never pruned")
        self.assertEqual(len(pruned.conflicts), 1)

    def test_empty_item_sanitation_is_not_weakened(self):
        """The Phase-A guarantee still holds: a row with nothing in it is still dropped."""
        evidence = interpret({
            "corrections": [{"relation": "UNKNOWN_RELATION", "from_value": None,
                             "to_value": None}],
            "vehicles": [{"make": None, "model": None, "year": None}],
            "scheduling_requests": [{"priority": "PRIMARY", "day_expression": None,
                                     "time": None, "rank": 1}]}, ["hola"])
        self.assertEqual(evidence.corrections, ())
        self.assertEqual(evidence.vehicle_mentions, ())
        self.assertEqual(evidence.scheduling_requests, ())


# ── the guarantees that must not regress ──────────────────────────────────────

class TestNoRegression(unittest.TestCase):

    def test_comp_10_no_false_accept(self):
        for signal in ("HESITATE", "FUTURE_INTENT", "REJECT", "QUESTION_ONLY"):
            evidence = interpret({"acceptance": {"signal": signal, "status": "CONFIRMED"}})
            self.assertNotEqual(evidence.acceptance.signal, AcceptanceSignal.ACCEPT)
            self.assertFalse(evidence.acceptance.value, signal)

    def test_comp_11_quote_request_stays_strict(self):
        asked = interpret({"service_intents": [{"kind": "QUOTE_REQUEST", "value": True,
                                                "status": "CONFIRMED"}]}, ["¿cuánto sale?"])
        self.assertEqual(by_field(asked)["quote_request"].value, True)
        not_asked = interpret({"service_intents": [
            {"kind": "INSPECTION", "value": "PREPURCHASE_INSPECTION", "status": "CONFIRMED"}]},
            ["¿ustedes hacen revisiones?"])
        self.assertNotIn("quote_request", by_field(not_asked))

    def test_comp_12_all_162_corpus_cases_evaluated(self):
        from semantic_corpus.corpus_mapping import (corpus_case_to_turn_evidence,
                                                    turn_evidence_to_harness_items)
        corpus = load_corpus()
        self.assertEqual(len(corpus), 162)
        for case in corpus:
            items = turn_evidence_to_harness_items(corpus_case_to_turn_evidence(case))
            self.assertIsInstance(items, list, case["id"])

    def test_prompt_version_and_model(self):
        self.assertEqual(PROMPT_VERSION, "understand/1.18")
        source = (ROOT / "backend" / "app" / "services" / "semantic_interpreter.py").read_text()
        self.assertIn('or "gpt-4o-mini"', source, "companion evidence, not a new model")


if __name__ == "__main__":
    unittest.main()
