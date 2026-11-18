"""WILD1-C-R4-R2 — the persisted-booking lead projection is coherent.

Owner ruling (2026-11-17): a successfully persisted Booking Flow submission wins over an
earlier PERDIDO, and its projection is `flag=ACEPTADO`, `motivo_perdida=NULL`, written by
`BookingAuthority` inside the booking's own transaction — never before persistence, never on a
refused / conflicting / failed / rolled-back booking, never twice.

Real routes: the Conversation Engine Flow-response turn (`run_turn`) and the Data Exchange
confirm (`BookingFlowService.handle_confirm_booking`). Operator actions are the real kanban
function. SQLite by default; the same file runs on PostgreSQL with `R6_PG_URL`.
"""
from __future__ import annotations

import unittest
from datetime import datetime, time, timedelta, timezone
from unittest import mock

import _wild1c_harness as W

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (BookingCommandRecord, Lead, ThreadRevision, WhatsAppBookingOffer,
                        WhatsAppThreadEvidence)
from app.services import booking_authority as BA

H = W.H

def _free_slots():
    """Unique (day, time) slots for SUCCESSFUL bookings, so none contends for the one launch
    professional — on SQLite (fresh database per class) and on a shared PostgreSQL database.
    Inside the 14-day booking horizon and business hours; skips Sundays and the days the H1 and
    R4-R1 booking tests use (tomorrow, +2, the SB2B Tuesday and its Thursday)."""
    from datetime import date
    today = date.today()
    avoid = {today + timedelta(days=1), today + timedelta(days=2), H.slot_day(),
             H.slot_day() + timedelta(days=2)}
    first = {0: time(14, 0), 1: time(10, 0), 2: time(10, 0), 3: time(10, 0), 4: time(10, 0),
             5: time(10, 0)}
    long_days = {2, 4}                       # Wednesday and Friday close at 18:00
    out = []
    for k in range(3, 14):
        d = today + timedelta(days=k)
        if d.weekday() == 6 or d in avoid:
            continue
        out.append((d, first[d.weekday()]))
        if d.weekday() in long_days:
            out.append((d, time(15, 0)))
    return out


_SLOTS = iter(_free_slots())
#: worlds that must NOT book share this slot (nothing ever occupies it in a passing run)
_NON_BOOKING_SLOT = (H.slot_day(), time(13, 0))


def op_perdido(eng, lead_id, motivo):
    from app.ui import kanban_actions as KA
    with Session(eng) as db:
        KA.ui_perdido(lead_id=lead_id, motivo_perdida=motivo, db=db)


def engine_decline(eng, w, motivo="PRECIO"):
    """The engine's own decline, as `_handle_quote_decline` writes it: PERDIDO + motivo + one
    append-only DECLINED quote_decision row (producer ENGINE)."""
    with Session(eng) as db:
        lead = db.get(Lead, w.lead_id)
        lead.flag, lead.motivo_perdida = "PERDIDO", motivo
        db.add(WhatsAppThreadEvidence(
            thread_id=w.thread_id, cycle_key=0, claim_type="quote_decision", value="DECLINED",
            evidence_class="DECISION", producer="ENGINE", producer_version="wild1-c/1",
            status="CONFIRMED", outcome="ACCEPT", reason=motivo, source_message_ids=[],
            claim_id=f"r4r2-declined-{w.thread_id}", created_at=datetime.now(timezone.utc)))
        db.commit()


class _Booking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        cls.week = H.slot_day()                  # a Tuesday 7–13 days ahead

    def world(self, *, books=True, slot=None, perdido=None, decline=None):
        day, at = slot or (next(_SLOTS) if books else _NON_BOOKING_SLOT)
        w = H.seed(self.eng, day=day, at=at)
        w_slot = (day, at)
        self._last_slot = w_slot
        if perdido:
            op_perdido(self.eng, w.lead_id, perdido)
        if decline:
            engine_decline(self.eng, w, decline)
        return w

    def state(self, w):
        with Session(self.eng) as db:
            lead = db.get(Lead, w.lead_id)
            booked = db.execute(select(func.count()).select_from(ThreadRevision).where(
                ThreadRevision.thread_id == w.thread_id,
                ThreadRevision.status == "booked")).scalar_one()
            ledger = db.execute(select(func.count()).select_from(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
            offers = [o.status for o in db.execute(select(WhatsAppBookingOffer).where(
                WhatsAppBookingOffer.thread_id == w.thread_id)).scalars()]
            decisions = [(r.value, r.producer) for r in db.execute(
                select(WhatsAppThreadEvidence).where(
                    WhatsAppThreadEvidence.thread_id == w.thread_id,
                    WhatsAppThreadEvidence.claim_type == "quote_decision")
                .order_by(WhatsAppThreadEvidence.id)).scalars()]
            return dict(flag=lead.flag, motivo=lead.motivo_perdida, booked=booked,
                        ledger=ledger, offers=offers, decisions=decisions)

    def ce_book(self, w, **kw):
        """The CE Flow-response route. Records, AT THE MOMENT THE RECEIPT IS TRANSPORTED, what
        the database (a separate session) holds: the receipt must follow persistence."""
        at_send = []
        with H.Meta() as m:
            original = m._send

            def observed(*, to_wa_id, text):
                at_send.append(self.state(w))
                return original(to_wa_id=to_wa_id, text=text)
            with mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text", observed):
                out, _ = H.run_turn(self.eng, H.flow_event(w, **kw), cfg=H.settings())
        return out, [t for _w, t in m.sent], at_send

    def assertCoherentBooking(self, s):
        self.assertEqual((s["flag"], s["motivo"]), ("ACEPTADO", None), s)
        self.assertEqual((s["booked"], s["ledger"]), (1, 1), s)
        self.assertIn("ACCEPTED", s["offers"])

    def assertUntouched(self, s, flag, motivo):
        self.assertEqual((s["flag"], s["motivo"], s["booked"]), (flag, motivo, 0), s)


# ══ A–D. successful bookings, both transports ═════════════════════════════════════════

class SuccessfulProjection(_Booking):

    def check_ce(self, w, before_flag, before_motivo):
        s0 = self.state(w)
        self.assertEqual((s0["flag"], s0["motivo"]), (before_flag, before_motivo))
        out, sent, at_send = self.ce_book(w)
        self.assertEqual(out.action, "booking_created", out)
        s = self.state(w)
        self.assertCoherentBooking(s)
        self.assertEqual(sent, [H.OWNER_TEXT])                       # one receipt
        self.assertEqual(len(at_send), 1)
        self.assertCoherentBooking(at_send[0])                       # sent AFTER persistence
        return s0, s

    def test_a_operator_perdido_precio_cleared_only_inside_the_booking_transaction(self):
        """Also: at the projection point the booking rows are already written (same
        transaction); before it, the lead still carries its loss reason."""
        w = self.world(perdido="PRECIO")
        seen = []
        original = BA.BookingAuthority._apply_canonical_state

        def spy(self_, cmd, plan, revision, tr_id, state, offer_id):
            lead = self_.db.get(Lead, revision.lead_id)
            seen.append(("before", revision.id is not None, tr_id is not None,
                         lead.flag, lead.motivo_perdida))
            out = original(self_, cmd, plan, revision, tr_id, state, offer_id)
            seen.append(("after", lead.flag, lead.motivo_perdida))
            return out
        with mock.patch.object(BA.BookingAuthority, "_apply_canonical_state", spy):
            self.check_ce(w, "PERDIDO", "PRECIO")
        self.assertEqual(seen, [("before", True, True, "PERDIDO", "PRECIO"),
                                ("after", "ACEPTADO", None)])

    def test_b_operator_perdido_otro(self):
        self.check_ce(self.world(perdido="OTRO"), "PERDIDO", "OTRO")

    def test_c_engine_declined_perdido_history_stays_append_only(self):
        w = self.world(decline="PRECIO")
        s0, s = self.check_ce(w, "PERDIDO", "PRECIO")
        self.assertEqual(s["decisions"], s0["decisions"])           # nothing rewritten/removed
        self.assertEqual(s["decisions"], [("DECLINED", "ENGINE")])

    def test_d_normal_booking_is_unchanged(self):
        self.check_ce(self.world(), "ACEPTADO", None)

    def test_data_exchange_projects_the_same_state(self):
        """Mutant 5: the CE route and the Data Exchange confirm produce ONE projection."""
        dx = self.world(perdido="PRECIO")
        self.assertIsNotNone(H.dx_confirm(self.eng, dx))
        s_dx = self.state(dx)
        self.assertCoherentBooking(s_dx)
        ce = self.world(perdido="PRECIO")
        self.ce_book(ce)
        s_ce = self.state(ce)
        self.assertCoherentBooking(s_ce)
        with Session(self.eng) as db:
            a, b = db.get(Lead, ce.lead_id), db.get(Lead, dx.lead_id)
            self.assertEqual((a.flag, a.motivo_perdida, a.estado, a.necesita_humano),
                             (b.flag, b.motivo_perdida, b.estado, b.necesita_humano))


# ══ E. failure paths preserve PERDIDO + motivo and create nothing ═════════════════════

class FailurePaths(_Booking):

    def test_invalid_token(self):
        w = self.world(books=False, perdido="PRECIO")
        bogus = "rcbk-not-issued-r4r2"
        out, _s, _ = self.ce_book(w, token=bogus, data=H.flow_payload(
            bogus, nombre_apellido="X Y", email="x@example.invalid", direccion="Z 1"))
        self.assertNotEqual(out.action, "booking_created")
        self.assertUntouched(self.state(w), "PERDIDO", "PRECIO")

    def test_expired_offer(self):
        w = self.world(books=False, perdido="PRECIO")
        with Session(self.eng) as db:
            db.execute(update(WhatsAppBookingOffer).where(
                WhatsAppBookingOffer.thread_id == w.thread_id).values(
                expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
            db.commit()
        out, _s, _ = self.ce_book(w)
        self.assertNotEqual(out.action, "booking_created")
        self.assertUntouched(self.state(w), "PERDIDO", "PRECIO")

    def test_withdrawn_offer(self):
        from app.models import WhatsAppThreadState
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
        w = self.world(books=False, perdido="OTRO")
        with Session(self.eng) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            OfferLifecycle(db).withdraw(st, thread_id=w.thread_id,
                                        reason=WithdrawReason.HUMAN_HANDOFF,
                                        clear_scheduling=False)
            db.commit()
        out, _s, _ = self.ce_book(w)
        self.assertNotEqual(out.action, "booking_created")
        self.assertUntouched(self.state(w), "PERDIDO", "OTRO")

    def test_occupied_slot(self):
        first = self.world()
        self.assertEqual(self.ce_book(first)[0].action, "booking_created")
        w = self.world(slot=self._last_slot, perdido="PRECIO")       # the same slot
        out, _s, _ = self.ce_book(w)
        self.assertNotEqual(out.action, "booking_created")
        self.assertUntouched(self.state(w), "PERDIDO", "PRECIO")
        dx = self.world(slot=(first.day, first.at), perdido="OTRO")  # the DX route, same slot
        try:
            H.dx_confirm(self.eng, dx)
        except Exception:
            pass                                                     # a typed refusal is fine
        self.assertUntouched(self.state(dx), "PERDIDO", "OTRO")

    def test_forced_persistence_exception_inside_the_transaction(self):
        """The ledger write (after the projection, before the commit) raises."""
        w = self.world(books=False, perdido="PRECIO")
        with mock.patch.object(BA.BookingAuthority, "_record",
                               side_effect=RuntimeError("ledger write failed (scripted)")):
            try:
                out, _s, _ = self.ce_book(w)
            except RuntimeError:
                out = None
        self.assertTrue(out is None or out.action != "booking_created", out)
        self.assertUntouched(self.state(w), "PERDIDO", "PRECIO")
        self.assertEqual(self.state(w)["ledger"], 0)

    def test_transaction_rollback_at_the_final_verification(self):
        w = self.world(books=False, perdido="OTRO")
        with mock.patch.object(BA.BookingAuthority, "_verify_before_commit",
                               side_effect=BA._InvariantBreach("SCRIPTED_ROLLBACK")):
            out, _s, _ = self.ce_book(w)
        self.assertNotEqual(out.action, "booking_created")
        self.assertUntouched(self.state(w), "PERDIDO", "OTRO")

    def test_data_exchange_rollback(self):
        w = self.world(books=False, perdido="PRECIO")
        with mock.patch.object(BA.BookingAuthority, "_verify_before_commit",
                               side_effect=BA._InvariantBreach("SCRIPTED_ROLLBACK")):
            try:
                H.dx_confirm(self.eng, w)
            except Exception:
                pass
        self.assertUntouched(self.state(w), "PERDIDO", "PRECIO")


# ══ F. idempotency ═══════════════════════════════════════════════════════════════════

class Idempotency(_Booking):

    def test_duplicate_same_payload_and_different_payload(self):
        w = self.world(perdido="PRECIO")
        out, sent, _ = self.ce_book(w)
        self.assertEqual(out.action, "booking_created")
        first = self.state(w)
        self.assertCoherentBooking(first)
        o2, s2, _ = self.ce_book(w)                                         # duplicate delivery
        o3, s3, _ = self.ce_book(w, wamid=w.wamid + ".same")               # same payload, new id
        o4, s4, _ = self.ce_book(w, wamid=w.wamid + ".diff", data=H.flow_payload(
            w.token, nombre_apellido="Otra Persona", email="otra@example.invalid",
            direccion="Otra Calle 9"))                                     # different payload
        for o in (o2, o3, o4):
            self.assertNotEqual(o.action, "booking_created", o)
        self.assertEqual((s2, s3, s4), ([], [], []))                        # no second receipt
        self.assertEqual(self.state(w), first)                              # nothing moved
        self.assertCoherentBooking(self.state(w))

    def test_data_exchange_duplicate_after_ce_booking(self):
        w = self.world(perdido="OTRO")
        self.assertEqual(self.ce_book(w)[0].action, "booking_created")
        first = self.state(w)
        try:
            H.dx_confirm(self.eng, w)
        except Exception:
            pass
        self.assertEqual(self.state(w), first)
        self.assertCoherentBooking(first)


class AuthorityCatalogue(unittest.TestCase):
    def test_the_projected_column_is_in_the_booking_schema_preflight(self):
        self.assertIn(("leads", "motivo_perdida"), set(BA._required_columns()))


if __name__ == "__main__":
    unittest.main()
