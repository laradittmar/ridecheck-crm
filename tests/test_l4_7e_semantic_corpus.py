"""L4.7E — semantic corpus integrity + evaluation harness.

Two things are certified here:

1. **The corpus is well-formed and honest** — schema, unique ids, provenance, REAL vs
   SYNTHETIC marking, owner-provided text stored verbatim, valid statuses, supported
   `must_not_infer` contracts, and uncertainty left uncertain (`owner_review_required`).
2. **The harness measures meaning** — precision/recall, role accuracy, unsupported
   inference, ambiguity handling and missing-field accuracy behave correctly against
   deliberately good, lazy and hallucinating stub interpreters.

No OpenAI call, no database, no ConversationEngine import, no runtime behaviour.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from semantic_corpus.evaluation import (  # noqa: E402
    CORPUS_PATH,
    STATUS_AMBIGUOUS,
    STATUS_CONFLICT,
    VALID_STATUSES,
    evaluate,
    evaluate_case,
    load_corpus,
    normalize,
    reconstruct_burst,
    values_match,
)

CORPUS = load_corpus()

# The four owner-provided messages, verbatim. Changing a character here is a corpus edit
# and must be justified — these are real customer words, not fixtures.
OWNER_VERBATIM = {
    "REAL-001": "Hola por ahora estoy buscando un auto agende esto para no perderlo asijina vez q decida aviso",
    "REAL-002": "Ok. Lobveobyo primoroby si me vierra t hablobpara q lo revisen",
    "REAL-003": "Quiero comprar un fox y ver en qu3 estado esta en breves te voy a estar hablando si todo marcha bieb michas gracias",
    "REAL-004": "Hola qué tal! Te quería consultar cotización para ir a revisar un auto a La Plata , yo cuento con movilidad como para pasar a buscarlos y ir a chequear el auto allá y volver obviamente, espero tu msj!",
}

WILD_IDS = {"WILD-A-01", "WILD-A-02", "WILD-A-03", "WILD-A-04",
            "WILD-B-01", "WILD-B-02", "WILD-01-01", "WILD-01-02"}


# ── corpus integrity ──────────────────────────────────────────────────────────

class TestCorpusIntegrity(unittest.TestCase):

    def test_corpus_file_is_committed_jsonl(self):
        self.assertTrue(CORPUS_PATH.exists())
        self.assertGreater(len(CORPUS), 100)

    def test_unique_case_ids(self):
        ids = [c["id"] for c in CORPUS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_schema_of_every_case(self):
        for c in CORPUS:
            with self.subTest(case=c["id"]):
                self.assertIn("schema_version", c)
                self.assertIn("provenance", c)
                self.assertIn("kind", c["provenance"])
                self.assertIn("source", c["provenance"])
                self.assertIn("raw", c)
                self.assertIsInstance(c["raw"]["messages"], list)
                self.assertTrue(all(isinstance(m, str) and m.strip() for m in c["raw"]["messages"]))
                self.assertIsInstance(c["groups"], list)
                self.assertTrue(c["groups"])
                self.assertIsInstance(c["expected_turn_evidence"], list)
                self.assertIsInstance(c["expected_canonical_state"], dict)
                self.assertIsInstance(c["expected_missing_fields"], list)
                self.assertIsInstance(c["must_not_infer"], list)
                self.assertIsInstance(c["owner_review_required"], bool)
                self.assertTrue(c["expected_next_action"])

    def test_provenance_kinds_are_real_or_synthetic(self):
        for c in CORPUS:
            self.assertIn(c["provenance"]["kind"], {"REAL", "SYNTHETIC"}, c["id"])

    def test_real_and_synthetic_counts(self):
        real = [c for c in CORPUS if c["provenance"]["kind"] == "REAL"]
        synthetic = [c for c in CORPUS if c["provenance"]["kind"] == "SYNTHETIC"]
        self.assertEqual(len(real), 12)
        self.assertEqual(len(synthetic), len(CORPUS) - 12)
        self.assertEqual(len(real) + len(synthetic), len(CORPUS))

    def test_owner_examples_present_and_verbatim(self):
        by_id = {c["id"]: c for c in CORPUS}
        for cid, text in OWNER_VERBATIM.items():
            self.assertIn(cid, by_id, f"{cid} missing from corpus")
            self.assertEqual(by_id[cid]["raw"]["messages"][0], text,
                             f"{cid} raw text was altered — owner language is immutable")
            self.assertEqual(by_id[cid]["provenance"]["kind"], "REAL")

    def test_failed_wild_cases_imported_with_source_and_failure_class(self):
        by_id = {c["id"]: c for c in CORPUS}
        for cid in WILD_IDS:
            self.assertIn(cid, by_id, f"{cid} missing")
            self.assertEqual(by_id[cid]["provenance"]["kind"], "REAL")
            self.assertIn("Wild", by_id[cid]["provenance"]["source"])
        failing = [c for c in CORPUS if c.get("failure_class")]
        self.assertGreaterEqual(len(failing), 4,
                                "each known Wild failure must name the class it exposed")

    def test_synthetic_cases_are_marked_and_never_claim_to_be_real(self):
        for c in CORPUS:
            if c["provenance"]["kind"] == "SYNTHETIC":
                self.assertIn("authored", c["provenance"]["source"].lower(), c["id"])

    def test_all_statuses_valid(self):
        for c in CORPUS:
            for item in c["expected_turn_evidence"]:
                self.assertIn(item.get("status"), VALID_STATUSES, f"{c['id']}:{item.get('field')}")

    def test_expected_turn_evidence_items_well_formed(self):
        for c in CORPUS:
            for item in c["expected_turn_evidence"]:
                with self.subTest(case=c["id"], field=item.get("field")):
                    self.assertTrue(item.get("field"))
                    self.assertIn("value", item)
                    if item["status"] in {STATUS_AMBIGUOUS, STATUS_CONFLICT}:
                        self.assertIsNone(item["value"],
                                          "unresolved evidence must not carry a value")

    def test_canonical_state_never_contradicts_unresolved_evidence(self):
        for c in CORPUS:
            unresolved = {i["field"] for i in c["expected_turn_evidence"]
                          if i["status"] in {STATUS_AMBIGUOUS, STATUS_CONFLICT}}
            for name in unresolved:
                self.assertIn(c["expected_canonical_state"].get(name, None), (None, "", [], {}),
                              f"{c['id']}: {name} is unresolved but canonical state asserts it")

    def test_must_not_infer_entries_are_supported(self):
        for c in CORPUS:
            for rule in c["must_not_infer"]:
                with self.subTest(case=c["id"]):
                    self.assertTrue(rule.get("field"))
                    self.assertTrue(rule.get("reason"), "every prohibition states why")
                    # A forbidden value must not also be the expected canonical value.
                    if "value" in rule:
                        canonical = c["expected_canonical_state"].get(rule["field"])
                        self.assertFalse(values_match(rule["value"], canonical),
                                         f"{c['id']}: {rule['field']} both required and forbidden")

    def test_owner_review_required_only_where_truth_is_uncertain(self):
        flagged = [c["id"] for c in CORPUS if c["owner_review_required"]]
        self.assertIn("REAL-002", flagged, "noisy ASR-like message needs owner adjudication")
        self.assertIn("REAL-004", flagged, "mobility offer → pricing/coverage is a business call")
        for cid in flagged:
            case = next(c for c in CORPUS if c["id"] == cid)
            self.assertEqual(case["provenance"]["kind"], "REAL")

    def test_uncertain_real_language_is_not_forced_into_certainty(self):
        """REAL-001/002 must keep vehicle and location unresolved."""
        by_id = {c["id"]: c for c in CORPUS}
        for cid in ("REAL-001", "REAL-002"):
            evidence = {i["field"]: i for i in by_id[cid]["expected_turn_evidence"]}
            self.assertEqual(evidence["vehicle"]["status"], STATUS_AMBIGUOUS)
            self.assertIsNone(by_id[cid]["expected_canonical_state"].get("candidate"))

    def test_equivalence_groups_present(self):
        groups = {g for c in CORPUS for g in c["groups"]}
        self.assertEqual(groups, set("ABCDEFGHIJKL"))

    def test_core_groups_have_at_least_twenty_variants(self):
        for group in ("A", "B", "C", "E"):
            members = [c for c in CORPUS if group in c["groups"]]
            self.assertGreaterEqual(len(members), 20, f"group {group} too small")
        scheduling = [c for c in CORPUS if {"G", "H"} & set(c["groups"])]
        self.assertGreaterEqual(len(scheduling), 20, "scheduling variants too few")

    def test_no_pii_in_corpus(self):
        """No phone numbers, emails, WhatsApp ids or token material in the corpus."""
        import re
        blob = json.dumps(CORPUS, ensure_ascii=False)
        for label, pattern in (
            ("AR mobile number", r"\b54\s?9?\s?11\d{8}\b"),
            ("email address", r"[\w.+-]+@[\w-]+\.[\w.]+"),
            ("WhatsApp message id", r"\bwamid\."),
            ("Meta token", r"\bEAA[A-Za-z0-9]{10,}"),
        ):
            self.assertIsNone(re.search(pattern, blob), f"corpus must not contain a {label}")

    def test_semantic_equivalence_within_a_group(self):
        """Every location-role case maps a different surface form to the same contract."""
        loc = [c for c in CORPUS if "C" in c["groups"]]
        self.assertGreaterEqual(len(loc), 20)
        for c in loc:
            fields = {i["field"] for i in c["expected_turn_evidence"]}
            self.assertIn("inspection_location", fields, c["id"])
        origin_cases = [c for c in loc
                        if any(i["field"] == "customer_origin" for i in c["expected_turn_evidence"])]
        self.assertGreaterEqual(len(origin_cases), 5)
        for c in origin_cases:
            self.assertTrue(any(r["field"] == "inspection_location" for r in c["must_not_infer"]),
                            f"{c['id']}: origin must be forbidden as inspection location")


# ── harness behaviour ─────────────────────────────────────────────────────────

def _perfect(case: dict):
    """An interpreter that returns exactly the corpus truth for one case."""
    def interp(_messages):
        evidence = [dict(i) for i in case["expected_turn_evidence"]
                    if i["status"] not in {STATUS_AMBIGUOUS, STATUS_CONFLICT}]
        return {"turn_evidence": evidence, "canonical_state": {}}
    return interp


class TestHarnessMetrics(unittest.TestCase):

    def setUp(self):
        self.case = next(c for c in CORPUS if c["id"] == "WILD-B-02")   # Berazategui / Tigre

    def test_perfect_interpreter_scores_clean(self):
        result = evaluate_case(self.case, _perfect(self.case)(None))
        self.assertTrue(result.clean, result.notes)
        self.assertEqual(result.false_positives, 0)
        self.assertEqual(result.false_negatives, 0)
        self.assertEqual(result.role_correct, result.role_expected)
        self.assertEqual(result.unsupported_inferences, [])

    def test_role_error_is_detected(self):
        produced = {"turn_evidence": [
            {"field": "inspection_location", "value": "Berazategui", "status": "CONFIRMED",
             "role": "CUSTOMER_ORIGIN"},
            {"field": "customer_origin", "value": "Tigre", "status": "CONFIRMED",
             "role": "CUSTOMER_ORIGIN"},
        ]}
        result = evaluate_case(self.case, produced)
        self.assertLess(result.role_correct, result.role_expected)

    def test_unsupported_inference_is_caught(self):
        """The Wild B failure itself: Tigre proposed as the inspection location."""
        produced = {"turn_evidence": [
            {"field": "inspection_location", "value": "Tigre", "status": "CONFIRMED",
             "role": "INSPECTION_LOCATION"},
        ]}
        result = evaluate_case(self.case, produced)
        self.assertTrue(result.unsupported_inferences)
        self.assertFalse(result.clean)

    def test_missing_evidence_counts_as_recall_loss(self):
        result = evaluate_case(self.case, {"turn_evidence": []})
        self.assertEqual(result.true_positives, 0)
        self.assertGreater(result.false_negatives, 0)

    def test_invented_field_counts_as_precision_loss(self):
        produced = _perfect(self.case)(None)
        produced["turn_evidence"].append(
            {"field": "quote", "value": 240000, "status": "CONFIRMED"})
        result = evaluate_case(self.case, produced)
        self.assertGreater(result.false_positives, 0)

    def test_ambiguity_must_not_be_forced(self):
        case = next(c for c in CORPUS if c["id"] == "REAL-001")
        honoured = evaluate_case(case, {"turn_evidence": [
            {"field": "service_intent", "value": "PREPURCHASE_INSPECTION", "status": "PROPOSED"},
            {"field": "readiness", "value": "SEARCHING_NOT_READY", "status": "CONFIRMED"},
        ]})
        self.assertEqual(honoured.ambiguity_honoured, honoured.ambiguity_expected)

        forced = evaluate_case(case, {"turn_evidence": [
            {"field": "service_intent", "value": "PREPURCHASE_INSPECTION", "status": "PROPOSED"},
            {"field": "readiness", "value": "SEARCHING_NOT_READY", "status": "CONFIRMED"},
            {"field": "vehicle", "value": "Volkswagen Fox", "status": "CONFIRMED"},
        ]})
        self.assertLess(forced.ambiguity_honoured, forced.ambiguity_expected)
        self.assertTrue(forced.unsupported_inferences, "inventing a vehicle is forbidden here")

    def test_scheduling_order_is_meaning(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-A-04")
        swapped = [{"day": "THURSDAY", "time": None, "rank": 1},
                   {"day": "TOMORROW", "time": "15:00", "rank": 2}]
        result = evaluate_case(case, {"turn_evidence": [
            {"field": "scheduling_preference", "value": swapped, "status": "CONFIRMED"}]})
        self.assertGreater(result.false_positives, 0, "swapped branches must not match")

    def test_missing_field_accuracy(self):
        case = next(c for c in CORPUS if c["id"] == "WILD-B-01")
        good = evaluate_case(case, _perfect(case)(None))
        self.assertEqual(good.missing_honoured, good.missing_expected)

    def test_report_metrics_and_slices(self):
        report = evaluate(lambda msgs: {"turn_evidence": []}, corpus=CORPUS[:20])
        metrics = report.metrics()
        for key in ("field_precision", "field_recall", "role_accuracy",
                    "unsupported_inference_rate", "ambiguity_handling_accuracy",
                    "missing_field_accuracy"):
            self.assertIn(key, metrics)
        self.assertIn("cases", metrics)
        self.assertTrue(report.by_kind())
        self.assertTrue(report.by_group())
        self.assertIn("SEMANTIC EVALUATION", report.render())

    def test_report_separates_real_from_synthetic(self):
        report = evaluate(lambda msgs: {"turn_evidence": []}, corpus=CORPUS)
        by_kind = report.by_kind()
        self.assertIn("REAL", by_kind)
        self.assertIn("SYNTHETIC", by_kind)
        self.assertEqual(by_kind["REAL"]["cases"], 12)

    def test_full_corpus_runs_end_to_end(self):
        """A perfect-per-case interpreter scores zero unsupported inferences corpus-wide."""
        def interp_factory():
            index = {}

            def interp(messages):
                key = tuple(messages)
                case = index.get(key)
                if case is None:
                    return {"turn_evidence": []}
                return _perfect(case)(messages)
            return interp, index

        interp, index = interp_factory()
        for c in CORPUS:
            index[tuple(c["raw"]["messages"])] = c
        report = evaluate(interp, corpus=CORPUS)
        metrics = report.metrics()
        self.assertEqual(metrics["unsupported_inference_rate"], 0.0)
        self.assertEqual(metrics["field_precision"], 1.0)
        self.assertEqual(metrics["cases"], len(CORPUS))

    def test_normalization_and_matching(self):
        self.assertTrue(values_match("Berazategui", "berazategui"))
        self.assertTrue(values_match("Peugeot  2008", "peugeot 2008"))
        self.assertFalse(values_match("Berazategui", "Tigre"))
        self.assertEqual(normalize(None), None)
        self.assertTrue(values_match([{"day": "TOMORROW"}], [{"day": "tomorrow"}]))


# ── replay support ────────────────────────────────────────────────────────────

class TestReplaySupport(unittest.TestCase):

    def test_reconstruct_burst_orders_inbound_only(self):
        from types import SimpleNamespace
        rows = [
            SimpleNamespace(id=3, direction="in", text="tercero", timestamp=3),
            SimpleNamespace(id=1, direction="in", text="primero", timestamp=1),
            SimpleNamespace(id=2, direction="out", text="respuesta del bot", timestamp=2),
            SimpleNamespace(id=4, direction="in", text=None, timestamp=4),
        ]
        self.assertEqual(reconstruct_burst(rows), ["primero", "tercero"])

    def test_reconstruct_burst_is_read_only(self):
        """The helper must not write to the objects it reads."""
        from types import SimpleNamespace
        row = SimpleNamespace(id=1, direction="in", text="hola", timestamp=1)
        before = dict(vars(row))
        reconstruct_burst([row])
        self.assertEqual(dict(vars(row)), before)

    def test_harness_imports_nothing_from_the_runtime_engine(self):
        source = (ROOT / "tests" / "semantic_corpus" / "evaluation.py").read_text(encoding="utf-8")
        for forbidden in ("conversation_engine", "openai", "app.db", "SessionLocal"):
            self.assertNotIn(forbidden, source,
                             "the harness must stay inert and non-mutating")


if __name__ == "__main__":
    unittest.main()
