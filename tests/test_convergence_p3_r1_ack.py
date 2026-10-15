"""Convergence Phase 3-R1 — the acknowledgement delivery policy, and the two Data Exchange
detectors the independent Phase 3 review found missing, in the DEFAULT regression gate.

SQLite, no network, production functions only (`BookingAuthority`, the shared acknowledgement
sender, the outbound gate, `BookingFlowService.handle_confirm_booking`). The crash window and
the horizons are proven on PostgreSQL in `test_convergence_p3_r1_ack_pg.py`.

  * P3R-05 is proven on PostgreSQL (`test_convergence_p3_r1_ack_pg`, r1_12): on SQLite the
    harness's "isolated" sessions share one StaticPool connection, whose close rolls the
    request's dirty write back before the guard can see it;
  * P3R-06: the Data Exchange reads SMART_BOOKING_CE_ENABLED by identity — only the literal
    boolean True enables it;
  * the transport-boundary taxonomy: before the sender, or a documented Meta 4xx, is DEFINITE;
    anything else after the sender was entered is UNCERTAIN — including a failure of the
    gate's own bookkeeping while classifying it;
  * the ledger claim: two attempts at most, the second only after a definite non-delivery,
    and a SENDING row is never reclaimed;
  * a booking acknowledgement is never sent without its command-bound attempt identity.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, Revision, WhatsAppMessage,  # noqa: E402
                        WhatsAppOutboundDedup)
from app.services import booking_flow_service as bfs  # noqa: E402
from app.services.booking_acknowledgement import (  # noqa: E402
    BOOKING_FLOW_RECEIPT_TEXT, send_booking_acknowledgement)
from app.services.booking_authority import (ACK_MAX_ATTEMPTS, AckDelivery,  # noqa: E402
                                            BookingAuthority,
                                            acknowledgement_attempt_fingerprint,
                                            acknowledgement_delivery_identity,
                                            acknowledgement_delivery_state,
                                            flow_idempotency_key)


class _Calls:
    def __init__(self, owner, name):
        self.owner, self.name, self.calls = owner, name, 0
        self.real = getattr(owner, name)

    def __enter__(self):
        real = self.real

        def wrapper(*a, **k):
            self.calls += 1
            return real(*a, **k)
        self._p = mock.patch.object(self.owner, self.name, wrapper)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False


def _confirm(db, w, flag):
    with mock.patch("app.settings.get_settings", lambda: H.settings(flag=flag)):
        return bfs.BookingFlowService(db).handle_confirm_booking(w.token, {
            "date": w.day.isoformat(), "time": w.at.strftime("%H:%M"), "name": "Ana Fixture",
            "email": "ana@example.invalid", "inspection_address": "Calle Falsa 123"})


def _counts(engine, w):
    with Session(engine) as db:
        return (db.query(Revision).filter(Revision.lead_id == w.lead_id).count(),
                db.query(BookingCommandRecord).count())


# ══ P3R-05 / P3R-06: the Data Exchange detectors ════════════════════════════════════════

class R1_DataExchangeDetectors(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def test_r1u_02_dx_flag_identity_only_literal_true_enables_booking(self):
        """P3R-06. "true", 1, a MagicMock and a hostile object that is truthy AND equal to
        everything are all OFF on the Data Exchange transport; only `True` books."""
        class Hostile:
            def __bool__(self):
                return True

            def __eq__(self, other):
                return True

            __hash__ = object.__hash__
        for value in ("true", 1, mock.MagicMock(), Hostile()):
            with self.subTest(value=type(value).__name__):
                w = H.seed(self.engine)
                with H.Meta() as m, _Calls(BookingAuthority, "execute") as ex, \
                        Session(self.engine) as db:
                    with self.assertRaises(bfs.BookingSlotConflictError):
                        _confirm(db, w, value)
                self.assertEqual(ex.calls, 0)
                self.assertEqual(_counts(self.engine, w)[0], 0, "OFF booked a revision")
                self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])
        w = H.seed(self.engine)
        with H.Meta(), _Calls(BookingAuthority, "execute") as ex, Session(self.engine) as db:
            self.assertEqual(_confirm(db, w, True)["screen"], "SUCCESS")
        self.assertEqual(ex.calls, 1)


# ══ the transport boundary ═════════════════════════════════════════════════════════════

class R1_Boundary(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        self.w = H.seed(self.engine)
        self.fp = acknowledgement_attempt_fingerprint(flow_idempotency_key(self.w.token), 1)

    def send(self):
        with Session(self.engine) as db:
            return send_booking_acknowledgement(db, wa_id=self.w.wa_id, thread_id=self.w.thread_id,
                                                delivery_fingerprint=self.fp)

    def test_r1u_10_success_is_sent_confirmed(self):
        with H.Meta():
            out = self.send()
        self.assertEqual(out.delivery, AckDelivery.SENT_CONFIRMED)

    def test_r1u_11_a_documented_meta_4xx_is_definitely_not_sent(self):
        with H.Meta("4xx"):
            out = self.send()
        self.assertEqual((out.delivery, out.error), (AckDelivery.DEFINITELY_NOT_SENT,
                                                     "META_REJECTED_400"))

    def test_r1u_12_the_kill_switch_never_enters_the_transport_and_is_definite(self):
        with H.kill_switch():
            out = self.send()
        self.assertEqual(out.delivery, AckDelivery.DEFINITELY_NOT_SENT)

    def test_r1u_13_timeout_5xx_and_generic_exceptions_after_entry_are_uncertain(self):
        from app.ui.whatsapp_ui import MetaSendError
        for mode, exc in (("timeout", None), ("5xx", MetaSendError(503, "unavailable")),
                          ("2xx-no-id", MetaSendError(200, "{}")), ("raise", None)):
            # one contact per case: four sends to one recipient inside 60 s would meet the
            # gate's flood guard (a definite refusal) instead of the transport
            self.w = H.seed(self.engine)
            self.fp = acknowledgement_attempt_fingerprint(flow_idempotency_key(self.w.token), 1)
            with self.subTest(mode=mode), H.Meta(mode if exc is None else "ok") as m:
                with (mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text",
                                 mock.Mock(side_effect=exc)) if exc is not None
                      else mock.patch.dict({})):
                    out = self.send()
                self.assertEqual(out.delivery, AckDelivery.UNCERTAIN, out.error)

    def test_r1u_14_gate_bookkeeping_failing_after_entry_is_uncertain_not_definite(self):
        """The defect the forensic map found: a 5xx is caught, then `gate.mark_failed` raises
        (database blip) — the outer handler used to answer PRE_SEND_FAILURE, a DEFINITE
        failure, which would have admitted a retry after a send Meta may have accepted."""
        from app.services.outbound_safety_gate import OutboundSafetyGate
        from app.ui.whatsapp_ui import MetaSendError
        with H.Meta() as m, mock.patch.object(OutboundSafetyGate, "mark_failed",
                                              side_effect=RuntimeError("db blip")), \
                mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text",
                           side_effect=MetaSendError(502, "bad gateway")):
            out = self.send()
        self.assertEqual(out.delivery, AckDelivery.UNCERTAIN, out.error)

    def test_r1u_15_a_seen_delivery_identity_is_uncertain_never_definite(self):
        with H.Meta() as m:
            first = self.send()
            again = self.send()
        self.assertEqual((first.delivery, again.delivery),
                         (AckDelivery.SENT_CONFIRMED, AckDelivery.UNCERTAIN))
        self.assertEqual(len(m.sent), 1)

    def test_r1u_16_no_booking_acknowledgement_without_its_attempt_identity(self):
        with H.Meta() as m, Session(self.engine) as db:
            for bad in (None, "", "short", BOOKING_FLOW_RECEIPT_TEXT):
                with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                    send_booking_acknowledgement(db, wa_id=self.w.wa_id,
                                                 thread_id=self.w.thread_id,
                                                 delivery_fingerprint=bad)
        self.assertEqual(m.sent, [])

    def test_r1u_17_the_outbound_row_carries_the_attempt_identity_not_the_text(self):
        from app.services.outbound_safety_gate import _content_fingerprint
        with H.Meta():
            self.send()
        with Session(self.engine) as db:
            fps = db.execute(select(WhatsAppMessage.content_fingerprint).where(
                WhatsAppMessage.thread_id == self.w.thread_id,
                WhatsAppMessage.direction == "out")).scalars().all()
            dedup = db.execute(select(WhatsAppOutboundDedup.content_fingerprint)).scalars().all()
        self.assertEqual((fps, dedup), ([self.fp], [self.fp]))
        self.assertNotIn(_content_fingerprint(BOOKING_FLOW_RECEIPT_TEXT), fps + dedup)


# ══ the ledger claim policy ════════════════════════════════════════════════════════════

class R1_ClaimPolicy(unittest.TestCase):
    """`claim_acknowledgement` on a committed ledger row, driven state by state."""

    def setUp(self):
        self.engine = H.sqlite_engine()
        w = H.seed(self.engine)
        with H.kill_switch():
            H.run_turn(self.engine, H.flow_event(w))          # booked; attempt 1 refused
        self.key = flow_idempotency_key(w.token)

    def set(self, **values):
        with Session(self.engine) as db:
            db.execute(update(BookingCommandRecord).where(
                BookingCommandRecord.idempotency_key == self.key).values(**values))
            db.commit()

    def claim(self):
        with Session(self.engine) as db:
            return BookingAuthority(db).claim_acknowledgement(self.key)

    def row(self):
        with Session(self.engine) as db:
            r = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.idempotency_key == self.key)).scalar_one()
            return r.ack_status, r.ack_attempts, bool(r.ack_duplicate_risk), r.ack_last_error

    def test_r1u_20_the_bound_is_two(self):
        self.assertEqual(ACK_MAX_ATTEMPTS, 2)

    def test_r1u_21_one_retry_after_a_definite_failure_then_never(self):
        self.assertEqual(self.row()[:3], ("FAILED", 1, False))
        c = self.claim()
        self.assertEqual((c.attempt, c.delivery), (2, AckDelivery.RETRY_ADMITTED))
        self.assertEqual(c.delivery_fingerprint,
                         acknowledgement_attempt_fingerprint(self.key, 2))
        with Session(self.engine) as db:
            BookingAuthority(db).record_acknowledgement(c, sent=False, error="META_REJECTED_400")
        self.assertIsNone(self.claim(), "a third automatic attempt was admitted")
        self.assertTrue(self.row()[3].startswith("ACK_EXHAUSTED"))

    def test_r1u_22_an_uncertain_failure_is_never_claimed(self):
        for err, risk in (("AMBIGUOUS_NETWORK_OR_TIMEOUT", True),
                          ("AMBIGUOUS_TRANSPORT_EXCEPTION", False),
                          ("META_REJECTED_400", True)):
            with self.subTest(err=err, risk=risk):
                self.set(ack_status="FAILED", ack_attempts=1, ack_duplicate_risk=risk,
                         ack_last_error=err)
                self.assertIsNone(self.claim())

    def test_r1u_23_an_expired_lease_never_admits_a_send_and_is_held(self):
        from app.services.booking_authority import _app_now
        for expires in (_app_now() - timedelta(seconds=5), None):
            with self.subTest(expires=expires):
                self.set(ack_status="SENDING", ack_attempts=1, ack_duplicate_risk=False,
                         ack_last_error=None, ack_lease_expires_at=expires)
                self.assertIsNone(self.claim(), "a lost sender was reclaimed")
                self.assertEqual(self.row()[:3], ("FAILED", 1, True))
                self.assertIn("AMBIGUOUS", self.row()[3])
                self.assertIsNone(self.claim())

    def test_r1u_24_a_live_lease_is_never_claimed(self):
        from app.services.booking_authority import _app_now
        self.set(ack_status="SENDING", ack_attempts=1,
                 ack_lease_expires_at=_app_now() + timedelta(seconds=60))
        self.assertIsNone(self.claim())
        self.assertEqual(self.row()[0], "SENDING")

    def test_r1u_25_the_identity_is_the_commands_alone(self):
        other = flow_idempotency_key("another-token")
        self.assertNotEqual(acknowledgement_delivery_identity(self.key),
                            acknowledgement_delivery_identity(other))
        self.assertEqual(acknowledgement_delivery_identity(self.key),
                         acknowledgement_delivery_identity(str(self.key)))
        self.assertNotEqual(acknowledgement_attempt_fingerprint(self.key, 1),
                            acknowledgement_attempt_fingerprint(self.key, 2))
        for bad in (None, "", 1):
            with self.assertRaises(ValueError):
                acknowledgement_delivery_identity(bad)

    def test_r1u_26_the_state_table(self):
        S = acknowledgement_delivery_state
        D = AckDelivery
        cases = [
            (("SENT", 1, False, None, False), (D.SENT_CONFIRMED, D.SENT_CONFIRMED, "NONE_OWED")),
            (("PENDING", 0, False, None, False), (D.NOT_ATTEMPTED, D.NOT_ATTEMPTED, "PERMITTED")),
            (("SENDING", 1, False, None, True), (D.IN_FLIGHT, D.IN_FLIGHT, "IN_FLIGHT")),
            (("SENDING", 1, False, None, False), (D.HELD_OPERATOR, D.UNCERTAIN, "HELD")),
            (("FAILED", 1, True, "AMBIGUOUS_X", False), (D.HELD_OPERATOR, D.UNCERTAIN, "HELD")),
            (("FAILED", 1, False, "META_REJECTED_400", False),
             (D.DEFINITELY_NOT_SENT, D.DEFINITELY_NOT_SENT, "PERMITTED")),
            (("FAILED", 2, False, "ACK_EXHAUSTED:META_REJECTED_400", False),
             (D.HELD_OPERATOR, D.DEFINITELY_NOT_SENT, "HELD")),
        ]
        for (st, n, risk, err, live), expected in cases:
            with self.subTest(st=st, n=n, risk=risk, err=err, live=live):
                self.assertEqual(S(ack_status=st, ack_attempts=n, ack_duplicate_risk=risk,
                                   ack_last_error=err, lease_live=live), expected)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
