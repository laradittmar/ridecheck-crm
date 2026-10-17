"""Pre-Wild PW1 — the four bounded runtime prerequisites, in the DEFAULT regression gate.

SQLite, no network, production functions and routes only. What needs a real lock, a real
cycle reset or the real ledger under concurrency is proven on PostgreSQL in
`test_prewild_pw1_bounded_runtime_pg.py`.

  PW1-A (R3RV-01)  a Flow submission is routed by the token it CARRIES, never by a mirror a
                   cycle reset cleared; the booking-shaped predicate reads the same value.
  PW1-B (P4RV-09)  lost booking evidence and a booking I/O-guard trip on the e-mail path are
                   operator-visible conditions, bounded, never recursive, never authority.
  PW1-C (CD-09)    the generic thread-state and candidate PATCH routes cannot write the
                   booking-owned pointers, nor the negotiated facts of a live offer.
  PW1-D (RV1-01)   a Meta 4xx is a DEFINITE rejection only when Graph's envelope proves it.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402
import test_m21_2_mf_motorcycle_flow as MF  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (SecurityEvent, WhatsAppBookingOffer,  # noqa: E402
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.services.conversation_engine import (_MOTORCYCLE_ADVISOR_REPLY,  # noqa: E402
                                              ConversationEngine)

MOTO = "moto-handoff-415-1700000000"
AUTO = "415-booking-offer-token"


# ══ PW1-A — R3RV-01: route by the submitted token ══════════════════════════════════════

class PW1A_SubmittedTokenRouting(unittest.TestCase):

    def _event(self, token, payload=None):
        from app.schemas.conversation import ConversationHandleIn
        return ConversationHandleIn(thread_id=1, wa_message_id="w", wa_id="549",
                                    message_type="flow_response",
                                    flow_response=payload or {"nombre_apellido": "Pedro"},
                                    flow_token=token)

    def test_pw1_a01_the_routing_token_is_the_submitted_one(self):
        from app.services.conversation_engine import _flow_routing_token
        self.assertEqual(_flow_routing_token(MOTO, None), MOTO)          # mirror cleared
        self.assertEqual(_flow_routing_token(AUTO, MOTO), AUTO)          # stale moto mirror
        self.assertEqual(_flow_routing_token(MOTO, AUTO), MOTO)
        self.assertEqual(_flow_routing_token(None, MOTO), MOTO)          # no token delivered
        self.assertEqual(_flow_routing_token("  ", MOTO), MOTO)
        self.assertEqual(_flow_routing_token(None, None), "")

    def test_pw1_a02_the_predicate_reads_the_value_the_router_reads(self):
        P = ConversationEngine._is_booking_shaped_submission
        cleared = MF._make_state(flow_booking_token=None)
        stale = MF._make_state(flow_booking_token=MOTO)
        self.assertFalse(P(self._event(MOTO), cleared),
                         "a submitted motorcycle token is not booking-shaped, mirror or not")
        self.assertTrue(P(self._event(AUTO), stale),
                        "a stale motorcycle mirror must not hide a submitted booking token")
        self.assertFalse(P(self._event(None), stale))      # tokenless: the pre-reset mirror

    def _route(self, *, mirror, token):
        eng = MF._make_engine()
        state = MF._make_state(flow_booking_token=mirror, needs_human=False)
        lead = MF._make_lead()
        ctx = MF._make_ctx(state=state, lead=lead, candidates=[MF._make_moto_candidate()])
        booked = []
        eng._converged_flow_book = lambda *a, **k: (booked.append(1), "BOOKING_PATH")[1]
        out = eng._process_flow_response(ctx, {"nombre_apellido": "Pedro García"},
                                         flow_token=token)
        return eng, out, state, lead, booked

    def test_pw1_a03_submitted_moto_wins_over_a_cleared_mirror(self):
        eng, out, state, lead, booked = self._route(mirror=None, token=MOTO)
        self.assertEqual(booked, [], "a motorcycle submission reached booking admission")
        self.assertEqual(out.action, "replied")
        self.assertTrue(state.needs_human)
        self.assertTrue(lead.necesita_humano)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _MOTORCYCLE_ADVISOR_REPLY)
        reasons = [c.kwargs.get("reason") for c in
                   eng._send_fallback_human_review_notification.call_args_list]
        self.assertEqual(reasons, ["motorcycle_enquiry"])

    def test_pw1_a04_a_stale_moto_mirror_cannot_turn_auto_into_moto(self):
        eng, out, state, _lead, booked = self._route(mirror=MOTO, token=AUTO)
        self.assertEqual((booked, out), ([1], "BOOKING_PATH"))
        self.assertFalse(state.needs_human)
        eng._send_text_to_wa.assert_not_called()

    def test_pw1_a05_auto_stays_on_the_auto_path(self):
        _eng, out, _state, _lead, booked = self._route(mirror=AUTO, token=AUTO)
        self.assertEqual((booked, out), ([1], "BOOKING_PATH"))

    def test_pw1_a06_the_moto_handler_consumes_only_a_moto_mirror(self):
        eng, _out, state, _lead, _ = self._route(mirror=MOTO, token=MOTO)
        self.assertIsNone(state.flow_booking_token)
        # a live booking offer's mirror is the offer authority's, not this handler's
        eng = MF._make_engine()
        state = MF._make_state(flow_booking_token=AUTO)
        ctx = MF._make_ctx(state=state, lead=MF._make_lead(),
                           candidates=[MF._make_moto_candidate()])
        eng._converged_flow_book = mock.MagicMock(side_effect=AssertionError("booking path"))
        eng._process_flow_response(ctx, {"nombre_apellido": "X"}, flow_token=MOTO)
        self.assertEqual(state.flow_booking_token, AUTO)
        self.assertTrue(state.needs_human)
        self.assertEqual(eng._send_text_to_wa.call_args[0][1], _MOTORCYCLE_ADVISOR_REPLY)


# ══ PW1-B — P4RV-09: operator-visible observability failures ═════════════════════════

def _conditions(engine, event_type=None):
    with Session(engine) as db:
        q = select(SecurityEvent).order_by(SecurityEvent.id)
        if event_type:
            q = q.where(SecurityEvent.event_type == event_type)
        return db.execute(q).scalars().all()


@contextlib.contextmanager
def _booking_scope(operation="pw1-probe"):
    """A booking transaction open in this context — what `forbid_external_io` guards."""
    from app.services import booking_authority as BA
    tok = BA._TXN_SCOPE.set(BA._TxnScope(operation))
    try:
        yield
    finally:
        BA._TXN_SCOPE.reset(tok)


def _resend_kwargs():
    return dict(api_key="re_test_not_a_key", from_email="n@example.invalid",
                to_email="team@example.invalid", reply_to_email="", lead_id=7, revision_id=11,
                buyer_name="Ana", buyer_phone="549", buyer_email="a@example.invalid",
                source="whatsapp", marca="Ford", modelo="Focus", anio="2017",
                tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
                address="Calle 1", seller_type="particular", seller_name="-",
                scheduled_date="2026-11-03", scheduled_time="10:00", precio_base="1",
                viaticos="0", precio_total="1")


class PW1B_OperationalConditions(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        # `record_operational_condition(None, …)` uses the application engine
        p = mock.patch.object(sys.modules["app.db"], "engine", self.engine)
        p.start()
        self.addCleanup(p.stop)

    def _event(self, **kw):
        base = dict(source="CONVERSATION_ENGINE", stage="NOTIFICATION", outcome="HELD",
                    reason="NOTIFICATION_NOT_CONFIGURED", command_key="flow:SECRETKEY",
                    attempt_ref="operator-notification:tr5", thread_id=3, revision_id=9,
                    thread_revision_id=5)
        base.update(kw)
        return base

    def test_pw1_b01_lost_evidence_becomes_a_typed_condition_with_no_secret(self):
        from app.services import booking_attempt_events as E
        with mock.patch.object(E, "insert_attempt_event", side_effect=RuntimeError("db down")):
            self.assertFalse(E.record_attempt_event(self.db, **self._event()))
        rows = _conditions(self.engine)
        self.assertEqual([r.event_type for r in rows], ["BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED"])
        self.assertEqual((rows[0].severity, rows[0].thread_id), ("HIGH", 3))
        self.assertEqual(rows[0].details["reason"], "NOTIFICATION_NOT_CONFIGURED")
        self.assertEqual(rows[0].details["error_class"], "RuntimeError")
        self.assertNotIn("SECRETKEY", repr(rows[0].details))
        self.assertNotIn("operator-notification", repr(rows[0].details))

    def test_pw1_b02_a_replayed_attempt_is_not_a_failure(self):
        from app.services.booking_attempt_events import record_attempt_event
        self.assertTrue(record_attempt_event(self.db, **self._event()))
        self.assertFalse(record_attempt_event(self.db, **self._event()))   # the same attempt
        self.assertEqual(_conditions(self.engine), [])

    def test_pw1_b03_a_failing_condition_write_is_bounded_and_logged_once(self):
        from app.services import booking_attempt_events as E
        from app.services import security_events as S
        with mock.patch.object(E, "insert_attempt_event", side_effect=RuntimeError("down")), \
                mock.patch.object(S, "create_security_event",
                                  side_effect=RuntimeError("also down")) as cse, \
                self.assertLogs("app.services.security_events", "ERROR") as logs:
            self.assertFalse(E.record_attempt_event(self.db, **self._event()))
        self.assertEqual(cse.call_count, 1, "the fallback must not retry or recurse")
        self.assertEqual(len([m for m in logs.output if "OPERATIONAL_CONDITION_NOT_RECORDED" in m]),
                         1)
        self.assertEqual(_conditions(self.engine), [])

    def test_pw1_b04_an_io_guard_trip_on_the_email_path_is_visible_and_sends_nothing(self):
        from app.services import resend_email as R
        for sender, kwargs in (
                (R.send_booking_notification, _resend_kwargs()),
                (R.send_human_review_notification, dict(
                    api_key="k", from_email="n@x.invalid", to_email="t@x.invalid", lead_id=1,
                    thread_id=4, wa_id="549", vehicle="v", zone_group="z", zone_detail="d",
                    reason="r"))):
            with self.subTest(sender=sender.__name__), _booking_scope(), \
                    mock.patch("urllib.request.urlopen",
                               side_effect=AssertionError("egress")) as egress:
                self.assertIs(sender(**kwargs), False)
                egress.assert_not_called()
        rows = _conditions(self.engine, "EXTERNAL_IO_INSIDE_BOOKING_TRANSACTION")
        self.assertEqual([r.details["sender"] for r in rows],
                         ["send_booking_notification", "send_human_review_notification"])
        self.assertEqual(rows[0].details["operation"], "pw1-probe")
        self.assertEqual(rows[1].thread_id, 4)

    def test_pw1_b05_an_ordinary_delivery_failure_is_not_a_guard_trip(self):
        from urllib import error
        from app.services import resend_email as R
        with mock.patch("urllib.request.urlopen", side_effect=error.URLError("unreachable")):
            self.assertIs(R.send_booking_notification(**_resend_kwargs()), False)
        self.assertEqual(_conditions(self.engine), [])


class PW1B_OperatorSurface(unittest.TestCase):
    """`/api/ops/booking-attention` lists the conditions behind the CRM session only."""

    KEY = "pw1-test-signing-key-not-a-secret-0123456789"

    def setUp(self):
        from app.routes import ops_dashboard
        self.engine = H.sqlite_engine()
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.app = FastAPI()
        self.app.include_router(ops_dashboard.router)
        for route in self.app.routes:
            for dep in getattr(getattr(route, "dependant", None), "dependencies", []):
                if getattr(dep.call, "__name__", "").endswith("get_db") or \
                        getattr(dep.call, "__name__", "") == "_get_db_gen":
                    self.app.dependency_overrides[dep.call] = lambda: self.session
        p = mock.patch.dict(os.environ, {"AUTH_SECRET_KEY": self.KEY, "SECRET_KEY": self.KEY})
        p.start()
        self.addCleanup(p.stop)
        from app.services.security_events import SecurityEventType, record_operational_condition
        record_operational_condition(self.session, SecurityEventType.BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED,
                                     source_component="probe", thread_id=12,
                                     details={"reason": "R", "stage": "NOTIFICATION",
                                              "unexpected": "dropped"})

    def test_pw1_b06_anonymous_gets_401_and_nothing_else(self):
        r = TestClient(self.app).get("/api/ops/booking-attention")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED", r.text)

    def test_pw1_b07_an_operator_sees_the_condition_beside_the_ledger_rows(self):
        from app.auth import SESSION_COOKIE, build_session, sign_session
        cookie = sign_session(build_session("operator@example.com"))
        r = TestClient(self.app, cookies={SESSION_COOKIE: cookie}).get(
            "/api/ops/booking-attention")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["count"], 0, "a condition is not a ledger row")
        [c] = body["operational_conditions"]
        self.assertEqual((c["condition"], c["thread_id"], c["next_action"]),
                         ("BOOKING_ATTEMPT_EVIDENCE_NOT_RECORDED", 12, "OPERATOR_REVIEW"))
        self.assertEqual(c["details"], {"reason": "R", "stage": "NOTIFICATION"})


# ══ PW1-C — CD-09: booking-owned thread-state fields ═══════════════════════════════════

class PW1C_ThreadStatePatch(unittest.TestCase):

    def setUp(self):
        from app.api import whatsapp as api
        self.engine = H.sqlite_engine()
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.app = FastAPI()
        self.app.include_router(api.router)
        for route in self.app.routes:
            for dep in getattr(getattr(route, "dependant", None), "dependencies", []):
                if getattr(dep.call, "__name__", "").endswith("get_db") or \
                        getattr(dep.call, "__name__", "") == "_get_db_gen":
                    self.app.dependency_overrides[dep.call] = lambda: self.session
        self.client = TestClient(self.app)
        self.w = H.seed(self.engine)                  # a thread with a live ISSUED offer
        self.prefix = next(r.path for r in self.app.routes
                           if getattr(r, "path", "").endswith("/thread/{thread_id}/state")
                           ).replace("{thread_id}", str(self.w.thread_id))

    def state(self):
        self.session.expire_all()
        st = self.session.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == self.w.thread_id)).scalar_one()
        return {c.name: getattr(st, c.name) for c in WhatsAppThreadState.__table__.columns
                if c.name != "updated_at"}

    def offers(self):
        self.session.expire_all()
        return [(o.id, o.status) for o in self.session.execute(
            select(WhatsAppBookingOffer).order_by(WhatsAppBookingOffer.id)).scalars()]

    def test_pw1_c01_booking_pointers_are_refused_whole_with_zero_mutation(self):
        before, offers = self.state(), self.offers()
        for body in ({"flow_booking_token": "forged"}, {"flow_booking_token": None},
                     {"current_focus_candidate_id": 999}, {"current_revision_id": 5},
                     {"current_revision_id": None, "customer_name": "Benito"},
                     {"last_stage": "QUOTED", "flow_booking_token": "x"}):
            with self.subTest(body=body):
                r = self.client.patch(self.prefix, json=body)
                self.assertEqual(r.status_code, 409, r.text)
                d = r.json()["detail"]
                self.assertEqual(d["code"], "THREAD_STATE_FIELD_AUTHORITY_OWNED")
                self.assertTrue(set(d["fields"]) <= {"flow_booking_token",
                                                     "current_focus_candidate_id",
                                                     "current_revision_id"})
                self.assertEqual(self.state(), before)
        self.assertEqual(self.offers(), offers)

    def test_pw1_c02_ordinary_fields_still_update(self):
        r = self.client.patch(self.prefix, json={"last_stage": "QUOTED", "needs_human": True,
                                                 "customer_name": "Benito"})
        self.assertEqual(r.status_code, 200, r.text)
        st = self.state()
        self.assertEqual((st["last_stage"], st["needs_human"], st["customer_name"]),
                         ("QUOTED", True, "Benito"))

    def test_pw1_c03_a_live_offers_negotiated_facts_are_refused_and_free_without_one(self):
        before = self.state()
        for body in ({"preferred_day": "2030-01-01"}, {"preferred_time": "18:00"},
                     {"home_zone_group": "SUR"}, {"home_zone_detail": "Quilmes"}):
            with self.subTest(body=body):
                r = self.client.patch(self.prefix, json=body)
                self.assertEqual(r.status_code, 409, r.text)
                self.assertEqual(r.json()["detail"]["code"],
                                 "THREAD_STATE_FIELD_BOUND_TO_LIVE_OFFER")
                self.assertEqual(self.state(), before)
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
        st = self.session.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == self.w.thread_id)).scalar_one()
        self.assertEqual(OfferLifecycle(self.session).withdraw(
            st, thread_id=self.w.thread_id, reason=WithdrawReason.HUMAN_HANDOFF), 1)
        self.session.commit()
        r = self.client.patch(self.prefix, json={"preferred_day": "2030-01-01"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.state()["preferred_day"], "2030-01-01")

    def _candidates_url(self, cid=None):
        base = self.prefix.rsplit("/state", 1)[0] + "/candidates"
        return base if cid is None else f"{base}/{cid}"

    def test_pw1_c04_focus_moves_outside_the_candidate_authority_are_refused(self):
        r = self.client.post(self._candidates_url(), json={"marca": "VW", "modelo": "Gol",
                                                           "status": "current_focus"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                         (409, "CANDIDATE_FOCUS_AUTHORITY_OWNED"))
        other = WhatsAppThreadCandidate(thread_id=self.w.thread_id, marca="VW", modelo="Gol",
                                        status="mentioned")
        self.session.add(other)
        self.session.commit()
        for cid, status in ((other.id, "current_focus"), (self.w.candidate_id, "mentioned")):
            with self.subTest(cid=cid):
                r = self.client.patch(self._candidates_url(cid), json={"status": status})
                self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                                 (409, "CANDIDATE_FOCUS_AUTHORITY_OWNED"))
        self.session.expire_all()
        self.assertEqual(self.session.get(WhatsAppThreadCandidate, other.id).status, "mentioned")

    def test_pw1_c05_the_offer_bound_vehicle_is_refused_and_others_stay_editable(self):
        before = self.session.get(WhatsAppThreadCandidate, self.w.candidate_id)
        before = (before.marca, before.modelo, before.tipo_vehiculo)
        r = self.client.patch(self._candidates_url(self.w.candidate_id),
                              json={"modelo": "Ranger", "tipo_vehiculo": "PICKUP_4X4"})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]),
                         (409, "CANDIDATE_BOUND_TO_LIVE_OFFER"))
        self.session.expire_all()
        c = self.session.get(WhatsAppThreadCandidate, self.w.candidate_id)
        self.assertEqual((c.marca, c.modelo, c.tipo_vehiculo), before)
        other = WhatsAppThreadCandidate(thread_id=self.w.thread_id, marca="VW", modelo="Gol",
                                        status="mentioned")
        self.session.add(other)
        self.session.commit()
        r = self.client.patch(self._candidates_url(other.id), json={"modelo": "Polo"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["modelo"], "Polo")


# ══ PW1-D — RV1-01: definite Meta rejection needs Graph's envelope ═════════════════════

ENVELOPE = '{"error": {"message": "(#131030) Recipient not allowed", "type": "OAuthException", ' \
           '"code": 131030, "fbtrace_id": "AbC"}}'


class PW1D_DefiniteRejection(unittest.TestCase):

    def test_pw1_d01_the_definiteness_matrix(self):
        from app.services.booking_acknowledgement import meta_rejection_is_definite
        from app.ui.whatsapp_ui import MetaSendError
        cases = [
            (MetaSendError(400, ENVELOPE), True),
            (MetaSendError(400, '{"error": {"code": 100, "message": "Invalid parameter"}}'), True),
            (MetaSendError(429, '{"error": {"code": 130429, "message": "Rate limit hit"}}'), True),
            (MetaSendError(403, ENVELOPE), True),
            (MetaSendError(400, ""), False),                                   # bare 400
            (MetaSendError(400, "{}"), False),
            (MetaSendError(400, "<html>Bad Request</html>"), False),           # malformed
            (MetaSendError(400, '{"error": "Bad Request"}'), False),
            (MetaSendError(400, '{"error": {"code": "100", "message": "x"}}'), False),
            (MetaSendError(400, '{"error": {"code": true, "message": "x"}}'), False),
            (MetaSendError(400, '{"error": {"code": 100, "message": "  "}}'), False),
            (MetaSendError(400, '[1, 2]'), False),
            (MetaSendError(408, ""), False),                                   # bare 408
            (MetaSendError(408, ENVELOPE), False),                             # 408, any body
            (MetaSendError(408, '{"error": {"message": "timeout"}}'), False),
            (MetaSendError(429, ""), False),
            (MetaSendError(500, ENVELOPE), False),
            (MetaSendError(502, ""), False),
            (MetaSendError(503, ENVELOPE), False),
            (MetaSendError(None, "timed out"), False),                         # no response
            (MetaSendError(200, "{}"), False),
        ]
        for exc, definite in cases:
            with self.subTest(status=exc.http_status, body=exc.raw_body[:40]):
                self.assertIs(meta_rejection_is_definite(exc), definite)

    def _send(self, effect):
        """The real sender, the real gate, a SQLite world; only the network edge stubbed."""
        from app.services.booking_acknowledgement import send_booking_acknowledgement
        from app.services.booking_authority import (acknowledgement_attempt_fingerprint,
                                                    flow_idempotency_key)
        engine = H.sqlite_engine()
        w = H.seed(engine)
        fp = acknowledgement_attempt_fingerprint(flow_idempotency_key(w.token), 1)
        calls = []

        def edge(*, to_wa_id, text):
            calls.append(text)
            if isinstance(effect, BaseException):
                raise effect
            return effect, 200
        with H.Meta(), mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text", edge), \
                Session(engine) as db:
            out = send_booking_acknowledgement(db, wa_id=w.wa_id, thread_id=w.thread_id,
                                               delivery_fingerprint=fp)
        return out, len(calls)

    def test_pw1_d02_the_sender_classifies_each_outcome(self):
        from app.services.booking_authority import AckDelivery
        from app.ui.whatsapp_ui import MetaSendError
        U, D, S = AckDelivery.UNCERTAIN, AckDelivery.DEFINITELY_NOT_SENT, AckDelivery.SENT_CONFIRMED
        cases = [
            (MetaSendError(400, ENVELOPE), D, "META_REJECTED_400"),
            (MetaSendError(400, ""), U, "AMBIGUOUS_META_400_UNPROVEN"),
            (MetaSendError(408, ""), U, "AMBIGUOUS_META_408_UNPROVEN"),
            (MetaSendError(408, ENVELOPE), U, "AMBIGUOUS_META_408_UNPROVEN"),
            (MetaSendError(429, '{"error": {"code": 130429, "message": "Rate limit"}}'), D,
             "META_REJECTED_429"),
            (MetaSendError(429, "Too Many Requests"), U, "AMBIGUOUS_META_429_UNPROVEN"),
            (MetaSendError(500, ENVELOPE), U, "AMBIGUOUS_META_500"),
            (MetaSendError(502, ""), U, "AMBIGUOUS_META_502"),
            (MetaSendError(503, ""), U, "AMBIGUOUS_META_503"),
            (MetaSendError(None, "connection reset by peer"), U, "AMBIGUOUS_NETWORK_OR_TIMEOUT"),
            (ValueError("malformed success body"), U, "AMBIGUOUS_TRANSPORT_EXCEPTION"),
            ("wamid.PW1.OK", S, None),
        ]
        for effect, delivery, error in cases:
            with self.subTest(effect=repr(effect)[:60]):
                out, calls = self._send(effect)
                self.assertEqual((out.delivery, out.error, calls), (delivery, error, 1))


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
