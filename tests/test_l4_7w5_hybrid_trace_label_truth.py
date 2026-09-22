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
`SEMANTIC NOT ROUTED`, not `SEMANTIC MISSING`, and the turn is not `CONFLICT`.

**Amended by L4.7W5-TRACE-ROW-SEMANTICS (hybrid-decision-trace/1.1).** This suite was
written against a row vocabulary derived from `InformationState`, which describes evidence
POLARITY and cannot say who supplied it. Under 1.1 a row is classified from recorded
producer participation, so the expectations below moved:

* a row with one contributing source is `SINGLE_PRODUCER`, never `AGREE`;
* a row with none is `NO_EVIDENCE` — or `NOT_ROUTED` when the interpreter provably
  produced claims elsewhere in the turn — and never `SEMANTIC_MISSING`, which blamed a
  named producer for an absence that was nobody's in particular;
* the preserved 1.0 trace can no longer reach `PARTIAL_RECONCILIATION`: that headline
  requires a proven agreement, and its surviving `AGREE` row was one producer. It reads
  `TRACE_INCOMPLETE`, which is weaker, and true.

Every assertion that changed changed because it pinned a false statement about producers.
No assertion about CONFLICT weakened: CONFLICT still requires a row that proves it, and now
also requires two distinct sources to have supplied incompatible evidence.

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
                                      SemanticEvidence, SourceContribution)
from app.services import hybrid_trace as svc

FIXTURE = ROOT / "tests" / "fixtures" / "hybrid_trace_live_partial_reconciliation.json"
LIVE = json.loads(FIXTURE.read_text(encoding="utf-8"))


SEM = "SEMANTIC"
DET = "DETERMINISTIC"
CAN = "CANONICAL_STATE"


def contribution(source, *claims, producer=None):
    """One source's contribution to one reconciliation, shaped as the engine shapes it.

    `canonical_values` is populated through the same resolver the real builder uses. Under
    `hybrid-decision-trace/1.2` a comparison needs a canonical identity on both sides to be
    provable, so a helper that omitted it would manufacture UNPROVEN rows and test nothing.
    """
    return SourceContribution(
        source=source,
        producer=producer or f"{source.lower()}:test",
        claim_types=tuple(t for t, _ in claims),
        claim_ids=tuple(f"{source.lower()}-{i}" for i, _ in enumerate(claims)),
        value_keys=tuple(svc.value_key(t, v) for t, v in claims),
        polarities=tuple("ASSERTED" for _ in claims),
        values=tuple(None for _ in claims),
        canonical_values=tuple(svc.canonical_identity(t, v)[0] for t, v in claims),
        confidences=tuple(None for _ in claims),
        withheld=True)


def row(family="vehicle.model", contributions=(), *, error=None, state=None,
        site="vehicle.identity.apply"):
    """A 1.1 reconciliation row, classified the way the engine classifies it."""
    present = svc.participating_sources(contributions)
    return ReconciliationEvidence(
        claim_family=family,
        semantic_input=("PRESENT" if SEM in present else "ABSENT"),
        ce_input=("PRESENT" if (DET in present or CAN in present) else "ABSENT"),
        classification=svc.classify_row(contributions, error_category=error),
        decision_site_id=site,
        participating_sources=tuple(dict.fromkeys(present)),
        source_evidence=tuple(contributions),
        information_state=state,
        error_category=error)


#: The evidence shape that produces each row outcome. Named for the outcome so the matrix
#: below stays readable, but every one of them is built from real contributions — the
#: classification is computed, never asserted into place.
def _agree(i):
    return (contribution(SEM, ("vehicle.model", "peugeot 208")),
            contribution(DET, ("vehicle.model", "peugeot 208")))


def _conflict(i):
    return (contribution(SEM, ("vehicle.model", "peugeot 208")),
            contribution(DET, ("vehicle.model", "ford ka")))


SHAPES = {
    Classification.AGREE: _agree,
    Classification.CONFLICT: _conflict,
    Classification.SINGLE_PRODUCER: lambda i: (
        contribution(DET, ("vehicle.model", "peugeot 208")),),
    Classification.NO_EVIDENCE: lambda i: (),
    Classification.NOT_ROUTED: lambda i: (),
    Classification.ERROR: lambda i: (),
}


def rows(*classifications):
    """Rows built from real contributions, one per requested outcome."""
    out = []
    for i, wanted in enumerate(classifications):
        contributions = SHAPES[wanted](i)
        out.append(row(family=f"family.{i}", contributions=contributions,
                       error=("ProducerError" if wanted == Classification.ERROR else None)))
    return tuple(out)


def _dc_to_dict(obj):
    """A dataclass row as the payload stores it."""
    import dataclasses
    return dataclasses.asdict(obj)


def final(reconciliations, semantic):
    """Rows as the writer stores them: settled against the turn's semantic evidence."""
    return svc.finalize_rows(reconciliations, semantic)


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
        """`AGREE if all(...) else CONFLICT`, over the labels the fixture actually holds."""
        stored = [r["classification"] for r in LIVE["reconciliation"]]
        self.assertEqual(
            Classification.AGREE if all(c == Classification.AGREE for c in stored)
            else Classification.CONFLICT,
            Classification.CONFLICT)
        self.assertEqual(LIVE["badges"][0], "CONFLICT",
                         "the fixture must keep the wrong headline it was written with")

    def test_live_04_the_corrected_algorithm_returns_trace_incomplete(self):
        """Weaker than the 1.0-era reading, and the only one the record can carry.

        `PARTIAL_RECONCILIATION` asserts that something WAS compared. This trace's one
        surviving `AGREE` row had a single contributing producer, and a 1.0 row cannot
        prove otherwise, so nothing here was compared and the honest headline says the
        trace is incomplete rather than partially reconciled.
        """
        self.assertEqual(self.headline, Classification.TRACE_INCOMPLETE)
        self.assertNotEqual(self.headline, Classification.AGREE)
        self.assertNotEqual(self.headline, Classification.CONFLICT)

    def test_live_05_the_conditions_name_both_reasons(self):
        self.assertEqual(self.conditions,
                         (Classification.NOT_ROUTED,
                          Classification.LEGACY_PROVENANCE_UNAVAILABLE))

    def test_live_06_it_is_not_called_semantic_missing(self):
        """The interpreter ran and produced three claim families. Nothing was missing."""
        self.assertNotIn(Classification.SEMANTIC_MISSING, self.conditions)
        self.assertNotEqual(self.headline, Classification.SEMANTIC_MISSING)

    def test_live_07_semantic_status_is_untouched(self):
        self.assertEqual(LIVE["semantic"]["status"], "OK")
        self.assertIn("vehicle_mentions", LIVE["semantic"]["produced_claims"])

    def test_live_08_the_reconciler_is_shown_as_not_having_received_claims(self):
        first, second = svc.row_summaries_from_payload(LIVE)
        self.assertEqual(first["effective_classification"], Classification.NOT_ROUTED)
        self.assertEqual(first["captured_classification"], "SEMANTIC_MISSING")
        self.assertTrue(first["legacy"])
        self.assertEqual(second["effective_classification"],
                         Classification.LEGACY_PROVENANCE_UNAVAILABLE)
        self.assertEqual(second["captured_classification"], "AGREE",
                         "the withdrawn label stays visible as the record of the claim")
        self.assertTrue(second["reclassified"])

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
                         Classification.TRACE_INCOMPLETE)
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
            reconciliations=rows(Classification.SINGLE_PRODUCER, Classification.AGREE),
            before=svc.snapshot_state(None, None), after=svc.snapshot_state(None, None),
            action="replied", detail=None, answer_source="CE_AI", outbound={})
        self.assertEqual(trace.badges[0], "PARTIAL RECONCILIATION")
        self.assertIn("SINGLE PRODUCER", trace.badges)
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
        return svc.classify(semantic, ce_rules, final(reconciliations, semantic))

    def test_mtx_01_single_agree_with_two_sources(self):
        self.assertEqual(self.head(rows(Classification.AGREE), sem()),
                         Classification.AGREE)

    def test_mtx_02_two_agrees(self):
        self.assertEqual(
            self.head(rows(Classification.AGREE, Classification.AGREE), sem()),
            Classification.AGREE)

    def test_mtx_03_single_conflict(self):
        self.assertEqual(self.head(rows(Classification.CONFLICT), sem()),
                         Classification.CONFLICT)

    def test_mtx_04_agree_plus_conflict_is_conflict(self):
        self.assertEqual(
            self.head(rows(Classification.AGREE, Classification.CONFLICT), sem()),
            Classification.CONFLICT)

    def test_mtx_05_one_producer_alone_is_a_single_source_decision(self):
        """Was `SEMANTIC_MISSING`. One producer decided; nothing was missing about it."""
        self.assertEqual(self.head(rows(Classification.SINGLE_PRODUCER), ABSENT_SEM),
                         Classification.SINGLE_SOURCE_DECISION)

    def test_mtx_06_agree_plus_single_with_semantic_absent(self):
        reconciliations = rows(Classification.AGREE, Classification.SINGLE_PRODUCER)
        self.assertEqual(self.head(reconciliations, ABSENT_SEM),
                         Classification.PARTIAL_RECONCILIATION)
        self.assertEqual(
            svc.supporting_conditions(ABSENT_SEM, final(reconciliations, ABSENT_SEM)),
            (Classification.SINGLE_PRODUCER,))

    def test_mtx_07_agree_plus_single_with_semantic_ok_names_the_unrouted_source(self):
        """The preserved live turn's shape, told truthfully."""
        reconciliations = rows(Classification.AGREE, Classification.SINGLE_PRODUCER)
        self.assertEqual(self.head(reconciliations, sem()),
                         Classification.PARTIAL_RECONCILIATION)
        self.assertEqual(svc.supporting_conditions(sem(), final(reconciliations, sem())),
                         (Classification.SINGLE_PRODUCER,
                          Classification.SEMANTIC_NOT_ROUTED))

    def test_mtx_08_an_errored_row_alone_cannot_be_summarised_more_strongly(self):
        self.assertEqual(self.head(rows(Classification.ERROR), ERROR_SEM),
                         Classification.TRACE_INCOMPLETE)

    def test_mtx_09_agree_plus_error_is_never_conflict(self):
        headline = self.head(rows(Classification.AGREE, Classification.ERROR), ERROR_SEM)
        self.assertEqual(headline, Classification.PARTIAL_RECONCILIATION)
        self.assertNotEqual(headline, Classification.CONFLICT)

    def test_mtx_10_a_row_that_received_nothing_is_no_comparison(self):
        self.assertEqual(self.head(rows(Classification.NO_EVIDENCE), ABSENT_SEM),
                         Classification.NO_COMPARISON)
        self.assertNotEqual(self.head(rows(Classification.NO_EVIDENCE), sem()),
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
                  Classification.SINGLE_PRODUCER, Classification.NO_EVIDENCE,
                  Classification.ERROR)

    def test_iff_01_every_single_row_combination(self):
        for value in self.ROW_VALUES:
            for semantic in (sem(), ABSENT_SEM, ERROR_SEM):
                with self.subTest(row=value, semantic=semantic.status):
                    headline = svc.classify(semantic, (),
                                            final(rows(value), semantic))
                    self.assertEqual(headline == Classification.CONFLICT,
                                     value == Classification.CONFLICT)

    def test_iff_02_every_pair(self):
        for a in self.ROW_VALUES:
            for b in self.ROW_VALUES:
                with self.subTest(pair=(a, b)):
                    headline = svc.classify(sem(), (), final(rows(a, b), sem()))
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
            reconciliations=rows(Classification.AGREE, Classification.SINGLE_PRODUCER),
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
                         Classification.TRACE_INCOMPLETE)
        self.assertEqual(out["supporting_conditions"],
                         [Classification.NOT_ROUTED,
                          Classification.LEGACY_PROVENANCE_UNAVAILABLE])

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
        return render_turn_trace_page({
            "captured": True, "trace": payload,
            "captured_classification": payload["badges"][0],
            "effective_classification": headline,
            "supporting_conditions": list(conditions),
            "reconciliation_rows": list(svc.row_summaries_from_payload(payload)),
            "reconciliation_counts": svc.counts_from_payload(payload)})

    def test_word_01_not_routed_sentence_is_shown(self):
        from app.ui.hybrid_trace_view import NOT_ROUTED_SENTENCE
        self.assertIn(NOT_ROUTED_SENTENCE, self.page())

    def test_word_02_the_absent_sentence_is_not_shown_for_this_turn(self):
        from app.ui.hybrid_trace_view import NO_SEMANTIC_SENTENCE
        self.assertNotIn(NO_SEMANTIC_SENTENCE, self.page())

    def test_word_03_the_page_states_there_was_no_contradiction(self):
        self.assertIn("Ninguna fila prueba una contradicción entre fuentes distintas "
                      "en este turno", self.page())

    def test_word_04_a_genuinely_absent_producer_says_so(self):
        payload = json.loads(json.dumps(LIVE))
        payload["semantic"]["status"] = "ABSENT"
        payload["semantic"]["produced_claims"] = []
        payload["semantic"]["evidence"] = None
        from app.ui.hybrid_trace_view import NOT_ROUTED_SENTENCE, NO_SEMANTIC_SENTENCE
        page = self.page(payload)
        self.assertIn(NO_SEMANTIC_SENTENCE, page)
        self.assertNotIn(NOT_ROUTED_SENTENCE, page)

    def test_word_05_no_row_is_described_as_agreement(self):
        """Nothing on this turn was compared, so nothing on the page may say it was."""
        page = self.page()
        self.assertNotIn("compararon dos o más fuentes distintas", page)
        self.assertIn("no se puede probar acuerdo ni contradicción a nivel de fila".lower(),
                      page.lower())

    def test_ui_01_the_headline_is_trace_incomplete(self):
        badges = self.page().split('class="badges"')[1].split("</div>")[0]
        self.assertIn("TRACE INCOMPLETE", badges)
        self.assertIn("NOT ROUTED", badges)
        self.assertIn("LEGACY PROVENANCE UNAVAILABLE", badges)
        self.assertIn("HYBRID CONVERSATION TRACE", badges)
        self.assertIn("FALLBACK", badges)
        self.assertNotIn("AGREE", badges)

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

    def test_ui_05_every_label_the_page_can_show_is_in_the_glossary(self):
        from app.ui.hybrid_trace_view import LABEL_GLOSSARY
        documented = {label for label, _ in LABEL_GLOSSARY}
        for label in ("PARTIAL RECONCILIATION", "SEMANTIC NOT ROUTED", "SINGLE PRODUCER",
                      "NO EVIDENCE", "NOT ROUTED", "ERROR",
                      "LEGACY PROVENANCE UNAVAILABLE", "SINGLE SOURCE DECISION",
                      "NO COMPARISON", "TRACE INCOMPLETE"):
            with self.subTest(label=label):
                self.assertIn(label, documented)

    def test_ui_06_a_real_conflict_still_reads_conflict(self):
        """A 1.1 row with two incompatible sources. A relabelled 1.0 row would not do:
        under the corrected contract a stored label is no longer what makes a conflict."""
        payload = json.loads(json.dumps(LIVE))
        payload["trace_version"] = "hybrid-decision-trace/1.1"
        payload["reconciliation"][0] = json.loads(json.dumps(
            _dc_to_dict(row("vehicle.model", _conflict(0)))))
        badges = self.page(payload).split('class="badges"')[1].split("</div>")[0]
        self.assertIn("CONFLICT", badges)
        self.assertNotIn("PARTIAL RECONCILIATION", badges)
        self.assertNotIn("TRACE INCOMPLETE", badges)


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
