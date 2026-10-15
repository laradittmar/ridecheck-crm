"""Convergence Phase 3-R1 — the booking acknowledgement across the crash window, ON REAL
POSTGRESQL, through the production Conversation Engine route and the production Data Exchange
service (both via `acknowledge_committed_booking` → `OutboundSafetyGate` → the transport).

The independent Phase 3 review (P3R-01, P3R-02) proved that after a crash between the
transport accepting the acknowledgement and `record_acknowledgement`, a later re-entry sent
the same acknowledgement again: Data Exchange once the 120 s lease and the 10-minute text
dedup had passed, the Conversation Engine as soon as the lease expired and a fresh inbound
WAMID arrived. The owner's delivery policy (Phase3-R1 prompt) is binding here:

* at most two automatic attempts per booking command — the initial one plus ONE retry, and
  the retry only after a DEFINITE non-delivery (transport never entered, or Meta rejected it
  without accepting it);
* an UNCERTAIN attempt (the transport may have accepted it) is never retried automatically;
  it is held and listed for an operator;
* a re-entry at any later time, by either transport, with any inbound WAMID, cannot reset
  that allowance;
* two DIFFERENT booking commands for the same contact are never deduplicated against each
  other.

Every test counts BOTH the sender invocations at the network edge (`H.Meta.sent`) AND the
persisted `whatsapp_messages` rows with `status='sent'` carrying the owner text.

Time: instead of sleeping 120 s / 10 min, a test AGES DURABLE TIMESTAMPS in this disposable
database (`booking_commands.ack_lease_expires_at`, `whatsapp_outbound_dedup.created_at`,
`whatsapp_messages.created_at`). No production constant is patched and no production claim,
gate or sender code is bypassed. The independent review should repeat at least the lease and
dedup horizons with real wall-clock time.

  CONV_P3_PG_URL   the Phase 3 disposable PostgreSQL 16 database (see
                   `test_convergence_p3_race_pg`). Skips cleanly without it, and says so.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import time
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_convergence_p3_race_pg import PG_URL, Crash, _Race  # noqa: E402

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, WhatsAppMessage,  # noqa: E402
                        WhatsAppOutboundDedup, WhatsAppThreadState)
from app.services.booking_authority import BookingAuthority, flow_idempotency_key  # noqa: E402


@unittest.skipUnless(PG_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class _Ack(_Race):

    # ── evidence ─────────────────────────────────────────────────────────────

    def sent_rows(self, w):
        """Customer-visible acknowledgements: persisted outbound rows, status 'sent', with
        the owner text, on this thread."""
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppMessage.id, WhatsAppMessage.content_fingerprint)
                              .where(WhatsAppMessage.thread_id == w.thread_id,
                                     WhatsAppMessage.direction == "out",
                                     WhatsAppMessage.status == "sent",
                                     WhatsAppMessage.text == H.OWNER_TEXT)
                              .order_by(WhatsAppMessage.id)).all()

    def ack_rows(self, w):
        """Every outbound row the acknowledgement produced (sent, failed, blocked, pending)."""
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppMessage.status, WhatsAppMessage.content_fingerprint)
                              .where(WhatsAppMessage.thread_id == w.thread_id,
                                     WhatsAppMessage.direction == "out",
                                     WhatsAppMessage.text == H.OWNER_TEXT)
                              .order_by(WhatsAppMessage.id)).all()

    def ledger(self, key):
        with Session(self.engine) as db:
            r = db.execute(select(BookingCommandRecord).where(
                BookingCommandRecord.idempotency_key == key)).scalar_one()
            return (r.ack_status, r.ack_attempts, bool(r.ack_duplicate_risk), r.ack_last_error)

    def age(self, w, *, lease=True, dedup_minutes=11):
        """Move this world's DURABLE timestamps into the past: the acknowledgement lease
        (expired 5 s ago by the database clock), the outbound dedup rows and outbound
        messages (`dedup_minutes` earlier — past the 10-minute window and the 60 s flood
        window). Data only; no production constant or code is touched."""
        with self.engine.begin() as c:
            if lease:
                c.execute(text(
                    "UPDATE booking_commands SET ack_lease_expires_at = clock_timestamp() "
                    "- interval '5 seconds' WHERE thread_id = :t AND ack_status = 'SENDING'"),
                    {"t": w.thread_id})
            c.execute(text(
                "UPDATE whatsapp_outbound_dedup SET created_at = created_at - make_interval("
                "mins => :m) WHERE thread_id = :t"), {"t": w.thread_id, "m": dedup_minutes})
            c.execute(text(
                "UPDATE whatsapp_messages SET created_at = created_at - make_interval("
                "mins => :m), timestamp = timestamp - make_interval(mins => :m) "
                "WHERE thread_id = :t AND direction = 'out'"),
                {"t": w.thread_id, "m": dedup_minutes})

    def crash_after_transport(self):
        """Process death AFTER the gate recorded the attempt, AFTER the transport accepted it
        and returned a WAMID, AFTER `gate.mark_sent` — and BEFORE the ledger records it."""
        return mock.patch.object(BookingAuthority, "record_acknowledgement",
                                 side_effect=Crash("died before record_acknowledgement"))

    def assert_held(self, key, *, attempts):
        status, n, risk, err = self.ledger(key)
        self.assertEqual((status, n), ("FAILED", attempts), (status, n, risk, err))
        with Session(self.engine) as db:
            rows = [r for r in BookingAuthority(db).ledger_rows_needing_attention()
                    if r["idempotency_key"] == key]
        self.assertEqual(len(rows), 1, "a held acknowledgement must be operator-visible")
        return rows[0]


# ══ 1, 2, 3: the crash window, per transport, then both, at every horizon ═══════════════

class R1_CrashWindow(_Ack):

    def test_r1_01_dx_crash_after_transport_never_resends_at_any_horizon(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with self.crash_after_transport(), H.Meta() as m1:
            with self.assertRaises(Crash):
                H.dx_confirm(self.engine, w)
        self.assertEqual(len(m1.sent), 1)
        self.assertEqual(len(self.sent_rows(w)), 1)
        self.assertEqual(self.ledger(key)[:2], ("SENDING", 1))
        horizons = []
        with H.Meta() as m2:
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")   # lease live
            horizons.append(len(m2.sent))
            self.age(w, dedup_minutes=0)                                   # past the lease
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
            horizons.append(len(m2.sent))
            self.age(w, lease=False)                              # past the 10-minute dedup
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
            horizons.append(len(m2.sent))
        self.assertEqual(horizons, [0, 0, 0], "a re-entry reached the sender again")
        self.assertEqual(len(m1.sent) + len(m2.sent), 1, "sender invocations")
        self.assertEqual(len(self.sent_rows(w)), 1, "customer-visible acknowledgements")
        row = self.assert_held(key, attempts=1)
        self.assertTrue(row["ack_duplicate_risk"])

    def test_r1_02_ce_crash_then_fresh_inbound_wamid_never_resends(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with self.crash_after_transport(), H.Meta() as m1:
            with self.assertRaises(Crash):
                self.ce(w)()
        self.assertEqual((len(m1.sent), len(self.sent_rows(w))), (1, 1))
        with H.Meta() as m2:
            self.age(w, dedup_minutes=0)          # lease gone; the 10-minute window is OPEN
            out = self.ce(w, wamid=w.wamid + ".fresh1")()
            self.assertEqual(out.action, "booking_replayed")
            self.age(w, lease=False)              # and past the window too
            self.assertEqual(self.ce(w, wamid=w.wamid + ".fresh2")().action, "booking_replayed")
        self.assertEqual(len(m2.sent), 0, "a fresh inbound WAMID reached the sender again")
        self.assertEqual(len(self.sent_rows(w)), 1, "customer-visible acknowledgements")
        self.assert_held(key, attempts=1)

    def test_r1_03_repeated_reentry_on_both_transports_never_exceeds_one(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with self.crash_after_transport(), H.Meta() as m1:
            with self.assertRaises(Crash):
                self.ce(w)()
        with H.Meta() as m2:
            for i in range(4):
                self.age(w)
                self.assertEqual(self.ce(w, wamid=f"{w.wamid}.re{i}")().action,
                                 "booking_replayed")
                self.age(w)
                self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
        self.assertEqual(len(m1.sent) + len(m2.sent), 1, "sender invocations")
        self.assertEqual(len(self.sent_rows(w)), 1, "customer-visible acknowledgements")
        self.assert_held(key, attempts=1)


# ══ 4: two different commands are never one acknowledgement ════════════════════════════

class R1_DistinctCommands(_Ack):

    def _second_offer(self, w, *, mirror: bool):
        """A second, legitimate booking command for the SAME contact and thread, minutes after
        the first: a new ISSUED offer at another slot (production `OfferLifecycle.issue`)."""
        from app.services.booking_flow_service import make_booking_token
        from app.services.offer_lifecycle import OfferLifecycle
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            token2 = make_booking_token(w.thread_id) + "-2"
            OfferLifecycle(db).issue(st, thread_id=w.thread_id, token=token2, cycle_key=0,
                                     candidate_id=w.candidate_id, tipo_vehiculo="Auto",
                                     zone_group="Norte", zone_detail="Benavidez")
            if mirror:                                   # CE admits only the mirrored token
                st.flow_booking_token = token2
                st.preferred_time = "11:30"
                st.last_stage = "SCHEDULING"
                st.needs_human = False                   # a person released the thread
            db.commit()
        return H.World(w.thread_id, w.lead_id, w.contact_id, w.wa_id, token2,
                       w.wamid + ".second", w.candidate_id, w.day, time(11, 30))

    def test_r1_04a_dx_two_bookings_same_contact_inside_ten_minutes_both_acknowledge(self):
        w = H.seed(self.engine)
        with H.Meta() as m:
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
            w2 = self._second_offer(w, mirror=False)
            self.assertEqual(H.dx_confirm(self.engine, w2)["screen"], "SUCCESS")
        self.assertEqual(len(m.sent), 2, "sender invocations")
        rows = self.sent_rows(w)
        self.assertEqual(len(rows), 2, "each command's acknowledgement is customer-visible")
        self.assertNotEqual(rows[0][1], rows[1][1], "two commands, one delivery identity")
        for tok in (w.token, w2.token):
            self.assertEqual(self.ledger(flow_idempotency_key(tok))[:2], ("SENT", 1))

    def test_r1_04b_ce_two_bookings_same_contact_inside_ten_minutes_both_acknowledge(self):
        w = H.seed(self.engine)
        with H.Meta() as m:
            self.assertEqual(self.ce(w)().action, "booking_created")
            w2 = self._second_offer(w, mirror=True)
            self.assertEqual(self.ce(w2, wamid=w2.wamid)().action, "booking_created")
        self.assertEqual(len(m.sent), 2, "sender invocations")
        self.assertEqual(len(self.sent_rows(w)), 2, "customer-visible acknowledgements")
        for tok in (w.token, w2.token):
            self.assertEqual(self.ledger(flow_idempotency_key(tok))[:2], ("SENT", 1))


# ══ 5–8: the owner's attempt policy ════════════════════════════════════════════════════

class R1_AttemptPolicy(_Ack):

    def test_r1_05a_ce_definite_gate_refusal_then_one_retry_on_a_fresh_wamid(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.kill_switch():                     # the gate refuses: transport never entered
            self.ce(w)()
        self.assertEqual(self.ledger(key)[:3], ("FAILED", 1, False))
        self.assertEqual(self.sent_rows(w), [])
        with H.Meta() as m:
            self.age(w, lease=False)
            self.ce(w, wamid=w.wamid + ".retry")()
            self.age(w, lease=False)
            self.ce(w, wamid=w.wamid + ".after")()
            H.dx_confirm(self.engine, w)
        self.assertEqual(len(m.sent), 1, "exactly one retry reached the sender")
        self.assertEqual(len(self.sent_rows(w)), 1)
        self.assertEqual(self.ledger(key)[:2], ("SENT", 2))

    def test_r1_05b_dx_definite_meta_rejection_then_one_retry(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.Meta("4xx") as m1:
            self.assertEqual(H.dx_confirm(self.engine, w)["screen"], "SUCCESS")
        self.assertEqual(len(m1.sent), 1)
        self.assertEqual(self.ledger(key)[:3], ("FAILED", 1, False))
        with H.Meta() as m2:
            self.age(w, lease=False)                    # inside or outside 10 min: the same
            H.dx_confirm(self.engine, w)
            H.dx_confirm(self.engine, w)
        self.assertEqual(len(m2.sent), 1, "exactly one retry reached the sender")
        self.assertEqual(len(self.sent_rows(w)), 1)
        self.assertEqual(self.ledger(key)[:2], ("SENT", 2))

    def test_r1_06_definite_then_definite_is_held_and_never_tried_a_third_time(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.Meta("4xx") as m:
            H.dx_confirm(self.engine, w)
            self.age(w)
            self.ce(w, wamid=w.wamid + ".r1")()
            for i in range(3):
                self.age(w)
                self.ce(w, wamid=f"{w.wamid}.r{i + 2}")()
                H.dx_confirm(self.engine, w)
        self.assertEqual(len(m.sent), 2, "a third automatic attempt was made")
        self.assertEqual(self.sent_rows(w), [])
        row = self.assert_held(key, attempts=2)
        self.assertFalse(row["ack_duplicate_risk"], "both failures were definite")

    def test_r1_07a_ce_uncertain_first_attempt_is_held_without_retry(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.Meta("timeout") as m1:
            self.ce(w)()
        with H.Meta() as m2:
            for i in range(3):
                self.age(w)
                self.ce(w, wamid=f"{w.wamid}.u{i}")()
                H.dx_confirm(self.engine, w)
        self.assertEqual((len(m1.sent), len(m2.sent)), (1, 0), "an uncertain send was retried")
        self.assertEqual(self.sent_rows(w), [])
        row = self.assert_held(key, attempts=1)
        self.assertTrue(row["ack_duplicate_risk"])

    def test_r1_07b_dx_generic_post_boundary_exception_is_uncertain_not_definite(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.Meta("raise") as m1:
            H.dx_confirm(self.engine, w)
        with H.Meta() as m2:
            self.age(w)
            H.dx_confirm(self.engine, w)
            self.ce(w, wamid=w.wamid + ".x")()
        self.assertEqual((len(m1.sent), len(m2.sent)), (1, 0), "an uncertain send was retried")
        self.assert_held(key, attempts=1)

    def test_r1_08_an_admitted_retry_that_becomes_uncertain_is_held(self):
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.Meta("4xx") as m1:
            self.ce(w)()
        with H.Meta("timeout") as m2:
            self.age(w)
            self.ce(w, wamid=w.wamid + ".retry")()
        with H.Meta() as m3:
            for i in range(3):
                self.age(w)
                H.dx_confirm(self.engine, w)
                self.ce(w, wamid=f"{w.wamid}.z{i}")()
        self.assertEqual((len(m1.sent), len(m2.sent), len(m3.sent)), (1, 1, 0))
        self.assertEqual(self.sent_rows(w), [])
        row = self.assert_held(key, attempts=2)
        self.assertTrue(row["ack_duplicate_risk"])


# ══ the identity itself ═══════════════════════════════════════════════════════════════

class R1_Identity(_Ack):

    def test_r1_09_the_identity_is_the_commands_on_both_transports_and_survives_a_new_wamid(self):
        from app.services.booking_authority import (acknowledgement_attempt_fingerprint,
                                                    acknowledgement_delivery_identity)
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with H.kill_switch():
            self.ce(w)()                                          # attempt 1, CE, refused
        with H.Meta():
            self.age(w, lease=False)
            H.dx_confirm(self.engine, w)                          # attempt 2, DX, sent
        ident = acknowledgement_delivery_identity(key)
        self.assertNotIn(w.token, ident)
        self.assertEqual(ident, acknowledgement_delivery_identity(key), "not stable")
        expected = [acknowledgement_attempt_fingerprint(key, 1),
                    acknowledgement_attempt_fingerprint(key, 2)]
        self.assertEqual([fp for _s, fp in self.ack_rows(w)], expected,
                         "the outbound rows are not bound to the command and attempt")
        with Session(self.engine) as db:
            dedup = db.execute(select(WhatsAppOutboundDedup.content_fingerprint).where(
                WhatsAppOutboundDedup.thread_id == w.thread_id)).scalars().all()
        self.assertEqual(dedup, expected[1:], "the admitted attempt's identity is not durable")

    def test_r1_10_the_gate_never_admits_one_delivery_identity_twice_whatever_the_inbound(self):
        from datetime import datetime, timedelta, timezone
        from app.services.booking_authority import acknowledgement_attempt_fingerprint
        from app.services.outbound_path_registry import OutboundPathId
        from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
        w = H.seed(self.engine)
        fp = acknowledgement_attempt_fingerprint(flow_idempotency_key(w.token), 1)
        now = datetime.now(timezone.utc)
        with H.Meta(), Session(self.engine) as db:
            gate = OutboundSafetyGate(db)
            first = gate.attempt(wa_id=w.wa_id, thread_id=w.thread_id, text=H.OWNER_TEXT,
                                 now=now, path_id=OutboundPathId.BOOKING_FLOW.value,
                                 causal_inbound_wa_message_id="wamid.A", delivery_identity=fp)
            later = gate.attempt(wa_id=w.wa_id, thread_id=w.thread_id, text=H.OWNER_TEXT,
                                 now=now + timedelta(days=2),
                                 path_id=OutboundPathId.BOOKING_FLOW.value,
                                 causal_inbound_wa_message_id="wamid.B", delivery_identity=fp)
        self.assertEqual((first.outcome, later.outcome),
                         (GateOutcome.ALLOWED, GateOutcome.BLOCKED_DUPLICATE))


# ══ P3R-05 on PostgreSQL: the Data Exchange clean-transaction guard ═════════════════════

class R1_DataExchangeGuard(_Ack):

    def test_r1_12_a_dirty_dx_request_session_never_reaches_the_authority(self):
        """A lead write already FLUSHED in the Data Exchange request's own session (it now
        holds a transaction id): delegating would let the authority's transaction wait on —
        or race — the request's own uncommitted write. The guard refuses first."""
        from app.models import Lead, Revision
        from app.services import booking_flow_service as bfs
        w = H.seed(self.engine)
        calls = []
        real = BookingAuthority.execute

        def counted(auth, *a, **k):
            calls.append(1)
            return real(auth, *a, **k)
        with H.Meta() as m, mock.patch.object(BookingAuthority, "execute", counted), \
                mock.patch("app.settings.get_settings", lambda: H.settings(flag=True)), \
                Session(self.engine) as db:
            db.get(Lead, w.lead_id).nombre = "dirty-before-delegation"
            db.flush()
            with self.assertRaises(bfs.BookingSlotConflictError) as refused:
                bfs.BookingFlowService(db).handle_confirm_booking(w.token, {
                    "date": w.day.isoformat(), "time": w.at.strftime("%H:%M"),
                    "name": "Ana Fixture", "email": "ana@example.invalid",
                    "inspection_address": "Calle Falsa 123"})
            db.rollback()
        self.assertEqual(calls, [], "the authority was entered from a dirty request session")
        self.assertEqual(refused.exception.refreshed_data["data"]["slot_conflict_message"],
                         H.HANDOFF_TEXT)
        with Session(self.engine) as db:
            self.assertEqual(db.query(Revision).filter(Revision.lead_id == w.lead_id).count(), 0)
            self.assertEqual(db.query(BookingCommandRecord).count(), 0)
        self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent])

# ══ operator evidence ══════════════════════════════════════════════════════════════════

class R1_OperatorEvidence(_Ack):

    def test_r1_11_the_operator_sees_a_committed_booking_and_a_held_uncertain_ack(self):
        from app.routes.ops_dashboard import booking_attention
        from app.services.booking_authority import acknowledgement_delivery_identity
        w = H.seed(self.engine)
        key = flow_idempotency_key(w.token)
        with self.crash_after_transport(), H.Meta():
            with self.assertRaises(Crash):
                H.dx_confirm(self.engine, w)
        self.age(w)
        with Session(self.engine) as db:
            row = next(r for r in booking_attention(db) if r["thread_id"] == w.thread_id)
        self.assertEqual(row["booking"], "COMMITTED")
        self.assertEqual(row["ack_delivery"], "UNCERTAIN")
        self.assertEqual(row["automatic_resend"], "HELD")
        self.assertEqual(row["ack_attempts"], 1)
        self.assertEqual(row["ack_identity"], acknowledgement_delivery_identity(key)[:20])
        self.assertEqual(row["recovery"], "OPERATOR_REVIEW")
        self.assertNotIn(w.token, repr(row))
        with Session(self.engine) as db:                         # the read wrote nothing
            self.assertEqual(db.execute(select(BookingCommandRecord.ack_status)).scalar_one(),
                             "SENDING")


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
