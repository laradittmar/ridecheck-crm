"""Convergence Phase 4 — evidence and operator paths, in the DEFAULT regression gate (SQLite).

Production functions only: the attempt-event writer, the operator API routes, the CRM form
and delete handlers, the K-E flush guard on a real Session, `booking_attention`, the external
transports. Locks, races and the append-only trigger are proven on PostgreSQL in
`test_convergence_p4_pg.py`.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_sb2a_r3_typed_plans as r3  # noqa: E402  (world/command helpers)

from sqlalchemy import inspect, select  # noqa: E402

from app.models import (BookingAttemptEvent, BookingCommandRecord, Lead, Revision,  # noqa: E402
                        WhatsAppThreadState)
from app.services import booking_attempt_events as bae  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingOperation,  # noqa: E402
                                            BookingOutcome, BookingSource)

D = r3.D


def _world():
    """r3's world (an explicit table list) plus the attempt-event table."""
    db, lead, tid = r3._world()
    BookingAttemptEvent.__table__.create(db.get_bind(), checkfirst=True)
    return db, lead, tid


def _events(db, **where):
    db.expire_all()
    q = db.query(BookingAttemptEvent)
    for k, v in where.items():
        q = q.filter(getattr(BookingAttemptEvent, k) == v)
    return q.order_by(BookingAttemptEvent.id).all()


def _api(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import revision_items, revisions as api
    from app.db import get_db
    app = FastAPI(); app.include_router(api.router); app.include_router(revision_items.router)
    # Other suites replace `app.db` in sys.modules; override the very objects the routes
    # captured at import, as test_sb2a_r7_temporal_capture does.
    for dep in {get_db, api.get_db, revision_items.get_db}:
        app.dependency_overrides[dep] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


# ══ 1. booking_attempt_events: evidence, never authority ═══════════════════════════════

class P4_Events(unittest.TestCase):

    def setUp(self):
        self.db, self.lead, self.tid = _world()

    def test_p4u_01_the_table_has_no_foreign_key_and_no_raw_secret_column(self):
        cols = {c["name"] for c in inspect(self.db.get_bind()).get_columns(
            "booking_attempt_events")}
        self.assertEqual(inspect(self.db.get_bind()).get_foreign_keys("booking_attempt_events"),
                         [], "an attempt event must never hold a foreign key")
        for forbidden in ("token", "idempotency_key", "phone", "wa_id", "text", "payload"):
            self.assertFalse(any(forbidden == c or c.startswith(forbidden) for c in cols),
                             f"column {forbidden!r} would carry raw data")
        self.assertTrue({"hist_thread_id", "hist_offer_id", "hist_revision_id",
                         "hist_thread_revision_id"} <= cols, "historical ids are labelled")

    def test_p4u_02_privacy_only_hashes_and_typed_codes(self):
        key, ref = "flow:" + "a" * 48, "wamid.SECRET.INBOUND.0001"
        self.assertTrue(bae.record_attempt_event(
            self.db, source="CONVERSATION_ENGINE", stage="AUTHORITY", outcome="REFUSED",
            reason="OFFER_LOCATION_CHANGED", command_key=key, attempt_ref=ref,
            thread_id=self.tid))
        ev = _events(self.db)[0]
        blob = repr({c.name: getattr(ev, c.name) for c in ev.__table__.columns})
        for secret in (key, key[5:], ref):
            self.assertNotIn(secret, blob)
        self.assertEqual((len(ev.command_key_hash), len(ev.attempt_ref_hash)), (16, 16))
        self.assertEqual(ev.hist_thread_id, self.tid)

    def test_p4u_03_one_logical_attempt_one_row_distinct_attempts_distinct_rows(self):
        e = dict(source="CRM", stage="AUTHORITY", outcome="REFUSED", reason="SLOT_CONFLICT",
                 command_key="form:crm:rev1:v3:abc", attempt_ref="form:crm:rev1:v3:abc")
        self.assertTrue(bae.record_attempt_event(self.db, **e))
        self.assertFalse(bae.record_attempt_event(self.db, **e), "a replay wrote a second row")
        self.assertTrue(bae.record_attempt_event(self.db, **{**e, "attempt_ref": "other"}))
        self.assertTrue(bae.record_attempt_event(self.db, **{**e, "reason": "STALE_VERSION"}))
        self.assertEqual(len(_events(self.db)), 3)
        # once-per-attempt (the finaliser's rule): the reason does not distinguish
        a = bae.fresh_attempt_ref()
        self.assertNotEqual(a, bae.fresh_attempt_ref())
        self.assertTrue(bae.insert_attempt_event(self.db, source="X", stage="FINALISE",
                                                 outcome="REFUSED", reason="R1", attempt_ref=a,
                                                 once_per_attempt=True))
        self.assertFalse(bae.insert_attempt_event(self.db, source="X", stage="FINALISE",
                                                  outcome="REFUSED", reason="R2", attempt_ref=a,
                                                  once_per_attempt=True))

    def test_p4u_04_vocabulary_is_typed(self):
        with self.assertRaises(ValueError):
            bae.insert_attempt_event(self.db, source="X", stage="WHENEVER", outcome="REFUSED",
                                     reason="R")
        with self.assertRaises(ValueError):
            bae.insert_attempt_event(self.db, source="X", stage="AUTHORITY", outcome="OK",
                                     reason="R")
        self.assertEqual((bae.outcome_for(BookingOutcome.RETRYABLE_FAILURE),
                          bae.outcome_for(BookingOutcome.INTERNAL_FAILURE),
                          bae.outcome_for(BookingOutcome.SLOT_CONFLICT)),
                         ("RETRYABLE", "INTERNAL", "REFUSED"))

    def test_p4u_05_an_evidence_failure_is_swallowed_and_changes_nothing(self):
        with mock.patch.object(bae, "insert_attempt_event", side_effect=RuntimeError("db down")):
            self.assertFalse(bae.record_attempt_event(self.db, source="X", stage="AUTHORITY",
                                                      outcome="REFUSED", reason="R"))
        self.assertEqual(_events(self.db), [])

    def test_p4u_06_the_application_never_updates_or_deletes_an_event(self):
        """Append-only in application code: no production module issues an UPDATE or DELETE
        against booking_attempt_events (PostgreSQL additionally enforces it, see the PG file)."""
        import re
        app = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"
        hits = []
        for f in app.rglob("*.py"):
            src = f.read_text(encoding="utf-8")
            if re.search(r"(update|delete)\(\s*BookingAttemptEvent", src) or \
                    re.search(r"(UPDATE|DELETE FROM)\s+booking_attempt_events", src):
                hits.append(f.name)
        self.assertEqual(hits, [])


# ══ 2. operator paths ═══════════════════════════════════════════════════════════════════

class P4_OperatorPaths(unittest.TestCase):

    def setUp(self):
        self.db, self.lead, self.tid = _world()
        res = BookingAuthority(self.db).execute(r3._cmd(self.lead, key="seed-booking"))
        self.assertEqual(res.outcome, BookingOutcome.COMMITTED, res.reason)
        self.rev = self.db.get(Revision, res.revision_id)
        self.c = _api(self.db)

    def snapshot(self):
        self.db.expire_all()
        r = self.db.get(Revision, self.rev.id)
        return (r.turno_fecha, r.turno_hora, r.estado_revision, r.booking_version,
                self.db.query(BookingCommandRecord).count())

    def test_p4u_10_cd05_patch_revision_refuses_every_booking_field(self):
        before = self.snapshot()
        for body in ({"turno_hora": "15:00:00"}, {"turno_fecha": (D + timedelta(days=1)).isoformat()},
                     {"estado_revision": "CANCELADO"}):
            with self.subTest(body=body):
                r = self.c.patch(f"/revisions/{self.rev.id}", json=body)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(r.json()["detail"]["code"], "BOOKING_CHANGE_REQUIRES_AUTHORITY")
                self.assertEqual(self.snapshot(), before, "a booking field was written")
        self.assertEqual({e.reason for e in _events(self.db, source="API")},
                         {"BOOKING_CHANGE_REQUIRES_AUTHORITY"})

    def test_p4u_11_cd05_non_booking_and_unchanged_fields_still_save(self):
        r = self.c.patch(f"/revisions/{self.rev.id}",
                         json={"marca": "Fiat", "turno_hora": self.rev.turno_hora.isoformat(),
                               "estado_revision": " " + self.rev.estado_revision.lower()})
        self.assertEqual(r.status_code, 200, r.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(Revision, self.rev.id).marca, "Fiat")

    def test_p4u_12_cp102_api_post_without_a_key_is_refused_unwritten(self):
        n = self.db.query(Revision).count()
        r = self.c.post(f"/leads/{self.lead.id}/revisions",
                        json={"turno_fecha": D.isoformat(), "turno_hora": "16:00:00"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                         (428, "IDEMPOTENCY_KEY_REQUIRED"))
        self.assertEqual(self.db.query(Revision).count(), n)
        ok1 = self.c.post(f"/leads/{self.lead.id}/revisions",
                          json={"turno_fecha": D.isoformat(), "turno_hora": "16:00:00"},
                          headers={"Idempotency-Key": "k-1"})
        ok2 = self.c.post(f"/leads/{self.lead.id}/revisions",
                          json={"turno_fecha": D.isoformat(), "turno_hora": "16:00:00"},
                          headers={"Idempotency-Key": "k-1"})
        self.assertEqual((ok1.status_code, ok2.json()["id"]), (200, ok1.json()["id"]),
                         "a retry with one key did not replay")
        other = self.c.post(f"/leads/{self.lead.id}/revisions",
                            json={"turno_fecha": D.isoformat(), "turno_hora": "17:00:00"},
                            headers={"Idempotency-Key": "k-2"})
        self.assertNotEqual(other.json()["id"], ok1.json()["id"], "two keys, one action")

    def test_p4u_13_cp102_patch_latest_without_if_match_or_key_writes_nothing(self):
        latest = self.db.query(Revision).filter(Revision.lead_id == self.lead.id).order_by(
            Revision.created_at.desc()).first()
        before = (latest.marca, latest.turno_hora)
        r = self.c.patch(f"/leads/{self.lead.id}/revisions/latest",
                         json={"turno_hora": "14:00:00", "marca": "Renault"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                         (428, "IDEMPOTENCY_KEY_REQUIRED"))
        self.db.expire_all()
        latest = self.db.get(Revision, latest.id)
        self.assertEqual((latest.marca, latest.turno_hora), before,
                         "the non-booking edit was committed beside a refused booking change")

    def test_p4u_14_crm_form_without_a_booking_version_is_a_typed_refusal(self):
        from app.ui import kanban_actions as ka
        before = self.snapshot()
        resp = ka.ui_revision_latest_update(**self._form(turno_hora="15:30", booking_version=""))
        self.assertEqual(resp.status_code, 422)
        self.assertIn(b"VALIDATION_FAILED", resp.body)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([e.reason for e in _events(self.db, source="CRM")],
                         ["BOOKING_VERSION_REQUIRED"])

    def test_p4u_15_crm_malformed_date_or_time_is_a_422_not_a_500(self):
        from fastapi import HTTPException
        from app.ui import kanban_actions as ka
        for bad in ({"turno_fecha": "03/11/2026"}, {"turno_hora": "10h"}):
            with self.subTest(bad=bad), self.assertRaises(HTTPException) as e:
                ka.ui_revision_latest_update(**self._form(**bad))
            self.assertEqual(e.exception.status_code, 422)

    def test_p4u_16_crm_overlap_is_a_typed_409_page_and_leaves_evidence(self):
        from app.ui import kanban_actions as ka
        other = BookingAuthority(self.db).execute(r3._cmd(self.lead, at=time(12), key="other"))
        self.assertTrue(other.ok)
        self.db.expire_all()
        rev = self.db.get(Revision, other.revision_id)
        resp = ka.ui_revision_latest_update(**self._form(
            turno_hora="10:15", booking_version=str(rev.booking_version)))
        self.assertEqual(resp.status_code, 409)
        self.assertIn(b"SLOT_CONFLICT", resp.body)
        self.assertEqual([(e.stage, e.outcome) for e in _events(self.db, source="CRM")],
                         [("AUTHORITY", "REFUSED")])

    def test_p4u_17_deleting_an_occupying_revision_cancels_it_through_the_authority(self):
        from app.ui import kanban_actions as ka
        item = ka._pending_store({"type": "revision", "revision_id": self.rev.id})
        item["deadline"] = ka._utcnow() - timedelta(seconds=1)
        calls = []
        real = BookingAuthority.execute

        def spy(auth, cmd, **k):
            calls.append(cmd.operation)
            return real(auth, cmd, **k)
        with mock.patch.object(BookingAuthority, "execute", spy):
            resp = ka.ui_commit_delete(token=item["token"], db=self.db)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, [BookingOperation.CANCEL])
        self.db.expire_all()
        self.assertIsNone(self.db.get(Revision, self.rev.id))
        self.assertIn("CANCEL", [r.operation for r in self.db.query(BookingCommandRecord)])

    def test_p4u_18_a_refused_cancellation_deletes_nothing(self):
        from app.services.booking_authority import refuse
        from app.ui import kanban_actions as ka
        item = ka._pending_store({"type": "lead", "lead_id": self.lead.id})
        item["deadline"] = ka._utcnow() - timedelta(seconds=1)
        with mock.patch.object(BookingAuthority, "execute", lambda *a, **k: refuse(
                BookingOutcome.RETRYABLE_FAILURE, "THREAD_STATE_CONTENDED")):
            resp = ka.ui_commit_delete(token=item["token"], db=self.db)
        self.assertEqual(resp.status_code, 503)
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(Lead, self.lead.id))
        self.assertIsNotNone(self.db.get(Revision, self.rev.id))

    def _form(self, **over):
        self.db.expire_all()
        latest = self.db.query(Revision).filter(Revision.lead_id == self.lead.id).order_by(
            Revision.created_at.desc()).first()
        base = dict(lead_id=self.lead.id, db=self.db,
                    turno_fecha=latest.turno_fecha.isoformat() if latest.turno_fecha else None,
                    turno_hora=latest.turno_hora.strftime("%H:%M") if latest.turno_hora else None,
                    estado_revision=latest.estado_revision,
                    booking_version=str(latest.booking_version))
        import inspect as _inspect
        from app.ui import kanban_actions as ka
        params = _inspect.signature(ka.ui_revision_latest_update).parameters
        for name, p in params.items():
            if name not in base:
                d = p.default
                base[name] = getattr(d, "default", d) if d is not _inspect._empty else None
        base.update(over)
        return base


# ══ 3. rule K-E (CD-07) on a real Session ═══════════════════════════════════════════════

class P4_FlushOrder(unittest.TestCase):

    def setUp(self):
        self.db, self.lead, self.tid = _world()
        self.state = self.db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == self.tid)).scalar_one()

    def test_p4u_20_a_state_written_after_a_lead_in_one_transaction_is_refused(self):
        from app.services.flush_order import LockOrderViolation
        self.db.get(Lead, self.lead.id).nombre = "first-the-lead"
        self.db.flush()
        self.state.needs_human = True
        with self.assertRaises(LockOrderViolation) as e:
            self.db.flush()
        self.assertEqual(e.exception.thread_ids, (self.tid,))
        self.db.rollback()
        self.db.expire_all()
        self.assertNotEqual(self.db.get(Lead, self.lead.id).nombre, "first-the-lead",
                            "the refused transaction left a write behind")

    def test_p4u_21_allowed_orders_are_unaffected(self):
        cases = {
            "lead only": lambda: setattr(self.db.get(Lead, self.lead.id), "nombre", "x"),
            "state only": lambda: setattr(self.state, "needs_human", True),
            "both in one flush": lambda: (setattr(self.db.get(Lead, self.lead.id), "nombre", "y"),
                                          setattr(self.state, "needs_human", False)),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                change()
                self.db.commit()
        # state first, then the lead in a later flush, then the state again: legal
        self.state.needs_human = True
        self.db.flush()
        self.db.get(Lead, self.lead.id).nombre = "z"
        self.db.flush()
        self.state.needs_human = False
        self.db.commit()

    def test_p4u_22_an_explicit_k1_acquisition_is_honoured(self):
        from app.services.flush_order import note_thread_states_locked
        note_thread_states_locked(self.db, [self.tid])
        self.db.get(Lead, self.lead.id).nombre = "lead"
        self.db.flush()
        self.state.needs_human = True
        self.db.commit()

    def test_p4u_23_the_bookkeeping_is_per_transaction(self):
        self.db.get(Lead, self.lead.id).nombre = "t1"
        self.db.commit()                                   # lead written in transaction 1
        self.state.needs_human = True
        self.db.commit()                                   # transaction 2 starts clean


# ══ 4. DETACHED wording, recovery status ═══════════════════════════════════════════════

class P4_Detached(unittest.TestCase):

    def test_p4u_30_detached_wording_is_truthful(self):
        from app.services.booking_authority import _MESSAGES
        msg = _MESSAGES[BookingOutcome.COMMAND_DETACHED]
        for needed in ("historial", "eliminado", "no puede repetirse", "No hay un turno vigente"):
            self.assertIn(needed, msg)
        for forbidden in ("anulado", "reintent", "volvé a", "reserv"):
            self.assertNotIn(forbidden, msg.lower())

    def test_p4u_31_recovery_reports_detached_and_claims_nothing(self):
        from app.services.booking_recovery import recover_acknowledgement
        db, lead, tid = _world()
        res = BookingAuthority(db).execute(r3._cmd(lead, key="to-detach",
                                                   tracks_acknowledgement=True))
        self.assertTrue(res.ok)
        db.query(BookingCommandRecord).update(
            {"command_state": "DETACHED", "detached_at": datetime.now(timezone.utc),
             "detached_reason": "TESTER_RESET", "revision_id": None, "lead_id": None,
             "thread_id": None, "profesional_id": None, "slot_start": None, "slot_end": None,
             "occupying": False})
        db.commit()
        with mock.patch("app.services.booking_flow_service.send_booking_acknowledgement") as send:
            self.assertEqual(recover_acknowledgement(db, "to-detach"), "DETACHED")
        send.assert_not_called()
        db.expire_all()
        self.assertEqual(db.query(BookingCommandRecord).one().ack_attempts, 0)


# ══ 5. external I/O guard at the raw transports ═══════════════════════════════════════

class P4_ExternalIO(unittest.TestCase):

    def test_p4u_40_no_transport_leaves_the_process_inside_a_booking_transaction(self):
        from app.services import booking_authority as ba
        from app.services import resend_email
        from app.ui import whatsapp_ui
        import os
        from types import SimpleNamespace
        token = ba._TXN_SCOPE.set(object())
        try:
            # outbound enabled and a non-dummy token, so the sender really reaches its network
            # call (the kill switch and the dummy mode both return before it)
            with mock.patch.dict(os.environ, {"OUTBOUND_ENABLED": "true"}), \
                    mock.patch.object(whatsapp_ui, "get_settings", lambda: SimpleNamespace(
                        whatsapp_token="probe-not-a-credential",
                        whatsapp_phone_number_id="000000000000000")), \
                    mock.patch("urllib.request.urlopen",
                               side_effect=AssertionError("network reached")) as net:
                with self.assertRaises(ba.ExternalIOInsideBookingTransaction):
                    whatsapp_ui._send_whatsapp_cloud_text(to_wa_id="5491100000000", text="x")
                self.assertFalse(resend_email.send_booking_notification(
                    api_key="k", from_email="a@example.invalid", to_email="b@example.invalid",
                    reply_to_email=None, lead_id=1, revision_id=1, buyer_name="-",
                    buyer_phone="-", buyer_email="-", source="whatsapp", marca="-",
                    modelo="-", anio="-", tipo_vehiculo="-", zone_group="-", zone_detail="-",
                    address="-", seller_type="-", seller_name="-", scheduled_date="-",
                    scheduled_time="-", precio_base="-", viaticos="-", precio_total="-"))
            self.assertFalse(net.called, "a transport opened a connection inside the booking")
        finally:
            ba._TXN_SCOPE.reset(token)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
