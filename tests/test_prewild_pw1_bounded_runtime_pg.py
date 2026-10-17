"""Pre-Wild PW1 — the four bounded runtime prerequisites ON REAL POSTGRESQL, through the
production Conversation Engine, Data Exchange service, acknowledgement ledger and routes.

  G37R_PG_URL      a Track A disposable database (create_all + stamp, upgraded to head) —
                   the motorcycle/cycle-reset world of `test_l4_7w5_g3_7r_r3_cycle_reset_bound_pg`
  CONV_P3_PG_URL   a convergence disposable database (bootstrap-built head) — the booking,
                   acknowledgement and operator worlds of the convergence suites

Each class skips cleanly without its database, and says so. Only the network edge is stubbed:
no Meta, Resend or SMTP call can leave the process (`_sb2b_harness.Meta` turns any `urlopen`
into an assertion), and every observation is read back from persisted rows.

  PW1-A (R3RV-01)  a motorcycle contact submission reaches the human handoff with and without a
                   pending cycle reset, flag on or off, once per delivery; a stale motorcycle
                   mirror cannot turn a submitted booking into a handoff.
  PW1-B (P4RV-09)  a booking's lost evidence or an I/O-guard trip on its e-mail is an operator
                   condition; booking, offer, ledger, acknowledgement and notification truth
                   are unchanged, and a failing condition write is bounded.
  PW1-C (CD-09)    a generic PATCH racing an acceptance can neither redirect nor invalidate it.
  PW1-D (RV1-01)   per Meta outcome: retry eligibility, ledger state, operator visibility,
                   dedup rows, sender calls, and the booking — never a second acknowledgement
                   after an outcome that does not prove non-acceptance.
"""
from __future__ import annotations

import contextlib
import dataclasses
import itertools
import os
import pathlib
import sys
import threading
import time as _t
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
import test_l4_7w5_g3_7r_r3_cycle_reset_bound_pg as R3  # noqa: E402
from test_convergence_p3_race_pg import PG_URL as CONV_URL, _Race  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, SecurityEvent, ThreadRevision,  # noqa: E402
                        WhatsAppMessage, WhatsAppOutboundDedup, WhatsAppThreadState)
from app.services.booking_authority import (AckClaim, BookingAuthority,  # noqa: E402
                                            flow_idempotency_key)

ADVISOR = "A la brevedad uno de nuestros asesores se estará contactando con vos."
_P = []


def setUpModule():
    from offer_fixture import start_converged_booking
    _P.extend(start_converged_booking())        # the flag ON for the CE worlds (R3's rule)


def tearDownModule():
    from offer_fixture import stop_converged_booking
    stop_converged_booking(_P)


# ══ PW1-A — R3RV-01 on the real engine ═════════════════════════════════════════════════

@unittest.skipUnless(R3.PG_URL, "G37R_PG_URL not set — no disposable PostgreSQL; nothing proven")
class PW1A_MotorcycleUnderCycleReset(R3._PGBase):

    def _moto_pending(self):
        """What the motorcycle Turn 1 leaves: a MOTO candidate and the contact Flow's token."""
        db = self.SF()
        db.add(self.M.WhatsAppThreadCandidate(thread_id=self.thread_id, tipo_vehiculo="MOTO",
                                              marca="Honda", modelo="CB500", status="mentioned",
                                              source_text="una moto honda cb500"))
        token = f"moto-handoff-{self.thread_id}-1700000000"
        db.execute(text("UPDATE whatsapp_thread_states SET flow_booking_token=:k "
                        "WHERE thread_id=:t"), {"k": token, "t": self.thread_id})
        db.commit(); db.close()
        return token

    def _deliver(self, token, wamid, payload=None):
        from app.schemas.conversation import ConversationHandleIn
        db, eng = self._engine_for()
        try:
            return eng.handle(ConversationHandleIn(
                thread_id=self.thread_id, wa_message_id=wamid, wa_id=R3.WA,
                message_type="flow_response",
                flow_response=dict(payload or {"nombre_apellido": "Pedro Moto",
                                               "email": "p@example.test",
                                               "como_llego": "Instagram"}),
                flow_token=token)).action
        finally:
            db.close()

    def assert_motorcycle_handoff(self, s):
        self.assertTrue(s["needs_human"] and s["lead_needs_human"], s)
        self.assertEqual((s["bookings"], s["revisions"]), (0, 0), s)
        self.assertFalse(any(o[1] == "ACCEPTED" for o in s["offers"]), s)
        self.assertEqual([x for x in self.sends if x[0] == "text"], [("text", ADVISOR[:60])],
                         self.sends)
        self.assertEqual(sum(1 for x in self.sends if x[0] == "notify"), 1, self.sends)

    def _case(self, *, reset):
        token = self._moto_pending()
        if reset:
            self._arm_cycle_reset()
        wamid = f"wamid.pw1a.{reset}.{_t.time()}"
        self._persist_inbound(wamid)
        action = self._deliver(token, wamid)
        s = self._snapshot()
        return token, wamid, action, s

    def test_pw1_pg_a01_moto_without_reset_reaches_the_human_handoff(self):
        _tok, _w, action, s = self._case(reset=False)
        self.assertEqual(action, "replied")
        self.assert_motorcycle_handoff(s)

    def test_pw1_pg_a02_moto_with_cycle_reset_reaches_the_same_handoff(self):
        _tok, _w, action, s = self._case(reset=True)
        self.assertEqual(action, "replied")
        self.assertFalse(s["cycle_reset_pending"], "the reset itself must still run")
        self.assert_motorcycle_handoff(s)

    def test_pw1_pg_a03_a_duplicate_delivery_escalates_and_sends_once(self):
        token, wamid, _action, first = self._case(reset=True)
        sends = list(self.sends)
        self.assertEqual(self._deliver(token, wamid), "skipped_dedup")
        self.assertEqual(self.sends, sends, "a redelivery produced outbound work")
        again = self._snapshot()
        self.assertEqual((again["out_rows"], again["needs_human"]),
                         (first["out_rows"], True))

    def test_pw1_pg_a04_flag_off_moto_with_reset_is_the_same_safe_handoff(self):
        from app.settings import get_settings
        off = dataclasses.replace(get_settings(), smart_booking_ce_enabled=False)
        with mock.patch("app.settings.get_settings", lambda: off):
            _tok, _w, action, s = self._case(reset=True)
        self.assertEqual(action, "replied")
        self.assert_motorcycle_handoff(s)

    def test_pw1_pg_a05_a_stale_moto_mirror_cannot_turn_a_booking_into_a_handoff(self):
        token = self._issue()                                   # a real ISSUED offer
        db = self.SF()
        db.execute(text("UPDATE whatsapp_thread_states SET flow_booking_token=:k "
                        "WHERE thread_id=:t"),
                   {"k": f"moto-handoff-{self.thread_id}-1", "t": self.thread_id})
        db.commit(); db.close()
        wamid = f"wamid.pw1a.stale.{_t.time()}"
        self._persist_inbound(wamid)
        self._deliver(token, wamid, payload=R3.BOOKING_PAYLOAD)
        s = self._snapshot()
        # Not a motorcycle handoff: the submission stays on the booking path, where the
        # existing SB2B rule — the mirror can refuse, never permit — refuses it, typed.
        self.assertNotIn(("text", ADVISOR[:60]), self.sends)
        self.assertEqual(s["bookings"], 0, s)
        self.assertEqual([(o[1], o[4]) for o in s["offers"]],
                         [("ISSUED", "FLOW_TOKEN_NOT_ACTIVE")], s)
        db = self.SF()
        moto = db.execute(text("SELECT count(*) FROM whatsapp_thread_candidates "
                               "WHERE tipo_vehiculo='MOTO'")).scalar()
        db.close()
        self.assertEqual(moto, 0, "no motorcycle evidence may be invented for an AUTO request")

    def test_pw1_pg_a06_auto_stays_on_the_auto_path(self):
        token = self._issue()
        wamid = f"wamid.pw1a.auto.{_t.time()}"
        self._persist_inbound(wamid)
        self._deliver(token, wamid, payload=R3.BOOKING_PAYLOAD)
        s = self._snapshot()
        self.assertEqual(s["bookings"], 1, s)
        self.assertEqual([o[1] for o in s["offers"]], ["ACCEPTED"], s)
        self.assertNotIn(("text", ADVISOR[:60]), self.sends)


# ══ shared convergence base: the booking worlds ════════════════════════════════════════

class _Edge(H.Meta):
    """The Meta edge answering with one chosen outcome, every call recorded."""

    _seq = itertools.count(1)

    def __init__(self, effect="ok"):
        super().__init__("ok")
        self.effect = effect

    def _send(self, *, to_wa_id, text):
        self.sent.append((to_wa_id, text))
        if isinstance(self.effect, BaseException):
            raise self.effect
        return f"wamid.PW1.OUT.{next(self._seq):06d}", {}


@unittest.skipUnless(CONV_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class _Conv(_Race):

    def setUp(self):
        super().setUp()
        p = mock.patch.object(sys.modules["app.db"], "engine", self.engine)
        p.start()
        self.addCleanup(p.stop)

    def conditions(self, event_type=None):
        with Session(self.engine) as db:
            q = select(SecurityEvent).order_by(SecurityEvent.id)
            if event_type:
                q = q.where(SecurityEvent.event_type == event_type)
            return db.execute(q).scalars().all()

    def notified_at(self, w):
        with Session(self.engine) as db:
            return db.execute(select(ThreadRevision.notification_sent_at).where(
                ThreadRevision.thread_id == w.thread_id,
                ThreadRevision.status == "booked")).scalar_one()

    def attention(self):
        from app.routes.ops_dashboard import booking_attention, operational_conditions
        with Session(self.engine) as db:
            return booking_attention(db, pending_older_than_seconds=0), operational_conditions(db)

    def booked_once(self, w):
        f = self.facts(w)
        self.assertEqual(f["occupying"], 1, f)
        self.assertEqual([s for _i, s in f["thread_revisions"]], ["booked"], f)
        self.assertEqual([o[0] for o in f["offers"]], ["ACCEPTED"], f)
        self.assertEqual([r[5] for r in f["ledger_full"]], ["COMMITTED"], f)
        return f


# ══ PW1-B — P4RV-09 on a real committed booking ═══════════════════════════════════════

def _with_resend(cfg):
    return dataclasses.replace(cfg, resend_api_key="re_pw1_not_a_key",
                               internal_booking_email_to="team@example.invalid",
                               internal_booking_email_from="n@example.invalid")


class PW1B_ObservabilityFailures(_Conv):

    def _book(self, *, hook=None, cfg=None):
        w = H.seed(self.engine)
        with H.Meta() as m:
            out, _eng = H.run_turn(self.engine, H.flow_event(w), cfg=cfg, engine_hook=hook)
        return w, out, m

    def test_pw1_pg_b01_an_io_guard_trip_on_the_booking_email_is_visible_and_changes_nothing(self):
        from app.services import booking_authority as BA

        def inside_booking_scope(eng):
            real = eng._send_booking_notification

            def guarded(*a, **k):                     # a future misplacement of the sender
                tok = BA._TXN_SCOPE.set(BA._TxnScope("pw1-probe"))
                try:
                    return real(*a, **k)
                finally:
                    BA._TXN_SCOPE.reset(tok)
            eng._send_booking_notification = guarded
        w, out, m = self._book(hook=inside_booking_scope, cfg=_with_resend(H.settings()))
        self.booked_once(w)
        self.assertEqual(m.egress, [], "the guard must stop the e-mail before the network")
        self.assertEqual([t for _w, t in m.sent].count(H.OWNER_TEXT), 1)
        self.assertEqual(self.owner_texts(w), ["sent"])
        self.assertIsNone(self.notified_at(w), "a failed notification must never read SENT")
        rows, conds = self.attention()
        [row] = [r for r in rows if r["thread_id"] == w.thread_id]
        self.assertEqual((row["notification_state"], row["notification_last_reason"]),
                         ("FAILED", "NOTIFICATION_SEND_FAILED"))
        self.assertEqual([(c["condition"], c["details"]["sender"]) for c in conds],
                         [("EXTERNAL_IO_INSIDE_BOOKING_TRANSACTION", "send_booking_notification")])

    def test_pw1_pg_b02_lost_notification_evidence_is_visible_and_never_reads_sent(self):
        from app.services import booking_attempt_events as E
        real = E.insert_attempt_event

        def failing(session, **kw):
            if kw.get("stage") == "NOTIFICATION":
                raise RuntimeError("evidence store unavailable")
            return real(session, **kw)
        with mock.patch.object(E, "insert_attempt_event", failing):
            w, out, m = self._book()                       # e-mail not configured → HELD
        self.booked_once(w)
        self.assertEqual(self.owner_texts(w), ["sent"])
        self.assertIsNone(self.notified_at(w))
        rows, conds = self.attention()
        [row] = [r for r in rows if r["thread_id"] == w.thread_id]
        self.assertEqual(row["notification_state"], "PENDING")
        self.assertEqual([(c["condition"], c["details"]["reason"], c["details"]["stage"])
                          for c in conds],
                         [("BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED",
                           "NOTIFICATION_NOT_CONFIGURED", "NOTIFICATION")])

    def test_pw1_pg_b04_a_condition_is_written_outside_the_callers_transaction(self):
        """Its own session: durable when the caller rolls back, and never a commit of the
        caller's pending work (a condition must not decide anything by committing it)."""
        from app.models import Lead
        from app.services.security_events import SecurityEventType, record_operational_condition
        w = H.seed(self.engine)
        with Session(self.engine) as caller:
            caller.get(Lead, w.lead_id).nombre = "UNCOMMITTED-BY-CALLER"
            caller.flush()
            self.assertTrue(record_operational_condition(
                caller, SecurityEventType.BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED,
                source_component="probe", thread_id=w.thread_id, details={"reason": "R"}))
            caller.rollback()
        with Session(self.engine) as db:
            self.assertNotEqual(db.get(Lead, w.lead_id).nombre, "UNCOMMITTED-BY-CALLER",
                                "the condition write committed the caller's transaction")
        self.assertEqual([c.thread_id for c in self.conditions()], [w.thread_id])

    def test_pw1_pg_b03_a_failing_condition_write_is_bounded_and_the_booking_stands(self):
        from app.services import booking_attempt_events as E
        from app.services import security_events as S
        with mock.patch.object(E, "insert_attempt_event", side_effect=RuntimeError("down")), \
                mock.patch.object(S, "create_security_event",
                                  side_effect=RuntimeError("also down")) as cse:
            w, out, m = self._book()
        self.booked_once(w)
        self.assertEqual(self.owner_texts(w), ["sent"])
        self.assertEqual(cse.call_count, 1)
        self.assertEqual(self.conditions(), [])


# ══ PW1-C — CD-09: a generic PATCH racing a real acceptance ═══════════════════════════

class PW1C_PatchVersusAcceptance(_Conv):

    def setUp(self):
        super().setUp()
        from app.api import thread_revisions as tr_api
        from app.api import whatsapp as api
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.app.include_router(tr_api.router)
        engine = self.engine

        def per_request():
            db = Session(engine)
            try:
                yield db
            finally:
                db.close()
        for route in self.app.routes:
            for dep in getattr(getattr(route, "dependant", None), "dependencies", []):
                if getattr(dep.call, "__name__", "").endswith("get_db") or \
                        getattr(dep.call, "__name__", "") == "_get_db_gen":
                    self.app.dependency_overrides[dep.call] = per_request
        self.client = TestClient(self.app)

    def url(self, w):
        return next(r.path for r in self.app.routes
                    if getattr(r, "path", "").endswith("/thread/{thread_id}/state")
                    ).replace("{thread_id}", str(w.thread_id))

    def state(self, w):
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            return (st.preferred_day, st.preferred_time, st.home_zone_group, st.flow_booking_token)

    def test_pw1_pg_c01_a_patch_behind_a_held_acceptance_is_bounded_typed_and_writes_nothing(self):
        w = H.seed(self.engine)
        before = self.state(w)
        held, release = threading.Event(), threading.Event()

        def authority_like():
            with Session(self.engine) as db:
                db.execute(text("SELECT 1 FROM whatsapp_thread_states WHERE thread_id=:t "
                                "FOR NO KEY UPDATE"), {"t": w.thread_id})
                held.set()
                release.wait(20)
                db.rollback()
        th = threading.Thread(target=authority_like, daemon=True)
        th.start()
        self.assertTrue(held.wait(10))
        started = _t.perf_counter()
        r = self.client.patch(self.url(w), json={"preferred_day": "2031-01-07"})
        elapsed = _t.perf_counter() - started
        release.set(); th.join(10)
        self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                         (409, "THREAD_STATE_CONTENDED"), r.text)
        self.assertLess(elapsed, 8.0, "the PATCH must be bounded, not wait out the holder")
        self.assertEqual(self.state(w), before)

    def test_pw1_pg_c02_concurrent_patches_cannot_redirect_the_booking(self):
        for trial in range(6):
            with self.subTest(trial=trial):
                self.setUp_world_reset()
                w = H.seed(self.engine)
                results, go = {}, threading.Barrier(2)

                def patch():
                    go.wait()
                    r = self.client.patch(self.url(w), json={"preferred_day": "2031-01-07",
                                                             "home_zone_group": "SUR"})
                    results["patch"] = (r.status_code, r.json().get("detail", {}).get("code")
                                        if r.status_code != 200 else None)

                def accept():
                    go.wait()
                    with H.Meta():
                        results["turn"] = H.run_turn(self.engine, H.flow_event(w))[0].action
                ts = [threading.Thread(target=f, daemon=True) for f in (patch, accept)]
                for x in ts:
                    x.start()
                for x in ts:
                    x.join(60)
                f = self.booked_once(w)
                self.assertEqual([(d, t) for _i, d, t, _e in f["revisions"]], [(w.day, w.at)], f)
                # the PATCH lost (typed) or ran after the acceptance (offer no longer ISSUED)
                self.assertIn(results["patch"], ((409, "THREAD_STATE_FIELD_BOUND_TO_LIVE_OFFER"),
                                                 (409, "THREAD_STATE_CONTENDED"), (200, None)),
                              results)

    def test_pw1_pg_c04_a_patch_before_acceptance_cannot_redirect_the_slot(self):
        """The decisive order: the generic PATCH lands while the offer is ISSUED and before
        the customer's submission. The Conversation Engine books the slot recorded in the
        thread state, so on the parent this moved the booking to the patched hour."""
        w = H.seed(self.engine)
        moved = (w.at.hour + 1) % 24
        r = self.client.patch(self.url(w), json={"preferred_time": f"{moved:02d}:00"})
        self.assertEqual((r.status_code, r.json().get("detail", {}).get("code")),
                         (409, "THREAD_STATE_FIELD_BOUND_TO_LIVE_OFFER"), r.text)
        with H.Meta():
            H.run_turn(self.engine, H.flow_event(w))
        f = self.booked_once(w)
        self.assertEqual([(d, t) for _i, d, t, _e in f["revisions"]], [(w.day, w.at)], f)

    def setUp_world_reset(self):
        from test_convergence_p3_race_pg import _truncate
        _truncate(self.engine)

    def test_pw1_pg_c03_pointers_refused_and_a_booked_revision_stays_protected(self):
        w = H.seed(self.engine)
        with H.Meta():
            H.run_turn(self.engine, H.flow_event(w))
        f = self.booked_once(w)
        tr_id = f["thread_revisions"][0][0]
        before = self.state(w)
        for body in ({"current_revision_id": 999}, {"flow_booking_token": "forged"},
                     {"current_focus_candidate_id": 999}):
            with self.subTest(body=body):
                r = self.client.patch(self.url(w), json=body)
                self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                                 (409, "THREAD_STATE_FIELD_AUTHORITY_OWNED"))
        self.assertEqual(self.state(w), before)
        # after acceptance no offer is ISSUED: the negotiated facts are ordinary fields again,
        # and editing them moves nothing that was booked
        r = self.client.patch(self.url(w), json={"preferred_day": "2031-01-07"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([(d, t) for _i, d, t, _e in self.facts(w)["revisions"]], [(w.day, w.at)])
        tr_url = next(r.path for r in self.app.routes
                      if getattr(r, "path", "").endswith("/{revision_id}")
                      and "PATCH" in getattr(r, "methods", ())).replace("{revision_id}", str(tr_id))
        r = self.client.patch(tr_url, json={"scheduled_date": "2031-01-07"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                         (409, "BOOKING_CHANGE_REQUIRES_AUTHORITY"))
        self.booked_once(w)


# ══ PW1-D — RV1-01 through the real ledger and Data Exchange confirmation ═════════════

ENVELOPE = '{"error": {"message": "(#131030) Recipient not allowed", "type": "OAuthException", ' \
           '"code": 131030, "fbtrace_id": "AbC"}}'


class PW1D_AcknowledgementPolicy(_Conv):

    def ledger(self, key):
        with Session(self.engine) as db:
            r = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.idempotency_key == key)).scalar_one()
            return (r.ack_status, r.ack_attempts, bool(r.ack_duplicate_risk), r.ack_last_error,
                    r.ack_claim_token)

    def dedup_rows(self, w):
        with Session(self.engine) as db:
            return db.query(WhatsAppOutboundDedup).filter(
                WhatsAppOutboundDedup.thread_id == w.thread_id).count()

    def listed(self, key):
        with Session(self.engine) as db:
            return any(r["idempotency_key"] == key
                       for r in BookingAuthority(db).ledger_rows_needing_attention(
                           pending_older_than_seconds=0))

    def _first(self, effect):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with _Edge(effect) as m:
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
        return w, key, len(m.sent)

    def _retry(self, w):
        with _Edge("ok") as m:
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
        return len(m.sent)

    def test_pw1_pg_d01_definite_rejections_admit_exactly_one_retry(self):
        from app.ui.whatsapp_ui import MetaSendError
        for effect, err in ((MetaSendError(400, ENVELOPE), "META_REJECTED_400"),
                            (MetaSendError(400, '{"error": {"code": 100, "message": "Bad"}}'),
                             "META_REJECTED_400"),
                            (MetaSendError(429, '{"error": {"code": 130429, "message": "Rate"}}'),
                             "META_REJECTED_429")):
            with self.subTest(err=err, body=effect.raw_body[:30]):
                self.setUp()
                w, key, calls = self._first(effect)
                self.assertEqual(calls, 1)
                self.assertEqual(self.ledger(key)[:4], ("FAILED", 1, False, err))
                self.booked_once(w)
                self.assertEqual(self._retry(w), 1, "a definite rejection admits ONE retry")
                self.assertEqual(self.ledger(key)[:3], ("SENT", 2, False))
                self.assertEqual(self.dedup_rows(w), 2)
                self.assertEqual(self.owner_texts(w).count("sent"), 1)
                self.assertFalse(self.listed(key))
                self.booked_once(w)

    def test_pw1_pg_d02_unproven_outcomes_are_held_and_never_sent_again(self):
        from app.services.booking_recovery import recover_acknowledgement
        from app.ui.whatsapp_ui import MetaSendError
        cases = (
            (MetaSendError(400, ""), "AMBIGUOUS_META_400_UNPROVEN"),
            (MetaSendError(400, "<html>Bad Request</html>"), "AMBIGUOUS_META_400_UNPROVEN"),
            (MetaSendError(408, ""), "AMBIGUOUS_META_408_UNPROVEN"),
            (MetaSendError(408, '{"error": {"message": "timeout"}}'), "AMBIGUOUS_META_408_UNPROVEN"),
            (MetaSendError(408, ENVELOPE), "AMBIGUOUS_META_408_UNPROVEN"),
            (MetaSendError(429, "Too Many Requests"), "AMBIGUOUS_META_429_UNPROVEN"),
            (MetaSendError(500, ENVELOPE), "AMBIGUOUS_META_500"),
            (MetaSendError(502, ""), "AMBIGUOUS_META_502"),
            (MetaSendError(503, ""), "AMBIGUOUS_META_503"),
            (MetaSendError(None, "connection reset by peer"), "AMBIGUOUS_NETWORK_OR_TIMEOUT"),
            (MetaSendError(None, "timed out"), "AMBIGUOUS_NETWORK_OR_TIMEOUT"),
            (ValueError("malformed response body"), "AMBIGUOUS_TRANSPORT_EXCEPTION"),
        )
        for effect, err in cases:
            with self.subTest(err=err, effect=repr(effect)[:50]):
                self.setUp()
                w, key, calls = self._first(effect)
                self.assertEqual(calls, 1)
                status, n, risk, last, claim_token = self.ledger(key)
                self.assertEqual((status, n, risk, last), ("FAILED", 1, True, err))
                self.assertTrue(self.listed(key), "an uncertain acknowledgement must be listed")
                self.booked_once(w)
                self.assertEqual(self._retry(w), 0, "an unproven outcome was sent again")
                with _Edge("ok") as m, Session(self.engine) as db:
                    recover_acknowledgement(db, key)                  # operator recovery
                self.assertEqual(len(m.sent), 0, "recovery after uncertainty sent again")
                # late completion of the uncertain attempt records nothing and re-arms nothing
                with Session(self.engine) as db:
                    late = BookingAuthority(db)
                    claim = AckClaim(key, claim_token, 1, False)
                    self.assertFalse(late.record_acknowledgement(
                        claim, sent=True, transport_id="wamid.PW1.LATE"))
                    self.assertFalse(late.record_acknowledgement(claim, sent=False,
                                                                 error="META_REJECTED_400"))
                self.assertEqual(self.ledger(key)[:4], ("FAILED", 1, True, err))
                self.assertEqual(self._retry(w), 0)
                self.assertEqual(self.dedup_rows(w), 1)
                self.assertEqual(self.owner_texts(w), ["failed"])
                self.booked_once(w)

    def test_pw1_pg_d03_success_is_acknowledged_exactly_once(self):
        w, key, calls = self._first("ok")
        self.assertEqual((calls, self.ledger(key)[:3]), (1, ("SENT", 1, False)))
        self.assertEqual(self._retry(w), 0)
        self.assertEqual(self.owner_texts(w), ["sent"])
        self.assertFalse(self.listed(key))
        self.booked_once(w)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
