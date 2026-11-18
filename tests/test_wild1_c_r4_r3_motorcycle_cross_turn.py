"""WILD1-C-R4-R3 — motorcycle / quad / UTV cross-turn human handoff (review RV-F-01).

Owner contract: once a motorcycle, quad (cuatriciclo) or UTV has been identified on a thread,
no later turn may auto-quote, accept, create or issue an offer, dispatch the Booking Flow, or
schedule/book — while the handoff is unresolved (MOTO candidate recorded, the contact-data
Flow pending or never submitted). Any later text turn takes the existing plain-text handoff
(`needs_human`, the existing review notification, `_MOTORCYCLE_HANDOFF_REPLY`); the contact
Flow, when the customer does submit it, still stores its data and stays human-owned.

Before the correction the first enquiry got the contact Flow with `needs_human=False`, and
"está en Palermo, ¿cuánto sale?" then auto-quoted the MOTO tariff, after which
"dale, ¿mañana puede ser?" moved the lead to ACEPTADO and dispatched the Booking Flow with an
ISSUED offer (reviewer probes `p_moto.py` / `p_moto2.py`, base `aa2c8db` and candidate).

Real `/api/conversation/handle` route throughout (`_wild1c_harness.run`); only the model and
network seams are scripted. SQLite in memory; set `R6_PG_URL` for PostgreSQL. The live
asynchronous interpreter posture runs every cross-turn case a second time.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import _wild1c_harness as W
import test_wild1_c_conversation_correction as T
import test_wild1_c_r4_retention_and_perdido_guard as R4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (Lead, WhatsAppBookingOffer, WhatsAppThreadCandidate,
                        WhatsAppThreadState)

CE = W.CE
MOTO_REPLY = str(CE._MOTORCYCLE_HANDOFF_REPLY)
ADVISOR_REPLY = str(CE._MOTORCYCLE_ADVISOR_REPLY)
CONTACT_INTRO = CE._MOTORCYCLE_CONTACT_INTRO
PREFIX = CE._MOTO_HANDOFF_TOKEN_PREFIX
PALERMO = R4.PALERMO

#: the three motorcycle-family first enquiries
FIRST = {
    "moto": "tengo una moto Honda CB 500",
    "cuatriciclo": "es un cuatriciclo Yamaha",
    "utv": "UTV Polaris RZR",
}
#: the review's follow-ups
ZONE_AND_PRICE = "está en Palermo, ¿cuánto sale?"
PRICE_ONLY = "¿cuánto sale?"
ZONE_ONLY = "en Palermo"
ACCEPT_DAY = "dale, ¿mañana puede ser?"

#: what a working interpreter reads on each follow-up (keys are test-side fragments)
SCRIPT = [
    ("está en Palermo", W.payload(quote=True, locations=(("Palermo", "INSPECTION_LOCATION"),))),
    ("en Palermo", W.payload(locations=(("Palermo", "INSPECTION_LOCATION"),))),
    ("cuánto sale", W.payload(quote=True)),
    ("mañana puede ser", W.payload(accept="ACCEPT", day="TOMORROW")),
    ("Corolla 2018", W.payload(quote=True, vehicle=("Toyota", "Corolla", 2018),
                               locations=(("Palermo", "INSPECTION_LOCATION"),))),
]
CONTACT_FORM = {"nombre_apellido": "Rita Moto Fixture", "email": "rita@example.invalid",
                "como_llego": "Instagram"}
COMMERCIAL = ("Total:", "$", "cotización", "podemos avanzar", "¿Qué día", "horarios disponibles",
              "Completá el formulario")


def counting_hook(calls, extra=None):
    """Count the existing operator-review notification (the Resend alert) per engine."""
    def hook(ce):
        real = ce._send_fallback_human_review_notification

        def counted(ctx, state, reason):
            calls.append(reason)
            return real(ctx, state, reason)
        ce._send_fallback_human_review_notification = counted
        if extra is not None:
            extra(ce)
    return hook


def text_of(r):
    return "\n".join(list(r.sent) + [f for f in r.flows if f])


def moto_candidates(eng, t):
    with Session(eng) as db:
        return db.execute(select(func.count()).select_from(WhatsAppThreadCandidate).where(
            WhatsAppThreadCandidate.thread_id == t.tid,
            WhatsAppThreadCandidate.tipo_vehiculo == "MOTO")).scalar_one()


def contact_event(eng, t, *, token):
    return R4.flow_event(eng, t, CONTACT_FORM, token=token)


class _Base(unittest.TestCase):
    LIVE = False

    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()

    def cfg(self, **over):
        return W.cfg_live_async(**over) if self.LIVE else W.cfg(**over)

    def turn(self, t, texts, calls, *, hook_extra=None, **kw):
        return W.run(self.eng, W.burst(self.eng, t, list(texts)),
                     interp=W.Interpreter(SCRIPT), config=self.cfg(**kw),
                     hook=counting_hook(calls, hook_extra))

    def flow_turn(self, t, event, calls, *, hook_extra=None):
        return W.run(self.eng, event, interp=W.Interpreter(SCRIPT), config=self.cfg(),
                     hook=counting_hook(calls, hook_extra))

    # ── shared assertions ───────────────────────────────────────────────────────────

    def assertNoCommercialStep(self, t, r=None):
        s = W.snap(self.eng, t)
        self.assertNotIn(s["flag"], ("PRESUPUESTO_ENVIADO", "ACEPTADO"), s)
        self.assertNotIn(s["stage"], ("QUOTED", "SCHEDULING"), s)
        self.assertEqual((s["offers"], s["revisions"], s["booked"], s["ledger"]), (0, 0, 0, 0), s)
        self.assertFalse((s["token"] or "") and not (s["token"] or "").startswith(PREFIX),
                         f"a non-motorcycle (booking) token was minted: {s['token']!r}")
        for _path, _mt, text in s["outbound"]:
            for bad in COMMERCIAL:
                self.assertNotIn(bad, text or "", f"commercial text reached the customer: {text!r}")
        if r is not None:
            out = text_of(r)
            for bad in COMMERCIAL:
                self.assertNotIn(bad, out)
        return s

    def assertFirstEnquiryFlow(self, t, r, calls):
        """First enquiry: the existing contact-data Flow, needs_human deferred."""
        self.assertEqual(r.out.action, "flow_button_sent", r.out)
        self.assertEqual(r.flows, [CONTACT_INTRO])
        self.assertEqual(r.sent, [])
        s = self.assertNoCommercialStep(t, r)
        self.assertTrue((s["token"] or "").startswith(PREFIX))
        self.assertFalse(s["needs_human"])
        self.assertEqual(calls, [])
        self.assertGreaterEqual(moto_candidates(self.eng, t), 1)
        return s

    def assertTextHandoff(self, t, r, calls, *, expect_calls=1):
        """A later text turn: the existing plain-text handoff, retained."""
        self.assertEqual(r.out.action, "replied", r.out)
        self.assertEqual(r.sent, [MOTO_REPLY], r.sent)
        self.assertEqual(r.flows, [])
        s = self.assertNoCommercialStep(t, r)
        self.assertTrue(s["needs_human"] and s["necesita_humano"], s)
        self.assertTrue((s["token"] or "").startswith(PREFIX), "the contact Flow stays open")
        self.assertEqual(calls, ["motorcycle_enquiry"] * expect_calls)
        return s

    def assertHumanOwned(self, t, r, calls):
        self.assertEqual(r.out.action, "skipped_human", r.out)
        self.assertEqual((r.sent, r.flows), ([], []))
        s = self.assertNoCommercialStep(t, r)
        self.assertTrue(s["needs_human"] and s["necesita_humano"])
        self.assertEqual(calls, ["motorcycle_enquiry"])
        return s


# ══ 1. the review's three-turn reproductions, every variant × every follow-up ═══════════

class CrossTurnHandoff(_Base):
    def run_case(self, first, follow_up):
        calls = []
        t = W.new_thread(self.eng)
        r1 = self.turn(t, [first], calls)
        self.assertFirstEnquiryFlow(t, r1, calls)
        token = W.snap(self.eng, t)["token"]
        r2 = self.turn(t, [follow_up], calls)
        self.assertTextHandoff(t, r2, calls)
        r3 = self.turn(t, [ACCEPT_DAY], calls)
        self.assertHumanOwned(t, r3, calls)
        # the contact Flow, submitted after the handoff, stores its data and stays human-owned
        r4 = self.flow_turn(t, contact_event(self.eng, t, token=token), calls)
        self.assertEqual(r4.out.action, "replied", r4.out)
        self.assertEqual(r4.sent, [ADVISOR_REPLY])
        s = self.assertNoCommercialStep(t, r4)
        self.assertTrue(s["needs_human"] and s["necesita_humano"])
        self.assertIsNone(s["token"])                                 # consumed once
        self.assertEqual(calls, ["motorcycle_enquiry"])               # notification idempotent
        with Session(self.eng) as db:
            lead = db.get(Lead, t.lead_id)
            self.assertEqual((lead.nombre, lead.apellido, lead.email, lead.canal),
                             ("Rita", "Moto Fixture", "rita@example.invalid", "Instagram"))
        # a replayed submission (new delivery id, same token) changes and sends nothing
        r5 = self.flow_turn(t, contact_event(self.eng, t, token=token), calls)
        self.assertEqual(r5.out.action, "skipped_human")
        self.assertEqual((r5.sent, r5.flows), ([], []))
        # and commerce stays closed afterwards
        r6 = self.turn(t, [ZONE_AND_PRICE], calls)
        self.assertHumanOwned(t, r6, calls)
        self.assertEqual(moto_candidates(self.eng, t), 1)             # no second candidate

    def test_zone_and_price_follow_up(self):
        for name, first in FIRST.items():
            with self.subTest(vehicle=name):
                self.run_case(first, ZONE_AND_PRICE)

    def test_price_only_follow_up(self):
        for name, first in FIRST.items():
            with self.subTest(vehicle=name):
                self.run_case(first, PRICE_ONLY)

    def test_zone_only_follow_up(self):
        for name, first in FIRST.items():
            with self.subTest(vehicle=name):
                self.run_case(first, ZONE_ONLY)

    def test_accept_and_day_as_the_first_follow_up(self):
        for name, first in FIRST.items():
            with self.subTest(vehicle=name):
                calls = []
                t = W.new_thread(self.eng, zone=PALERMO)
                self.assertFirstEnquiryFlow(t, self.turn(t, [first], calls), calls)
                self.assertTextHandoff(t, self.turn(t, [ACCEPT_DAY], calls), calls)

    def test_repeating_the_motorcycle_does_not_re_offer_the_flow(self):
        calls = []
        t = W.new_thread(self.eng)
        self.assertFirstEnquiryFlow(t, self.turn(t, [FIRST["moto"]], calls), calls)
        r = self.turn(t, ["la moto está en Palermo, ¿cuánto sale?"], calls)
        self.assertTextHandoff(t, r, calls)
        self.assertEqual(moto_candidates(self.eng, t), 1)

    def test_contact_flow_submitted_directly_is_unchanged(self):
        """The pre-existing path: the form is submitted before any other text."""
        calls = []
        t = W.new_thread(self.eng)
        self.assertFirstEnquiryFlow(t, self.turn(t, [FIRST["utv"]], calls), calls)
        token = W.snap(self.eng, t)["token"]
        r = self.flow_turn(t, contact_event(self.eng, t, token=token), calls)
        self.assertEqual(r.sent, [ADVISOR_REPLY])
        s = self.assertNoCommercialStep(t, r)
        self.assertTrue(s["needs_human"] and s["necesita_humano"])
        self.assertEqual(calls, ["motorcycle_enquiry"])
        self.assertHumanOwned(t, self.turn(t, [ACCEPT_DAY], calls), calls)


class CrossTurnHandoffLiveAsync(CrossTurnHandoff):
    """Every case again in the live posture (shadow worker, 6.0 s bounded wait)."""
    LIVE = True


# ══ 2. other entries: vehicle-fallback form and website form ════════════════════════════

class OtherEntries(_Base):
    def test_vehicle_fallback_entry(self):
        for tipo_words in ("MOTO", "Cuatriciclo", "UTV"):
            with self.subTest(tipo=tipo_words):
                calls = []
                t = W.new_thread(self.eng, zone=PALERMO)
                form = {"tipo_vehiculo": tipo_words, "marca": "Honda", "modelo": "XR 250"}
                r1 = self.flow_turn(t, R4.flow_event(self.eng, t, form), calls)
                self.assertFirstEnquiryFlow(t, r1, calls)
                self.assertTextHandoff(t, self.turn(t, [ZONE_AND_PRICE], calls), calls)
                self.assertHumanOwned(t, self.turn(t, [ACCEPT_DAY], calls), calls)

    def test_a_car_fallback_form_after_a_motorcycle_is_not_quoted(self):
        calls = []
        t = W.new_thread(self.eng, zone=PALERMO)
        self.assertFirstEnquiryFlow(t, self.turn(t, [FIRST["moto"]], calls), calls)
        r = self.flow_turn(t, R4.flow_event(self.eng, t, R4.VEHICLE_FORM,
                                            token="vehicle-fallback-fixture"), calls)
        self.assertTextHandoff(t, r, calls)
        r = self.flow_turn(t, R4.flow_event(self.eng, t, R4.LOCATION_FORM,
                                            token="location-fallback-fixture"), calls)
        self.assertEqual(r.out.action, "skipped_human")
        self.assertNoCommercialStep(t, r)

    def test_website_form_entry(self):
        form = ("Hola, quiero solicitar una revisión pre-compra.\n* Nombre: Rita Fixture\n"
                "* Auto a revisar: Honda CB 500\n* Tipo: Moto\n* Localidad: Palermo\n"
                "* Total estimado: $120.000")
        calls = []
        t = W.new_thread(self.eng)
        self.assertFirstEnquiryFlow(t, self.turn(t, [form], calls), calls)
        self.assertTextHandoff(t, self.turn(t, [ZONE_AND_PRICE], calls), calls)
        self.assertHumanOwned(t, self.turn(t, [ACCEPT_DAY], calls), calls)

    def test_a_website_car_form_after_a_motorcycle_is_not_quoted(self):
        calls = []
        t = W.new_thread(self.eng)
        self.assertFirstEnquiryFlow(t, self.turn(t, [FIRST["cuatriciclo"]], calls), calls)
        form = ("Hola, quiero solicitar una revisión pre-compra.\n* Nombre: Rita Fixture\n"
                "* Auto a revisar: Toyota Corolla 2018\n* Tipo: Auto mediano\n"
                "* Localidad: Palermo\n* Total estimado: $140.000")
        self.assertTextHandoff(t, self.turn(t, [form], calls), calls)


# ══ 3. kill switch OFF ═══════════════════════════════════════════════════════════════════

def kill_switch_off(_ce):
    os.environ["OUTBOUND_ENABLED"] = "false"     # restored by the harness's Meta() scope


class KillSwitchOff(_Base):
    def test_handoff_state_is_kept_and_nothing_commercial_happens(self):
        for name, first in FIRST.items():
            with self.subTest(vehicle=name):
                calls = []
                t = W.new_thread(self.eng)
                r1 = self.turn(t, [first], calls, hook_extra=kill_switch_off)
                self.assertEqual(r1.out.action, "human_handoff_blocked", r1.out)
                self.assertGreaterEqual(moto_candidates(self.eng, t), 1)
                self.assertIsNone(W.snap(self.eng, t)["token"])       # rolled back (existing)
                r2 = self.turn(t, [ZONE_AND_PRICE], calls, hook_extra=kill_switch_off)
                self.assertEqual(r2.out.action, "human_handoff_blocked", r2.out)
                self.assertEqual((r2.sent, r2.flows), ([], []))
                s = self.assertNoCommercialStep(t, r2)
                self.assertTrue(s["needs_human"] and s["necesita_humano"])
                self.assertEqual(calls, ["motorcycle_enquiry"])
                r3 = self.turn(t, [ACCEPT_DAY], calls, hook_extra=kill_switch_off)
                self.assertEqual(r3.out.action, "skipped_human")
                self.assertNoCommercialStep(t, r3)

    def test_kill_switch_off_on_the_follow_up_only(self):
        calls = []
        t = W.new_thread(self.eng)
        self.assertFirstEnquiryFlow(t, self.turn(t, [FIRST["moto"]], calls), calls)
        r = self.turn(t, [ZONE_AND_PRICE], calls, hook_extra=kill_switch_off)
        self.assertEqual(r.out.action, "human_handoff_blocked", r.out)
        s = self.assertNoCommercialStep(t, r)
        self.assertTrue(s["needs_human"])


# ══ 4. defence in depth: with the Layer A thread check bypassed, the commercial paths ════
#      still refuse a recorded motorcycle

def bypass_first_thread_check(ce):
    """Make the FIRST `_thread_motorcycle_recorded` call of the turn (the Layer A check)
    answer False, so the turn proceeds into the ordinary paths and each later gate is what
    is under test."""
    real = ce._thread_motorcycle_recorded
    seen = {"n": 0}

    def patched(ctx, state):
        seen["n"] += 1
        return False if seen["n"] == 1 else real(ctx, state)
    ce._thread_motorcycle_recorded = patched


def seed_moto(eng, *, stage, flag=None, zone=PALERMO):
    t = W.new_thread(eng, stage=stage, flag=flag,
                     vehicle=("Honda", "CB 500", None, "MOTO"), zone=zone)
    return t


class DefenceInDepth(_Base):
    def test_price_is_refused_for_a_recorded_motorcycle(self):
        calls = []
        t = seed_moto(self.eng, stage="QUALIFYING")
        r = self.turn(t, [ZONE_AND_PRICE], calls, hook_extra=bypass_first_thread_check)
        self.assertNoCommercialStep(t, r)

    def test_acceptance_is_refused_for_a_recorded_motorcycle(self):
        calls = []
        t = seed_moto(self.eng, stage="QUOTED", flag="PRESUPUESTO_ENVIADO")
        r = self.turn(t, [ACCEPT_DAY], calls, hook_extra=bypass_first_thread_check)
        self.assertEqual(r.flows, [])
        s = W.snap(self.eng, t)                      # the seeded flag stays as it was
        self.assertEqual((s["flag"], s["stage"]), ("PRESUPUESTO_ENVIADO", "QUOTED"))
        self.assertEqual((s["offers"], s["revisions"], s["booked"], s["token"]), (0, 0, 0, None))
        for bad in COMMERCIAL:
            self.assertNotIn(bad, text_of(r))
        # and the acceptance handler itself refuses, into the existing plain-text handoff
        t2 = seed_moto(self.eng, stage="QUOTED", flag="PRESUPUESTO_ENVIADO")
        out, sent, s2 = self.direct(t2, lambda eng, ctx: eng._handle_quoted_acceptance(
            ctx, ctx.state))
        self.assertEqual(sent, [MOTO_REPLY])
        self.assertEqual((s2["flag"], s2["stage"], s2["offers"]),
                         ("PRESUPUESTO_ENVIADO", "QUOTED", 0))
        self.assertTrue(s2["needs_human"] and s2["necesita_humano"])

    def test_website_form_handler_refuses_a_recorded_motorcycle(self):
        t = seed_moto(self.eng, stage="QUALIFYING", zone=None)
        form = {"vehicle_text": "Toyota Corolla 2018", "submitted_tipo": "Auto mediano",
                "zone_detail": "Palermo", "submitted_total": 140000}
        out, sent, s = self.direct(t, lambda eng, ctx: eng._handle_website_form(
            ctx, ctx.state, form))
        self.assertEqual(sent, [MOTO_REPLY])
        self.assertNotIn(s["flag"], ("PRESUPUESTO_ENVIADO", "ACEPTADO"))
        self.assertTrue(s["needs_human"])

    def direct(self, t, call):
        db = W.H.session_factory(self.eng)()
        sent = []
        try:
            eng = CE.ConversationEngine(db=db, settings=self.cfg())
            ctx = eng._load_context(t.tid)
            with mock.patch.object(eng, "_send_text_to_wa",
                                   lambda _ctx, text, **_k: sent.append(str(text)) or "wamid.X"):
                out = call(eng, ctx)
            db.commit()
        finally:
            db.close()
        return out, sent, W.snap(self.eng, t)

    def test_booking_flow_dispatch_is_refused_for_a_recorded_motorcycle(self):
        t = seed_moto(self.eng, stage="SCHEDULING", flag="ACEPTADO")
        Sess = W.H.session_factory(self.eng)
        db = Sess()
        try:
            eng = CE.ConversationEngine(db=db, settings=self.cfg())
            ctx = eng._load_context(t.tid)
            self.assertTrue(eng._thread_motorcycle_recorded(ctx, ctx.state))
            self.assertIsNone(eng._compute_price_quote(ctx, ctx.state))
            sent = []
            with mock.patch.object(eng, "_send_text_to_wa",
                                   lambda _ctx, text, **_k: sent.append(str(text)) or "wamid.X"):
                out = eng._send_booking_flow(ctx, ctx.state, W.FLOW_ID)
            self.assertIsNotNone(out)
            self.assertEqual(sent, [MOTO_REPLY])
            self.assertEqual(db.execute(select(func.count()).select_from(
                WhatsAppBookingOffer).where(WhatsAppBookingOffer.thread_id == t.tid)
            ).scalar_one(), 0)
        finally:
            db.close()

    def test_the_predicate_reads_both_persisted_authorities(self):
        """A pending contact-Flow token alone, or a MOTO candidate alone, is enough; an
        archived (prior-cycle) candidate and a booking token are not."""
        from types import SimpleNamespace as NS
        eng = CE.ConversationEngine.__new__(CE.ConversationEngine)
        moto = NS(tipo_vehiculo="MOTO", status="mentioned")
        car = NS(tipo_vehiculo="AUTO", status="current_focus")
        cases = [
            (NS(candidates=[]), NS(flow_booking_token=f"{PREFIX}7-1"), True),
            (NS(candidates=[car, moto]), NS(flow_booking_token=None), True),
            (NS(candidates=[NS(tipo_vehiculo="motocicleta", status="mentioned")]),
             NS(flow_booking_token=None), True),
            (NS(candidates=[NS(tipo_vehiculo="MOTO", status="archived")]),
             NS(flow_booking_token=None), False),
            (NS(candidates=[car]), NS(flow_booking_token="7-1794956450-abc"), False),
            (NS(candidates=[car]), None, False),
        ]
        for ctx, state, expected in cases:
            with self.subTest(ctx=ctx, state=state):
                self.assertIs(eng._thread_motorcycle_recorded(ctx, state), expected)

    def test_a_car_thread_is_never_reported_as_motorcycle(self):
        t = W.new_thread(self.eng, vehicle=R4.COROLLA, zone=PALERMO)
        with Session(self.eng) as db:
            eng = CE.ConversationEngine(db=db, settings=self.cfg())
            ctx = eng._load_context(t.tid)
            self.assertFalse(eng._thread_motorcycle_recorded(ctx, ctx.state))
            self.assertIsNotNone(eng._compute_price_quote(ctx, ctx.state))
            db.rollback()


# ══ 5. ordinary car behaviour is unchanged ═══════════════════════════════════════════════

class CarUnchanged(_Base):
    def test_car_is_quoted_then_accepted_into_the_booking_flow(self):
        calls = []
        t = W.new_thread(self.eng)
        r1 = self.turn(t, ["Tengo un Toyota Corolla 2018 en Palermo, ¿cuánto sale?"], calls)
        self.assertIn("Total:", text_of(r1))
        s1 = W.snap(self.eng, t)
        self.assertEqual(s1["flag"], "PRESUPUESTO_ENVIADO")
        self.assertFalse(s1["needs_human"])
        r2 = self.turn(t, [ACCEPT_DAY], calls)
        s2 = W.snap(self.eng, t)
        self.assertEqual((s2["flag"], s2["stage"]), ("ACEPTADO", "SCHEDULING"), s2)
        self.assertEqual(len(r2.flows), 1, r2)                         # the Booking Flow
        with Session(self.eng) as db:
            offers = [o.status for o in db.execute(select(WhatsAppBookingOffer).where(
                WhatsAppBookingOffer.thread_id == t.tid)).scalars()]
        self.assertEqual(offers, ["ISSUED"])
        self.assertFalse(s2["needs_human"])
        self.assertEqual(calls, [])


class CarUnchangedLiveAsync(CarUnchanged):
    LIVE = True


# ══ 6. RV-F-02 (test-only pin): the PERDIDO guard restores what the database holds NOW ═══

class PerdidoGuardReadsTheDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        from app.ui import kanban_actions as KA
        cls.KA = KA

    def perdido_world(self):
        t = W.new_thread(self.eng, vehicle=R4.COROLLA, zone=PALERMO)
        with Session(self.eng) as db:
            self.KA.ui_perdido(lead_id=t.lead_id, motivo_perdida="PRECIO", db=db)
        return t

    def armed(self, t):
        db = W.H.session_factory(self.eng)()
        eng = CE.ConversationEngine(db=db, settings=W.cfg())
        ctx = eng._load_context(t.tid)
        eng._turn_perdido_left_by = None
        eng._arm_perdido_guard(ctx)
        self.assertIsNotNone(eng._turn_perdido_guard)
        db.commit()                                         # a mid-turn commit (expires)
        return db, eng, ctx

    def test_restore_uses_the_operators_concurrent_reason_not_the_armed_one(self):
        t = self.perdido_world()
        db, eng, ctx = self.armed(t)
        try:
            with Session(self.eng) as op_db:
                self.KA.ui_perdido(lead_id=t.lead_id, motivo_perdida="DISPONIBILIDAD", db=op_db)
            ctx.lead.flag = "PRESUPUESTANDO"                 # the engine's side door
            db.flush()
            # in-session, before any reload: the restored pair is the database's current one
            self.assertEqual((ctx.lead.flag, ctx.lead.motivo_perdida),
                             ("PERDIDO", "DISPONIBILIDAD"))
            db.commit()
        finally:
            eng._disarm_perdido_guard()
            db.close()

    def test_an_operator_reopen_is_not_this_guards_business(self):
        t = self.perdido_world()
        db, eng, ctx = self.armed(t)
        try:
            with Session(self.eng) as op_db:
                lead = op_db.get(Lead, t.lead_id)
                lead.flag, lead.motivo_perdida = "PRESUPUESTO_ENVIADO", None
                op_db.commit()
            ctx.lead.flag = "ACEPTADO"
            db.commit()
        finally:
            eng._disarm_perdido_guard()
            db.close()
        self.assertEqual(W.snap(self.eng, t)["flag"], "ACEPTADO")


if __name__ == "__main__":
    unittest.main()
