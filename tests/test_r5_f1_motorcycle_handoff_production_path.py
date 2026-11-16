"""R5-F1 — the motorcycle contact-form handoff, through the REAL production path.

The final-WILD kit review reproduced R5-F1 on f16b1f3: after a motorcycle contact Flow is
submitted, `_MOTORCYCLE_ADVISOR_REPLY` ("A la brevedad uno de nuestros asesores…") reached
the response validator, the reply vehicle resolver read "uno" as Fiat Uno, the sentence was
dropped and `_FALLBACK_REPLY` ("…¿Me contás qué vehículo querés revisar y en qué zona
está?") was sent instead — and, with the R2 contract armed, that fallback opened an
outstanding INSPECTION_LOCATION question on a thread that had just been handed to a person.

The existing motorcycle suites stub `_send_text_to_wa`, so none of them reached the
validator; that is how the defect shipped. Every turn here goes through
`POST /api/conversation/handle`'s route function, the real `ConversationEngine`, the real
`_send_text_to_wa` → `_validate_outbound_text` → `OutboundSafetyGate`, on a fresh SQLite
database. Only the Meta edge is a recorder (`_sb2b_harness.Meta`); any other egress raises.

This file deliberately imports nothing introduced by the R5-F1 correction, so it can be run
unchanged against the pristine base as the red control.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import os
import pathlib
import sys
import unittest
from datetime import datetime, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DATABASE_URL", "sqlite://")

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, BookingCommandRecord, Lead, OperatorNotification,  # noqa: E402
                        SlotRecoveryJob, ThreadRevision, WhatsAppBookingOffer,
                        WhatsAppContact, WhatsAppMessage, WhatsAppOutstandingQuestion,
                        WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services.conversation_engine import (_MOTORCYCLE_ADVISOR_REPLY,  # noqa: E402
                                              _MOTO_HANDOFF_TOKEN_PREFIX,
                                              STAGE_QUALIFYING)
from app.services.response_validator import _FALLBACK_REPLY  # noqa: E402

_SEQ = itertools.count(1)
EXPECTED = "A la brevedad uno de nuestros asesores se estará contactando con vos."


def _cfg(*, smart_booking: bool):
    """The launch posture for this path: the R1/R2/R3 interlock ARMED (so a question WOULD
    be recorded if the reply asked one), the client-data Flow configured (so the motorcycle
    handoff is the Flow path, the one R5-F1 is on), operator WhatsApp notification off."""
    return dataclasses.replace(
        H.settings(flag=smart_booking),
        evidence_admission_enabled=True, outstanding_question_enabled=True,
        locality_resolution_safety_enabled=True,
        operator_whatsapp_notify_enabled=False, operator_whatsapp_to="",
        booking_flow_details_prefill_enabled=False)


@dataclasses.dataclass
class MotoWorld:
    thread_id: int
    lead_id: int
    wa_id: str
    token: str
    wamid: str
    candidate_id: int


def _seed_moto(engine, *, marca="Honda", modelo="CB500", focus: bool = False) -> MotoWorld:
    """A thread in the state `_motorcycle_human_handoff` leaves it in on the Flow path:
    a 'mentioned' MOTO candidate, a `moto-handoff-` token, needs_human still false."""
    n = next(_SEQ)
    with Session(engine) as db:
        contact = WhatsAppContact(wa_id=f"54911{70000000 + n:08d}", display_name="Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False)
        db.add_all([contact, lead]); db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0)
        db.add(thread); db.flush()
        cand = WhatsAppThreadCandidate(thread_id=thread.id, tipo_vehiculo="MOTO",
                                       marca=marca, modelo=modelo, status="mentioned",
                                       source_text=f"moto {marca} {modelo}")
        db.add(cand); db.flush()
        token = f"{_MOTO_HANDOFF_TOKEN_PREFIX}{thread.id}-1700000000"
        db.add(WhatsAppThreadState(
            thread_id=thread.id, last_stage=STAGE_QUALIFYING, needs_human=False,
            flow_booking_token=token,
            current_focus_candidate_id=(cand.id if focus else None),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        wamid = f"wamid.R5F1.IN.{n:06d}"
        data = {"flow_token": token, "nombre_apellido": "Ana Fixture",
                "email": f"ana{n}@example.invalid", "como_llego": "Instagram"}
        db.add(WhatsAppMessage(
            thread_id=thread.id, direction="in", wa_message_id=wamid,
            message_type="flow_response", timestamp=datetime.now(timezone.utc),
            status="received",
            raw_payload={"from": contact.wa_id, "id": wamid, "type": "interactive",
                         "interactive": {"type": "nfm_reply", "nfm_reply": {
                             "response_json": json.dumps(data), "name": "flow"}}}))
        db.add(AiEvent(thread_id=thread.id, wa_message_id=wamid, wa_id=contact.wa_id,
                       status="triggered"))
        db.commit()
        return MotoWorld(thread.id, lead.id, contact.wa_id, token, wamid, cand.id)


def _event(w: MotoWorld) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=w.thread_id, wa_message_id=w.wamid, wa_id=w.wa_id,
        message_type="flow_response",
        flow_response={"flow_token": w.token, "nombre_apellido": "Ana Fixture",
                       "email": "ana@example.invalid", "como_llego": "Instagram"},
        flow_token=w.token)


def _count(db, model, **where) -> int:
    q = select(func.count()).select_from(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return db.execute(q).scalar_one()


class R5F1ProductionPath(unittest.TestCase):
    """A + F: Honda CB500 contact form → the exact handoff, through the production route."""

    SMART_BOOKING = False
    FOCUS = False

    def setUp(self):
        self.engine = H.sqlite_engine()
        self.w = _seed_moto(self.engine, focus=self.FOCUS)
        with H.Meta() as meta:
            self.out, self.ce = H.run_turn(self.engine, _event(self.w),
                                           cfg=_cfg(smart_booking=self.SMART_BOOKING))
        self.meta = meta

    def _outbound(self):
        with Session(self.engine) as db:
            return db.execute(select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == self.w.thread_id,
                WhatsAppMessage.direction == "out").order_by(WhatsAppMessage.id)).scalars().all()

    def test_r5f1_a01_the_route_handled_the_turn_as_a_reply(self):
        self.assertEqual(getattr(self.out, "action", None), "replied", self.out)

    def test_r5f1_a02_the_customer_receives_exactly_the_approved_handoff(self):
        self.assertEqual(_MOTORCYCLE_ADVISOR_REPLY, EXPECTED, "the approved copy itself moved")
        self.assertEqual([t for _to, t in self.meta.sent], [EXPECTED],
                         "Meta must receive the approved handoff, verbatim, once")
        self.assertEqual(self.meta.egress, [])

    def test_r5f1_a03_the_ledger_records_the_same_text_on_ce_text(self):
        outs = self._outbound()
        self.assertEqual([(o.path_id, o.message_type, o.text) for o in outs],
                         [("CE_TEXT", "text", EXPECTED)])
        self.assertEqual(outs[0].status, "sent")

    def test_r5f1_a04_no_vehicle_mismatch_fallback_anywhere(self):
        texts = [o.text for o in self._outbound()] + [t for _to, t in self.meta.sent]
        self.assertTrue(texts)
        for t in texts:
            self.assertNotEqual(t, _FALLBACK_REPLY)
            self.assertNotIn("qué vehículo querés revisar", t)
            self.assertNotIn("en qué zona está", t)

    def test_r5f1_a05_needs_human_is_true_on_state_and_lead(self):
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.w.thread_id)).scalar_one()
            lead = db.get(Lead, self.w.lead_id)
            self.assertIs(st.needs_human, True)
            self.assertIs(lead.necesita_humano, True)
            self.assertIsNone(st.flow_booking_token, "the moto token is consumed")
            self.assertEqual(lead.nombre, "Ana")

    def test_r5f1_a06_no_outstanding_question_is_opened(self):
        self.assertTrue(self.ce._integrated_features_armed(),
                        "control: the R2 contract must be ARMED, or this proves nothing")
        with Session(self.engine) as db:
            self.assertEqual(_count(db, WhatsAppOutstandingQuestion,
                                    thread_id=self.w.thread_id), 0)

    def test_r5f1_a07_no_booking_flow_offer_ledger_notice_or_recovery_job(self):
        with Session(self.engine) as db:
            self.assertEqual(_count(db, ThreadRevision, thread_id=self.w.thread_id), 0)
            self.assertEqual(_count(db, BookingCommandRecord, thread_id=self.w.thread_id), 0)
            self.assertEqual(_count(db, WhatsAppBookingOffer), 0)
            self.assertEqual(_count(db, OperatorNotification), 0)
            self.assertEqual(_count(db, SlotRecoveryJob), 0)
            self.assertEqual(_count(db, WhatsAppMessage, thread_id=self.w.thread_id,
                                    direction="out", message_type="flow"), 0,
                             "no Flow (booking or other) is dispatched by the handoff")

    def test_r5f1_a08_moto_evidence_is_preserved(self):
        with Session(self.engine) as db:
            c = db.get(WhatsAppThreadCandidate, self.w.candidate_id)
            self.assertEqual((c.tipo_vehiculo, c.marca, c.modelo), ("MOTO", "Honda", "CB500"))


class R5F1ProductionPathSmartBookingOn(R5F1ProductionPath):
    """The same handoff with SMART_BOOKING_CE_ENABLED on: the booking authority must not
    be reached from a motorcycle contact form either way."""
    SMART_BOOKING = True


class R5F1ProductionPathMotoFocus(R5F1ProductionPath):
    """The Honda CB500 is the CURRENT FOCUS, so the validator's canonical vehicle is
    'honda cb500' — the exact posture in which the review measured "'fiat uno' differs
    from canonical 'honda cb500'"."""
    FOCUS = True

    def test_r5f1_a09_control_the_canonical_vehicle_is_the_motorcycle(self):
        from app.services.conversation_engine import _Context  # noqa: F401  (import check)
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.w.thread_id)).scalar_one()
            self.assertEqual(st.current_focus_candidate_id, self.w.candidate_id)


class R5F1NoFlowMotorcycleHandoff(unittest.TestCase):
    """The OTHER registered template, `_MOTORCYCLE_HANDOFF_REPLY`: a customer text naming a
    motorcycle with WHATSAPP_FLOW_ID unset, through the same production route. It passed on
    f16b1f3 too (no catalogue token in it); this pins that it still does, unchanged."""

    def setUp(self):
        from app.services.conversation_engine import _MOTORCYCLE_HANDOFF_REPLY
        self.expected = str(_MOTORCYCLE_HANDOFF_REPLY)
        self.engine = H.sqlite_engine()
        n = next(_SEQ)
        with Session(self.engine) as db:
            contact = WhatsAppContact(wa_id=f"54911{80000000 + n:08d}", display_name="Fixture")
            lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False)
            db.add_all([contact, lead]); db.flush()
            thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0)
            db.add(thread); db.flush()
            wamid = f"wamid.R5F1.TXT.{n:06d}"
            text = "Hola, quiero revisar una moto Honda CB500"
            db.add(WhatsAppMessage(thread_id=thread.id, direction="in", wa_message_id=wamid,
                                   message_type="text", text=text,
                                   timestamp=datetime.now(timezone.utc), status="received"))
            db.add(AiEvent(thread_id=thread.id, wa_message_id=wamid, wa_id=contact.wa_id,
                           status="triggered"))
            db.commit()
            self.thread_id, self.lead_id = thread.id, lead.id
            event = ConversationHandleIn(thread_id=thread.id, wa_message_id=wamid,
                                         wa_id=contact.wa_id, message_type="text", text=text)
        cfg = dataclasses.replace(_cfg(smart_booking=False), whatsapp_flow_id="")
        with H.Meta() as meta:
            self.out, self.ce = H.run_turn(self.engine, event, cfg=cfg)
        self.meta = meta

    def test_r5f1_b01_the_no_flow_handoff_is_sent_verbatim_and_needs_human(self):
        self.assertEqual([t for _to, t in self.meta.sent], [self.expected])
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.thread_id)).scalar_one()
            self.assertIs(st.needs_human, True)
            self.assertEqual(_count(db, WhatsAppOutstandingQuestion, thread_id=self.thread_id), 0)
            self.assertEqual(_count(db, ThreadRevision, thread_id=self.thread_id), 0)
            self.assertEqual(_count(db, OperatorNotification), 0)
            self.assertEqual(_count(db, SlotRecoveryJob), 0)


if __name__ == "__main__":
    unittest.main()
