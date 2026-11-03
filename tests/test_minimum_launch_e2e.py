"""PRELAUNCH-MINIMUM-LAUNCH — test H1: FAQ → quote → Flow → booking, end to end, on SQLite.

Seeded only what the inbound webhook creates on a first message (contact, lead, thread, a
QUALIFYING state — no vehicle, no zone). Everything else is DERIVED by production code from
the customer's words: candidate, locality, FAQ answer, quote, acceptance, availability, the
offer and its token; then the Data Exchange prepare_summary and confirm_booking; then the
acknowledgement and the operator notice.

Edges replaced, and only these: the OpenAI completion (a canned JSON decision returned at
`urllib.request.urlopen`), the Meta text / Flow / template senders (recorders). No socket is
opened: any other `urlopen` raises.
"""
from __future__ import annotations

import dataclasses
import io
import json
import pathlib
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, Lead, OperatorNotification, Revision,  # noqa: E402
                        WhatsAppContact, WhatsAppMessage, WhatsAppThread, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import booking_flow_service as bfs  # noqa: E402

_DAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre")


def target_wednesday() -> date:
    d = date.today() + timedelta(days=3)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


class _AiResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return 200


def _ai(*_a, **_k):
    url = getattr(_a[0], "full_url", _a[0]) if _a else ""
    if "openai" not in str(url):
        raise AssertionError(f"E2E harness: network egress attempted to {url}")
    decision = {"intent": "OTHER", "reply": "Perfecto.", "lead_flag": None,
                "flag_accepted": False, "needs_human": False, "extracted": {},
                "candidate": {"action": "none"}}
    return _AiResponse(json.dumps({"choices": [{"message": {
        "content": json.dumps(decision)}}]}).encode())


class E2E_FaqQuoteFlowBooking(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()
        with Session(self.engine) as db:
            H.ensure_professional(db)
            # Pricing zones, as production holds them (a subset): the locality resolver and the
            # quote both read this table.
            from app.models import ViaticosZone
            for g, d, v in (("CABA", None, 0), ("CABA", "CABA", 0), ("CABA", "Palermo", 0),
                            ("Norte", None, 0), ("Norte", "Benavidez", 0)):
                db.add(ViaticosZone(zone_group=g, zone_detail=d, viaticos=v))
            c = WhatsAppContact(wa_id="5491100000777", display_name="E2E")
            lead = Lead(estado="CONSULTA_NUEVA", flag="PRESUPUESTANDO", necesita_humano=False)
            db.add_all([c, lead]); db.flush()
            t = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
            db.add(t); db.flush()
            db.add(WhatsAppThreadState(thread_id=t.id, needs_human=False, last_stage="QUALIFYING",
                                       vehicle_clarification_sent=False,
                                       location_clarification_sent=False,
                                       vehicle_fallback_flow_sent=False,
                                       location_fallback_flow_sent=False))
            db.commit()
            self.thread_id, self.lead_id, self.wa_id = t.id, lead.id, c.wa_id
        # The probe's five-flag launch posture: the three W1F conversation corrections are an
        # interlock (`_integrated_features_armed`) and are armed only together.
        self.cfg = dataclasses.replace(H.settings(flag=True), openai_api_key="sk-fixture-not-real",
                                       booking_flow_id="FLOW_E2E_FIXTURE",
                                       evidence_admission_enabled=True,
                                       outstanding_question_enabled=True,
                                       locality_resolution_safety_enabled=True)

    def turn(self, n, text):
        ev = ConversationHandleIn(thread_id=self.thread_id, wa_message_id=f"wamid.E2E.IN.{n}",
                                  wa_id=self.wa_id, text=text, recent_user_messages=[text],
                                  unanswered_recent_user_messages=[text])
        out, _eng = H.run_turn(self.engine, ev, cfg=self.cfg)
        return out

    def texts(self):
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppMessage.message_type, WhatsAppMessage.text,
                                     WhatsAppMessage.path_id)
                              .where(WhatsAppMessage.thread_id == self.thread_id,
                                     WhatsAppMessage.direction == "out")
                              .order_by(WhatsAppMessage.id)).all()

    def test_h1_faq_quote_flow_booking_ack_and_operator_notice(self):
        from test_minimum_launch import Meta
        from test_w1f_operator_signal import notifier, set_floor
        from app.services import operator_notification as ON
        day = target_wednesday()
        phrase = f"el {_DAYS[day.weekday()]} {day.day} de {_MONTHS[day.month - 1]} a las 11"
        meta = Meta()
        with meta, mock.patch("urllib.request.urlopen", _ai), \
                mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                           meta._flow, create=True):
            self.turn(1, "Hola, quiero una revision para un Ford Focus 2019 que estoy por "
                         "comprar en Palermo. Se puede pagar en efectivo?")
            self.turn(2, "si, me sirve el precio")
            out3 = self.turn(3, f"puede ser {phrase}?")
        replies = [t for k, t, _p in self.texts() if k == "text"]
        self.assertTrue(any("$" in (t or "") for t in replies), replies)        # the quote
        self.assertTrue(any("efectivo" in (t or "").lower() for t in replies), replies)  # FAQ
        self.assertEqual(out3.action, "flow_button_sent", out3)
        self.assertEqual(len(meta.flows), 1)
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.thread_id)).scalar_one()
            token = st.flow_booking_token
        self.assertTrue(token)
        details = {"date": day.isoformat(), "time": "11:00", "name": "Elena Fixture",
                   "email": "elena@example.invalid", "inspection_address": "Honduras 4000",
                   "seller_name": "Concesionaria Fixture", "seller_phone": "1100000001",
                   "listing_url": "https://example.invalid/aviso/e2e"}
        # A real customer spends minutes between the quote and the confirmation; this test
        # spends milliseconds, so the gate's per-recipient flood limit (3 per 60 s) would hold
        # the acknowledgement (measured: GATE_blocked_flood). The window is widened for the
        # booking step only; every other gate rule applies unchanged.
        with Meta() as m2, Session(self.engine) as db, mock.patch(
                "app.settings.get_settings", lambda: self.cfg), mock.patch(
                "app.services.outbound_safety_gate.FLOOD_MAX_MESSAGES", 50):
            svc = bfs.BookingFlowService(db)
            self.assertEqual(svc.handle_prepare_summary(token, details)["screen"], "SUMMARY")
            self.assertEqual(bfs.BookingFlowService(db).handle_confirm_booking(
                token, details)["screen"], "SUCCESS")
        self.assertEqual([t for _w, t in m2.sent], [H.OWNER_TEXT])            # one ack
        with Session(self.engine) as db:
            revs = db.query(Revision).filter(Revision.lead_id == self.lead_id).all()
            self.assertEqual([(r.turno_fecha, r.turno_hora.strftime("%H:%M")) for r in revs],
                             [(day, "11:00")])
            self.assertEqual(revs[0].direccion_texto, "Honduras 4000")
            self.assertEqual(db.query(BookingCommandRecord).count(), 1)
        # the CRM card shows it
        from app.services.lead_booking_details import lead_booking_details
        with Session(self.engine) as db:
            d = lead_booking_details(db, [self.lead_id])[self.lead_id]
            self.assertEqual((d.customer_name, d.inspection_address), ("Elena Fixture",
                                                                       "Honduras 4000"))
            self.assertIn("Ford", d.vehicle or "")
        # exactly one operator notice, from the committed booking
        set_floor(self.engine, "0")
        with notifier() as tpl:
            db = H.session_factory(self.engine)()
            try:
                ON.run_once(db)
                ON.run_once(db)
            finally:
                db.close()
        with Session(self.engine) as db:
            self.assertEqual([(n.status, n.attempts) for n in
                              db.query(OperatorNotification).all()], [("SENT", 1)])
        self.assertEqual(len(tpl.sent), 1)


if __name__ == "__main__":
    unittest.main()
