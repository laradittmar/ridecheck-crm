"""L4.7W5-HYBRID-TRACE-HARDENING — the three guarantees that had no permanent test.

The vertical slice proved its behaviour in a scratchpad harness. Harness evidence dies with
the session; the properties it proved are precisely the ones a later change can break in
silence. Each class below turns one of them into a test that fails loudly.

BND-01..09  the burst boundary the semantic engine and the deterministic rules actually saw
ASY-01..04  the two pre-gates that see a WIDER boundary — pinned as asymmetric, not as equal
NEU-01..08  externally observable behaviour with tracing enabled, off, and broken three ways
AUTH-01..13 the trace endpoints require the CRM session; the rest of /api/ops does not change
SCP-01..07  scope labels: a conversational decision is never confused with a Flow transaction
FLW-01..03  the Flow Data Exchange path is untouched and is not represented as hybrid

On the burst boundary, the claim this file makes is the one that is true and measurable:
the semantic engine and the scheduling/handoff rules receive the same ORDERED CONTENT, and
the trace hashes exactly that content. Object identity holds at the top-level call sites but
not everywhere below them — some handlers copy the list — so identity is not the invariant
worth asserting. Content and order are, because a future change that hands one engine a
different burst changes content, and that is what these tests detect.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import pathlib
import sys
import types
import unittest
from unittest import mock

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
import app.services.conversation_engine as ce
from app.models import (HybridDecisionTraceRow, Lead, WhatsAppContact, WhatsAppMessage,
                        WhatsAppThread, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn
from app.schemas.hybrid_trace import burst_hash
from app.settings import Settings

UTC = dt.timezone.utc
CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")

# The failed Wild's own burst. Kept as module constants so every test that claims to cover
# it is demonstrably using the same two sentences.
WILD_FIRST = "Mmm, no me sirve"
WILD_SECOND = "¿No tenés algo más temprano?"
WILD_BURST = [WILD_FIRST, WILD_SECOND]


def _db() -> Session:
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session, db_texts) -> tuple:
    """A thread mid-scheduling with `db_texts` persisted after the processed cursor."""
    contact = WhatsAppContact(wa_id="5491133334444", display_name="Tester")
    db.add(contact)
    db.flush()
    lead = Lead(nombre="Tester", telefono="5491133334444")
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread)
    db.flush()
    base = dt.datetime(2026, 9, 17, 18, 40, 0, tzinfo=UTC)
    db.add(WhatsAppMessage(thread_id=thread.id, wa_message_id="wamid.PREV", direction="in",
                           timestamp=base, text="Dale", message_type="text"))
    for index, text in enumerate(db_texts):
        db.add(WhatsAppMessage(
            thread_id=thread.id, wa_message_id=f"wamid.B{index}", direction="in",
            timestamp=base + dt.timedelta(seconds=10 * (index + 1)), text=text,
            message_type="text"))
    db.add(WhatsAppThreadState(
        thread_id=thread.id, last_stage="SCHEDULING",
        last_processed_inbound_wa_message_id="wamid.PREV",
        last_visible_slots='["2026-09-18 09:00"]',
        last_offered_slots='["2026-09-18 09:00"]'))
    db.commit()
    return contact, lead, thread


class _Observation:
    """What each producer was actually handed during one real `handle()` call."""

    def __init__(self):
        self.semantic = None
        self.scheduling_rule = None
        self.pre_gate = None
        self.out = None
        self.trace = None


def _observe(db: Session, thread, contact, n8n_list, *, trigger_wamid, trace_enabled=True,
             settings=None) -> _Observation:
    """Run one REAL turn and record the burst each producer received.

    The interception is deliberately at the two ends that matter: the single call that hands
    the burst to the semantic engine, and a deterministic rule reached on the scheduling
    path. Both wrappers delegate to the original, so routing is unchanged by observing it.
    """
    obs = _Observation()
    engine = ce.ConversationEngine(
        db, settings or Settings(hybrid_trace_enabled=trace_enabled))

    def capture_shadow(self, ctx, event, messages):
        obs.semantic = list(messages)
        self._turn_semantic = None       # no model call, and no provider to read

    engine._run_shadow_understand = types.MethodType(capture_shadow, engine)

    original_human = ce._is_human_request
    original_phone = ce._is_phone_call_request

    def wrapped_human(texts, *a, **k):
        if obs.scheduling_rule is None:
            obs.scheduling_rule = list(texts)
        return original_human(texts, *a, **k)

    def wrapped_phone(texts, *a, **k):
        if obs.pre_gate is None:         # the FIRST call is the Layer-B pre-gate
            obs.pre_gate = list(texts)
        return original_phone(texts, *a, **k)

    ce._is_human_request = wrapped_human
    ce._is_phone_call_request = wrapped_phone
    try:
        obs.out = engine.handle(ConversationHandleIn(
            thread_id=thread.id, wa_message_id=trigger_wamid,
            wa_id=contact.wa_id, text=n8n_list[-1],
            unanswered_recent_user_messages=list(n8n_list)))
    finally:
        ce._is_human_request = original_human
        ce._is_phone_call_request = original_phone
    try:
        row = db.query(HybridDecisionTraceRow).first()
    except Exception:
        row = None               # the "no table" case is one of the cases under test
        db.rollback()
    obs.trace = row.payload if row is not None else None
    return obs


# ── BND: the shared burst boundary ───────────────────────────────────────────

class SharedBurstBoundary(unittest.TestCase):
    """One real turn, the Wild's own burst, observed at both producers."""

    @classmethod
    def setUpClass(cls):
        cls.db = _db()
        contact, _lead, thread = _seed(cls.db, WILD_BURST)
        cls.obs = _observe(cls.db, thread, contact, WILD_BURST, trigger_wamid="wamid.B1")

    def test_bnd_01_the_semantic_engine_received_the_complete_ordered_burst(self):
        self.assertEqual(self.obs.semantic, WILD_BURST)

    def test_bnd_02_a_deterministic_rule_received_the_same_ordered_burst(self):
        self.assertIsNotNone(self.obs.scheduling_rule,
                             "no deterministic rule ran — the fixture no longer reaches "
                             "the scheduling path and this test proves nothing")
        self.assertEqual(self.obs.scheduling_rule, self.obs.semantic)

    def test_bnd_03_the_trace_boundary_is_that_same_burst(self):
        self.assertIsNotNone(self.obs.trace, "the turn produced no trace")
        self.assertEqual(self.obs.trace["input_hash"], burst_hash(self.obs.semantic))
        self.assertEqual(self.obs.trace["input_hash"], burst_hash(self.obs.scheduling_rule))

    def test_bnd_04_the_wild_burst_is_the_one_under_test(self):
        self.assertEqual(self.obs.semantic, [WILD_FIRST, WILD_SECOND])
        self.assertEqual(self.obs.trace["ordered_message_ids"], ["wamid.B0", "wamid.B1"])
        self.assertEqual(self.obs.trace["message_count"], 2)

    def test_bnd_05_order_changes_the_hash(self):
        self.assertNotEqual(burst_hash(WILD_BURST), burst_hash(list(reversed(WILD_BURST))))

    def test_bnd_06_joining_the_burst_changes_the_hash(self):
        self.assertNotEqual(burst_hash(WILD_BURST), burst_hash([" ".join(WILD_BURST)]))

    def test_bnd_07_removing_a_message_changes_the_hash(self):
        self.assertNotEqual(burst_hash(WILD_BURST), burst_hash([WILD_SECOND]))
        self.assertNotEqual(burst_hash(WILD_BURST), burst_hash([WILD_FIRST]))

    def test_bnd_08_adding_a_message_changes_the_hash(self):
        self.assertNotEqual(burst_hash(WILD_BURST), burst_hash(WILD_BURST + ["y bueno"]))

    def test_bnd_09_one_variable_feeds_both_producers(self):
        """Structural backstop: both call sites must still name `ai_input_messages`.

        The runtime tests above compare content. This one compares source, so a change that
        hands a *different but coincidentally equal* list to one engine is still caught.
        """
        self.assertIn("self._run_shadow_understand(ctx, event, ai_input_messages)", CE_SOURCE)
        self.assertIn("self._turn_burst_texts = list(ai_input_messages or [])", CE_SOURCE)
        for call in ("_is_human_request(ai_input_messages)",
                     "self._earliest_option_rejected(ai_input_messages)",
                     "_should_escalate_scheduling_to_human(ai_input_messages, state)"):
            self.assertIn(call, CE_SOURCE, f"{call} no longer reads ai_input_messages")


# ── ASY: the disclosed pre-gate asymmetry ────────────────────────────────────

class PreGateAsymmetry(unittest.TestCase):
    """The motorcycle and phone-call pre-gates read `_current_evidence`, not the burst.

    After WILD-04R burst completion `_current_evidence` can be a strict superset: it holds
    messages n8n omitted but the database has. Those two gates therefore decide on evidence
    the trace does not hash. This is pre-existing engine behaviour, and these tests exist so
    it is recorded as an asymmetry rather than silently described as agreement.
    """

    @classmethod
    def setUpClass(cls):
        cls.db = _db()
        cls.db_texts = ["Hola de nuevo"] + WILD_BURST     # the DB has three
        contact, _lead, thread = _seed(cls.db, cls.db_texts)
        cls.obs = _observe(cls.db, thread, contact, WILD_BURST,   # n8n sent two
                           trigger_wamid="wamid.B2")

    def test_asy_01_the_pre_gate_sees_a_wider_boundary(self):
        self.assertEqual(self.obs.pre_gate, self.db_texts)
        self.assertEqual(self.obs.semantic, WILD_BURST)
        self.assertGreater(len(self.obs.pre_gate), len(self.obs.semantic),
                           "the asymmetry this test pins no longer reproduces")

    def test_asy_02_the_trace_hashes_the_narrow_boundary_not_the_wide_one(self):
        self.assertEqual(self.obs.trace["input_hash"], burst_hash(self.obs.semantic))
        self.assertNotEqual(self.obs.trace["input_hash"], burst_hash(self.obs.pre_gate))

    def test_asy_03_message_count_follows_the_database_not_the_hash(self):
        """Disclosed: ordered ids come from the DB burst, the hash from n8n's list."""
        self.assertEqual(self.obs.trace["ordered_message_ids"],
                         ["wamid.B0", "wamid.B1", "wamid.B2"])
        self.assertEqual(self.obs.trace["message_count"], 3)
        self.assertNotEqual(self.obs.trace["message_count"], len(self.obs.semantic))

    def test_asy_04_the_two_pre_gate_call_sites_are_still_the_only_ones(self):
        self.assertIn("current_turn_text = \" \".join(_current_evidence)", CE_SOURCE)
        self.assertIn("if _is_phone_call_request(_current_evidence):", CE_SOURCE)
        predicate_calls = [line.strip() for line in CE_SOURCE.splitlines()
                           if "(_current_evidence)" in line
                           and "len(" not in line and "set(" not in line
                           and "list(" not in line]
        self.assertEqual(len(predicate_calls), 2, f"a third consumer of the wider list "
                         f"appeared, so the trace's boundary claim must be re-examined: "
                         f"{predicate_calls}")


# ── NEU: behavioural neutrality and fail-open ────────────────────────────────

class TraceNeutrality(unittest.TestCase):
    """Externally observable behaviour must not depend on the inspector, at all."""

    @staticmethod
    def _external(obs, db) -> dict:
        out = obs.out
        state = db.query(WhatsAppThreadState).one()
        lead = db.query(Lead).one()
        return {
            "action": out.action, "detail": out.detail, "ok": out.ok,
            "handled": out.handled, "answer_source": out.answer_source,
            "reply_produced": out.reply_produced, "reply_required": out.reply_required,
            "stage": state.last_stage, "needs_human": state.needs_human,
            "booking_token": state.flow_booking_token,
            "lead_estado": lead.estado, "lead_needs_human": lead.necesita_humano,
            "bookings": db.query(models.ThreadRevision).count(),
            "outbound_rows": db.query(WhatsAppMessage).filter(
                WhatsAppMessage.direction == "out").count(),
            "outbound_blocked": db.query(WhatsAppMessage).filter(
                WhatsAppMessage.direction == "out",
                WhatsAppMessage.status == "blocked").count(),
        }

    def _run(self, mode) -> tuple:
        db = _db()
        contact, _lead, thread = _seed(db, WILD_BURST)
        if mode == "no_table":
            HybridDecisionTraceRow.__table__.drop(db.get_bind())
        obs = _observe(db, thread, contact, WILD_BURST, trigger_wamid="wamid.B1",
                       trace_enabled=(mode != "disabled"))
        return obs, db

    def _run_broken_store(self):
        db = _db()
        contact, _lead, thread = _seed(db, WILD_BURST)
        original = ce.ConversationEngine._write_hybrid_trace

        def dead_store(self, event, out, started_at, duration_ms):
            class Dead:
                def add(self, *a): raise OSError("trace store unavailable")
                def commit(self): raise OSError("trace store unavailable")
                def rollback(self): raise OSError("trace store unavailable")
                def execute(self, *a, **k): raise OSError("trace store unavailable")
            real, self.db = self.db, Dead()
            try:
                return original(self, event, out, started_at, duration_ms)
            finally:
                self.db = real

        ce.ConversationEngine._write_hybrid_trace = dead_store
        try:
            obs = _observe(db, thread, contact, WILD_BURST, trigger_wamid="wamid.B1")
        finally:
            ce.ConversationEngine._write_hybrid_trace = original
        return obs, db

    def _run_malformed(self):
        db = _db()
        contact, _lead, thread = _seed(db, WILD_BURST)
        original = ce.ConversationEngine._write_hybrid_trace

        def poisoned(self, event, out, started_at, duration_ms):
            self._turn_trace_reconciliations = ["not-a-record", None, 42]
            self._turn_trace_before = "not-a-snapshot"
            self._turn_trace_rows = [{"db_id": object()}]
            self._turn_trace_ctx = types.SimpleNamespace(
                thread=None, lead=object(), state=object())
            return original(self, event, out, started_at, duration_ms)

        ce.ConversationEngine._write_hybrid_trace = poisoned
        try:
            obs = _observe(db, thread, contact, WILD_BURST, trigger_wamid="wamid.B1")
        finally:
            ce.ConversationEngine._write_hybrid_trace = original
        return obs, db

    @classmethod
    def setUpClass(cls):
        cls.cases = {}

    def _baseline(self):
        obs, db = self._run("enabled")
        return self._external(obs, db), db

    def test_neu_01_enabled_writes_exactly_one_trace(self):
        obs, db = self._run("enabled")
        self.assertEqual(db.query(HybridDecisionTraceRow).count(), 1)
        self.assertIsNotNone(obs.trace)

    def test_neu_02_disabled_is_externally_identical(self):
        base, _ = self._baseline()
        obs, db = self._run("disabled")
        self.assertEqual(self._external(obs, db), base)
        self.assertEqual(db.query(HybridDecisionTraceRow).count(), 0)

    def test_neu_03_missing_table_is_externally_identical(self):
        base, _ = self._baseline()
        obs, db = self._run("no_table")
        self.assertEqual(self._external(obs, db), base)

    def test_neu_04_unopenable_store_is_externally_identical(self):
        base, _ = self._baseline()
        obs, db = self._run_broken_store()
        self.assertEqual(self._external(obs, db), base)
        self.assertEqual(db.query(HybridDecisionTraceRow).count(), 0)

    def test_neu_05_malformed_trace_only_data_is_externally_identical(self):
        base, _ = self._baseline()
        obs, db = self._run_malformed()
        self.assertEqual(self._external(obs, db), base)
        self.assertEqual(db.query(HybridDecisionTraceRow).count(), 0,
                         "malformed trace input must produce no row, not a wrong row")

    def test_neu_06_no_booking_and_no_outbound_is_created_by_tracing(self):
        for runner in (lambda: self._run("enabled"), lambda: self._run("disabled"),
                       self._run_broken_store, self._run_malformed):
            obs, db = runner()
            snap = self._external(obs, db)
            with self.subTest(action=snap["action"]):
                self.assertEqual(snap["bookings"], 0)
                self.assertIsNone(snap["booking_token"])

    def test_neu_07_the_writer_result_is_never_consulted(self):
        """`_write_hybrid_trace` returns None and its call is a bare statement."""
        self.assertIn("self._write_hybrid_trace(event, out, _trace_started_at,", CE_SOURCE)
        for forbidden in ("= self._write_hybrid_trace", "if self._write_hybrid_trace",
                          "return self._write_hybrid_trace"):
            self.assertNotIn(forbidden, CE_SOURCE)

    def test_neu_08_persist_never_raises(self):
        from app.services import hybrid_trace as svc

        class Broken:
            def add(self, *a): raise RuntimeError("gone")
            def commit(self): raise RuntimeError("gone")
            def rollback(self): raise RuntimeError("worse")

        from app.schemas.hybrid_trace import CanonicalSnapshot, SemanticEvidence
        trace = svc.build_trace(
            turn_id="t", thread_id=1, lead_id=None, deployment_sha=None,
            started_at="s", completed_at="c", duration_ms=0,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=["x"],
            semantic=SemanticEvidence(), ce_rules=(), reconciliations=(),
            before=CanonicalSnapshot(), after=CanonicalSnapshot(),
            action="replied", detail=None, answer_source=None, outbound={})
        self.assertFalse(svc.persist(Broken(), trace))


# ── AUTH: the trace endpoints require the CRM session ────────────────────────

class EndpointAuthorization(unittest.TestCase):
    """Only the two trace endpoints changed. Everything else on /api/ops is as it was."""

    # Other suites patch AUTH_SECRET_KEY for their own runs, so the signing key cannot be
    # set once in setUpClass and trusted afterwards: a cookie minted under one key is
    # worthless under another. Every authenticated request below therefore mints its cookie
    # inside a patched environment that pins the key for the duration of that request.
    KEY = "hybrid-trace-hardening-test-signing-key"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DATABASE_URL", "sqlite://")
        import sqlalchemy
        from fastapi.testclient import TestClient
        from app.auth import SESSION_COOKIE, build_session, sign_session
        from app.db import get_db
        from app.main import app
        from app.services import hybrid_trace as svc
        from app.schemas.hybrid_trace import (CanonicalSnapshot, RuleEvidence,
                                              SemanticEvidence, token_fingerprint)

        cls.SECRET_TOKEN = "bk_tok_DO_NOT_LEAK_0001"
        cls.TURN = "11111111-2222-3333-4444-555555555555"
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=sqlalchemy.pool.StaticPool)
        models.Base.metadata.create_all(engine)
        cls.session = Session(engine)
        cls.app = app
        app.dependency_overrides[get_db] = lambda: cls.session
        cls._build_seed = staticmethod(lambda session: svc.persist(session, svc.build_trace(
            turn_id=cls.TURN, thread_id=2053, lead_id=7, deployment_sha="ce978a2",
            started_at="2026-09-17T18:40:53.000+00:00",
            completed_at="2026-09-17T18:40:53.412+00:00", duration_ms=412,
            ordered_message_ids=("wamid.B0", "wamid.B1"),
            message_timestamps=("2026-09-17T18:40:41+00:00", "2026-09-17T18:40:53+00:00"),
            burst_texts=WILD_BURST,
            semantic=SemanticEvidence(status="PENDING", dispatch="async"),
            ce_rules=(RuleEvidence("scheduling.earliest_option_rejected", "1.0", True,
                                   "d", "n"),),
            reconciliations=(),
            before=CanonicalSnapshot(stage="SCHEDULING", needs_human=False,
                                     booking_token_present=True,
                                     booking_token_fingerprint=token_fingerprint(
                                         cls.SECRET_TOKEN)),
            after=CanonicalSnapshot(stage="SCHEDULING", needs_human=True),
            action="skipped_human", detail="human_rescue", answer_source=None, outbound={})))
        cls._build_seed(cls.session)
        cls.TestClient = TestClient
        cls.SESSION_COOKIE = SESSION_COOKIE
        cls._build_session = staticmethod(build_session)
        cls._sign_session = staticmethod(sign_session)
        cls.anon = TestClient(app, follow_redirects=False)

    def _install_override(self):
        """Key the override on the callable the ROUTE holds, not on a freshly imported one.

        Other suites in the same session swap `sys.modules["app.db"]`, so `from app.db
        import get_db` can yield a different object from the dependency the route captured
        at import time; an override keyed on that object applies to nothing and the request
        runs against the application's own database. This surfaced here exactly as it did
        for the agenda-payment suite: `no such table`, rather than an obvious fixture error.
        """
        import app.db as app_db
        keys = {app_db.get_db}
        for route in self.app.routes:
            if getattr(route, "path", "") in ("/api/ops/turns", "/api/ops/turn/{turn_id}",
                                              "/control/turn/{turn_id}"):
                # Measured in a full session: the route's dependency is
                # `test_l3_dirty_history._get_db_gen`, because that module replaced
                # `app.db.get_db` before `ops_dashboard` was imported. Matching on a name
                # ending in `get_db` misses it, so `_get_db_gen` is named explicitly — the
                # same allowance the agenda-payment suite already carries.
                keys.update(d.call for d in route.dependant.dependencies
                            if getattr(d.call, "__name__", "").endswith("get_db")
                            or getattr(d.call, "__name__", "") == "_get_db_gen")
        for key in keys:
            self.app.dependency_overrides[key] = lambda: self.session
        # Belt and braces, copied from the agenda-payment suite: `get_db` reads the
        # module-global `SessionLocal` at call time, and a module reload mutates the module
        # object rather than replacing it, so patching `SessionLocal` reaches every version
        # of `get_db` no matter which one the route captured.
        import app.db as app_db
        patcher = mock.patch.object(app_db, "SessionLocal", lambda: self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def setUp(self):
        # Re-assert both per test: other suites install and then clear
        # `app.dependency_overrides`, and can leave the shared declarative metadata in a
        # state where this suite's engine is missing a table.
        self._install_override()
        HybridDecisionTraceRow.__table__.create(self.session.get_bind(), checkfirst=True)
        if self.session.query(HybridDecisionTraceRow).count() == 0:
            self._build_seed(self.session)

    @classmethod
    def tearDownClass(cls):
        from app.db import get_db
        from app.main import app
        app.dependency_overrides.pop(get_db, None)

    @contextlib.contextmanager
    def authed(self):
        """A TestClient carrying a valid CRM session, with the signing key pinned."""
        with mock.patch.dict(os.environ, {"AUTH_SECRET_KEY": self.KEY, "SECRET_KEY": self.KEY}):
            cookie = self._sign_session(self._build_session("operator@example.com"))
            # Deliberately NOT a context-managed client: entering TestClient fires the
            # application's startup events, which build schema against the real engine and
            # would replace the session this suite overrode.
            yield self.TestClient(self.app, follow_redirects=False,
                                  cookies={self.SESSION_COOKIE: cookie})

    # authenticated
    def test_auth_01_authenticated_valid_trace(self):
        with self.authed() as client:
            r = client.get(f"/api/ops/turn/{self.TURN}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["captured"])

    def test_auth_02_authenticated_missing_trace(self):
        with self.authed() as client:
            r = client.get("/api/ops/turn/does-not-exist")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["captured"])
        self.assertIn("TRACE NOT CAPTURED", r.json()["reason"])

    def test_auth_03_authenticated_malformed_turn_id(self):
        with self.authed() as client:
            r = client.get("/api/ops/turn/%27%20OR%201%3D1--")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["captured"])
        self.assertEqual(self.session.query(HybridDecisionTraceRow).count(), 1,
                         "a malformed id must not reach the store as anything but a value")

    def test_auth_04_authenticated_listing(self):
        with self.authed() as client:
            r = client.get("/api/ops/turns")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["count"], 1)

    # unauthenticated
    def test_auth_05_unauthenticated_listing_is_refused(self):
        self.assertEqual(self.anon.get("/api/ops/turns").status_code, 401)

    def test_auth_06_unauthenticated_valid_id_is_refused(self):
        self.assertEqual(self.anon.get(f"/api/ops/turn/{self.TURN}").status_code, 401)

    def test_auth_07_unauthenticated_missing_id_is_refused(self):
        self.assertEqual(self.anon.get("/api/ops/turn/does-not-exist").status_code, 401)

    def test_auth_08_unauthenticated_malformed_id_is_refused(self):
        self.assertEqual(self.anon.get("/api/ops/turn/%27%20OR%201%3D1--").status_code, 401)

    def test_auth_09_refusal_does_not_disclose_whether_a_trace_exists(self):
        """The three unauthenticated answers must be indistinguishable."""
        answers = {self.anon.get(u).status_code: self.anon.get(u).text for u in (
            f"/api/ops/turn/{self.TURN}",
            "/api/ops/turn/does-not-exist",
            "/api/ops/turn/%27%20OR%201%3D1--")}
        self.assertEqual(list(answers), [401])
        bodies = {self.anon.get(u).text for u in (
            f"/api/ops/turn/{self.TURN}",
            "/api/ops/turn/does-not-exist",
            "/api/ops/turn/%27%20OR%201%3D1--")}
        self.assertEqual(len(bodies), 1, "the refusal body leaks which turn was asked for")
        self.assertNotIn(self.TURN, bodies.pop())

    # the page
    def test_auth_10_control_turn_page_without_a_session_redirects_to_login(self):
        r = self.anon.get(f"/control/turn/{self.TURN}")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers.get("location"), "/login")
        self.assertNotIn(self.TURN, r.text)

    def test_auth_11_control_pages_work_with_a_session(self):
        with self.authed() as client:
            self.assertEqual(client.get("/control").status_code, 200)
            page = client.get(f"/control/turn/{self.TURN}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Motor semántico", page.text)

    # the rest of /api/ops is untouched
    def test_auth_12_only_the_two_trace_routes_gained_the_session_gate(self):
        """The invariant this milestone owns, asserted where it actually lives.

        Read off the application's own route table rather than by calling each endpoint: a
        status code for `/api/ops/summary` depends on database contents this suite does not
        own, while the dependency list is exactly the thing that was changed.
        """
        from app.routes.ops_dashboard import require_crm_session
        gated, ungated = [], []
        for route in self.app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/ops"):
                continue
            calls = [d.call for d in getattr(getattr(route, "dependant", None),
                                             "dependencies", [])]
            (gated if require_crm_session in calls else ungated).append(path)
        self.assertEqual(sorted(gated), ["/api/ops/turn/{turn_id}", "/api/ops/turns"])
        self.assertEqual(sorted(ungated),
                         ["/api/ops/critical-events", "/api/ops/messages",
                          "/api/ops/path-registry", "/api/ops/paths",
                          "/api/ops/summary", "/api/ops/threads"],
                         "no unrelated /api/ops route may gain or lose the session gate")

    def test_auth_13_no_secret_or_customer_text_in_api_or_page(self):
        with self.authed() as client:
            response = client.get(f"/api/ops/turn/{self.TURN}")
            body, payload = response.text, response.json()["trace"]
            page = client.get(f"/control/turn/{self.TURN}").text
        for blob in (body, page):
            self.assertNotIn(self.SECRET_TOKEN, blob)
            for text in WILD_BURST:
                self.assertNotIn(text, blob)
            self.assertNotIn("5491133334444", blob)
        self.assertEqual(sorted(payload["canonical_before"]), [
            "active_requested_date", "booking_token_fingerprint", "booking_token_present",
            "candidate_id", "lead_estado", "lead_necesita_humano", "needs_human",
            "offer_outstanding", "offered_slots_count", "revision_id", "stage",
            "zone_detail", "zone_group"],
            "the canonical snapshot must stay allowlisted")


# ── SCP: truthful scope labels ───────────────────────────────────────────────

class ScopeLabels(unittest.TestCase):

    def setUp(self):
        from app.routes.ops_dashboard import read_turn
        from app.services import hybrid_trace as svc
        from app.schemas.hybrid_trace import (CanonicalSnapshot, ReconciliationEvidence,
                                              Classification, RuleEvidence, SemanticEvidence)
        self.read_turn = read_turn
        self.svc = svc
        self.S = CanonicalSnapshot
        self.Rule = RuleEvidence
        self.Sem = SemanticEvidence
        self.Rec = ReconciliationEvidence
        self.Cls = Classification

    def _page(self, **over):
        db = _db()
        base = dict(
            turn_id="t1", thread_id=1, lead_id=None, deployment_sha="d",
            started_at="s", completed_at="c", duration_ms=1,
            ordered_message_ids=("w",), message_timestamps=("t",), burst_texts=WILD_BURST,
            semantic=self.Sem(status="PENDING", dispatch="async"),
            ce_rules=(self.Rule("scheduling.earliest_option_rejected", "1.0", True),),
            reconciliations=(), before=self.S(needs_human=False),
            after=self.S(needs_human=True), action="skipped_human", detail=None,
            answer_source=None, outbound={})
        base.update(over)
        self.svc.persist(db, self.svc.build_trace(**base))
        from app.ui.hybrid_trace_view import render_turn_trace_page
        return render_turn_trace_page(self.read_turn("t1", db))

    def test_scp_01_the_page_declares_it_is_a_conversational_trace(self):
        from app.ui.hybrid_trace_view import SCOPE_LABEL
        page = self._page()
        self.assertIn(SCOPE_LABEL, page)
        self.assertEqual(SCOPE_LABEL, "HYBRID CONVERSATION TRACE")
        self.assertIn("ConversationEngine.handle", page)

    def test_scp_02_the_page_says_a_flow_booking_is_not_a_hybrid_decision(self):
        page = self._page()
        self.assertIn("Flow de Meta", page)
        self.assertIn("transacción operativa", page)

    def test_scp_03_a_flow_originated_send_is_labelled_as_a_transaction(self):
        from app.ui.hybrid_trace_view import FLOW_TRANSACTION_LABEL, FLOW_TRANSACTION_NOTE
        self.assertEqual(FLOW_TRANSACTION_LABEL,
                         "FLOW TRANSACTION — NOT A HYBRID DECISION")
        flow_page = self._page(outbound={"message_id": 5, "path_id": "BOOKING_FLOW",
                                         "status": "sent", "wamid_tail": "ABCDEFGH"})
        self.assertIn(FLOW_TRANSACTION_LABEL, flow_page)
        self.assertIn(FLOW_TRANSACTION_NOTE, flow_page)
        # The glossary always names the label; the CONTEXTUAL note must appear only when a
        # Flow-originated send is actually on the page.
        self.assertNotIn(FLOW_TRANSACTION_NOTE, self._page())
        self.assertNotIn(FLOW_TRANSACTION_NOTE,
                         self._page(outbound={"message_id": 5, "path_id": "CE_TEXT",
                                              "status": "sent", "wamid_tail": "ABCDEFGH"}))

    def test_scp_04_a_floor_is_never_labelled_reconciled(self):
        page = self._page()
        self.assertIn("DETERMINISTIC FLOOR", page)
        badges = page.split('class="badges"')[1].split("</div>")[0]
        self.assertNotIn("RECONCILED", badges)
        self.assertNotIn(">AGREE<", badges)

    def test_scp_05_a_reconciled_decision_says_so(self):
        """The row now carries the participation that makes it an agreement.

        Setting `classification=AGREE` by hand no longer produces an AGREE badge, and that
        is the correction: under `hybrid-decision-trace/1.1` a row is classified from the
        sources recorded on it, so a label with no participation behind it cannot survive.
        """
        from app.schemas.hybrid_trace import SourceContribution
        from app.services.hybrid_trace import value_key

        def contribution(source):
            return SourceContribution(
                source=source, producer=f"{source.lower()}:test",
                claim_types=("vehicle.model",), claim_ids=(f"{source}-0",),
                value_keys=(value_key("vehicle.model", "peugeot 208"),),
                polarities=("ASSERTED",), values=(None,), withheld=True)

        contributions = (contribution("SEMANTIC"), contribution("DETERMINISTIC"))
        agree = self.Rec(claim_family="VEHICLE_MODEL", semantic_input="PRESENT",
                         ce_input="PRESENT", classification=self.Cls.AGREE,
                         rule_id="vehicle.identity", rule_version="1.0", outcome="ACCEPT",
                         source_evidence=contributions,
                         participating_sources=("SEMANTIC", "DETERMINISTIC"))
        page = self._page(semantic=self.Sem(ok=True, status="OK",
                                            produced_claims=("vehicle_mentions",)),
                          ce_rules=(), reconciliations=(agree,), action="replied",
                          answer_source="RECONCILER",
                          before=self.S(needs_human=False), after=self.S(needs_human=False))
        badges = page.split('class="badges"')[1].split("</div>")[0]
        self.assertIn("AGREE", badges)
        self.assertIn("RECONCILED", badges)

    def test_scp_06_every_required_label_is_documented_on_the_page(self):
        from app.ui.hybrid_trace_view import LABEL_GLOSSARY
        documented = {label for label, _ in LABEL_GLOSSARY}
        for required in ("HYBRID CONVERSATION TRACE", "DETERMINISTIC FLOOR", "NO RULE",
                         "RECONCILED", "SEMANTIC PENDING", "SEMANTIC MISSING",
                         "SEMANTIC ERROR", "CE MISSING", "TRACE NOT CAPTURED",
                         "FLOW TRANSACTION — NOT A HYBRID DECISION"):
            self.assertIn(required, documented)
        page = self._page()
        for label in documented:
            self.assertIn(label, page)

    def test_scp_07_semantic_pending_is_badged_not_hidden(self):
        page = self._page()
        badges = page.split('class="badges"')[1].split("</div>")[0]
        self.assertIn("SEMANTIC PENDING", badges)
        self.assertNotIn("AGREE", badges, "pending is not agreement")


# ── FLW: the Flow Data Exchange boundary ─────────────────────────────────────

class FlowDataExchangeBoundary(unittest.TestCase):
    """Gate 1 covers conversational hybrid decisions. Flow Data Exchange is not one."""

    def test_flw_01_the_flow_path_is_untouched_by_the_inspector(self):
        for name in ("routes/flow_data_exchange.py", "services/booking_flow_service.py"):
            source = (ROOT / "backend" / "app" / name).read_text(encoding="utf-8-sig")
            self.assertNotIn("hybrid_trace", source)
            self.assertNotIn("HybridDecisionTrace", source)

    def test_flw_02_the_flow_endpoint_does_not_run_the_conversation_engine(self):
        source = (ROOT / "backend" / "app" / "routes"
                  / "flow_data_exchange.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("ConversationEngine", source)
        self.assertNotIn(".handle(", source)

    def test_flw_03_only_handle_writes_traces(self):
        self.assertEqual(CE_SOURCE.count("_write_hybrid_trace"), 2,
                         "expected exactly the definition and the single call in handle()")
        for name in ("routes/flow_data_exchange.py", "routes/whatsapp.py",
                     "services/booking_flow_service.py"):
            source = (ROOT / "backend" / "app" / name).read_text(encoding="utf-8-sig")
            self.assertNotIn("_write_hybrid_trace", source)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
