"""WILD1-C — the H-1 live conversation as a regression fixture (operator message 6272 excluded).

Customer words are the H-1 thread's own (thread 2078, messages 6269–6285; 6269/6270 were
voice notes, shown here as n8n's transcription). The interpreter is scripted with what the
live interpreter would plausibly return for each burst — the topics are ITS labels, so the
production path that reads them is the one exercised; no production code sees this script.
"""
from __future__ import annotations

import os
import unittest

import _wild1c_harness as W

T1 = ["Hola, buen día. Yo soy de Tigre, pero tengo un auto en Berazategui. Quería saber si "
      "ustedes hacían eso. Básicamente revisaban autos.",
      "¿De qué consta el servicio? ¿Qué onda? ¿Se puede pagar con efectivo o tienen tarjetas "
      "de débito?"]
T2 = ["Si jeep renegade 2024", "Te hice otras preguntas que no me contestaste"]
T3 = ["Me dirías de qué consta la revisión !?", "Me parece caro, cuánto hay de viático ahí ?"]
T4 = ["Estuve averiguando por otro lado y están en precio. Para mañana tenes horarios ?",
      "Me decís cuánto es de viatico", "Y que informan en la revisión ?"]
T5 = ["Si"]
T6 = ["Mañana"]

SCRIPT = [
    ("Yo soy de Tigre", W.payload(inspection=True, faq=("service_scope", "payment"),
                                  locations=(("Tigre", "CUSTOMER_ORIGIN"),
                                             ("Berazategui", "INSPECTION_LOCATION")))),
    ("jeep renegade", W.payload(vehicle=("Jeep", "Renegade", 2024))),
    ("Me parece caro", W.payload(faq=("service_scope",), quote=True, accept="HESITATE")),
    ("Estuve averiguando", W.payload(faq=("report",), quote=True, day="TOMORROW",
                                     availability=True)),
    ("- Si", W.payload(accept="ACCEPT")),
    ("- Mañana", W.payload(day="TOMORROW")),
]
#: The H-1 quote, as PricingService computes it: 150.000 + 90.000 = 240.000.
BLOCK = W.block(150_000, 90_000, 240_000)
SCOPE = W.CE._FAQ_SCOPE_LEAD
REPORT = W.CE._FAQ_REPORT_ANSWER
PAYMENT = W.CE._FAQ_PAYMENT_ANSWER


class H1Replay(unittest.TestCase):
    """Runs the six customer bursts in order on one thread; each test reads its turn."""

    @classmethod
    def setUpClass(cls):
        cls.eng = W.engine()
        cls.t = W.new_thread(cls.eng)
        cls.interp = W.Interpreter(SCRIPT)
        cls.turns = {}
        cls.snaps = {}
        for name, texts in (("T1", T1), ("T2", T2), ("T3", T3), ("T4", T4), ("T5", T5),
                            ("T6", T6)):
            ev = W.burst(cls.eng, cls.t, texts,
                         message_type=("audio" if name == "T1" else "text"))
            cls.turns[name] = W.run(cls.eng, ev, interp=cls.interp)
            cls.snaps[name] = W.snap(cls.eng, cls.t)
        if os.environ.get("WILD1C_PRINT"):
            for name, r in cls.turns.items():
                print(f"\n===== {name} action={r.out.action} flag={cls.snaps[name]['flag']} "
                      f"stage={cls.snaps[name]['stage']} cov="
                      f"{r.coverage.summary() if r.coverage else None}")
                for s in r.sent:
                    print("TEXT>", s)
                for f in r.flows:
                    print("FLOW>", f)

    def text(self, name):
        r = self.turns[name]
        return "\n".join(list(r.sent) + [f for f in r.flows if f])

    # ── turn 1: inspection scope + payment (cash, debit), voice-note transcription ──
    def test_t1_answers_scope_and_payment_beside_the_vehicle_question(self):
        t = self.text("T1")
        self.assertIn(SCOPE, t)
        self.assertIn(REPORT, t)                    # what the report contains
        self.assertIn(PAYMENT, t)                   # cash yes, debit no
        self.assertIn("¿Me contás qué vehículo querés revisar?", t)
        self.assertTrue(self.turns["T1"].coverage.complete)

    # ── turn 2: the vehicle → the quote, as the three-value block ──────────────
    def test_t2_quote_shows_revision_viaticos_total(self):
        t = self.text("T2")
        self.assertIn(BLOCK, t)
        self.assertNotIn("es de $240.000", t)       # never a total alone
        self.assertEqual(self.snaps["T2"]["flag"], "PRESUPUESTO_ENVIADO")
        self.assertEqual(self.snaps["T2"]["stage"], "QUOTED")

    def test_t2_block_reaches_the_ledger_byte_exact(self):
        quotes = [o[2] for o in self.snaps["T2"]["outbound"] if o[2] and "Total:" in o[2]]
        self.assertTrue(quotes and BLOCK in quotes[-1], quotes)

    # ── turn 3: scope asked AGAIN + price concern + viáticos ───────────────────
    def test_t3_answers_viaticos_and_repeated_scope(self):
        t = self.text("T3")
        self.assertIn(BLOCK, t)
        self.assertIn(SCOPE, t)
        self.assertNotIn("Gracias por tu mensaje. Cualquier otra consulta", t)  # H-1 6278
        self.assertTrue(self.turns["T3"].coverage.complete)

    def test_t3_price_concern_is_not_a_decline(self):
        s = self.snaps["T3"]
        self.assertEqual(s["flag"], "PRESUPUESTO_ENVIADO")
        self.assertIsNone(s["motivo"])
        self.assertEqual(s["decisions"], [])
        self.assertFalse(s["needs_human"])

    # ── turn 4: reconsiders, asks tomorrow + viáticos + report → the Booking Flow ──
    def test_t4_flow_carries_every_owed_answer_first(self):
        r = self.turns["T4"]
        self.assertEqual(r.out.action, "flow_button_sent")
        self.assertEqual(len(r.flows), 1)
        body = r.flows[0]
        self.assertIn(BLOCK, body)
        self.assertIn(REPORT, body)
        self.assertLess(body.index(BLOCK), body.index("Para mañana"))
        self.assertLess(body.index(REPORT), body.index("Para mañana"))
        self.assertLessEqual(len(body), W.CE._FLOW_BODY_MAX)
        self.assertTrue(r.coverage.complete, r.coverage.summary())

    def test_t4_no_premature_ese_horario(self):
        self.assertNotIn("Ese horario está disponible", self.turns["T4"].flows[0])
        # WILD1-C-R2: the required Flow-first wording, no confirmation claim
        self.assertIn("tengo 5 horarios disponibles. Elegí el horario que te sirva y "
                      "completá tus datos para solicitar el turno.", self.turns["T4"].flows[0])
        self.assertNotIn("confirm", self.turns["T4"].flows[0].lower())
        for name in self.turns:
            self.assertNotIn("Ese horario está disponible", self.text(name))

    def test_t4_availability_is_not_a_booking(self):
        s = self.snaps["T4"]
        self.assertEqual((s["flag"], s["stage"]), ("ACEPTADO", "SCHEDULING"))
        self.assertEqual((s["booked"], s["ledger"]), (0, 0))
        self.assertIsNotNone(s["token"])

    # ── turns 5–6: "Si" / "Mañana" with the Flow open → no second Flow ─────────
    def test_t5_t6_no_duplicate_flow_and_no_confirmation(self):
        for name in ("T5", "T6"):
            r = self.turns[name]
            self.assertEqual(r.flows, [], name)
            self.assertNotIn("confirmado", self.text(name).lower())
            self.assertTrue(r.coverage is None or r.coverage.complete,
                            r.coverage and r.coverage.summary())
        self.assertEqual(self.snaps["T6"]["booked"], 0)

    # ── every turn ─────────────────────────────────────────────────────────────
    def test_no_hostile_model_body_and_no_egress(self):
        for name, r in self.turns.items():
            t = self.text(name)
            self.assertNotIn("$99.999", t, name)
            self.assertNotIn("Aceptamos débito", t, name)
            self.assertEqual(r.egress, [], name)

    def test_no_operator_notification_and_no_handoff(self):
        for name in self.snaps:
            self.assertFalse(self.snaps[name]["needs_human"], name)
            self.assertFalse(self.snaps[name]["necesita_humano"], name)

    def test_all_amounts_reconcile(self):
        import re
        for name in self.turns:
            t = self.text(name)
            if "Total:" not in t:
                continue
            amt = lambda label: int(re.search(label + r": \$([\d.]+)", t).group(1)  # noqa
                                    .replace(".", ""))
            self.assertEqual(amt("Revisión") + amt("Viáticos"), amt("Total"), name)


class H1BookingCompletes(unittest.TestCase):
    """After the H-1 replay, the Flow is completed through the production Data Exchange
    confirm and the Flow response turn: booking authority and persistence are unchanged."""

    #: WILD1-C-R3: days ahead of the confirmed slot. The live-async twin
    #: (`test_wild1_c_live_async_posture`) books one day later, so on a shared PostgreSQL
    #: database the two runs never contend for the one launch professional's slot.
    CONFIRM_DAYS_AHEAD = 1

    def test_flow_completion_books_once_and_acknowledges(self):
        from datetime import date, timedelta
        from sqlalchemy.orm import Session
        from app.models import WhatsAppThreadState
        from app.services.booking_flow_service import BookingFlowService
        eng = W.engine()
        t = W.new_thread(eng)
        interp = W.Interpreter(SCRIPT)
        for texts in (T1, T2, T3, T4):
            W.run(eng, W.burst(eng, t, texts), interp=interp)
        with Session(eng) as db:
            token = db.query(WhatsAppThreadState).filter_by(thread_id=t.tid).one() \
                .flow_booking_token
        self.assertIsNotNone(token)
        day = date.today() + timedelta(days=self.CONFIRM_DAYS_AHEAD)
        settings = W.cfg()
        from unittest import mock
        with Session(eng) as db, mock.patch("app.settings.get_settings", lambda: settings):
            res = BookingFlowService(db).handle_confirm_booking(token, {
                "date": day.isoformat(), "time": "11:00", "name": "Ana Fixture",
                "email": "ana@example.invalid", "inspection_address": "Calle Falsa 123"})
        self.assertIsNotNone(res)
        s = W.snap(eng, t)
        self.assertEqual(s["booked"], 1, s)
        self.assertEqual(s["ledger"], 1, s)
