"""R6-C4A — the transactional provenance boundary.

Transactional truth is authorised by PROVENANCE and canonical structured facts, not by a
successful parse of generated prose. On a transactional route (QUOTATION, SCHEDULING,
FLOW_PENDING, BOOKED — classified from the reply role and canonical state only) the reply
model's body is discarded at `ConversationEngine._apply_transactional_boundary`, the one
boundary every reply decision crosses, and the canonical deterministic renderer is sent.

What is proven here, on the FULL production path (`handle_message` → CE → reply model with a
canned hostile body → reply admission → boundary → `_send_text_to_wa` → validation →
`plan.compose` → OutboundSafetyGate → Meta recorder), in all four launch postures:

  A. every R6-C3 reviewer counterexample, as the model's body, never reaches the customer on
     a transactional route — and neither does a CANONICAL body;
  B. completely novel temporal / price / booking nonsense that no recogniser knows never
     reaches the customer either: the safety comes from provenance, not recognition;
  C. each route renders from canonical facts (PricingService quote, ScheduleService-free day
     question, the outstanding Flow, the committed revision) with its StructuredClaims
     validated, and missing facts give the deterministic clarification or a handoff;
  D. no state is derived from the discarded body (no preferred day/time, no revision, no
     offer, no outstanding question from the model), and the model's extracted scheduling
     fields are never state;
  E. ordinary FAQ prose still reaches the customer verbatim (non-transactional route);
  F. structure: one model call site, one boundary, a typed send gate, a send-side tripwire,
     renderers built only from canonical sources.

Database: SQLite in memory; set R6_PG_URL to run the identical cases on PostgreSQL.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import unittest
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (the shared harness module)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, WhatsAppBookingOffer,  # noqa: E402
                        WhatsAppContact, WhatsAppMessage, WhatsAppOutstandingQuestion,
                        WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import response_validator as RV  # noqa: E402
from app.services import transactional_boundary as TB  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.response_validator import (CanonicalFacts, ComposedText,  # noqa: E402
                                             DomainClaim, StructuredClaim, composed_text,
                                             validate_envelope)
from app.services.transactional_boundary import TransactionalRoute as R  # noqa: E402

H = C.H
NOON_WED = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)      # BA Wed 11 12:00
MON = "2026-11-16"
SLOTS = ["10:00", "11:00", "15:00", "15:30"]
BOOKED = (date(2026, 11, 16), time(10, 0))
SRC = (_ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text(
    encoding="utf-8")

#: A. the R6-C3 reviewer counterexamples (audit §4/§5), as the MODEL's body
REVIEWER = (
    "Te esperamos el lunes veintitrés a las 10:00.",
    "Te esperamos el 2026/11/23 a las 10:00.",
    "Te esperamos en 12 días a las 10:00.",
    "Te esperamos el próximo lunes a las 10:00.",
    "Te esperamos el lunes 16 a las diez y veinte.",
    "No hay turnos el jueves, sí el lunes 23 a las 10:00.",
)
#: B. novel nonsense no recogniser knows (no digit, no weekday, no month, no clock word)
NOVEL = (
    "Te esperamos el plurdi de zorbembre cuando suene la ñeque.",
    "Listo, quedó agendado para el día del ganso, después del mediodía largo.",
    "Tu reserva glorp quedó confirmada, nos vemos allá.",
    "La revisión te sale tres mangos con cincuenta, ya quedó.",
    "Tu turno fue cancelado y pasado al jueves que no es jueves.",
)
#: the CANONICAL body: before R6-C4A this was delivered verbatim on QUOTED
CANONICAL = ("Te esperamos el lunes 16 a las 10:00.",)
HOSTILE = REVIEWER + NOVEL + CANONICAL

#: R6-C4B-R1. The interpreter body is discarded on EVERY route and a historical body is never
#: a claim-free bridge (`tests/_c4b_model_fakes.py`): every turn below delivers the canonical
#: deterministic message, whatever the body says.

_PRICE_Q = "¿Cuánto sale la revisión?"
_HANDOFF = "Para no equivocarme con tu caso, lo sigue un asesor de RideCheck. Te contactamos a la brevedad."
_THINK = "Perfecto, gracias. Lo voy a pensar y te aviso"


def _decision(reply, extracted=None):
    d = json.loads(C._decision(reply))
    if extracted:
        d["extracted"].update(extracted)
    return json.dumps(d)


def _seed(eng, *, stage="QUOTED", flag="PRESUPUESTO_ENVIADO", zone=True, booked=None,
          revision_status="booked", token=None, date_iso=MON):
    n = next(C._SEQ)
    with Session(eng) as db:
        c = WhatsAppContact(wa_id=f"54900{84000000 + n:08d}", display_name="R6C4A Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False, flag=flag)
        db.add_all([c, lead]); db.flush()
        th = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
        db.add(th); db.flush()
        cand = WhatsAppThreadCandidate(thread_id=th.id, tipo_vehiculo="AUTO", marca="Toyota",
                                       modelo="Corolla", anio=2018, status="current_focus",
                                       source_text="Toyota Corolla 2018")
        db.add(cand); db.flush()
        rev_id = None
        if booked is not None:
            rev = ThreadRevision(thread_id=th.id, candidate_id=cand.id, status=revision_status,
                                 scheduled_date=booked[0], scheduled_time=booked[1],
                                 marca="Toyota", modelo="Corolla", anio=2018, zone_group="CABA")
            db.add(rev); db.flush(); rev_id = rev.id
        db.add(WhatsAppThreadState(
            thread_id=th.id, last_stage=stage, needs_human=False,
            current_focus_candidate_id=cand.id, current_revision_id=rev_id,
            home_zone_group=("CABA" if zone else None),
            home_zone_detail=("Palermo" if zone else None),
            flow_booking_token=token, active_requested_date=date_iso,
            last_offered_slots=json.dumps(SLOTS), last_visible_slots=json.dumps(SLOTS),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        db.commit()
        return th.id, c.wa_id


def _turn(eng, tid, wa, customer, body, sb, direct, *, extracted=None, now=NOON_WED):
    n = next(C._SEQ)
    wamid = f"wamid.R6C4A.IN.{n:07d}"
    with Session(eng) as db:
        db.add(WhatsAppMessage(thread_id=tid, direction="in", wa_message_id=wamid,
                               message_type="text", text=customer, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=tid, wa_message_id=wamid, wa_id=wa, status="triggered"))
        db.commit()
    ev = ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                              message_type="text", text=customer)

    def hook(ce):
        ce._call_openai_inner = lambda messages: _decision(body, extracted)
    with mock.patch.object(RV, "_utcnow", lambda: now), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                       lambda *a, **k: (f"wamid.R6C4A.FLOW.{next(C._FSEQ):06d}", 200)), \
            H.Meta() as m:
        out, ce = H.run_turn(eng, ev, cfg=C._cfg(sb, direct), engine_hook=hook)
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        lead = db.get(Lead, db.get(WhatsAppThread, tid).lead_id)
        oqs = db.execute(select(WhatsAppOutstandingQuestion).where(
            WhatsAppOutstandingQuestion.thread_id == tid)).scalars().all()
        env = getattr(ce, "_turn_outbound_envelope", None)
        count = lambda M: db.execute(select(func.count()).select_from(M).where(  # noqa: E731
            M.thread_id == tid)).scalar_one()
        return dict(
            action=getattr(out, "action", None), ai_calls=getattr(ce, "_reply_calls", 0),
            armed=bool(ce._integrated_features_armed()),
            route=getattr(getattr(ce, "_turn_route", None), "name", None),
            discarded=getattr(ce, "_discarded_ai_reply", None),
            sent=[t for _w, t in m.sent], egress=len(m.egress), flows=len(m.flows)
            if hasattr(m, "flows") else None,
            needs_human=st.needs_human, stage=st.last_stage, flag=lead.flag,
            preferred_time=st.preferred_time, preferred_day=st.preferred_day,
            pending_fuzzy=st.pending_fuzzy_catalog_key, token=st.flow_booking_token,
            oq=[(q.question_kind, q.asked_by) for q in oqs],
            revisions=count(ThreadRevision), offers=count(WhatsAppBookingOffer),
            provenance=getattr(getattr(env, "provenance", None), "name", None),
            findings=[(f.claim, f.allowed) for f in getattr(env, "findings", ())])


def _norm(s):
    return " ".join(str(s).split()).casefold()


class _Base(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = C._engine()

    def discarded(self, body, r, *, route, expect=None, startswith=None, revisions=0,
                  needs_human=False):
        """The model was consulted, its body was discarded, the canonical renderer was sent,
        and nothing the model wrote reached the customer or the state."""
        self.assertTrue(r["armed"], r)
        self.assertEqual(r["ai_calls"], 1, f"the turn must reach the reply model: {r}")
        self.assertEqual(r["egress"], 0, r)
        self.assertEqual(r["route"], route, r)
        self.assertEqual(r["discarded"], body, f"the body was not discarded at the boundary: {r}")
        self.assertEqual(len(r["sent"]), 1, r)
        sent = r["sent"][0]
        self.assertNotIn(_norm(body), _norm(sent), f"model body delivered: {r}")
        for word in body.replace(",", " ").replace(".", " ").split():
            if len(word) >= 6 and word.casefold() not in _norm(expect or startswith or ""):
                self.assertNotIn(word.casefold(), sent.casefold(), f"model word delivered: {r}")
        if expect is not None:
            self.assertEqual(sent, expect, r)
        if startswith is not None:
            self.assertTrue(sent.startswith(startswith), r)
        self.assertIsNone(r["preferred_time"], f"model time became state: {r}")
        self.assertIsNone(r["preferred_day"], f"model day became state: {r}")
        self.assertIsNone(r["pending_fuzzy"], r)
        self.assertEqual(r["revisions"], revisions, r)
        self.assertEqual(r["offers"], 0, f"a booking offer was created: {r}")
        self.assertEqual(r["needs_human"], needs_human, r)
        for _kind, by in r["oq"]:
            self.assertNotEqual(by, "REPLY_MODEL", f"the model opened a question: {r}")

    def each(self, bodies, fn):
        for body in bodies:
            for sb, direct in C.POSTURES:
                with self.subTest(body=body, sb=sb, direct=direct):
                    fn(body, sb, direct)


# ── A/B/C/D: each transactional route, hostile and canonical bodies, 4 postures ─────────

_QUOTE = "Genial! La cotización para la revisión del Toyota Corolla 2018 en Palermo es de "


class T01_Quotation(_Base):
    """QUOTED stage: the PricingService quote, never the model's words."""

    def test_reviewer_novel_and_canonical_bodies_are_discarded(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng)
            r = _turn(self.eng, tid, wa, _THINK, body, sb, direct)
            self.discarded(body, r, route="QUOTATION", startswith=_QUOTE)
            self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
            self.assertIn(("PRICE", True), r["findings"], "the quote's PRICE claim validated")
            self.assertEqual(r["flag"], "PRESUPUESTO_ENVIADO", r)
        # R6-C4B-R1 (review B-1): the WHOLE hostile set is discarded again — including the
        # canonical-looking "Te esperamos el lunes 16 a las 10:00.", an implied confirmation
        # on a thread that is not booked
        self.each(HOSTILE, run)

    def test_a_price_question_before_the_quote_without_a_zone_asks_for_it(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="QUALIFYING", flag="PRESUPUESTANDO", zone=False)
            r = _turn(self.eng, tid, wa, _PRICE_Q, body, sb, direct)
            # the existing G3-7F clarification authority may own this turn (it hands off
            # when no location Flow can be sent); either way only deterministic words go out
            handoff = r["needs_human"]
            self.discarded(body, r, route="QUOTATION", needs_human=handoff)
            self.assertIn(r["sent"][0], (
                "Para avanzar necesito saber en qué zona está el auto. "
                "¿Me podés indicar el barrio?",
                _HANDOFF), r)
            self.assertNotIn("$", r["sent"][0], r)
        self.each(("La revisión sale $35.000.", "Te sale tres mangos, ya quedó.") + REVIEWER[:2],
                  run)


class T02_Scheduling(_Base):
    """SCHEDULING stage: the day/time question; no date, time or slot from the model."""

    def test_bodies_are_discarded(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="SCHEDULING", flag="ACEPTADO", date_iso=None)
            r = _turn(self.eng, tid, wa, _THINK, body, sb, direct,
                      extracted={"preferred_time_str": "10:00"})
            self.discarded(body, r, route="SCHEDULING",
                           expect="¿Qué día y horario te viene mejor para la revisión?")
            self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
            self.assertIsNone(r["token"], "no Flow from the model's words")
        self.each(HOSTILE, run)

    def test_with_an_evaluated_day_the_schedule_service_handler_owns_the_reply(self):
        # the existing post-model deterministic block (day-only request on the CANONICAL
        # active date) answers; the model's body and its extracted time are not used
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="SCHEDULING", flag="ACEPTADO")
            r = _turn(self.eng, tid, wa, _THINK, body, sb, direct,
                      extracted={"preferred_time_str": "15:30"})
            self.assertEqual(r["route"], "SCHEDULING", r)
            self.assertEqual(r["discarded"], body, r)
            # Flow-first: with Smart Booking on, ScheduleService's slots open the Booking Flow
            # (one offer, no text); otherwise one deterministic text
            if r["action"] == "flow_button_sent":
                self.assertEqual((r["sent"], r["offers"]), ([], 1), r)
            else:
                self.assertEqual(len(r["sent"]), 1, r)
                self.assertNotIn(_norm(body), _norm(r["sent"][0]), r)
            self.assertIsNone(r["preferred_time"], r)
            self.assertIn(r["preferred_day"], (None, MON), r)     # the canonical active date
            self.assertEqual(r["revisions"], 0, r)
        self.each(HOSTILE, run)


class T03_FlowPending(_Base):
    """An outstanding Booking Flow: the Flow reminder, nothing restated."""

    def test_bodies_are_discarded(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="FLOW_SENT", flag="ACEPTADO",
                            token=f"r6c4a-tok-{next(C._SEQ)}")
            r = _turn(self.eng, tid, wa, _THINK, body, sb, direct)
            self.discarded(body, r, route="FLOW_PENDING",
                           expect="Te envié el formulario para confirmar el turno. "
                                  "Completalo con tus datos y terminamos la reserva.")
        self.each(HOSTILE, run)


class T04_CommittedBooking(_Base):
    """BOOKED: the committed revision's OWN date and start time, and a person takes over."""

    def test_bodies_are_discarded_and_the_committed_slot_is_stated(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="BOOKED", flag="ACEPTADO", booked=BOOKED)
            r = _turn(self.eng, tid, wa, _THINK, body, sb, direct)
            self.discarded(body, r, route="BOOKED", revisions=1, needs_human=True,
                           expect="Tu turno está confirmado para el lunes 16/11 a las 10:00."
                                  "\n\n" + _HANDOFF)
            self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
            self.assertIn(("BOOKING", True), r["findings"], r)
        self.each(HOSTILE, run)

    def test_the_status_is_the_committed_revision_not_the_requested_or_offered_slot(self):
        # the thread's requested date and visible slots name ANOTHER day and time; only the
        # committed revision may be restated
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="BOOKED", flag="ACEPTADO", booked=BOOKED,
                            date_iso="2026-11-23")
            with Session(self.eng) as db:
                st = db.execute(select(WhatsAppThreadState).where(
                    WhatsAppThreadState.thread_id == tid)).scalar_one()
                st.last_visible_slots = st.last_offered_slots = json.dumps(["15:30"])
                st.preferred_day, st.preferred_time = "2026-11-23", None
                db.commit()
            r = _turn(self.eng, tid, wa, _THINK, body, sb, direct)
            self.assertEqual(r["sent"], ["Tu turno está confirmado para el lunes 16/11 a las "
                                         "10:00.\n\n" + _HANDOFF], r)
            self.assertIn(("BOOKING", True), r["findings"], r)
        self.each(REVIEWER[:1] + NOVEL[:1], run)

    def test_cancellation_and_recovery_requests_go_to_a_person(self):
        for customer in ("Quiero cancelar el turno", "Necesito cambiar el horario del turno"):
            def run(body, sb, direct, customer=customer):
                tid, wa = _seed(self.eng, stage="BOOKED", flag="ACEPTADO", booked=BOOKED)
                r = _turn(self.eng, tid, wa, customer, body, sb, direct)
                self.discarded(body, r, route="BOOKED", revisions=1, needs_human=True,
                               startswith="Tu turno está confirmado para el lunes 16/11 a las 10:00.")
                with Session(self.eng) as db:
                    rev = db.execute(select(ThreadRevision).where(
                        ThreadRevision.thread_id == tid)).scalar_one()
                    self.assertEqual((rev.status, rev.scheduled_date, rev.scheduled_time),
                                     ("booked",) + BOOKED, "the model cancelled nothing")
            self.each(("Listo, tu turno fue cancelado.",
                       "Te lo pasé al jueves 19 a las 11:00, quedó confirmado.") + NOVEL[-1:],
                      run)

    def test_missing_booking_facts_hand_off_without_stating_a_slot(self):
        for status, link in (("provisional", True), ("cancelled", True), (None, False)):
            def run(body, sb, direct, status=status):
                tid, wa = _seed(self.eng, stage="BOOKED", flag="ACEPTADO",
                                booked=(BOOKED if status else None),
                                revision_status=status or "booked")
                r = _turn(self.eng, tid, wa, _THINK, body, sb, direct)
                self.discarded(body, r, route="BOOKED", revisions=(1 if status else 0),
                               needs_human=True)
                self.assertNotIn("10:00", r["sent"][0], r)
                self.assertNotIn("Tu turno", r["sent"][0], r)
            self.each(REVIEWER[:1] + NOVEL[:1] + CANONICAL, run)


class T05_ExtractedSchedulingFieldsAreNeverState(_Base):

    def test_model_extracted_day_and_time_are_dropped_on_every_route(self):
        for stage, flag in (("QUALIFYING", "PRESUPUESTANDO"), ("QUOTED", "PRESUPUESTO_ENVIADO")):
            for sb, direct in C.POSTURES:
                with self.subTest(stage=stage, sb=sb, direct=direct):
                    tid, wa = _seed(self.eng, stage=stage, flag=flag)
                    r = _turn(self.eng, tid, wa, _THINK, "¡Dale, cualquier cosa escribime!",
                              sb, direct, extracted={"preferred_time_str": "10:00",
                                                     "preferred_day_iso": MON})
                    self.assertEqual(r["ai_calls"], 1, r)
                    self.assertIsNone(r["preferred_time"], r)
                    self.assertIsNone(r["preferred_day"], r)


# ── E: ordinary FAQ prose is non-transactional and still reaches the customer ──────────

class T06_FaqIsNotTransactional(_Base):

    def test_ordinary_faq_prose_is_never_delivered_the_canonical_message_is(self):
        # R6-C4B-R1: FAQ facts come from canonical FAQ answers only; the model may not
        # paraphrase or supplement them, so even an accurate scope sentence is not sent
        body = ("Vamos hasta donde está el auto, hacemos la revisión ahí mismo y después te "
                "mandamos el informe.")
        for stage, flag in (("QUALIFYING", "PRESUPUESTANDO"), ("QUOTED", "PRESUPUESTO_ENVIADO"),
                            ("SCHEDULING", "ACEPTADO")):
            for sb, direct in C.POSTURES:
                with self.subTest(stage=stage, sb=sb, direct=direct):
                    tid, wa = _seed(self.eng, stage=stage, flag=flag)
                    r = _turn(self.eng, tid, wa, "¿Qué incluye la revisión?", body, sb, direct)
                    self.assertEqual(r["route"], "NON_TRANSACTIONAL", r)
                    self.assertEqual(len(r["sent"]), 1, r)
                    self.assertNotIn(_norm(body), _norm(r["sent"][0]), r)
                    self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
                    self.assertIsNone(r["preferred_time"], r)
                    self.assertEqual(r["revisions"], 0, r)

    def test_an_unapproved_faq_fact_is_not_delivered(self):
        # "más de 250 puntos de control" is no approved fact: it is never delivered; the
        # canonical message is
        body = "La revisión incluye más de 250 puntos de control y un informe completo."
        for sb, direct in C.POSTURES:
            with self.subTest(sb=sb, direct=direct):
                tid, wa = _seed(self.eng, stage="QUALIFYING", flag="PRESUPUESTANDO")
                r = _turn(self.eng, tid, wa, "¿Qué incluye la revisión?", body, sb, direct)
                self.assertEqual(r["route"], "NON_TRANSACTIONAL", r)
                self.assertEqual(len(r["sent"]), 1, r)
                self.assertNotIn("250", r["sent"][0], r)
                self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)

    def test_a_price_question_on_the_faq_route_is_transactional(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="QUOTED")
            r = _turn(self.eng, tid, wa, "Consulta, ¿cuál es el costo del servicio?", body, sb,
                      direct)
            self.assertEqual(r["route"], "QUOTATION", r)
            self.assertTrue(r["sent"][0].startswith(_QUOTE), r)
            self.assertNotIn(_norm(body), _norm(r["sent"][0]), r)
        self.each(("El costo del servicio es de $12.000.", NOVEL[3]), run)


class T11_HoursExemptionIsFaqOnly(_Base):
    """The general-hours exemption belongs to the FAQ route only: a COMMERCIAL turn that names a
    day stays a SCHEDULING route even when it also uses an hours marker ("trabajan")."""

    def test_a_commercial_turn_naming_a_day_is_scheduling(self):
        def run(body, sb, direct):
            tid, wa = _seed(self.eng, stage="QUALIFYING", flag="PRESUPUESTANDO", zone=False)
            r = _turn(self.eng, tid, wa, "Quiero revisar el Corolla, ¿trabajan el sábado?",
                      body, sb, direct)
            self.discarded(body, r, route="SCHEDULING", needs_human=r["needs_human"])
            self.assertIn(r["sent"][0], (
                "Para avanzar necesito saber en qué zona está el auto. "
                "¿Me podés indicar el barrio?", _HANDOFF), r)
        self.each(("Sí, te esperamos el sábado 14 a las 10:00.", NOVEL[0]), run)


# ── classification, claims and the tripwire (units) ───────────────────────────────────

class T07_Classification(unittest.TestCase):

    def c(self, role="COMMERCIAL_TURN", stage="QUALIFYING", token=False, rev=False, price=False,
          sched=False):
        return TB.classify_route(role, stage=stage, has_flow_token=token,
                                 has_committed_revision=rev, price_signal=price,
                                 scheduling_signal=sched)

    def test_the_route_matrix(self):
        self.assertIs(self.c(), R.NON_TRANSACTIONAL)
        self.assertIs(self.c(stage=None), R.NON_TRANSACTIONAL)
        self.assertIs(self.c(stage="QUOTED"), R.QUOTATION)
        self.assertIs(self.c(stage="SCHEDULING"), R.SCHEDULING)
        self.assertIs(self.c(stage="FLOW_SENT"), R.FLOW_PENDING)
        self.assertIs(self.c(stage="BOOKED"), R.BOOKED)
        self.assertIs(self.c(token=True), R.FLOW_PENDING)
        self.assertIs(self.c(rev=True), R.BOOKED)
        self.assertIs(self.c(rev=True, token=True), R.BOOKED)
        self.assertIs(self.c(price=True), R.QUOTATION)
        self.assertIs(self.c(sched=True), R.SCHEDULING)
        self.assertIs(self.c(stage="SCHEDULING", price=True), R.SCHEDULING)
        for stage in ("QUALIFYING", "QUOTED", "SCHEDULING"):
            self.assertIs(self.c("GENERAL_INFORMATION", stage=stage), R.NON_TRANSACTIONAL)
        self.assertIs(self.c("GENERAL_INFORMATION", price=True), R.QUOTATION)
        self.assertIs(self.c("GENERAL_INFORMATION", sched=True), R.SCHEDULING)
        self.assertIs(self.c("GENERAL_INFORMATION", stage="BOOKED"), R.BOOKED)
        self.assertIs(self.c("GENERAL_INFORMATION", token=True), R.FLOW_PENDING)

    def test_an_unknown_role_fails_closed(self):
        for role in (None, "", "SOMETHING_ELSE", 7):
            self.assertTrue(self.c(role).transactional, role)

    def test_classification_reads_no_text(self):
        fn = next(n for n in ast.walk(ast.parse(pathlib.Path(TB.__file__).read_text()))
                  if isinstance(n, ast.FunctionDef) and n.name == "classify_route")
        self.assertEqual([a.arg for a in fn.args.args + fn.args.kwonlyargs],
                         ["role", "stage", "has_flow_token", "has_committed_revision",
                          "price_signal", "scheduling_signal"])

    def test_classification_failure_is_transactional(self):
        e = object.__new__(ConversationEngine)
        e._turn_burst_texts = ["x"]
        e._norm_text = mock.Mock(side_effect=RuntimeError("boom"))
        self.assertIs(e._transactional_route_for("COMMERCIAL_TURN", SimpleNamespace(
            state=SimpleNamespace(last_stage="QUALIFYING"), thread=None)), R.SCHEDULING)


class T08_BookingClaim(unittest.TestCase):

    def facts(self, **kw):
        base = dict(booking_confirmed=True, booked_date=BOOKED[0], booked_times=("10:00", "10:45"))
        base.update(kw)
        return CanonicalFacts(**base)

    def v(self, value, facts):
        return RV._validate_structured_claim(StructuredClaim(DomainClaim.BOOKING, value), facts)

    def test_the_committed_date_and_start_time_validate(self):
        self.assertIsNone(self.v((MON, "10:00"), self.facts()))
        self.assertIsNone(self.v((), self.facts()))

    def test_any_other_value_is_refused(self):
        for value in (("2026-11-23", "10:00"), (MON, "10:45"), (MON, "11:00"), (MON,),
                      ("10:00",), (MON, "10:00", "11:00"), ("16/11", "10:00"), (MON, "10")):
            self.assertIsNotNone(self.v(value, self.facts()), value)
        self.assertIsNotNone(self.v((MON, "10:00"), self.facts(booking_confirmed=False)))
        self.assertIsNotNone(self.v((MON, "10:00"), self.facts(booked_date=None)))
        self.assertIsNotNone(self.v((MON, "10:00"), self.facts(booked_times=())))

    def test_a_noncanonical_booking_status_is_refused_by_the_envelope(self):
        text = composed_text("Tu turno está confirmado para el lunes 23/11 a las 10:00.",
                             claims=[StructuredClaim(DomainClaim.BOOKING, ("2026-11-23", "10:00"))])
        env = validate_envelope(text, self.facts())
        self.assertTrue(env.fell_back, env)


class T12_StageAdvancedThisTurn(unittest.TestCase):
    """A deterministic acceptance moved QUOTED → SCHEDULING after the route was classified:
    the renderer follows the canonical stage (the day/time question), never the stale route's
    quote or missing-fact request. (Found by tests/test_wild04r_f3_faq_preservation F3-01/03.)"""

    def test_a_quotation_route_on_a_scheduling_state_asks_for_the_day_and_time(self):
        from app.services.conversation_engine import _TX_SCHEDULING_ASK_TEXT
        e = object.__new__(ConversationEngine)
        for route in (R.QUOTATION, R.SCHEDULING):
            for quote in (None, SimpleNamespace(precio_total=240_000)):
                with self.subTest(route=route, quote=quote):
                    state = SimpleNamespace(last_stage="SCHEDULING", needs_human=False)
                    value, handoff = e._render_transactional_reply(
                        SimpleNamespace(thread=None), state, route, quote)
                    self.assertIs(type(value), ComposedText)
                    self.assertEqual(str(value), _TX_SCHEDULING_ASK_TEXT)
                    self.assertIsNone(handoff)
                    self.assertEqual(value.declaration.claims, frozenset())


class T09_Tripwire(unittest.TestCase):

    def eng(self, body):
        e = object.__new__(ConversationEngine)
        e._discarded_ai_reply = body
        e._turn_route = R.SCHEDULING
        return e

    def test_an_undeclared_value_carrying_the_discarded_body_is_refused(self):
        body = NOVEL[1]
        for text in (body, "Hola! " + body, body.upper(), "  ".join(body.split())):
            self.assertEqual(self.eng(body)._refuse_discarded_model_body(
                SimpleNamespace(thread=None), text), RV.safe_failure_text(), text)

    def test_declared_values_and_other_text_pass(self):
        body = NOVEL[1]
        e = self.eng(body)
        ct = composed_text(body)
        self.assertIs(e._refuse_discarded_model_body(SimpleNamespace(thread=None), ct), ct)
        self.assertEqual(e._refuse_discarded_model_body(SimpleNamespace(thread=None), "Otra cosa."),
                         "Otra cosa.")
        self.assertEqual(self.eng(None)._refuse_discarded_model_body(
            SimpleNamespace(thread=None), body), body)


# ── F: structure ──────────────────────────────────────────────────────────────────────

def _fn(name, src=SRC):
    return next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node):
    return {getattr(c.func, "attr", getattr(c.func, "id", None))
            for c in ast.walk(node) if isinstance(c, ast.Call)}


class T10_Structure(unittest.TestCase):

    def test_one_model_call_site_and_it_crosses_the_boundary(self):
        tree = ast.parse(SRC)
        callers = {f.name for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                   and "_call_openai" in _calls(f)}
        self.assertEqual(callers, {"_reply_decision"})
        rd = _fn("_reply_decision")
        rets = sorted((n for n in ast.walk(rd) if isinstance(n, ast.Return)),
                      key=lambda n: n.lineno)
        last = rets[-1].value
        self.assertIsInstance(last, ast.Tuple)
        self.assertEqual(getattr(last.elts[0].func, "attr", None), "_apply_transactional_boundary")
        # the only earlier return is the bounded refusal, which carries no model words
        self.assertEqual(len(rets), 2)
        self.assertEqual(getattr(rets[0].value.elts[0].func, "attr", None), "_refuse_reply")

    def test_only_the_boundary_and_the_tripwire_read_the_discarded_body(self):
        readers = set()
        for f in ast.walk(ast.parse(SRC)):
            if isinstance(f, ast.FunctionDef):
                for n in ast.walk(f):
                    if isinstance(n, ast.Constant) and n.value == "_discarded_ai_reply":
                        readers.add(f.name)
                    if isinstance(n, ast.Attribute) and n.attr == "_discarded_ai_reply":
                        readers.add(f.name)
        # `handle` only resets it per turn; nothing else may read or write it — R6-C4B-R1:
        # `_approved_bridge` reads it only to DROP a bridge that carries it
        self.assertEqual(readers, {"_apply_transactional_boundary",
                                   "_refuse_discarded_model_body", "handle",
                                   "_approved_bridge"})
        self.assertIn("self._discarded_ai_reply = None", ast.unparse(_fn("handle")))

    def test_the_boundary_discards_before_any_consumer(self):
        b = _fn("_apply_transactional_boundary")
        text = ast.unparse(b)
        self.assertIn("decision['reply'] = None", text)
        # R6-C4B: unconditional — the interpreter body is discarded on every route
        self.assertNotIn("route.transactional", text)
        discard = next(n for n in ast.walk(b) if isinstance(n, ast.Assign)
                       and ast.unparse(n) == "decision['reply'] = None")
        parents = [n for n in ast.walk(b) if isinstance(n, ast.If)
                   and any(c is discard for c in ast.walk(n))]
        self.assertEqual(parents, [], "the discard must not be conditional")
        for f in TB.AI_SCHEDULE_FIELDS:
            self.assertIn(f, TB.AI_SCHEDULE_FIELDS)
        self.assertIn("_TB_AI_SCHEDULE_FIELDS", text)
        self.assertEqual(set(TB.AI_SCHEDULE_FIELDS), {"preferred_time_str", "preferred_day_iso"})

    def test_no_state_is_derived_from_model_prose(self):
        self.assertNotIn("state.preferred_time = ai_proposed_time", SRC)
        self.assertNotIn("_parse_scheduling_text([ai_reply_text]", SRC)
        self.assertNotIn("fallback = ai_reply or", SRC)
        # R6-C4B-R1: the commercial tail sends a model-routed turn only as canonical text
        # (the renderer, or the qualification request), optionally behind a claim-free
        # bridge; the plain send takes declared values
        i = SRC.index("_raw_reply = decision.get(\"reply\")")
        j = SRC.index("        sent_id = self._send_text_to_wa(ctx, reply)\n        return _out(\"replied\", wa_message_id=sent_id)", i)
        tail = SRC[i:j]
        self.assertIn("if type(_raw_reply) is ComposedText:", tail)
        self.assertIn("return self._send_transactional_reply(ctx, state, _route, real_price_quote,\n                                                      bridge_label=_label)", tail)
        self.assertIn("return self._send_with_bridge(ctx, state, _label,\n                                          self._canonical_qualification_value(ctx, state))", tail)
        self.assertNotIn("self._send_text_to_wa(", tail)     # a call (a comment names it)

    def test_the_faq_route_checks_the_route_before_reading_the_model_reply(self):
        # R6-C4B-R1: the FAQ route reads only a declared engine value from the decision; a
        # routed model turn is answered by canonical text (optionally behind a bridge)
        text = ast.unparse(_fn("_handle_general_information_ai"))
        i = text.index("if type(_raw) is not ComposedText and isinstance(_route, TransactionalRoute):")
        self.assertLess(i, text.index("_send_with_bridge("))
        self.assertLess(text.index("_send_with_bridge("), text.index("reply = _raw or ''"))

    def test_the_renderer_builds_only_from_canonical_sources(self):
        r = _fn("_render_transactional_reply")
        calls = _calls(r)
        self.assertIn("_committed_booking_slot", calls)
        self.assertIn("_composed_quote_reply", calls)
        self.assertNotIn("_parse_scheduling_text", calls)
        names = {n.id for n in ast.walk(r) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(r) if isinstance(n, ast.Attribute)}
        for forbidden in ("decision", "_discarded_ai_reply", "active_requested_date",
                          "preferred_time", "preferred_day", "last_visible_slots", "reply"):
            self.assertNotIn(forbidden, names | attrs, forbidden)
        for ret in (n for n in ast.walk(r) if isinstance(n, ast.Return)):
            head = ret.value.elts[0]
            ok = (isinstance(head, ast.Constant) and head.value is None) or (
                isinstance(head, ast.Call) and getattr(head.func, "id", getattr(
                    head.func, "attr", None)) in ("composed_text", "_composed_quote_reply",
                                                  "_missing_quote_facts_reply",
                                                  "_scheduling_next_step"))
            self.assertTrue(ok, ast.unparse(ret))
        # R6-C4B-R1: the SCHEDULING fact block reads ScheduleService's current evaluation
        # (CanonicalFacts) only — never requested / visible / preferred values or prose
        nxt = _fn("_scheduling_next_step")
        names = {n.id for n in ast.walk(nxt) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(nxt) if isinstance(n, ast.Attribute)}
        for forbidden in ("decision", "_discarded_ai_reply", "active_requested_date",
                          "preferred_time", "preferred_day", "last_visible_slots", "reply"):
            self.assertNotIn(forbidden, names | attrs, forbidden)
        self.assertIn("_build_canonical_facts", _calls(nxt))

    def test_every_rendered_value_is_declared_composed_text(self):
        e = object.__new__(ConversationEngine)
        e.db = mock.Mock()
        e._committed_booking_slot = lambda st: (BOOKED[0], ("10:00", "10:45"))
        e._focus_candidate = lambda ctx: None
        state = SimpleNamespace(needs_human=False, last_stage="SCHEDULING",
                                home_zone_group="CABA", home_zone_detail=None)
        for route in (R.BOOKED, R.FLOW_PENDING, R.SCHEDULING, R.QUOTATION):
            value, _h = e._render_transactional_reply(SimpleNamespace(), state, route, None)
            self.assertIs(type(value), ComposedText, route)
        booked, h = e._render_transactional_reply(SimpleNamespace(), state, R.BOOKED, None)
        self.assertEqual(booked._declaration.claims,
                         frozenset({StructuredClaim(DomainClaim.BOOKING, (MON, "10:00"))}))
        self.assertEqual(h, "post_booking_message")

    def test_the_tripwire_runs_before_validation_in_the_send_function(self):
        text = ast.unparse(_fn("_send_text_to_wa"))
        self.assertLess(text.index("_refuse_discarded_model_body"),
                        text.index("_validate_outbound_text"))


if __name__ == "__main__":
    unittest.main()
