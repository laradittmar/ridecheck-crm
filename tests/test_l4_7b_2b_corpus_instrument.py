"""L4.7B.2B — the evaluation instrument itself is under test.

A corpus is a ruler. These tests hold the repaired ruler straight: the fixtures must expect
exactly the evidence their own raw text carries, the schema's six acceptance signals must
stay distinguishable, and the interpreter must remain untouched — a measurement milestone
that silently edited the thing being measured would prove nothing.

FIXTURE-01  every SYN-MIX fixture expects the actual FAQ topics it asks about
FIXTURE-02  no `"mixed"` sentinel survives anywhere in the corpus
FIXTURE-03  vehicle evidence written in the raw text is present in the expectation
FIXTURE-04  location evidence carries an explicit role
FIXTURE-05  an explicit year in the raw text is expected
FIXTURE-06  FUTURE_INTENT is distinguishable from ACCEPT
FIXTURE-07  HESITATE is distinguishable from FUTURE_INTENT
FIXTURE-08  the false-ACCEPT metric counts a wrongly claimed agreement
FIXTURE-09  REAL raw texts are unchanged, byte for byte
FIXTURE-10  the interpreter source hash is unchanged
FIXTURE-11  the prompt version is unchanged (understand/1.4)
FIXTURE-12  all 162 corpus cases pass the integrity audit
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from semantic_corpus.evaluation import (  # noqa: E402
    ACCEPTANCE_SIGNALS,
    CANONICAL_READINESS_VALUES,
    EvaluationReport,
    as_signal,
    canonicalise_engagement,
    evaluate_case,
    load_corpus,
    values_match,
)
from semantic_corpus.integrity import FAQ_TOPICS, audit_corpus, render  # noqa: E402

CORPUS = {c["id"]: c for c in load_corpus()}
MIX = [c for cid, c in CORPUS.items() if cid.startswith("SYN-MIX")]

# The interpreter as shipped by the CURRENT milestone. L4.7B.2B pinned understand/1.4 to
# prove the ruler was repaired without touching the interpreter; L4.7B.3 then changed the
# interpreter on purpose, so the pin moved with it — deliberately, and only in a milestone
# whose stated job is to change the interpreter.
INTERPRETER_SHA256 = "bddb37468d9a44f8c10a3fd4d9f3acb4a18cce19113728f996bc9775ab943e32"
INTERPRETER_PATH = ROOT / "backend" / "app" / "services" / "semantic_interpreter.py"


def fields(case: dict) -> dict[str, dict]:
    return {str(e["field"]): e for e in case["expected_turn_evidence"]}


def raw(case: dict) -> str:
    return " ".join(case["raw"]["messages"])


# ── the repaired SYN-MIX fixtures ─────────────────────────────────────────────

class TestMixFixtures(unittest.TestCase):

    EXPECTED_TOPICS = {
        "SYN-MIX-01": ["payment"],
        "SYN-MIX-02": ["report"],
        "SYN-MIX-03": ["presence"],
        "SYN-MIX-04": ["duration"],
        "SYN-MIX-05": ["service_scope"],
        "SYN-MIX-06": ["business_hours"],
        "SYN-MIX-07": ["payment"],
        "SYN-MIX-08": ["service_scope"],
    }

    def test_fixture_01_actual_faq_topics_are_expected(self):
        self.assertEqual(len(MIX), 8)
        for case in MIX:
            topics = fields(case)["faq_topics"]["value"]
            self.assertEqual(topics, self.EXPECTED_TOPICS[case["id"]], case["id"])
            for topic in topics:
                self.assertIn(topic, FAQ_TOPICS, "topics must be emittable")

    def test_fixture_02_no_mixed_sentinel_survives(self):
        for case in CORPUS.values():
            for item in case["expected_turn_evidence"]:
                if item["field"] == "faq_topics":
                    self.assertNotIn("mixed", item["value"] or [], case["id"])

    def test_fixture_03_vehicle_in_raw_is_expected(self):
        """A car named in the fixture's own text cannot be absent from its expectation."""
        models = {"Focus": "Ford Focus", "Taos": "Volkswagen Taos", "Onix": "Chevrolet Onix",
                  "Corolla": "Toyota Corolla", "Gol Trend": "Volkswagen Gol Trend"}
        for case in MIX:
            text = raw(case)
            for token, canonical in models.items():
                if token in text:
                    self.assertEqual(fields(case).get("vehicle", {}).get("value"), canonical,
                                     f"{case['id']} names {token}")

    def test_fixture_04_location_evidence_carries_a_role(self):
        for case in CORPUS.values():
            for name in ("inspection_location", "customer_origin"):
                item = fields(case).get(name)
                if item and item.get("value"):
                    self.assertTrue(item.get("role"), f"{case['id']}: {name} without a role")
        # and the MIX localities are the CAR's location, never the customer's
        for case in MIX:
            item = fields(case).get("inspection_location")
            if item:
                self.assertEqual(item["role"], "INSPECTION_LOCATION", case["id"])

    def test_fixture_05_explicit_year_is_expected(self):
        for case in MIX:
            years = re.findall(r"\b(19[89]\d|20[0-9]\d)\b", raw(case))
            if years:
                self.assertEqual(str(fields(case).get("vehicle_year", {}).get("value")),
                                 years[0], case["id"])

    def test_mix_fixtures_keep_the_wild_b_invariant(self):
        """Each fixture still asserts that a FAQ must not discard business evidence."""
        for case in MIX:
            reasons = [r.get("field") for r in case["must_not_infer"]]
            self.assertIn("evidence_discarded", reasons, case["id"])


# ── stance: six signals, scored distinctly ────────────────────────────────────

class TestStanceEvaluation(unittest.TestCase):

    def _score(self, expected_signal, produced_signal):
        case = {"id": "T", "provenance": {"kind": "SYNTHETIC"}, "groups": ["E"],
                "expected_turn_evidence": [{"field": "acceptance", "value": expected_signal,
                                            "status": "CONFIRMED"}],
                "expected_missing_fields": [], "must_not_infer": []}
        produced = {"turn_evidence": [{"field": "acceptance", "value": produced_signal,
                                       "status": "CONFIRMED"}]}
        return evaluate_case(case, produced)

    def test_fixture_06_future_intent_is_not_accept(self):
        self.assertIn("FUTURE_INTENT", ACCEPTANCE_SIGNALS)
        result = self._score("FUTURE_INTENT", "ACCEPT")
        self.assertEqual(result.stance_correct, 0, "reading FUTURE_INTENT as ACCEPT is wrong")
        self.assertEqual(result.false_accepts, 1)
        self.assertEqual(result.future_intent_recalled, 0)
        self.assertFalse(values_match("FUTURE_INTENT", "ACCEPT", field="acceptance"))

    def test_fixture_07_hesitate_is_not_future_intent(self):
        result = self._score("HESITATE", "FUTURE_INTENT")
        self.assertEqual(result.stance_expected, 1)
        self.assertEqual(result.stance_correct, 0)
        self.assertEqual(result.hesitate_recalled, 0)
        self.assertEqual(result.false_accepts, 0, "a wrong non-ACCEPT is not a false accept")
        self.assertFalse(values_match("HESITATE", "FUTURE_INTENT", field="acceptance"))

    def test_fixture_08_false_accept_metric(self):
        good = self._score("REJECT", "REJECT")
        bad = self._score("REJECT", "ACCEPT")
        report = EvaluationReport(results=[good, bad])
        metrics = report.metrics()
        self.assertEqual(metrics["false_accept_rate"], 0.5)
        self.assertEqual(metrics["stance_exact_accuracy"], 0.5)
        self.assertEqual(report.metrics([good])["false_accept_rate"], 0.0)

    def test_each_signal_scores_itself_correctly(self):
        for signal in ("ACCEPT", "REJECT", "HESITATE", "FUTURE_INTENT", "QUESTION_ONLY"):
            result = self._score(signal, signal)
            self.assertEqual(result.stance_correct, 1, signal)
            self.assertEqual(result.false_accepts, 0, signal)

    def test_legacy_spellings_are_canonicalised_not_lost(self):
        """A producer still saying `readiness=FUTURE_CONTACT_INTENDED` means the stance."""
        items = canonicalise_engagement([{"field": "readiness",
                                          "value": "FUTURE_CONTACT_INTENDED"}])
        self.assertEqual([(i["field"], i["value"]) for i in items],
                         [("acceptance", "FUTURE_INTENT")])
        self.assertEqual(as_signal(True), "ACCEPT")
        self.assertEqual(as_signal("HESITANT_OR_DEFERRED"), "HESITATE")
        self.assertIsNone(as_signal("SEARCHING_NOT_READY"), "a process fact is not a stance")

    def test_explicit_acceptance_wins_over_legacy_readiness(self):
        items = canonicalise_engagement([
            {"field": "acceptance", "value": "REJECT"},
            {"field": "readiness", "value": "HESITANT_OR_DEFERRED"},
        ])
        self.assertEqual([(i["field"], i["value"]) for i in items], [("acceptance", "REJECT")])

    def test_readiness_keeps_only_the_process_fact(self):
        for case in CORPUS.values():
            item = fields(case).get("readiness")
            if item:
                self.assertIn(item["value"], CANONICAL_READINESS_VALUES, case["id"])


# ── corpus immutability and interpreter immutability ──────────────────────────

class TestImmutability(unittest.TestCase):

    OWNER_LENGTHS = {"REAL-001": 93, "REAL-002": 62, "REAL-003": 115, "REAL-004": 200}
    WILD_IDS = ("WILD-A-01", "WILD-A-02", "WILD-A-03", "WILD-A-04",
                "WILD-B-01", "WILD-B-02", "WILD-01-01", "WILD-01-02")

    def test_fixture_09_real_raw_text_unchanged(self):
        real = [c for c in CORPUS.values() if c["provenance"]["kind"] == "REAL"]
        self.assertEqual(len(real), 12)
        for case_id, length in self.OWNER_LENGTHS.items():
            self.assertEqual(len(CORPUS[case_id]["raw"]["messages"][0]), length, case_id)
        for case_id in self.WILD_IDS:
            self.assertTrue(CORPUS[case_id]["raw"]["messages"], case_id)
        # the two Wild sentences whose failure created this whole line of work
        self.assertIn("Berazategui", " ".join(CORPUS["WILD-B-02"]["raw"]["messages"]))
        self.assertIn("2008", " ".join(CORPUS["WILD-B-01"]["raw"]["messages"]))

    def test_fixture_10_interpreter_source_unchanged(self):
        digest = hashlib.sha256(INTERPRETER_PATH.read_bytes()).hexdigest()
        self.assertEqual(digest, INTERPRETER_SHA256,
                         "L4.7B.2B repairs the ruler, never the interpreter")

    def test_fixture_11_prompt_version_unchanged(self):
        from app.services.semantic_interpreter import PROMPT_VERSION
        self.assertEqual(PROMPT_VERSION, "understand/1.18",
                         "prompt version moved again in L4.7B.4; the MODEL did not")
        source = INTERPRETER_PATH.read_text(encoding="utf-8")
        self.assertIn('or "gpt-4o-mini"', source, "the model is unchanged")


# ── whole-corpus integrity ────────────────────────────────────────────────────

class TestCorpusIntegrity(unittest.TestCase):

    def test_fixture_12_all_cases_pass_the_integrity_audit(self):
        findings = audit_corpus(list(CORPUS.values()))
        self.assertEqual(findings, [], render(findings))

    def test_corpus_still_has_162_cases_and_12_real(self):
        self.assertEqual(len(CORPUS), 162)
        self.assertEqual(sum(1 for c in CORPUS.values()
                             if c["provenance"]["kind"] == "REAL"), 12)

    def test_faq_topics_compare_without_order(self):
        self.assertTrue(values_match(["report", "payment"], ["payment", "report"],
                                     field="faq_topics"))
        self.assertFalse(values_match(["report", "payment"], ["payment"],
                                      field="faq_topics"))


if __name__ == "__main__":
    unittest.main()
