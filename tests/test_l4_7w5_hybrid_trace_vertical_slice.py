"""L4.7W5 — hybrid decision trace, Gates 1 and 2, end to end.

The Wild that motivated this could not be read back. The evidence that existed recorded
only the WAMID that triggered the call, so a two-message burst could not be reconstructed;
no reconciliation record was written for the turn that went wrong; and nothing anywhere
said which authority produced the answer. Every test below pins one of those gaps shut.

The invariant the whole slice rests on: **the inspector observes, it never participates.**
It may not change routing, may not wait on a producer, may not re-derive a value, and may
not cost a customer turn when it fails.

TRC-01..09   contract — hashing, fingerprints, allowlisted state
CLS-01..06   classification and result kind, including the floor/reconciliation boundary
CE-01..06    engine integration, fail-open, flag control
API-01..04   read-only endpoints, absence rendered as absence
UI-01..05    rendering, including the not-captured page and secret hygiene
MIG-01..03   the migration is additive and chains correctly
"""
from __future__ import annotations

import ast
import json
import pathlib
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

import app.models
from app.models import HybridDecisionTraceRow
from app.routes.ops_dashboard import TRACE_NOT_CAPTURED, get_turn, get_turns
from app.schemas.hybrid_trace import (
    TRACE_VERSION, CanonicalSnapshot, Classification, HybridDecisionTrace,
    ReconciliationEvidence, ResultKind, RuleEvidence, SemanticEvidence,
    burst_hash, inputs_digest, normalize_burst, token_fingerprint,
)
from app.services import hybrid_trace as svc
from app.ui.hybrid_trace_view import render_turn_not_captured, render_turn_trace_page

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")
MIGRATION = (ROOT / "backend" / "migrations" / "versions"
             / "20260918_hybrid_decision_traces.py").read_text(encoding="utf-8-sig")


SECRET_TOKEN = "booking-token-A1B2C3D4E5"


def _db() -> Session:
    # `app.models.Base`, not `app.db.Base`: in a shared pytest session the modules can be
    # imported in an order that leaves the two registries distinct, and create_all() on the
    # wrong one silently creates nothing (M21.3-TRACE-HARDENING-FINAL hit this exact trap).
    engine = create_engine("sqlite://")
    app.models.Base.metadata.create_all(engine)
    return Session(engine)


def _trace(**over) -> HybridDecisionTrace:
    base = dict(
        turn_id="turn-1", thread_id=2053, lead_id=7, deployment_sha="ce978a2",
        started_at="2026-09-18T10:00:00.000+00:00",
        completed_at="2026-09-18T10:00:01.000+00:00", duration_ms=1000,
        ordered_message_ids=("wamid.A", "wamid.B"),
        message_timestamps=("2026-09-18T09:59:58+00:00", "2026-09-18T09:59:59+00:00"),
        burst_texts=["Mmm, no me sirve", "No tenes algo más temprano ?"],
        semantic=SemanticEvidence(status="PENDING", dispatch="async"),
        ce_rules=(RuleEvidence(rule_id="scheduling.earliest_option_rejected",
                               rule_version="1.0", value=True,
                               inputs_digest=inputs_digest("x"), note="burst"),),
        reconciliations=(), before=CanonicalSnapshot(stage="SCHEDULING"),
        after=CanonicalSnapshot(stage="SCHEDULING", needs_human=True),
        action="skipped_human", detail=None, answer_source=None, outbound={},
    )
    base.update(over)
    return svc.build_trace(**base)


# ── TRC: the contract ────────────────────────────────────────────────────────

class TraceContract(unittest.TestCase):

    def test_trc_01_version_is_pinned(self):
        """1.1 since L4.7W5-TRACE-ROW-SEMANTICS. 1.0 stays readable, and is named."""
        from app.schemas.hybrid_trace import READABLE_VERSIONS, TRACE_VERSION_1_0
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.1")
        self.assertEqual(TRACE_VERSION_1_0, "hybrid-decision-trace/1.0")
        self.assertIn(TRACE_VERSION_1_0, READABLE_VERSIONS)
        self.assertEqual(_trace().trace_version, TRACE_VERSION)

    def test_trc_02_normalization_folds_case_and_accents(self):
        self.assertEqual(normalize_burst(["Más TEMPRANO"]), normalize_burst(["mas temprano"]))

    def test_trc_03_hash_is_order_sensitive(self):
        a = burst_hash(["uno", "dos"])
        b = burst_hash(["dos", "uno"])
        self.assertNotEqual(a, b, "arrival order changed the outcome in the live Wild")

    def test_trc_04_message_boundaries_survive_the_hash(self):
        """Two messages are not the same input as one message holding both words."""
        self.assertNotEqual(burst_hash(["no me sirve", "algo mas temprano"]),
                            burst_hash(["no me sirve algo mas temprano"]))

    def test_trc_05_same_burst_hashes_identically(self):
        self.assertEqual(burst_hash(["  Hola   mundo "]), burst_hash(["hola mundo"]))

    def test_trc_06_token_fingerprint_never_carries_the_token(self):
        token = "booking_token_super_secret_value"
        fp = token_fingerprint(token)
        self.assertNotIn(token, fp)
        self.assertNotIn(token[:8], fp)
        self.assertEqual(len(fp), 8)
        self.assertEqual(fp, token_fingerprint(token), "must be stable across turns")
        self.assertNotEqual(fp, token_fingerprint(token + "x"))

    def test_trc_07_snapshot_is_allowlisted_and_holds_no_orm_object(self):
        class FakeState:
            stage = "SCHEDULING"
            needs_human = True
            flow_booking_token = "tok-abc"
            secret_internal = "must not appear"
        snap = svc.snapshot_state(FakeState(), None)
        payload = json.dumps(snap.__dict__, default=str)
        self.assertNotIn("must not appear", payload)
        self.assertNotIn("tok-abc", payload, "the token itself may never be snapshotted")
        self.assertTrue(snap.booking_token_present)
        self.assertEqual(snap.booking_token_fingerprint, token_fingerprint("tok-abc"))
        self.assertTrue(snap.offer_outstanding, "a live token is an outstanding offer")

    def test_trc_08_snapshot_survives_a_state_that_is_none(self):
        snap = svc.snapshot_state(None, None)
        self.assertIsInstance(snap, CanonicalSnapshot)
        self.assertIsNone(snap.stage)

    def test_trc_09_payload_is_json_serialisable(self):
        payload = _trace().to_payload()
        json.dumps(payload)              # must not raise
        self.assertEqual(payload["message_count"], 2)
        self.assertEqual(payload["ordered_message_ids"], ["wamid.A", "wamid.B"])


# ── CLS: classification and result kind ──────────────────────────────────────

class Classifying(unittest.TestCase):

    def test_cls_01_no_rule_when_ce_fired_and_nobody_owned_it(self):
        """The failed Wild exactly: a floor fired, no claim represented it, no reconciler ran."""
        fired = (RuleEvidence(rule_id="scheduling.earliest_option_rejected",
                              rule_version="1.0", value=True),)
        self.assertEqual(
            svc.classify(SemanticEvidence(status="PENDING"), fired, ()),
            Classification.NO_RULE)

    def test_cls_02_conflict_when_a_reconciliation_disagrees(self):
        rec = (ReconciliationEvidence(claim_family="VEHICLE_MODEL", semantic_input="PRESENT",
                                      ce_input="PRESENT",
                                      classification=Classification.CONFLICT),)
        self.assertEqual(svc.classify(SemanticEvidence(status="OK"), (), rec),
                         Classification.CONFLICT)

    def test_cls_03_agree_only_when_every_reconciliation_agrees(self):
        agree = ReconciliationEvidence(claim_family="A", semantic_input="PRESENT",
                                       ce_input="PRESENT",
                                       classification=Classification.AGREE)
        self.assertEqual(svc.classify(SemanticEvidence(status="OK"), (), (agree, agree)),
                         Classification.AGREE)

    def test_cls_04_semantic_error_outranks_missing_claims(self):
        self.assertEqual(svc.classify(SemanticEvidence(status="ERROR"), (), ()),
                         Classification.SEMANTIC_ERROR)

    def test_cls_05_a_floor_never_reads_as_reconciliation(self):
        trace = _trace()
        self.assertEqual(trace.result_kind, ResultKind.DETERMINISTIC_FLOOR)
        self.assertNotEqual(trace.result_kind, ResultKind.RECONCILED)
        self.assertIn("DETERMINISTIC FLOOR", trace.badges)

    def test_cls_06_blocked_is_not_allowed(self):
        trace = _trace(action="blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        self.assertEqual(trace.result_kind, ResultKind.BLOCKED)
        self.assertFalse(trace.allowed)


class ProducerAdapters(unittest.TestCase):

    class _Provider:
        def __init__(self, ready, result=None):
            self._ready, self._result, self.waited = ready, result, False

        @property
        def ready(self):
            return self._ready

        def get(self, timeout=None):
            self.waited = True
            return self._result

    def test_cls_07_pending_semantic_is_never_waited_on(self):
        provider = self._Provider(ready=False)
        sem = svc.semantic_evidence_from(provider, "async")
        self.assertEqual(sem.status, "PENDING")
        self.assertFalse(provider.waited,
                         "blocking to prettify a trace would alter the turn it observes")

    def test_cls_08_an_absent_provider_is_absent_not_an_error(self):
        self.assertEqual(svc.semantic_evidence_from(None, "async").status, "ABSENT")

    def test_cls_09_information_state_no_longer_decides_the_row(self):
        """`BOTH` with no claims is not a conflict — nobody was there to disagree.

        This assertion used to read CONFLICT. `InformationState` describes the polarity of
        the evidence about a claim type; with an empty claim set there is no evidence and
        no producer, and calling that a contradiction between engines was the defect
        L4.7W5-TRACE-ROW-SEMANTICS exists to remove. The state is still recorded on the row.
        """
        class Rec:
            claim_type, information_state = "VEHICLE_MODEL", "BOTH"
            rule_id, rule_version, outcome, reason = "r", "1.0", "NEEDS_HUMAN", "why"
            evidence_ids = ()
        out = svc.reconciliation_from(Rec(), ())
        self.assertEqual(out.classification, Classification.NO_EVIDENCE)
        self.assertEqual(out.information_state, "BOTH")
        self.assertEqual(out.participating_sources, ())
        self.assertEqual(out.semantic_input, "ABSENT")
        self.assertEqual(out.ce_input, "ABSENT")


# ── CE: engine integration ───────────────────────────────────────────────────

class EngineIntegration(unittest.TestCase):

    def _engine(self, enabled: bool):
        from app.services.conversation_engine import ConversationEngine
        from app.settings import Settings
        db = _db()
        settings = Settings(hybrid_trace_enabled=enabled)
        return ConversationEngine(db, settings), db

    def test_ce_01_disabled_by_default(self):
        from app.settings import Settings
        self.assertFalse(Settings().hybrid_trace_enabled)

    def test_ce_02_flag_off_writes_nothing(self):
        engine, db = self._engine(False)
        engine._capture_trace_before(None, None)
        engine._collect_trace_reconciliation(object(), ())
        self.assertEqual(db.query(HybridDecisionTraceRow).count(), 0)

    def test_ce_03_handle_writes_exactly_one_row_per_turn(self):
        from app.schemas.conversation import ConversationHandleIn
        from app.services.conversation_engine import _out
        engine, db = self._engine(True)
        engine._handle = lambda event: _out("replied")
        event = ConversationHandleIn(thread_id=1, wa_message_id="wamid.T", wa_id="549110",
                                     text="hola")
        out = engine.handle(event)
        rows = db.query(HybridDecisionTraceRow).all()
        self.assertEqual(out.action, "replied")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].turn_id, engine._correlation_id)
        self.assertEqual(rows[0].thread_id, 1)

    def test_ce_04_a_trace_failure_never_costs_the_turn(self):
        from app.schemas.conversation import ConversationHandleIn
        from app.services.conversation_engine import _out
        engine, db = self._engine(True)
        engine._handle = lambda event: _out("replied")
        db.close()                        # every trace write will now fail
        out = engine.handle(ConversationHandleIn(thread_id=1, wa_message_id="wamid.T",
                                                 wa_id="549110", text="hola"))
        self.assertEqual(out.action, "replied", "the answer must survive a trace defect")

    def test_ce_05_the_full_ordered_burst_is_recorded(self):
        """Gate 1: the shadow record carried only the triggering WAMID."""
        tree = ast.parse(CE_SOURCE)
        writer = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_write_hybrid_trace")
        src = ast.get_source_segment(CE_SOURCE, writer) or ""
        self.assertIn("_turn_trace_rows", src)
        self.assertIn("ordered_message_ids", src)
        self.assertIn('self._turn_trace_rows = [', CE_SOURCE,
                      "the burst rows must be captured from _fetch_burst_messages")

    def test_ce_06_the_writer_runs_after_the_answer_is_final(self):
        pos_source = CE_SOURCE.index("out.contributing_sources = getattr")
        pos_write = CE_SOURCE.index("self._write_hybrid_trace(event, out")
        self.assertLess(pos_source, pos_write,
                        "the trace must observe the outcome, not precede it")
        self.assertIn("def _capture_trace_before", CE_SOURCE)

    def test_ce_07_persist_is_fail_open(self):
        class Broken:
            def add(self, *a): raise RuntimeError("db gone")
            def commit(self): raise RuntimeError("db gone")
            def rollback(self): raise RuntimeError("worse")
        self.assertFalse(svc.persist(Broken(), _trace()))     # must not raise

    def test_ce_08_persist_round_trips_the_payload(self):
        db = _db()
        self.assertTrue(svc.persist(db, _trace()))
        row = db.query(HybridDecisionTraceRow).one()
        self.assertEqual(row.classification, "NO_RULE")
        self.assertEqual(row.result_kind, ResultKind.DETERMINISTIC_FLOOR)
        self.assertEqual(row.semantic_status, "PENDING")
        self.assertEqual(row.payload["trace_version"], TRACE_VERSION)


# ── API ──────────────────────────────────────────────────────────────────────

class ReadOnlyApi(unittest.TestCase):

    def test_api_01_unknown_turn_is_not_captured(self):
        out = get_turn("no-such-turn", _db())
        self.assertFalse(out["captured"])
        self.assertEqual(out["reason"], TRACE_NOT_CAPTURED)
        self.assertNotIn("trace", out, "absence must not be rendered as an empty decision")

    def test_api_02_a_stored_turn_comes_back_whole(self):
        db = _db()
        svc.persist(db, _trace())
        out = get_turn("turn-1", db)
        self.assertTrue(out["captured"])
        self.assertEqual(out["trace"]["ordered_message_ids"], ["wamid.A", "wamid.B"])

    def test_api_03_listing_is_filterable_and_newest_first(self):
        db = _db()
        svc.persist(db, _trace(turn_id="t1", thread_id=1))
        svc.persist(db, _trace(turn_id="t2", thread_id=2))
        listed = get_turns(thread_id=None, result_kind=None, classification=None,
                           limit=50, db=db)
        self.assertEqual([t["turn_id"] for t in listed["turns"]], ["t2", "t1"])
        filtered = get_turns(thread_id=1, result_kind=None, classification=None,
                             limit=50, db=db)
        self.assertEqual(filtered["count"], 1)

    def test_api_04_no_message_text_is_served(self):
        db = _db()
        svc.persist(db, _trace())
        blob = json.dumps(get_turn("turn-1", db))
        self.assertNotIn("no me sirve", blob,
                         "customer words belong to the thread view, not to two places")


# ── UI ───────────────────────────────────────────────────────────────────────

class Rendering(unittest.TestCase):

    def _html(self):
        db = _db()
        svc.persist(db, _trace(
            reconciliations=(ReconciliationEvidence(
                claim_family="VEHICLE_MODEL", semantic_input="ABSENT", ce_input="PRESENT",
                classification=Classification.AGREE, rule_id="vehicle.identity",
                rule_version="1.0", outcome="ACCEPT", reason_code="supported"),),
            before=CanonicalSnapshot(stage="QUOTED", booking_token_present=True,
                                     booking_token_fingerprint=token_fingerprint(SECRET_TOKEN)),
            after=CanonicalSnapshot(stage="SCHEDULING", needs_human=True)))
        return render_turn_trace_page(get_turn("turn-1", db))

    def test_ui_01_not_captured_page_says_exactly_that(self):
        html = render_turn_not_captured("turn-x")
        self.assertIn(TRACE_NOT_CAPTURED, html)
        self.assertIn("turn-x", html)

    def test_ui_02_an_uncaptured_record_renders_the_not_captured_page(self):
        html = render_turn_trace_page(get_turn("missing", _db()))
        self.assertIn(TRACE_NOT_CAPTURED, html)

    def test_ui_03_every_evidence_section_is_rendered(self):
        html = self._html()
        for section in ("Turno del cliente", "Motor semántico", "Motor determinista",
                        "Reconciliación", "Estado canónico", "Resultado"):
            self.assertIn(section, html)
        self.assertIn("wamid.A", html)
        self.assertIn("scheduling.earliest_option_rejected", html)
        self.assertIn("DETERMINISTIC FLOOR", html)

    def test_ui_04_the_state_transition_is_visible(self):
        html = self._html()
        self.assertIn("QUOTED", html)
        self.assertIn("SCHEDULING", html)
        self.assertIn("changed", html, "a changed field must be marked, not just listed")

    def test_ui_05_no_token_and_no_customer_text_reaches_the_page(self):
        html = self._html()
        self.assertNotIn(SECRET_TOKEN, html, "the booking token may never be rendered")
        self.assertNotIn("no me sirve", html, "customer words stay in the thread view")
        self.assertIn(token_fingerprint(SECRET_TOKEN), html,
                      "the fingerprint proves the token changed, safely")

    def test_ui_06_the_control_page_links_to_the_trace(self):
        control = (ROOT / "backend" / "app" / "ui"
                   / "control_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("/control/turn/", control)
        self.assertIn("/api/ops/turns", control)
        kanban = (ROOT / "backend" / "app" / "ui" / "kanban.py").read_text(encoding="utf-8-sig")
        self.assertIn('@router.get("/control/turn/{turn_id}"', kanban)


# ── Migration ────────────────────────────────────────────────────────────────

class Migration(unittest.TestCase):

    def test_mig_01_chains_onto_the_current_head(self):
        self.assertIn('revision: str = "20260918_hybrid_traces"', MIGRATION)
        self.assertIn('down_revision: str = "20260906_pending_location_proposal"', MIGRATION)

    def test_mig_02_is_purely_additive(self):
        upgrade = MIGRATION.split("def upgrade")[1].split("def downgrade")[0]
        for forbidden in ("drop_table", "drop_column", "alter_column", "drop_constraint",
                          "execute("):
            self.assertNotIn(forbidden, upgrade,
                             "an additive migration touches nothing that already exists")

    def test_mig_03_creates_the_table_the_model_declares(self):
        self.assertIn('"hybrid_decision_traces"', MIGRATION)
        self.assertEqual(HybridDecisionTraceRow.__tablename__, "hybrid_decision_traces")
        for column in ("turn_id", "thread_id", "lead_id", "deployment_sha", "input_hash",
                       "message_count", "result_kind", "classification", "semantic_status",
                       "payload", "created_at"):
            self.assertIn(f'"{column}"', MIGRATION)
            self.assertTrue(hasattr(HybridDecisionTraceRow, column))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
