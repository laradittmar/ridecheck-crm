"""WILD1-C-R4-R1 — PERDIDO conversation consistency.

* R4-D1 — an OPERATOR-held PERDIDO holds before every text-path quote, re-quote,
  clarification and progression renderer: exact owner copy, nothing else composed, no stage,
  flag, motivo, offer, token or booking change, on the authority-on and legacy paths.
* R4-D2 — a fallback form after the ENGINE's own decline keeps PERDIDO and gets the neutral
  "Recibí los datos…" copy (never the manual-review copy); explicit textual reconsideration
  still works; supplying vehicle/location data is not a reconsideration.
* Motorcycle/quad/UTV discovered while PERDIDO: classification and human-handoff safety state
  kept, no quote, no schedule, no Flow.
* Owner ruling (R4 §13, option a): a valid booking-shaped Flow submission for an ISSUED offer
  is honoured by BookingAuthority after an operator's PERDIDO — characterized, not changed.
  Invalid / withdrawn / fallback-shaped submissions never override PERDIDO.

Real `/api/conversation/handle` route throughout; operator actions are the real kanban
functions; only model and network seams are scripted.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, time, timedelta, timezone
from unittest import mock

import _wild1c_harness as W
import test_wild1_c_conversation_correction as T
import test_wild1_c_r4_retention_and_perdido_guard as R4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (BookingCommandRecord, Lead, ThreadRevision, WhatsAppBookingOffer,
                        WhatsAppMessage, WhatsAppThreadCandidate, WhatsAppThreadState)

CE = W.CE
H = W.H                                          # the SB2B booking harness
HOLD = CE._PERDIDO_HOLD_REPLY
DATA_REPLY = CE._PERDIDO_FALLBACK_DATA_REPLY
ACK = CE._PENDING_QUESTION_ACK
MOTO_REPLY = str(CE._MOTORCYCLE_HANDOFF_REPLY)
COROLLA, PALERMO = R4.COROLLA, R4.PALERMO
VEHICLE_FORM, LOCATION_FORM = R4.VEHICLE_FORM, R4.LOCATION_FORM

#: every scripted reading the text cases need, keyed by a test-side fragment
SCRIPT = [
    ("Corolla 2018 y", W.payload(vehicle=("Toyota", "Corolla", 2018),
                                 locations=(("Palermo", "INSPECTION_LOCATION"),))),
    ("Es un Toyota", W.payload(vehicle=("Toyota", "Corolla", 2018))),
    ("Está en Palermo", W.payload(locations=(("Palermo", "INSPECTION_LOCATION"),))),
    ("Cuánto sale", W.payload(quote=True)),
    ("dale, avancemos", W.payload(accept="ACCEPT")),
    ("martes a las 10", W.payload(accept="ACCEPT", day="TUESDAY")),
    ("no gracias", W.payload(accept="REJECT", decline_reason="PRICE")),
    ("en realidad", W.payload(vehicle=("Toyota", "Corolla", 2018))),
]
TEXTS = {
    "vehicle_and_zone": "Es un Toyota Corolla 2018 y está en Palermo",
    "vehicle_only": "Es un Toyota Corolla 2018",
    "zone_only": "Está en Palermo",
    "price_request": "¿Cuánto sale la revisión?",
    "accept": "dale, avancemos",
    "day_time": "¿el martes a las 10 puede ser?",
}
LEGACY = dict(reconciler_acceptance_authority_enabled=False)


def text_of(r):
    return "\n".join(list(r.sent) + [f for f in r.flows if f])


def op_perdido(eng, lead_id, motivo):
    from app.ui import kanban_actions as KA
    with Session(eng) as db:
        KA.ui_perdido(lead_id=lead_id, motivo_perdida=motivo, db=db)


def engine_decline(eng, t):
    W.run(eng, W.burst(eng, t, ["no gracias, es caro"]), interp=W.Interpreter(SCRIPT))
    s = W.snap(eng, t)
    assert (s["flag"], s["motivo"]) == ("PERDIDO", "PRECIO"), s
    return s


class _Held(unittest.TestCase):
    """Shared assertions: an exclusive hold changes nothing and composes nothing else."""

    def assertExclusiveHold(self, r, before, after, *, copy=HOLD):
        self.assertEqual(r.sent, [copy], r.sent)                       # exact, alone
        self.assertEqual(r.flows, [])                                  # no Flow of any kind
        out = text_of(r)
        for bad in ("Total:", "Revisión: $", "podemos avanzar", "¿Qué día", ACK,
                    "localidad o barrio", "qué vehículo"):
            self.assertNotIn(bad, out)
        self.assertEqual((after["flag"], after["motivo"]), (before["flag"], before["motivo"]))
        self.assertEqual(after["stage"], before["stage"])              # no stage movement
        self.assertEqual((after["token"], after["offers"], after["booked"], after["revisions"]),
                         (before["token"], before["offers"], before["booked"],
                          before["revisions"]))
        self.assertEqual(after["decisions"], before["decisions"])      # provenance untouched
        self.assertEqual(after["notices"], before["notices"])
        self.assertNotEqual(after["flag"], "PRESUPUESTO_ENVIADO")      # no follow-up eligibility


# ══ A. R4-D1 — operator PERDIDO holds before every text-path renderer ═════════════════

class OperatorPerdidoText(_Held):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def world(self, stage):
        if stage == "QUALIFYING":
            t = W.new_thread(self.eng)
            op_perdido(self.eng, t.lead_id, "OTRO")
        else:
            t = T.quoted_world(self.eng)
            op_perdido(self.eng, t.lead_id, "PRECIO")
        return t

    def held_turn(self, t, words, *, config=None):
        before = W.snap(self.eng, t)
        r = W.run(self.eng, W.burst(self.eng, t, [words]), interp=W.Interpreter(SCRIPT),
                  config=config)
        return r, before, W.snap(self.eng, t)

    def test_every_text_case_is_held_on_both_paths(self):
        for path, over in (("authority_on", {}), ("legacy", LEGACY)):
            for stage in ("QUALIFYING", "QUOTED"):
                for case, words in TEXTS.items():
                    with self.subTest(path=path, stage=stage, case=case):
                        t = self.world(stage)
                        r, before, after = self.held_turn(t, words, config=W.cfg(**over))
                        self.assertNotEqual(r.out.action, "error", r.out)
                        self.assertExclusiveHold(r, before, after)

    def test_vehicle_and_zone_in_qualifying_no_longer_quotes(self):
        """R4-D1 exactly: R4 sent the quote + CTA and moved the stage to QUOTED."""
        t = self.world("QUALIFYING")
        r, before, after = self.held_turn(t, TEXTS["vehicle_and_zone"])
        self.assertExclusiveHold(r, before, after)
        self.assertEqual(after["stage"], "QUALIFYING")
        with Session(self.eng) as db:                     # no admission write either
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == t.tid)).scalar_one()
            self.assertIsNone(st.home_zone_detail)

    def test_text_after_a_fallback_form_is_held(self):
        t = W.new_thread(self.eng, vehicle=COROLLA)
        op_perdido(self.eng, t.lead_id, "OTRO")
        before = W.snap(self.eng, t)
        rf = W.run(self.eng, R4.flow_event(self.eng, t, LOCATION_FORM), interp=W.Interpreter([]))
        self.assertExclusiveHold(rf, before, W.snap(self.eng, t))
        r, before2, after = self.held_turn(t, TEXTS["zone_only"])
        self.assertExclusiveHold(r, before2, after)

    def test_an_earlier_pending_question_is_not_acknowledged_on_a_held_turn(self):
        t = T.quoted_world(self.eng)
        words = "¿Cuándo me mandan el informe?"
        W.run(self.eng, W.burst(self.eng, t, [words]),
              interp=R4.SlowFirst([(words[:10], W.payload(faq=("report",)))],
                                  slow_key=words[:10], raises=True),
              config=W.cfg_live_async())
        R4.drain()
        self.assertEqual([p[0] for p in W.snap(self.eng, t)["pending"]], ["PENDING"])
        op_perdido(self.eng, t.lead_id, "PRECIO")
        before = W.snap(self.eng, t)
        r = W.run(self.eng, W.burst(self.eng, t, ["dale, avancemos"]),
                  interp=W.Interpreter(SCRIPT), config=W.cfg_live_async())
        R4.drain()
        self.assertExclusiveHold(r, before, W.snap(self.eng, t))
        self.assertLess(r.elapsed, 2.0)                    # a held turn never waits on the model

    def test_held_under_the_kill_switch(self):
        t = self.world("QUALIFYING")

        def kill(_ce):
            os.environ["OUTBOUND_ENABLED"] = "false"
        before = W.snap(self.eng, t)
        r = W.run(self.eng, W.burst(self.eng, t, [TEXTS["vehicle_and_zone"]]),
                  interp=W.Interpreter(SCRIPT), hook=kill)
        after = W.snap(self.eng, t)
        self.assertEqual(r.out.action, "blocked_dispatch")
        self.assertEqual((after["flag"], after["motivo"], after["stage"]),
                         (before["flag"], before["motivo"], before["stage"]))


# ══ B. R4-D2 — engine decline is distinct from operator PERDIDO ═══════════════════════

class EngineDeclineVersusOperator(_Held):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def test_fallback_after_engine_decline_gets_the_neutral_data_copy(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = T.quoted_world(self.eng)
                before = engine_decline(self.eng, t)
                r = W.run(self.eng, R4.flow_event(self.eng, t, form), interp=W.Interpreter([]))
                after = W.snap(self.eng, t)
                self.assertExclusiveHold(r, before, after, copy=DATA_REPLY)
                self.assertNotIn("manualmente", text_of(r))
                self.assertEqual([d[0] for d in after["decisions"]], ["DECLINED"])

    def test_fallback_after_operator_perdido_gets_the_manual_review_copy(self):
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                t = (W.new_thread(self.eng, zone=PALERMO) if form is VEHICLE_FORM
                     else W.new_thread(self.eng, vehicle=COROLLA))
                op_perdido(self.eng, t.lead_id, "OTRO")
                before = W.snap(self.eng, t)
                r = W.run(self.eng, R4.flow_event(self.eng, t, form), interp=W.Interpreter([]))
                self.assertExclusiveHold(r, before, W.snap(self.eng, t), copy=HOLD)

    def test_explicit_textual_reconsideration_still_reactivates(self):
        t = T.quoted_world(self.eng)
        engine_decline(self.eng, t)
        W.run(self.eng, R4.flow_event(self.eng, t, LOCATION_FORM), interp=W.Interpreter([]))
        r = W.run(self.eng, W.burst(self.eng, t, [TEXTS["accept"]]), interp=W.Interpreter(SCRIPT))
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"], s["stage"]), ("ACEPTADO", None, "SCHEDULING"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED", "RECONSIDERED"])
        self.assertNotIn(HOLD, text_of(r))

    def test_vehicle_information_alone_is_not_a_reconsideration(self):
        t = T.quoted_world(self.eng)
        engine_decline(self.eng, t)
        W.run(self.eng, W.burst(self.eng, t, ["perdón, en realidad es un Toyota Corolla 2018"]),
              interp=W.Interpreter(SCRIPT))
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), ("PERDIDO", "PRECIO"))
        self.assertEqual([d[0] for d in s["decisions"]], ["DECLINED"])
        self.assertEqual((s["token"], s["offers"], s["booked"]), (None, 0, 0))


# ══ C. motorcycle / quad / UTV while PERDIDO ══════════════════════════════════════════

class MotorcycleWhilePerdido(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def assertHeldHandoff(self, t, r, before):
        s = W.snap(self.eng, t)
        self.assertEqual((s["flag"], s["motivo"]), (before["flag"], before["motivo"]))
        self.assertTrue(s["needs_human"] and s["necesita_humano"])   # human-handoff safety
        self.assertEqual(r.flows, [])                                # no Flow, no token
        self.assertEqual((s["token"], s["offers"], s["booked"]), (None, 0, 0))
        out = text_of(r)
        self.assertEqual(r.sent, [MOTO_REPLY], r.sent)
        for bad in ("Total:", "podemos avanzar", "¿Qué día", ACK, HOLD):
            self.assertNotIn(bad, out)
        with Session(self.eng) as db:
            motos = db.execute(select(func.count()).select_from(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == t.tid,
                WhatsAppThreadCandidate.tipo_vehiculo == "MOTO")).scalar_one()
        self.assertGreaterEqual(motos, 1)                            # classification kept

    def test_text_motorcycle_on_operator_perdido(self):
        for words in ("tengo una moto Honda CB 500", "es un cuatriciclo",
                      "tengo un UTV Can-Am Maverick"):
            with self.subTest(words=words):
                t = W.new_thread(self.eng)
                op_perdido(self.eng, t.lead_id, "OTRO")
                before = W.snap(self.eng, t)
                r = W.run(self.eng, W.burst(self.eng, t, [words]), interp=W.Interpreter([]))
                self.assertHeldHandoff(t, r, before)

    def test_motorcycle_fallback_form_on_operator_and_engine_perdido(self):
        form = {"tipo_vehiculo": "MOTO", "marca": "Honda", "modelo": "CB 500"}
        t = W.new_thread(self.eng, zone=PALERMO)
        op_perdido(self.eng, t.lead_id, "OTRO")
        before = W.snap(self.eng, t)
        r = W.run(self.eng, R4.flow_event(self.eng, t, form), interp=W.Interpreter([]))
        self.assertHeldHandoff(t, r, before)
        t2 = T.quoted_world(self.eng)
        before2 = engine_decline(self.eng, t2)
        r2 = W.run(self.eng, R4.flow_event(self.eng, t2, form), interp=W.Interpreter([]))
        self.assertHeldHandoff(t2, r2, before2)


class TerminalTurnsCarryNoPendingAck(unittest.TestCase):
    """A terminal / human-owned turn never carries "si me escribís nuevamente, la retomo":
    an earlier pending question simply stays PENDING for a later continuing turn."""

    def test_phone_request_after_a_pending_question(self):
        """The provider stays down on the terminal turn too, so the earlier row is still
        pending there: a phone-request handoff must not carry the acknowledgement."""
        eng = W.engine()
        t = T.quoted_world(eng)
        interp = R4.Switchable([("informe", W.payload(faq=("report",)))])
        interp.mode = "raise"
        W.run(eng, W.burst(eng, t, ["¿Cuándo me mandan el informe?"]), interp=interp,
              config=W.cfg_live_async())
        R4.drain()
        self.assertEqual([p[0] for p in W.snap(eng, t)["pending"]], ["PENDING"])
        r = W.run(eng, W.burst(eng, t, ["¿Me podés llamar por teléfono?"]), interp=interp,
                  config=W.cfg_live_async())
        R4.drain()
        self.assertTrue(r.sent)
        self.assertNotIn(ACK, text_of(r))
        self.assertEqual([p[0] for p in W.snap(eng, t)["pending"]], ["PENDING"])   # kept


# ══ D. owner ruling — booking after operator PERDIDO (characterization) ═══════════════

class BookingAfterOperatorPerdido(unittest.TestCase):
    """R4 §13 option (a), owner ruling 2026-11-17: a valid booking-shaped Flow submission for
    an ISSUED offer is honoured by BookingAuthority even after an operator's PERDIDO. This
    pins EXISTING behaviour; BookingAuthority and the offer lifecycle are not modified."""

    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        cls.day = H.slot_day()

    def world(self, at, *, day=None):
        w = H.seed(self.eng, day=day or self.day, at=at)
        op_perdido(self.eng, w.lead_id, "PRECIO")
        f = H.facts(self.eng, w)
        self.assertEqual(f["lead_flag"], "PERDIDO")
        return w

    def counts(self, w):
        with Session(self.eng) as db:
            booked = db.execute(select(func.count()).select_from(ThreadRevision).where(
                ThreadRevision.thread_id == w.thread_id,
                ThreadRevision.status == "booked")).scalar_one()
            ledger = db.execute(select(func.count()).select_from(BookingCommandRecord).where(
                BookingCommandRecord.thread_id == w.thread_id)).scalar_one()
            offers = [o.status for o in db.execute(select(WhatsAppBookingOffer).where(
                WhatsAppBookingOffer.thread_id == w.thread_id)).scalars()]
            lead = db.get(Lead, w.lead_id)
            return booked, ledger, offers, lead.flag, lead.motivo_perdida

    def test_valid_submission_is_honoured_and_idempotent(self):
        w = self.world(time(10, 0))
        with H.Meta() as m:
            out, _ = H.run_turn(self.eng, H.flow_event(w), cfg=H.settings())
        self.assertEqual(out.action, "booking_created", out)
        booked, ledger, offers, flag, _motivo = self.counts(w)
        self.assertEqual((booked, ledger, offers, flag), (1, 1, ["ACCEPTED"], "ACEPTADO"))
        # the receipt is the ledger's post-persistence acknowledgement, sent once
        self.assertEqual([t for _w, t in m.sent], [H.OWNER_TEXT])
        f = H.facts(self.eng, w)
        self.assertEqual(f["outbound"], [("BOOKING_FLOW", "sent", H.OWNER_TEXT)])
        # duplicate delivery, and a replay with a DIFFERENT payload: still one booking
        with H.Meta() as m2:
            out2, _ = H.run_turn(self.eng, H.flow_event(w), cfg=H.settings())
            out3, _ = H.run_turn(self.eng, H.flow_event(
                w, wamid=w.wamid + ".replay", data=H.flow_payload(
                    w.token, nombre_apellido="Otra Persona", email="otra@example.invalid",
                    direccion="Otra Calle 9")), cfg=H.settings())
        self.assertIn(out2.action, ("booking_replayed", "skipped_dedup"))
        self.assertNotEqual(out3.action, "booking_created")
        self.assertEqual(self.counts(w)[:2], (1, 1))
        self.assertEqual(m2.sent, [])

    def test_data_exchange_confirm_is_honoured_too(self):
        w = self.world(time(10, 0), day=self.day + timedelta(days=2))  # Thursday
        self.assertIsNotNone(H.dx_confirm(self.eng, w))
        booked, ledger, _offers, flag, _ = self.counts(w)
        self.assertEqual((booked, ledger, flag), (1, 1, "ACEPTADO"))

    def test_invalid_token_never_overrides_perdido(self):
        w = self.world(time(12, 0))
        bogus = "rcbk-not-an-issued-offer-r4r1"
        with H.Meta():
            out, _ = H.run_turn(self.eng, H.flow_event(
                w, token=bogus, data=H.flow_payload(bogus, nombre_apellido="X",
                                                    email="x@example.invalid",
                                                    direccion="Y 1")), cfg=H.settings())
        booked, ledger, offers, flag, motivo = self.counts(w)
        self.assertEqual((booked, flag, motivo), (0, "PERDIDO", "PRECIO"), out)

    def test_withdrawn_offer_never_overrides_perdido(self):
        from app.services.offer_lifecycle import OfferLifecycle, WithdrawReason
        w = self.world(time(12, 0))
        with Session(self.eng) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertEqual(OfferLifecycle(db).withdraw(
                st, thread_id=w.thread_id, reason=WithdrawReason.HUMAN_HANDOFF,
                clear_scheduling=False), 1)
            db.commit()
        with H.Meta():
            out, _ = H.run_turn(self.eng, H.flow_event(w), cfg=H.settings())
        booked, _ledger, offers, flag, motivo = self.counts(w)
        self.assertEqual((booked, flag, motivo), (0, "PERDIDO", "PRECIO"), out)
        self.assertNotIn("ACCEPTED", offers)

    def test_fallback_payload_cannot_masquerade_as_booking_shaped(self):
        w = self.world(time(13, 0))
        for form in (VEHICLE_FORM, LOCATION_FORM):
            with self.subTest(form=sorted(form)[0]):
                data = H.flow_payload(w.token, **form)
                ev = H.flow_event(w, data=data, wamid=f"{w.wamid}.{sorted(form)[0]}")
                with Session(self.eng) as db:
                    st = db.execute(select(WhatsAppThreadState).where(
                        WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
                    self.assertFalse(CE.ConversationEngine._is_booking_shaped_submission(ev, st))
                with Session(self.eng) as db:            # the inbound row the route expects
                    db.add(WhatsAppMessage(thread_id=w.thread_id, direction="in",
                                           wa_message_id=ev.wa_message_id,
                                           message_type="flow_response", status="received",
                                           timestamp=datetime.now(timezone.utc)))
                    db.commit()
                with H.Meta() as m:
                    out, _ = H.run_turn(self.eng, ev, cfg=H.settings())
                booked, _l, offers, flag, motivo = self.counts(w)
                self.assertEqual((booked, flag, motivo), (0, "PERDIDO", "PRECIO"), out)
                self.assertEqual(offers, ["ISSUED"])                # the offer is untouched
                self.assertEqual([t for _w, t in m.sent], [HOLD])


if __name__ == "__main__":
    unittest.main()
