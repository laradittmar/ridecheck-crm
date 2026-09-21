"""L4.7W5-HYBRID-TRACE-LABEL-TRUTH — absence is not disagreement.

The first trace the deployed Inspector ever captured was headlined `CONFLICT`. Its own
reconciliation rows read `SEMANTIC_MISSING` and `AGREE`; not one row said `CONFLICT`. The
aggregation was `AGREE if all(...) else CONFLICT`, so a family with nothing to compare was
reported as a contradiction between the engines.

That is the same class of error this programme has been correcting all along, pointed the
other way. The hardening milestone pinned "missing evidence is never labelled agreement" and
never pinned the mirror, so the mirror is what shipped.

Two things are separated here and must stay separated:

* **producer availability** — did the interpreter run and produce evidence?
* **reconciliation coverage** — did that evidence reach the reconciler that needed it?

A turn where the interpreter succeeded but its claims were routed elsewhere is
`SEMANTIC NOT ROUTED`, not `SEMANTIC MISSING`, and the turn is
`PARTIAL RECONCILIATION`, not `CONFLICT`.

LIVE-01..12  the preserved production trace, sanitized, as the permanent regression
MTX-01..12   the classification matrix
IFF-01..03   CONFLICT if and only if a row says CONFLICT
HIST-01..05  captured history is never rewritten; effective is derived at read time
WORD-01..05  reconciliation wording
UI-01..06    headline, dashboard row, panels
PRIV-01..03  authentication and privacy unchanged
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models as models
from app.models import HybridDecisionTraceRow
from app.schemas.hybrid_trace import (CanonicalSnapshot, Classification,
                                      ReconciliationEvidence, ResultKind, RuleEvidence,
                                      SemanticEvidence)
from app.services import hybrid_trace as svc

FIXTURE = ROOT / "tests" / "fixtures" / "hybrid_trace_live_partial_reconciliation.json"
LIVE = json.loads(FIXTURE.read_text(encoding="utf-8"))


def rows(*classifications, semantic_present=()):
    """Reconciliation rows by classification; `semantic_present` marks routed indices."""
    return tuple(
        ReconciliationEvidence(
            claim_family=f"family.{i}",
            semantic_input="PRESENT" if i in semantic_present else "ABSENT",
            ce_input="PRESENT", classification=c)
        for i, c in enumerate(classifications))


def sem(status="OK", claims=("vehicle_mentions",)):
    return SemanticEvidence(ok=(status == "OK"), status=status, produced_claims=tuple(claims))


ABSENT_SEM = SemanticEvidence(status="ABSENT", produced_claims=())
ERROR_SEM = SemanticEvidence(status="ERROR", error_category="HTTPError", produced_claims=())


def old_algorithm(reconciliations) -> str:
    """The aggregation exactly as it shipped, kept so the regression can prove the delta."""
    if reconciliations:
        return (Classification.AGREE
                if all(r.classification == Classification.AGREE for r in reconciliations)
                else Classification.CONFLICT)
    return Classification.NO_RULE


# ── LIVE: the preserved production trace ─────────────────────────────────────

class PreservedLiveTrace(unittest.TestCase):
    """The real turn, sanitized. If this ever reads CONFLICT again, the defect is back."""

    def setUp(self):
        self.headline, self.conditions = svc.effective_from_payload(LIVE)

    def test_live_01_the_fixture_reproduces_the_defect_conditions(self):
        self.assertEqual([r["classification"] for r in LIVE["reconciliation"]],
                         ["SEMANTIC_MISSING", "AGREE"])
        self.assertEqual(LIVE["semantic"]["status"], "OK")
        self.assertTrue(LIVE["semantic"]["produced_claims"])
        self.assertEqual(LIVE["result_kind"], "FALLBACK")
        self.assertIsNone(LIVE["outbound_message_id"])
        self.assertEqual(LIVE["canonical_before"], LIVE["canonical_after"])

    def test_live_02_no_row_is_a_conflict(self):
        self.assertEqual(
            [r for r in LIVE["reconciliation"] if r["classification"] == "CONFLICT"], [])

    def test_live_03_the_old_algorithm_returned_conflict(self):
        stored = rows(*[r["classification"] for r in LIVE["reconciliation"]])
        self.assertEqual(old_algorithm(stored), Classification.CONFLICT)
        self.assertEqual(LIVE["badges"][0], "CONFLICT",
                         "the fixture must keep the wrong headline it was written with")

    def test_live_04_the_corrected_algorithm_returns_partial_reconciliation(self):
        self.assertEqual(self.headline, Classification.PARTIAL_RECONCILIATION)

    def test_live_05_the_supporting_condition_is_semantic_not_routed(self):
        self.assertEqual(self.conditions, (Classification.SEMANTIC_NOT_ROUTED,))

    def test_live_06_it_is_not_called_semantic_missing(self):
        """The interpreter ran and produced three claim families. Nothing was missing."""
        self.assertNotIn(Classification.SEMANTIC_MISSING, self.conditions)
        self.assertNotEqual(self.headline, Classification.SEMANTIC_MISSING)

    def test_live_07_semantic_status_is_untouched(self):
        self.assertEqual(LIVE["semantic"]["status"], "OK")
        self.assertIn("vehicle_mentions", LIVE["semantic"]["produced_claims"])

    def test_live_08_the_reconciler_is_shown_as_not_having_received_claims(self):
        from app.ui.hybrid_trace_view import _row_semantic_cell
        cell = _row_semantic_cell(LIVE["reconciliation"][0], LIVE["semantic"])
        self.assertIn("NOT ROUTED", cell)
        self.assertNotIn("ABSENT", cell)

    def test_live_09_captured_and_effective_stay_distinguishable(self):
        db = _db()
        row = HybridDecisionTraceRow(
            turn_id=LIVE["turn_id"], thread_id=LIVE["thread_id"], lead_id=LIVE["lead_id"],
            deployment_sha=LIVE["deployment_sha"], input_hash=LIVE["input_hash"],
            message_count=LIVE["message_count"], result_kind=LIVE["result_kind"],
            classification="CONFLICT", semantic_status=LIVE["semantic"]["status"],
            payload=LIVE)
        db.add(row)
        db.commit()
        from app.routes.ops_dashboard import _trace_row_summary
        summary = _trace_row_summary(db.query(HybridDecisionTraceRow).one())
        self.assertEqual(summary["captured_classification"], "CONFLICT")
        self.assertEqual(summary["effective_classification"],
                         Classification.PARTIAL_RECONCILIATION)
        self.assertTrue(summary["reclassified"])
        self.assertEqual(summary["classification"], summary["captured_classification"],
                         "the compatibility field must be the CAPTURED value, unambiguously")

    def test_live_10_the_stored_payload_is_not_modified_by_reading_it(self):
        before = json.dumps(LIVE, sort_keys=True)
        svc.effective_from_payload(LIVE)
        from app.ui.hybrid_trace_view import render_turn_trace_page
        render_turn_trace_page({"captured": True, "trace": LIVE,
                                "effective_classification": self.headline,
                                "supporting_conditions": list(self.conditions)})
        self.assertEqual(json.dumps(LIVE, sort_keys=True), before)

    def test_live_11_the_fixture_carries_no_identifier_or_secret(self):
        """Asserted by SHAPE, not by literal.

        Writing the tester's number here to prove it is absent would put it in the
        repository to say it is not in the repository. Matching the shape catches any
        phone, WAMID or live fingerprint that leaks into this fixture later, including
        ones nobody thought to enumerate.
        """
        blob = FIXTURE.read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"\b549\d{8,12}\b", blob), [],
                         "an Argentine mobile number reached the fixture")
        self.assertEqual([w for w in re.findall(r"wamid\.[A-Za-z0-9+/=]{12,}", blob)
                          if not w.startswith("wamid.SANITISED")], [],
                         "a real WAMID reached the fixture")
        self.assertNotIn("chain_of_thought", blob)
        self.assertIsNone(LIVE["semantic"].get("raw_response"))
        for snap in ("canonical_before", "canonical_after"):
            self.assertEqual(LIVE[snap]["booking_token_fingerprint"], "f" * 8,
                             "the live token fingerprint must be replaced, not copied")

    def test_live_12_a_new_trace_with_the_same_shape_stores_the_truth(self):
        """Requirement: future traces must not need reinterpreting."""
        trace = svc.build_trace(
            turn_id="new-0001", thread_id=1, lead_id=1, deployment_sha="d",
            started_at="s", completed_at="c", duration_ms=1,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=["x"],
            semantic=sem(), ce_rules=(),
            reconciliations=rows(Classification.SEMANTIC_MISSING, Classification.AGREE,
                                 semantic_present=(1,)),
            before=svc.snapshot_state(None, None), after=svc.snapshot_state(None, None),
            action="replied", detail=None, answer_source="CE_AI", outbound={})
        self.assertEqual(trace.badges[0], "PARTIAL RECONCILIATION")
        self.assertIn("SEMANTIC NOT ROUTED", trace.badges)
        db = _db()
        self.assertTrue(svc.persist(db, trace))
        stored = db.query(HybridDecisionTraceRow).one()
        self.assertEqual(stored.classification, Classification.PARTIAL_RECONCILIATION)


def _db() -> Session:
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    return Session(engine)


# ── MTX: the classification matrix ───────────────────────────────────────────

class ClassificationMatrix(unittest.TestCase):

    def head(self, reconciliations, semantic, ce_rules=()):
        return svc.classify(semantic, ce_rules, reconciliations)

    def test_mtx_01_single_agree_with_semantic_participating(self):
        self.assertEqual(self.head(rows(Classification.AGREE, semantic_present=(0,)), sem()),
                         Classification.AGREE)

    def test_mtx_02_two_agrees_with_semantic_participating(self):
        self.assertEqual(
            self.head(rows(Classification.AGREE, Classification.AGREE,
                           semantic_present=(0, 1)), sem()),
            Classification.AGREE)

    def test_mtx_03_single_conflict(self):
        self.assertEqual(self.head(rows(Classification.CONFLICT), sem()),
                         Classification.CONFLICT)

    def test_mtx_04_agree_plus_conflict_is_conflict(self):
        self.assertEqual(
            self.head(rows(Classification.AGREE, Classification.CONFLICT,
                           semantic_present=(0, 1)), sem()),
            Classification.CONFLICT)

    def test_mtx_05_semantic_missing_alone_with_no_semantic_evidence(self):
        self.assertEqual(self.head(rows(Classification.SEMANTIC_MISSING), ABSENT_SEM),
                         Classification.SEMANTIC_MISSING)

    def test_mtx_06_agree_plus_missing_with_semantic_absent(self):
        headline = self.head(rows(Classification.AGREE, Classification.SEMANTIC_MISSING,
                                  semantic_present=(0,)), ABSENT_SEM)
        self.assertEqual(headline, Classification.PARTIAL_RECONCILIATION)
        self.assertEqual(
            svc.supporting_conditions(ABSENT_SEM,
                                      rows(Classification.AGREE,
                                           Classification.SEMANTIC_MISSING,
                                           semantic_present=(0,))),
            (Classification.SEMANTIC_MISSING,))

    def test_mtx_07_agree_plus_missing_with_semantic_ok_is_not_routed(self):
        """The preserved live turn's shape."""
        reconciliations = rows(Classification.AGREE, Classification.SEMANTIC_MISSING,
                               semantic_present=(0,))
        self.assertEqual(self.head(reconciliations, sem()),
                         Classification.PARTIAL_RECONCILIATION)
        self.assertEqual(svc.supporting_conditions(sem(), reconciliations),
                         (Classification.SEMANTIC_NOT_ROUTED,))

    def test_mtx_08_semantic_error_row(self):
        self.assertEqual(self.head(rows(Classification.SEMANTIC_ERROR), ERROR_SEM),
                         Classification.SEMANTIC_ERROR)

    def test_mtx_09_agree_plus_semantic_error_is_never_conflict(self):
        headline = self.head(rows(Classification.AGREE, Classification.SEMANTIC_ERROR,
                                  semantic_present=(0,)), ERROR_SEM)
        self.assertEqual(headline, Classification.PARTIAL_RECONCILIATION)
        self.assertNotEqual(headline, Classification.CONFLICT)

    def test_mtx_10_ce_missing_row(self):
        self.assertEqual(self.head(rows(Classification.CE_MISSING), sem()),
                         Classification.CE_MISSING)
        self.assertNotEqual(self.head(rows(Classification.CE_MISSING), sem()),
                            Classification.CONFLICT)

    def test_mtx_11_no_reconciliation_rows_is_no_rule(self):
        fired = (RuleEvidence("scheduling.earliest_option_rejected", "1.0", True),)
        self.assertEqual(svc.classify(SemanticEvidence(status="PENDING"), fired, ()),
                         Classification.NO_RULE)

    def test_mtx_12_a_deterministic_floor_is_never_agreement(self):
        fired = (RuleEvidence("scheduling.earliest_option_rejected", "1.0", True),)
        trace = svc.build_trace(
            turn_id="t", thread_id=1, lead_id=None, deployment_sha="d",
            started_at="s", completed_at="c", duration_ms=1,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=["x"],
            semantic=SemanticEvidence(status="PENDING"), ce_rules=fired, reconciliations=(),
            before=CanonicalSnapshot(needs_human=False),
            after=CanonicalSnapshot(needs_human=True),
            action="skipped_human", detail=None, answer_source=None, outbound={})
        self.assertEqual(trace.result_kind, ResultKind.DETERMINISTIC_FLOOR)
        self.assertIn("DETERMINISTIC FLOOR", trace.badges)
        self.assertNotIn("AGREE", trace.badges)
        self.assertNotIn("CONFLICT", trace.badges)


# ── IFF: the invariant in one sentence ───────────────────────────────────────

class ConflictIffAConflictRow(unittest.TestCase):

    ROW_VALUES = (Classification.AGREE, Classification.CONFLICT,
                  Classification.SEMANTIC_MISSING, Classification.SEMANTIC_ERROR,
                  Classification.CE_MISSING, Classification.NO_RULE)

    def test_iff_01_every_single_row_combination(self):
        for value in self.ROW_VALUES:
            for semantic in (sem(), ABSENT_SEM, ERROR_SEM):
                with self.subTest(row=value, semantic=semantic.status):
                    headline = svc.classify(semantic, (), rows(value))
                    self.assertEqual(headline == Classification.CONFLICT,
                                     value == Classification.CONFLICT)

    def test_iff_02_every_pair(self):
        for a in self.ROW_VALUES:
            for b in self.ROW_VALUES:
                with self.subTest(pair=(a, b)):
                    headline = svc.classify(sem(), (), rows(a, b, semantic_present=(0, 1)))
                    expected = Classification.CONFLICT in (a, b)
                    self.assertEqual(headline == Classification.CONFLICT, expected)

    def test_iff_03_no_reconciliation_can_never_be_conflict(self):
        for semantic in (sem(), ABSENT_SEM, ERROR_SEM,
                         SemanticEvidence(status="PENDING")):
            with self.subTest(semantic=semantic.status):
                self.assertNotEqual(svc.classify(semantic, (), ()), Classification.CONFLICT)


# ── HIST: history is evidence ────────────────────────────────────────────────

class HistoryIsNotRewritten(unittest.TestCase):

    def test_hist_01_effective_from_payload_returns_none_on_junk(self):
        self.assertEqual(svc.effective_from_payload(None), (None, ()))
        self.assertEqual(svc.effective_from_payload("not-a-dict"), (None, ()))

    def test_hist_02_a_corrected_trace_recomputes_to_what_it_stored(self):
        trace = svc.build_trace(
            turn_id="t", thread_id=1, lead_id=None, deployment_sha="d",
            started_at="s", completed_at="c", duration_ms=1,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=["x"],
            semantic=sem(), ce_rules=(),
            reconciliations=rows(Classification.AGREE, Classification.SEMANTIC_MISSING,
                                 semantic_present=(0,)),
            before=svc.snapshot_state(None, None), after=svc.snapshot_state(None, None),
            action="replied", detail=None, answer_source="CE_AI", outbound={})
        headline, conditions = svc.effective_from_payload(trace.to_payload())
        self.assertEqual(headline, trace.badges[0].replace(" ", "_"))
        self.assertEqual(list(conditions), list(trace.supporting_conditions))

    def test_hist_03_the_row_is_never_updated_by_a_read(self):
        db = _db()
        db.add(HybridDecisionTraceRow(turn_id="h1", classification="CONFLICT",
                                      message_count=1, payload=LIVE))
        db.commit()
        from app.routes.ops_dashboard import _trace_row_summary, read_turn
        _trace_row_summary(db.query(HybridDecisionTraceRow).one())
        read_turn("h1", db)
        self.assertEqual(db.query(HybridDecisionTraceRow).one().classification, "CONFLICT")
        self.assertEqual(db.query(HybridDecisionTraceRow).one().payload["badges"][0],
                         "CONFLICT")

    def test_hist_04_read_turn_exposes_both_values(self):
        db = _db()
        db.add(HybridDecisionTraceRow(turn_id="h2", classification="CONFLICT",
                                      message_count=1, payload=LIVE))
        db.commit()
        from app.routes.ops_dashboard import read_turn
        out = read_turn("h2", db)
        self.assertEqual(out["captured_classification"], "CONFLICT")
        self.assertEqual(out["effective_classification"],
                         Classification.PARTIAL_RECONCILIATION)
        self.assertEqual(out["supporting_conditions"], [Classification.SEMANTIC_NOT_ROUTED])

    def test_hist_05_no_backfill_helper_exists(self):
        source = (ROOT / "backend" / "app" / "services"
                  / "hybrid_trace.py").read_text(encoding="utf-8-sig")
        for forbidden in ("UPDATE hybrid_decision_traces", "update(HybridDecisionTraceRow",
                          "def backfill"):
            self.assertNotIn(forbidden, source)


# ── WORD / UI ────────────────────────────────────────────────────────────────

class WordingAndRendering(unittest.TestCase):

    def page(self, payload=None):
        from app.ui.hybrid_trace_view import render_turn_trace_page
        payload = payload or LIVE
        headline, conditions = svc.effective_from_payload(payload)
        return render_turn_trace_page({"captured": True, "trace": payload,
                                       "captured_classification": payload["badges"][0],
                                       "effective_classification": headline,
                                       "supporting_conditions": list(conditions)})

    def test_word_01_not_routed_sentence_is_shown(self):
        from app.ui.hybrid_trace_view import NOT_ROUTED_SENTENCE
        self.assertIn(NOT_ROUTED_SENTENCE, self.page())

    def test_word_02_the_absent_sentence_is_not_shown_for_this_turn(self):
        from app.ui.hybrid_trace_view import NO_SEMANTIC_SENTENCE
        self.assertNotIn(NO_SEMANTIC_SENTENCE, self.page())

    def test_word_03_the_page_states_there_was_no_contradiction(self):
        self.assertIn("No hubo contradicción entre los motores en este turno", self.page())

    def test_word_04_a_genuinely_absent_producer_says_so(self):
        payload = json.loads(json.dumps(LIVE))
        payload["semantic"]["status"] = "ABSENT"
        payload["semantic"]["produced_claims"] = []
        payload["semantic"]["evidence"] = None
        from app.ui.hybrid_trace_view import NOT_ROUTED_SENTENCE, NO_SEMANTIC_SENTENCE
        page = self.page(payload)
        self.assertIn(NO_SEMANTIC_SENTENCE, page)
        self.assertNotIn(NOT_ROUTED_SENTENCE, page)

    def test_word_05_the_compared_family_is_described_honestly(self):
        self.assertIn("coincidieron con la evidencia que", self.page())

    def test_ui_01_the_headline_is_partial_reconciliation(self):
        badges = self.page().split('class="badges"')[1].split("</div>")[0]
        self.assertIn("PARTIAL RECONCILIATION", badges)
        self.assertIn("SEMANTIC NOT ROUTED", badges)
        self.assertIn("HYBRID CONVERSATION TRACE", badges)
        self.assertIn("FALLBACK", badges)

    def test_ui_02_the_headline_is_not_conflict(self):
        badges = self.page().split('class="badges"')[1].split("</div>")[0]
        self.assertNotIn("CONFLICT", badges)

    def test_ui_03_the_semantic_panel_still_shows_a_successful_interpretation(self):
        page = self.page()
        self.assertIn("Motor semántico", page)
        self.assertIn("vehicle_mentions", page)
        self.assertIn(">OK<", page)

    def test_ui_04_the_dashboard_list_uses_the_effective_value(self):
        control = (ROOT / "backend" / "app" / "ui"
                   / "control_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("r.effective_classification", control)
        self.assertIn("esc(eff ", control)

    def test_ui_05_both_new_labels_are_in_the_glossary(self):
        from app.ui.hybrid_trace_view import LABEL_GLOSSARY
        documented = {label for label, _ in LABEL_GLOSSARY}
        self.assertIn("PARTIAL RECONCILIATION", documented)
        self.assertIn("SEMANTIC NOT ROUTED", documented)

    def test_ui_06_a_real_conflict_still_reads_conflict(self):
        payload = json.loads(json.dumps(LIVE))
        payload["reconciliation"][0]["classification"] = "CONFLICT"
        badges = self.page(payload).split('class="badges"')[1].split("</div>")[0]
        self.assertIn("CONFLICT", badges)
        self.assertNotIn("PARTIAL RECONCILIATION", badges)


class PrivacyUnchanged(unittest.TestCase):

    def test_priv_01_no_customer_text_or_identifier_on_the_page(self):
        from app.ui.hybrid_trace_view import render_turn_trace_page
        headline, conditions = svc.effective_from_payload(LIVE)
        page = render_turn_trace_page({"captured": True, "trace": LIVE,
                                       "effective_classification": headline,
                                       "supporting_conditions": list(conditions)})
        self.assertEqual(re.findall(r"\b549\d{8,12}\b", page), [])
        self.assertEqual([w for w in re.findall(r"wamid\.[A-Za-z0-9+/=]{12,}", page)
                          if not w.startswith("wamid.SANITISED")], [])
        self.assertNotIn("chain_of_thought", page)

    def test_priv_02_the_snapshot_stays_allowlisted(self):
        self.assertEqual(sorted(LIVE["canonical_before"]), [
            "active_requested_date", "booking_token_fingerprint", "booking_token_present",
            "candidate_id", "lead_estado", "lead_necesita_humano", "needs_human",
            "offer_outstanding", "offered_slots_count", "revision_id", "stage",
            "zone_detail", "zone_group"])

    def test_priv_03_the_endpoints_still_require_the_crm_session(self):
        from app.routes.ops_dashboard import get_turn, get_turns, require_crm_session
        import inspect
        for fn in (get_turn, get_turns):
            defaults = [d.default for d in inspect.signature(fn).parameters.values()]
            self.assertTrue(any(getattr(d, "dependency", None) is require_crm_session
                                for d in defaults),
                            f"{fn.__name__} lost its session dependency")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
