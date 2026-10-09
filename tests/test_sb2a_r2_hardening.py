"""SB2A-R2 — authority hardening (SQLite / pure).

Closes the SB2A independent-review defects that do not need real concurrency to prove:
partial manual scheduling (R-01), adapter isolation at the ORM level (R-02), event identity
(R-09), the enforced callback guard (R-04), the acknowledgement lease (R-03), the
ThreadRevision PATCH occupancy bypass (R-05) and the fingerprint coverage gap (H1).
Database-level evidence — real isolation, raw-SQL callback writes, concurrent reclaimers,
the per-transaction preflight (R-07 / H10) — lives in test_sb2a_r2_hardening_pg.py.
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import (BookingCommandRecord, CalendarException, Lead, Profesional,  # noqa: E402
                        Revision, ThreadRevision, WhatsAppContact, WhatsAppThread)
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingCommand,  # noqa: E402
                                            BookingOperation as Op, BookingOutcome as Out,
                                            BookingSource as Src)

D = date(2026, 11, 3)
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    from app.models import Agencia, Vendedor, WhatsAppThreadCandidate
    tables = [Lead.__table__, Profesional.__table__, Revision.__table__,
              CalendarException.__table__, BookingCommandRecord.__table__,
              WhatsAppContact.__table__, WhatsAppThread.__table__, ThreadRevision.__table__,
              Agencia.__table__, Vendedor.__table__, WhatsAppThreadCandidate.__table__]
    Lead.__table__.metadata.create_all(engine, tables=tables)
    return Session(engine)


def _prof(db, *, default=True):
    p = Profesional(nombre="Fixture", apellido="SB2A-R2-FIXTURE", email="fixture@example.invalid",
                    resource_key=f"fixture.r2.{id(db)}.{default}" if default else None,
                    is_launch_default=default, is_active=True)
    db.add(p); db.commit()
    return p


def _lead(db):
    lead = Lead(); db.add(lead); db.commit()
    return lead


def _cmd(lead, *, op=Op.CREATE, day=D, at=time(10), key=None, **kw):
    return BookingCommand(operation=op, source=Src.OTHER, idempotency_key=key, lead_id=lead.id,
                          requested_date=day, requested_time=at, **kw)


def _counts(db):
    return (db.query(Revision).count(), db.query(ThreadRevision).count(),
            db.query(BookingCommandRecord).count())


# ══ R-01 — manual scheduling accepts partial slots; occupying operations do not ═══════════

class R01PartialManualScheduling(unittest.TestCase):

    def test_r2_01_manual_scheduling_accepts_every_partial_shape_without_occupying(self):
        db = _db(); _prof(db); lead = _lead(db)
        shapes = {"complete": (D, time(10)), "date_only": (D, None),
                  "time_only": (None, time(10)), "neither": (None, None)}
        for name, (day, at) in shapes.items():
            r = BookingAuthority(db).execute(_cmd(lead, op=Op.REQUEST_MANUAL_SCHEDULING,
                                                  day=day, at=at, key=f"m-{name}"))
            self.assertEqual(r.outcome, Out.COMMITTED, (name, r.reason))
            self.assertFalse(r.occupying, name)
            if day is None or at is None:
                self.assertIsNone(r.slot_start, name)
            rev = db.get(Revision, r.revision_id)
            # stored exactly as given — the missing half is never invented
            self.assertEqual((rev.turno_fecha, rev.turno_hora, rev.estado_revision),
                             (day, at, "REPROGRAMAR"), name)

    def test_r2_02_manual_scheduling_never_collides_even_on_a_taken_slot(self):
        db = _db(); _prof(db); lead = _lead(db)
        self.assertEqual(BookingAuthority(db).execute(_cmd(lead)).outcome, Out.COMMITTED)
        for day, at in ((D, time(10)), (D, None), (None, time(10))):
            r = BookingAuthority(db).execute(_cmd(lead, op=Op.REQUEST_MANUAL_SCHEDULING,
                                                  day=day, at=at))
            self.assertEqual(r.outcome, Out.COMMITTED)

    def test_r2_03_create_and_reschedule_still_refuse_partial_slots(self):
        db = _db(); _prof(db); lead = _lead(db)
        booked = BookingAuthority(db).execute(_cmd(lead))
        for day, at in ((D, None), (None, time(11))):
            r = BookingAuthority(db).execute(_cmd(lead, day=day, at=at))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "SLOT_REQUIRED"))
            r = BookingAuthority(db).execute(BookingCommand(
                operation=Op.RESCHEDULE, source=Src.OTHER, revision_id=booked.revision_id,
                requested_date=day, requested_time=at, expected_version=booked.booking_version))
            self.assertEqual(r.outcome, Out.VALIDATION_FAILED)
            self.assertIn(r.reason, ("SLOT_REQUIRED", "PARTIAL_SLOT"))
        # any other operation carrying half a slot is still refused
        r = BookingAuthority(db).execute(BookingCommand(
            operation=Op.SET_STATUS, source=Src.OTHER, revision_id=booked.revision_id,
            requested_date=D, target_status="CONFIRMADO", expected_version=booked.booking_version))
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "PARTIAL_SLOT"))

    def test_r2_04_ce_w2_adapter_accepts_partial_slots(self):
        db = _db(); _prof(db); lead = _lead(db)
        for i, (day, at) in enumerate(((D, None), (None, time(10)), (None, None), (D, time(10)))):
            r = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
                thread_id=40 + i, lead_id=lead.id, cycle_key=7, requested_date=day,
                requested_time=at))
            self.assertEqual(r.outcome, Out.COMMITTED, (day, at, r.reason))
            self.assertFalse(r.occupying)

    def test_r2_05_ce_w1_with_incomplete_slot_becomes_non_occupying_manual_request(self):
        db = _db(); _prof(db); lead = _lead(db)
        r = ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
            thread_id=9, lead_id=lead.id, inbound_event_id="wamid.R2-05", flow_token=None,
            requested_date=D, requested_time=None))
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        self.assertFalse(r.occupying)
        self.assertEqual(db.get(Revision, r.revision_id).estado_revision, "REPROGRAMAR")


# ══ R-09 — event identity ═════════════════════════════════════════════════════════════

class R09EventIdentity(unittest.TestCase):

    def test_r2_06_missing_or_blank_identity_fails_closed_without_writing(self):
        db = _db(); _prof(db); lead = _lead(db)
        for blank in (None, "", "   ", "\t"):
            r = ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
                thread_id=1, lead_id=lead.id, inbound_event_id=blank, flow_token=blank,
                requested_date=D, requested_time=time(10)))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "EVENT_IDENTITY_REQUIRED"))
            r = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
                thread_id=1, lead_id=lead.id, escalation_id=blank, inbound_event_id=blank))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "EVENT_IDENTITY_REQUIRED"))
        self.assertEqual(_counts(db), (0, 0, 0))

    def test_r2_07_keys_never_hash_an_empty_identity_and_are_thread_scoped(self):
        self.assertIsNone(ce.ce_flow_booking_key(ce.CeFlowBookingRequest(
            thread_id=1, lead_id=1, inbound_event_id="", flow_token="")))
        self.assertIsNone(ce.ce_escalation_key(ce.CeSchedulingEscalationRequest(
            thread_id=1, lead_id=1, escalation_id=" ")))
        a = ce.ce_flow_booking_key(ce.CeFlowBookingRequest(1, 1, "wamid.A", None))
        b = ce.ce_flow_booking_key(ce.CeFlowBookingRequest(1, 1, "wamid.B", None))
        a2 = ce.ce_flow_booking_key(ce.CeFlowBookingRequest(2, 1, "wamid.A", None))
        tok = ce.ce_flow_booking_key(ce.CeFlowBookingRequest(1, 1, "wamid.A", "tok"))
        self.assertEqual(len({a, b, a2, tok}), 4)
        cyc = ce.ce_escalation_key(ce.CeSchedulingEscalationRequest(1, 1, cycle_key=0))
        cyc_other = ce.ce_escalation_key(ce.CeSchedulingEscalationRequest(2, 1, cycle_key=0))
        self.assertNotEqual(cyc, cyc_other)
        # a cycle key of 0 (no cycle start yet) is a real, durable identity
        self.assertIsNotNone(cyc)

    def test_r2_08_two_distinct_events_are_two_escalations(self):
        db = _db(); _prof(db); lead = _lead(db)
        r1 = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
            thread_id=3, lead_id=lead.id, inbound_event_id="wamid.E1"))
        r2 = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
            thread_id=3, lead_id=lead.id, inbound_event_id="wamid.E2"))
        self.assertEqual((r1.outcome, r2.outcome), (Out.COMMITTED, Out.COMMITTED))
        self.assertNotEqual(r1.revision_id, r2.revision_id)


# ══ R-02 — the adapter never touches the caller's session (ORM level) ════════════════════

class R02AdapterIsolationOrm(unittest.TestCase):

    def test_r2_09_adapter_does_not_flush_commit_or_roll_back_the_caller(self):
        db = _db(); _prof(db); lead = _lead(db)
        self.assertEqual(BookingAuthority(db).execute(_cmd(lead)).outcome, Out.COMMITTED)
        lead_id = lead.id                     # read before instrumenting: a refresh autoflushes
        calls = []
        for name in ("commit", "rollback", "flush", "begin", "begin_nested", "close"):
            orig = getattr(db, name)
            setattr(db, name, (lambda n, o: (lambda *a, **k: (calls.append(n), o(*a, **k))[1]))(name, orig))
        pending = Lead(); db.add(pending)                          # caller's unfinished turn
        ok = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
            thread_id=1, lead_id=lead_id, cycle_key=1))
        refused = ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
            thread_id=1, lead_id=lead_id, inbound_event_id="w", flow_token=None,
            requested_date=D, requested_time=time(10)))
        self.assertEqual(ok.outcome, Out.COMMITTED)
        self.assertEqual(refused.outcome, Out.SLOT_CONFLICT)       # refusal rolls back ITS session
        self.assertEqual(calls, [])
        self.assertIn(pending, db.new)                             # still the caller's to decide

    def test_r2_10_authority_runs_on_the_adapter_session_not_the_callers(self):
        # SB2A-R3 rewrite: there is no callback to observe; observe the authority's session.
        db = _db(); _prof(db); lead = _lead(db)
        seen = []
        real = ce.BookingAuthority

        class Spy(real):
            def __init__(self, s, **k):
                seen.append(s); super().__init__(s, **k)
        ce.BookingAuthority = Spy
        try:
            ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
                thread_id=1, lead_id=lead.id, inbound_event_id="w", flow_token="t",
                requested_date=D, requested_time=time(10)))
        finally:
            ce.BookingAuthority = real
        self.assertEqual(len(seen), 1)
        self.assertIsNot(seen[0], db)

    def test_r2_11_adapter_session_is_closed_even_when_the_authority_raises(self):
        db = _db()
        made = []
        real = ce.Session

        class Tracking(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k); made.append(self); self.closed = False

            def close(self):
                self.closed = True; super().close()

        orig_exec = BookingAuthority.execute
        ce.Session = Tracking
        BookingAuthority.execute = lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            with self.assertRaises(RuntimeError):
                ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
                    thread_id=1, lead_id=1, cycle_key=1))
        finally:
            ce.Session = real
            BookingAuthority.execute = orig_exec
        self.assertEqual(len(made), 1)
        self.assertTrue(made[0].closed)


# ══ R-04 — superseded by SB2A-R3: there is no callback interface at all ═════════════════
#
# SB2A-R2 guarded caller callbacks; the R2 audit showed a guard over arbitrary write-capable
# code is bypassable (comment-prefixed COMMIT, DO-block DDL, raw DELETE…). SB2A-R3 removed the
# callbacks: callers hand TYPED DATA plans to the authority. The R2 guard tests that drove
# callbacks are replaced by these interface assertions; behaviour and defence in depth are
# proven in test_sb2a_r3_typed_plans.py / test_sb2a_r3_typed_plans_pg.py.

class R04NoCallbackInterface(unittest.TestCase):

    def test_r2_12_execute_rejects_callback_keywords(self):
        db = _db(); _prof(db); lead = _lead(db)
        for kw in ({"attach": lambda s, r: None}, {"revalidate": lambda s, t: None}):
            with self.assertRaises(TypeError):
                BookingAuthority(db).execute(_cmd(lead), **kw)
        self.assertEqual(_counts(db), (0, 0, 0))

    def test_r2_13_adapter_rejects_callback_keywords(self):
        db = _db(); _prof(db); lead = _lead(db)
        with self.assertRaises(TypeError):
            ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
                thread_id=1, lead_id=lead.id, inbound_event_id="w", flow_token="t",
                requested_date=D, requested_time=time(10)), create_thread_revision=lambda s, r: None)

    def test_r2_23_outbound_gate_carries_the_guard_as_its_first_statement(self):
        from app.services.outbound_safety_gate import OutboundSafetyGate
        src = inspect.getsource(OutboundSafetyGate.attempt)
        body = src.split('"""', 2)[2]
        first = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")][:2]
        self.assertEqual(first, ["from .booking_authority import forbid_external_io",
                                 'forbid_external_io("outbound_gate")'])


# ══ R-03 — acknowledgement lease ══════════════════════════════════════════════════════

class R03AckLease(unittest.TestCase):

    def setUp(self):
        self.db = _db(); _prof(self.db); lead = _lead(self.db)
        r = BookingAuthority(self.db).execute(_cmd(lead, key="ack", tracks_acknowledgement=True))
        # SB2A-R3 rewrite: claims take no `now=`; PostgreSQL uses the database clock and
        # SQLite the application clock, which these tests control.
        from unittest import mock
        self._mock = mock
        self.assertEqual(r.ack_status, "PENDING")
        self.auth = BookingAuthority(self.db)

    def _claim(self, at):
        with self._mock.patch.object(ba, "_app_now", return_value=at):
            return self.auth.claim_acknowledgement("ack")

    def rec(self):
        self.db.expire_all()
        return self.db.query(BookingCommandRecord).one()

    def test_r2_24_pending_is_claimable_once_and_an_active_lease_cannot_be_stolen(self):
        c = self._claim(T0)
        self.assertIsNotNone(c)
        self.assertFalse(c.duplicate_risk)
        self.assertIsNone(self._claim(T0 + timedelta(seconds=60)))
        rec = self.rec()
        self.assertEqual((rec.ack_status, rec.ack_attempts, rec.ack_claim_token),
                         ("SENDING", 1, c.token))

    def test_r2_25_expired_sending_is_reclaimed_and_flags_duplicate_risk(self):
        dead = self._claim(T0)        # process dies mid-send
        later = T0 + timedelta(seconds=ba.ACK_LEASE_SECONDS + 1)
        c = self._claim(later)
        self.assertIsNotNone(c)
        self.assertTrue(c.duplicate_risk)
        self.assertEqual(c.attempt, 2)
        # the dead claimant, if it wakes up, no longer owns the row
        self.assertFalse(self.auth.record_acknowledgement(dead, sent=True, transport_id="wamid.DEAD"))
        self.assertTrue(self.auth.record_acknowledgement(c, sent=True, transport_id="wamid.OK"))
        rec = self.rec()
        self.assertEqual((rec.ack_status, rec.ack_transport_id, rec.ack_duplicate_risk),
                         ("SENT", "wamid.OK", True))

    def test_r2_26_failed_is_retried_and_records_the_error_category(self):
        c = self._claim(T0)
        self.assertTrue(self.auth.record_acknowledgement(c, sent=False, error="META_400"))
        rec = self.rec()
        self.assertEqual((rec.ack_status, rec.ack_last_error), ("FAILED", "META_400"))
        c2 = self._claim(T0 + timedelta(seconds=1))
        self.assertIsNotNone(c2)
        self.assertFalse(c2.duplicate_risk)                         # a FAILED send never left

    def test_r2_27_sent_is_never_resent(self):
        c = self._claim(T0)
        self.auth.record_acknowledgement(c, sent=True, transport_id="wamid.1")
        for dt in (0, 10_000, 10**7):
            self.assertIsNone(self._claim(T0 + timedelta(seconds=dt)))

    def test_r2_28_retries_are_bounded(self):
        now = T0
        for i in range(ba.ACK_MAX_ATTEMPTS):
            c = self._claim(now)
            self.assertIsNotNone(c, i)
            self.auth.record_acknowledgement(c, sent=False, error="TRANSPORT_ERROR")
            now += timedelta(seconds=1)
        self.assertIsNone(self._claim(now))
        self.assertEqual((self.rec().ack_status, self.rec().ack_attempts),
                         ("FAILED", ba.ACK_MAX_ATTEMPTS))

    def test_r2_29_non_tracking_commands_are_never_claimable(self):
        db = _db(); _prof(db); lead = _lead(db)
        BookingAuthority(db).execute(_cmd(lead, key="crm"))
        self.assertIsNone(BookingAuthority(db).claim_acknowledgement("crm"))


# ══ R-05 — ThreadRevision PATCH cannot move occupancy ═══════════════════════════════════

class R05ThreadRevisionPatch(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.thread_revisions import get_thread_revision_service, router
        from app.repositories.thread_revisions import ThreadRevisionRepository
        from app.services.thread_revisions import ThreadRevisionService
        self.db = _db()
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_thread_revision_service] = \
            lambda: ThreadRevisionService(repository=ThreadRevisionRepository(db=self.db))
        self.client = TestClient(app, raise_server_exceptions=False)

    def tr(self, **kw):
        t = ThreadRevision(thread_id=1, **{"status": "collecting_data", **kw})
        self.db.add(t); self.db.commit()
        return t.id

    def patch(self, tid, body):
        return self.client.patch(f"/api/revisions/{tid}", json=body)

    def assertRefused(self, resp, fields):
        self.assertEqual(resp.status_code, 409, resp.text)
        detail = resp.json()["detail"]
        self.assertEqual(detail["code"], "BOOKING_CHANGE_REQUIRES_AUTHORITY")
        self.assertEqual(detail["fields"], fields)
        for leak in ("SQL", "psycopg", "Traceback", "constraint"):
            self.assertNotIn(leak.lower(), resp.text.lower())

    def test_r2_30_occupancy_creating_status_is_refused(self):
        tid = self.tr(scheduled_date=D, scheduled_time=time(10))
        self.assertRefused(self.patch(tid, {"status": "booked"}), ["status"])
        self.assertRefused(self.patch(tid, {"status": "completed"}), ["status"])
        self.db.expire_all()
        self.assertEqual(self.db.get(ThreadRevision, tid).status, "collecting_data")

    def test_r2_31_occupancy_removing_status_is_refused(self):
        tid = self.tr(status="booked", scheduled_date=D, scheduled_time=time(10))
        self.assertRefused(self.patch(tid, {"status": "draft"}), ["status"])

    def test_r2_32_slot_edits_are_refused(self):
        tid = self.tr(status="booked", scheduled_date=D, scheduled_time=time(10))
        self.assertRefused(self.patch(tid, {"scheduled_time": "15:00:00"}), ["scheduled_time"])
        self.assertRefused(self.patch(tid, {"scheduled_date": "2026-11-04"}), ["scheduled_date"])
        draft = self.tr()
        self.assertRefused(self.patch(draft, {"status": "booked", "scheduled_date": "2026-11-03",
                                              "scheduled_time": "10:00:00"}),
                           ["scheduled_date", "scheduled_time"])
        self.db.expire_all()
        t = self.db.get(ThreadRevision, draft)
        self.assertEqual((t.status, t.scheduled_date, t.scheduled_time),
                         ("collecting_data", None, None))

    def test_r2_33_non_scheduling_fields_stay_editable(self):
        tid = self.tr(status="booked", scheduled_date=D, scheduled_time=time(10))
        resp = self.patch(tid, {"buyer_email": "x@example.invalid", "marca": "Fiat",
                                "scheduled_time": "10:00:00", "status": "booked"})
        self.assertEqual(resp.status_code, 200, resp.text)            # unchanged slot is fine
        dateless = self.tr()
        self.assertEqual(self.patch(dateless, {"status": "booked"}).status_code, 200)


# ══ H1 — the fingerprint covers every semantic field ════════════════════════════════════

class H1Fingerprint(unittest.TestCase):

    def test_r2_34_key_reuse_with_any_different_semantic_field_is_refused(self):
        db = _db(); p = _prof(db); other_prof = _prof(db, default=False)
        lead, lead2 = _lead(db), _lead(db)
        base = dict(operation=Op.CREATE, source=Src.OTHER, idempotency_key="fp", lead_id=lead.id,
                    thread_id=5, profesional_id=p.id, requested_date=D, requested_time=time(10),
                    target_status="PENDIENTE")
        first = BookingAuthority(db).execute(BookingCommand(**base))
        self.assertEqual(first.outcome, Out.COMMITTED, first.reason)
        variants = {"lead": {"lead_id": lead2.id}, "thread": {"thread_id": 6},
                    "revision": {"revision_id": first.revision_id},
                    "operation": {"operation": Op.REQUEST_MANUAL_SCHEDULING},
                    "professional": {"profesional_id": other_prof.id},
                    "date": {"requested_date": D + timedelta(days=1)},
                    "time": {"requested_time": time(11)}, "status": {"target_status": "CONFIRMADO"},
                    "service": {"service_minutes": 60}, "source": {"source": Src.CRM}}
        for name, change in variants.items():
            r = BookingAuthority(db).execute(BookingCommand(**{**base, **change}))
            self.assertEqual((r.outcome, r.reason),
                             (Out.VALIDATION_FAILED, "IDEMPOTENCY_KEY_REUSED"), name)
        self.assertEqual(BookingAuthority(db).execute(BookingCommand(**base)).outcome,
                         Out.IDEMPOTENT_REPLAY)
        self.assertEqual(db.query(Revision).count(), 1)

    def test_r2_35_fingerprint_function_distinguishes_lead_and_thread(self):
        c = BookingCommand(operation=Op.CREATE, source=Src.OTHER, lead_id=1, thread_id=1,
                           requested_date=D, requested_time=time(10))
        from dataclasses import replace
        fps = {ba.request_fingerprint(c), ba.request_fingerprint(replace(c, lead_id=2)),
               ba.request_fingerprint(replace(c, thread_id=2)),
               ba.request_fingerprint(replace(c, revision_id=3))}
        self.assertEqual(len(fps), 4)


# ══ R-07 — preflight is not cached ═════════════════════════════════════════════════════

class R07PreflightUncached(unittest.TestCase):

    def test_r2_36_no_process_wide_cache_remains(self):
        self.assertFalse(hasattr(ba, "_SCHEMA_OK"))
        src = inspect.getsource(ba.verify_booking_schema)
        self.assertNotIn("_SCHEMA_OK", src)
        sql = ba._preflight_sql(ba._required_columns())       # SB2A-R3: column set expanded
        self.assertIn("tgenabled IN ('O', 'A')", sql)
        self.assertIn("session_replication_role", sql)
        run = inspect.getsource(BookingAuthority.execute)
        self.assertIn("verify_booking_schema(self.db)", run)


if __name__ == "__main__":
    unittest.main()
