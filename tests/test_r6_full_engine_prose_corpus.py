"""R6 (R5F1RV-01) — AI-composed prose through the FULL ConversationEngine path.

The R5-F1 independent review measured that ordinary AI prose ("uno por uno", "buena idea",
"a las 10 en punto", "pilares", "norteamericano", hours, "según la disponibilidad") was read
as VEHICLE / LOCATION / AVAILABILITY claims: the whole reply was replaced by the
vehicle/zone question, an INSPECTION_LOCATION outstanding question was opened, and a
question that survived could append "¿Es un Fiat …?" and arm `pending_fuzzy_catalog_key`.

Every case here is one real turn: `app.api.conversation.handle_message` →
`ConversationEngine.handle` → reply model (`_call_openai_inner` replaced by a canned
provider answer, so the call accounting and the reply ADMISSION are the real ones) →
`_send_text_to_wa` → `_validate_outbound_text` (structured contract + claim-shaped defence
in depth) → `plan.compose` → `OutboundSafetyGate` → the Meta recorder. The viaticos table
holds the production zone-name lexicon, so LOCATION sees real place names.

Worlds:   new           QUALIFYING, no vehicle, no inspection location
          corolla       QUALIFYING, Toyota Corolla 2018 current focus, location unknown
          located       QUALIFYING, no vehicle, inspection location CABA / Palermo known
Postures: CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED false / true × Smart Booking false /
          true, with the R1/R2/R3 interlock armed.

Database: SQLite in memory; set R6_PG_URL to run the identical cases on PostgreSQL.

Assertions:
  ORDINARY answers       sent verbatim; no fallback; no outstanding question (unless the
                         reply itself asks for a location that is genuinely missing); no
                         pending_fuzzy_catalog_key; no candidate; needs_human unchanged.
  CONTRADICTIONS         wrong explicit vehicle / location, invented slot, wrong price,
                         false booking, false acceptance are refused; the fallback asks only
                         for what canonical state genuinely lacks (never a known vehicle).
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
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_PG = os.environ.get("R6_PG_URL", "")
os.environ.setdefault("DATABASE_URL", _PG or "sqlite://")

import _sb2b_harness as H  # noqa: E402

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, ViaticosZone,  # noqa: E402
                        WhatsAppContact, WhatsAppMessage, WhatsAppOutstandingQuestion,
                        WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import response_validator as RV  # noqa: E402

_FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "r6_zone_names.txt"
_SEQ = itertools.count(int(os.environ.get("R6_SEQ0", "1")))
_FSEQ = itertools.count(1)

# ── the corpus ───────────────────────────────────────────────────────────────────────

#: The R5-F1 independent review's §6.2 full-engine replies (verbatim) plus the R6 milestone's
#: named examples, each of which must reach the customer unchanged.
AUDIT_ORDINARY = {
    "control": "Perfecto, cualquier duda me escribís por acá.",
    "uno_por_uno": "Sí, en la revisión vemos todo uno por uno: motor, frenos, suspensión y carrocería.",
    "uno_de_los_dos": "Podemos revisar primero uno de los dos autos y después el otro.",
    "uno_de_los_dos_q": "¿La revisión sería para uno de los dos autos que estás viendo?",
    "buena_idea": "¡Buena idea! Revisarlo antes de comprar te ahorra sorpresas.",
    "buena_idea_mixed": "¡Buena idea! La revisión incluye motor, frenos y tren delantero.",
    "buena_idea_q": "¿Te parece buena idea revisarlo antes de comprar?",
    "punto_por_punto": "Revisamos carrocería, motor, frenos y suspensión, punto por punto.",
    "pilar_noun": "En la revisión del auto chequeamos los pilares y la carrocería.",
    "pilar_name": "Gracias Pilar, la revisión del auto la hacemos donde esté.",
    "norteamericano": "La revisión del auto incluye el chequeo del chasis norteamericano.",
    "flores_plain": "Las flores del jardín no influyen en la revisión del auto.",
    "norte_conditional": "Si el auto está en zona Norte, también vamos.",
    "hours": "Trabajamos de lunes a viernes de 9:00 a 18:00.",
    "hours_mixed": "Trabajamos de lunes a viernes de 9:00 a 18:00. Cualquier duda me escribís.",
    "disponibilidad": "Cuanto antes nos avises, mejor: coordinamos según la disponibilidad de agenda.",
}
#: R6-C3 (R6C2RV-S01): "Perfecto, te esperamos a las 10 en punto." was an R5-F1 audit example of
#: ordinary prose ("punto" is not a Fiat Punto). Since R6-C3 every clock form is read, so it is an
#: arrival-time proposition: with no ScheduleService evaluation (every world here) it is refused by
#: AVAILABILITY — never by VEHICLE — through the plan-appropriate fallback (section D).
CLOCK_PROPOSITIONS = {
    "en_punto": "Perfecto, te esperamos a las 10 en punto.",
}
#: A reply that genuinely asks for the inspection location.
ASKS_LOCATION = {
    "faq_mixed_zone_q": "La revisión incluye más de 250 puntos, uno por uno. ¿En qué zona está el auto?",
}

#: The independent reviewer's 40-answer prompt-derived corpus (§6.3), verbatim.
REVIEWER_40 = [
    "Revisamos el vehículo en el lugar donde está, con más de 250 puntos de control.",
    "La revisión tiene más de 250 puntos de control y al final te mandamos un informe detallado.",
    "Revisamos carrocería, motor, frenos, suspensión y tren delantero, punto por punto.",
    "Chequeamos uno por uno los sistemas del auto: motor, transmisión, frenos y electrónica.",
    "Medimos los espesores de pintura para detectar golpes y reparaciones previas.",
    "Hacemos lectura de errores con escáner y verificamos el kilometraje real.",
    "Cada uno de los puntos de control queda en el informe.",
    "Es una revisión pre-compra completa, de primera clase.",
    "La revisión dura aproximadamente una hora.",
    "Cuanto antes nos avises, mejor: coordinamos según la disponibilidad de agenda.",
    "Llegamos a la hora acordada, en punto.",
    "Podés pagar por transferencia, Mercado Pago o efectivo.",
    "No aceptamos débito ni tarjeta de crédito; sí transferencia, Mercado Pago o efectivo.",
    "La revisión se hace en el lugar donde está el auto.",
    "Trabajamos en CABA y GBA, zona Norte, Sur y Oeste.",
    "Si el auto está en zona Norte, también vamos.",
    "La revisión del auto la hacemos en Flores sin problema.",
    "Vamos a donde esté el auto, incluso si está en el Centro.",
    "El auto puede estar en una concesionaria o en un domicilio particular.",
    "¡Buena idea revisarlo antes de comprar!",
    "Me parece una excelente idea.",
    "Genial, cualquier duda me escribís.",
    "Perfecto, quedo atento.",
    "Uno nunca sabe lo que puede tener un usado, por eso conviene revisarlo.",
    "Si estás viendo varios, podemos revisar uno y después el otro.",
    "Es un servicio pensado para que compres tranquilo.",
    "El informe te llega el mismo día.",
    "Trabajamos de lunes a sábado de 9:00 a 18:00.",
    "Atendemos de 9 a 18 hs.",
    "La revisión del auto la hace un técnico especializado.",
    "La revisión del auto se puede hacer en un estacionamiento o en la calle.",
    "La serie de controles incluye motor, frenos y suspensión.",
    "Revisamos también autos importados, incluso norteamericanos.",
    "Cualquier auto, desde un utilitario hasta una camioneta grande.",
    "Si querés, te paso el detalle punto a punto.",
    "Contamos con un equipo de primer nivel.",
    "Para el auto de tu hija también te sirve.",
    "El vendedor puede estar presente durante la revisión del auto.",
    "No hace falta que el auto esté en movimiento.",
    "Te recomiendo coordinar con el vendedor antes.",
]
#: The one reviewer reply that IS a claim-shaped assertion of where the inspection happens
#: ("la hacemos EN Flores"). It is supported only when canonical state holds Flores; in these
#: worlds it is refused, and that is the intended reading (owner decision: an asserted
#: inspection place needs canonical proof). Recorded, not hidden.
REVIEWER_LOCATIVE_CLAIM = "La revisión del auto la hacemos en Flores sin problema."

#: Additional RideCheck-style answers (wild-session style FAQ turns).
RIDECHECK_EXTRA = [
    "¡Hola! Sí, hacemos revisiones pre-compra a domicilio, vamos a donde esté el auto.",
    "La revisión la hace un técnico y dura más o menos una hora.",
    "Te mandamos el informe con fotos y el detalle punto por punto.",
    "Revisamos uno por uno los puntos de la carrocería y el motor.",
    "Sí, podés estar presente durante la revisión, es una buena idea.",
    "El pago es por transferencia, Mercado Pago o efectivo.",
    "Revisamos tanto autos nacionales como importados.",
    "Perfecto, cuando tengas los datos del auto me avisás.",
    "Si el vendedor está en Pilar o en otra zona, coordinamos igual.",
    "Atendemos de lunes a sábado de 9:00 a 13:00 y coordinamos según disponibilidad.",
]

#: Genuine contradictions: (key, reply, the claim class that must refuse it).
CONTRADICTIONS = [
    ("wrong_vehicle", "Perfecto, la revisión del Fiat Uno la coordinamos.", "VEHICLE", "Fiat Uno"),
    ("wrong_location", "Perfecto, el auto está en Pilar.", "LOCATION", "Pilar"),
    ("invented_slot", "Te ofrezco el turno de mañana a las 10:30.", "AVAILABILITY", "10:30"),
    ("wrong_price", "La revisión sale $10.000.", "PRICE", "10.000"),
    ("false_booking", "Listo, tu turno quedó confirmado.", "BOOKING", "confirmado"),
    ("false_acceptance", "Genial, ya aceptaste el presupuesto.", "ACCEPTANCE", "aceptaste"),
]
#: Cells where an EARLIER deterministic guard already removes the contradiction before the
#: validator sees it: with a focus vehicle and no zone, `_scrub_invented_price` (BUG-2)
#: replaces an invented amount with its own missing-zone request. Blocked either way.
UPSTREAM_GUARD = set()   # R6-C4B: was {("wrong_price", "corolla")}; model prose no longer reaches the BUG-2 scrub — the invented price is refused by verification (standard fallback)

WORLDS = ("new", "corolla", "located")
CUSTOMER = {"new": "Hola, buenas tardes, quería hacer una consulta",
            "corolla": "Perfecto, gracias. Lo voy a pensar y te aviso",
            "located": "Hola, buenas tardes, quería hacer una consulta"}
POSTURES = [(sb, direct) for sb in (False, True) for direct in (False, True)]


def _cfg(sb: bool, direct: bool):
    return dataclasses.replace(
        H.settings(flag=sb), openai_api_key="r6-test-key-not-real",
        shadow_understand_enabled=False, semantic_same_turn_enabled=False,
        evidence_admission_enabled=True, outstanding_question_enabled=True,
        locality_resolution_safety_enabled=True, operator_whatsapp_notify_enabled=False,
        operator_whatsapp_to="", booking_flow_details_prefill_enabled=False,
        conversation_engine_direct_webhook_enabled=direct)


def _decision(reply: str) -> str:
    return json.dumps({"intent": "OTHER", "reply": reply, "lead_flag": None, "needs_human": False,
                       "extracted": {"customer_name": None, "zone_detail": None,
                                     "preferred_time_str": None, "tipo_vehiculo": None,
                                     "vendedor_tipo": None},
                       "candidate": {"action": "none", "id": None, "marca": None, "modelo": None,
                                     "version_text": None, "anio": None, "tipo_vehiculo": None,
                                     "status": "current_focus"}})


def _engine():
    eng = create_engine(_PG) if _PG else H.sqlite_engine()
    if _PG:
        from app.models import Lead as _L
        _L.__table__.metadata.create_all(eng)
    with Session(eng) as db:
        if not db.execute(select(func.count()).select_from(ViaticosZone)).scalar_one():
            for line in _FIXTURE.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                g, d = line.split("|")
                db.add(ViaticosZone(zone_group=g, zone_detail=d or None, viaticos=0))
            db.commit()
    return eng


def _seed(eng, world: str, text: str):
    n = next(_SEQ)
    with Session(eng) as db:
        c = WhatsAppContact(wa_id=f"54900{81000000 + n:08d}", display_name="R6 Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False)
        db.add_all([c, lead]); db.flush()
        th = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
        db.add(th); db.flush()
        focus = None
        if world == "corolla":
            cand = WhatsAppThreadCandidate(thread_id=th.id, tipo_vehiculo="AUTO", marca="Toyota",
                                           modelo="Corolla", anio=2018, status="current_focus",
                                           source_text="Toyota Corolla 2018")
            db.add(cand); db.flush(); focus = cand.id
        db.add(WhatsAppThreadState(
            thread_id=th.id, last_stage="QUALIFYING", needs_human=False,
            current_focus_candidate_id=focus,
            home_zone_group=("CABA" if world == "located" else None),
            home_zone_detail=("Palermo" if world == "located" else None),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        wamid = f"wamid.R6.IN.{n:07d}"
        db.add(WhatsAppMessage(thread_id=th.id, direction="in", wa_message_id=wamid,
                               message_type="text", text=text, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=th.id, wa_message_id=wamid, wa_id=c.wa_id, status="triggered"))
        db.commit()
        return th.id, c.wa_id, wamid


def run_case(eng, world: str, reply: str, sb: bool, direct: bool) -> dict:
    tid, wa, wamid = _seed(eng, world, CUSTOMER[world])
    ev = ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                              message_type="text", text=CUSTOMER[world])

    def hook(ce):
        ce._call_openai_inner = lambda messages: _decision(reply)
    with mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                    lambda *a, **k: (f"wamid.R6.FLOW.{next(_FSEQ):06d}", 200)), H.Meta() as m:
        out, ce = H.run_turn(eng, ev, cfg=_cfg(sb, direct), engine_hook=hook)
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        oqs = db.execute(select(WhatsAppOutstandingQuestion).where(
            WhatsAppOutstandingQuestion.thread_id == tid)).scalars().all()
        cands = db.execute(select(WhatsAppThreadCandidate).where(
            WhatsAppThreadCandidate.thread_id == tid)).scalars().all()
        env = getattr(ce, "_turn_outbound_envelope", None)
        return dict(
            action=getattr(out, "action", None), ai_calls=getattr(ce, "_reply_calls", 0),
            armed=bool(ce._integrated_features_armed()),
            sent=[t for _w, t in m.sent], egress=len(m.egress),
            pending_fuzzy=st.pending_fuzzy_catalog_key, needs_human=st.needs_human,
            oq=[(q.question_kind, q.asked_by) for q in oqs],
            candidates=[(x.marca, x.modelo, x.status) for x in cands],
            revisions=db.execute(select(func.count()).select_from(ThreadRevision).where(
                ThreadRevision.thread_id == tid)).scalar_one(),
            route=getattr(getattr(ce, "_turn_route", None), "name", None),
            discarded=getattr(ce, "_discarded_ai_reply", None),
            provenance=getattr(getattr(env, "provenance", None), "name", None),
            fallback=getattr(getattr(env, "fallback_id", None), "name", None),
            refused=sorted({f.claim for f in getattr(env, "findings", ())
                            if not f.allowed and f.action != RV.ACTION_ALLOWED}))


def _facts_for(world: str) -> RV.CanonicalFacts:
    return RV.CanonicalFacts(
        vehicle_marca=("Toyota" if world == "corolla" else None),
        vehicle_modelo=("Corolla" if world == "corolla" else None),
        inspection_zone_group=("CABA" if world == "located" else None),
        inspection_zone_detail=("Palermo" if world == "located" else None))


class _Base(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = _engine()

    def _ordinary(self, key: str, reply: str, *, asks_location: bool = False):
        for world in WORLDS:
            for sb, direct in POSTURES:
                with self.subTest(case=key, world=world, sb=sb, direct=direct):
                    r = run_case(self.eng, world, reply, sb, direct)
                    self.assertTrue(r["armed"], "R1/R2/R3 interlock must be armed")
                    self.assertEqual(r["ai_calls"], 1, "the turn must reach the reply model")
                    self.assertEqual(r["egress"], 0)
                    self.assertEqual(len(r["sent"]), 1, r)
                    # R6-C4B-R1: model prose never reaches the customer on any route; the
                    # canonical deterministic message does (a historical body is never a
                    # claim-free bridge — see `_c4b_model_fakes`)
                    self.assertNotIn(" ".join(reply.split()).casefold(),
                                     " ".join(r["sent"][0].split()).casefold(), r)
                    self.assertIsNone(r["fallback"], r)
                    self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
                    self.assertNotIn("¿Es un", r["sent"][0])
                    self.assertIsNone(r["pending_fuzzy"], "prose armed a pending vehicle")
                    expected_cands = ([("Toyota", "Corolla", "current_focus")]
                                      if world == "corolla" else [])
                    self.assertEqual(r["candidates"], expected_cands, "candidate state mutated")
                    self.assertFalse(r["needs_human"])
                    self.assertEqual(r["revisions"], 0)
                    # every question is the canonical one (`asks_location` described the
                    # model body, which is no longer delivered)
                    for _kind, by in r["oq"]:
                        self.assertEqual(by, "DETERMINISTIC_TEMPLATE", r)


class A_AuditExamplesFullEngine(_Base):
    """Every R5F1RV-01 example, every world, both webhook postures, Smart Booking off/on."""

    def test_a01_ordinary_answers_reach_the_customer_unchanged(self):
        for key, reply in AUDIT_ORDINARY.items():
            self._ordinary(key, reply)

    def test_a02_a_genuine_location_question_is_recorded_only_when_the_place_is_missing(self):
        for key, reply in ASKS_LOCATION.items():
            self._ordinary(key, reply, asks_location=True)


class B_ReviewerCorpusFullEngine(_Base):
    """The independent reviewer's 40 prompt-derived answers, full engine."""

    def test_b01_thirty_nine_ordinary_answers_unchanged(self):
        for i, reply in enumerate(REVIEWER_40):
            if reply == REVIEWER_LOCATIVE_CLAIM:
                continue
            self._ordinary(f"rv40_{i:02d}", reply)

    def test_b02_the_one_locative_assertion_needs_canonical_proof(self):
        for world in WORLDS:
            for sb, direct in POSTURES:
                with self.subTest(world=world, sb=sb, direct=direct):
                    r = run_case(self.eng, world, REVIEWER_LOCATIVE_CLAIM, sb, direct)
                    # R6-C4B-R1: the claim never reaches the customer or the validator —
                    # the canonical request (the same contract words) is sent instead
                    self.assertEqual(r["refused"], [], r)
                    self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
                    self.assertEqual(r["sent"], [RV.select_validation_fallback(
                        _facts_for(world)).text])

    def test_b03_ridecheck_style_answers_unchanged(self):
        for i, reply in enumerate(RIDECHECK_EXTRA):
            self._ordinary(f"extra_{i:02d}", reply)


class C_RealContradictionsStayBlocked(_Base):
    """Genuine claims are refused, and the fallback asks only for what is really missing."""

    def test_c01_each_contradiction_is_refused_with_a_plan_appropriate_fallback(self):
        for key, reply, claim, forbidden in CONTRADICTIONS:
            for world in WORLDS:
                for sb, direct in POSTURES:
                    with self.subTest(case=key, world=world, sb=sb, direct=direct):
                        r = run_case(self.eng, world, reply, sb, direct)
                        self.assertEqual(len(r["sent"]), 1, r)
                        self.assertNotIn(forbidden, r["sent"][0], f"contradiction delivered: {r}")
                        self.assertIsNone(r["pending_fuzzy"])
                        self.assertEqual(r["revisions"], 0)
                        if (key, world) in UPSTREAM_GUARD:
                            self.assertEqual(r["refused"], [], r)   # never reached the validator
                            continue
                        # R6-C4B-R1: model prose is never delivered or validated; the canonical
                        # qualification request — the same plan-appropriate contract words —
                        # is sent, declared, instead
                        self.assertEqual(r["refused"], [], r)
                        expected = RV.select_validation_fallback(_facts_for(world))
                        self.assertEqual(r["sent"], [expected.text], r)
                        self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
                        if world == "corolla":
                            self.assertNotIn("qué vehículo", r["sent"][0],
                                             "a known vehicle must not be re-asked")
                        # the fallback's question is DECLARED; recorded only if the place
                        # is genuinely missing, and attributed to the deterministic template
                        if world == "located":
                            self.assertEqual(r["oq"], [])
                        else:
                            self.assertEqual(r["oq"], [("INSPECTION_LOCATION",
                                                        "DETERMINISTIC_TEMPLATE")], r)

    def test_c02_a_supported_claim_is_kept(self):
        # R6-C4B-R1: even a supported claim is not the model's to state — vehicle and location
        # are rendered by canonical code only; the canonical request is sent
        for world, body, direct in (("located", "Perfecto, el auto está en Palermo.", False),
                                    ("corolla", "Perfecto, la revisión del Toyota Corolla la "
                                     "coordinamos.", True)):
            r = run_case(self.eng, world, body, False, direct)
            self.assertEqual(r["sent"], [RV.select_validation_fallback(_facts_for(world)).text],
                             r)
            self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)


class D_ClockTimesAreAvailabilityPropositions(_Base):
    """R6-C3: a clock time without an evaluation is refused by AVAILABILITY, not VEHICLE."""

    def test_d01_an_unevaluated_clock_time_is_refused_as_availability(self):
        for key, reply in CLOCK_PROPOSITIONS.items():
            for world in WORLDS:
                for sb, direct in POSTURES:
                    with self.subTest(case=key, world=world, sb=sb, direct=direct):
                        r = run_case(self.eng, world, reply, sb, direct)
                        self.assertEqual(len(r["sent"]), 1, r)
                        # R6-C4B-R1: never delivered, never validated (canonical request sent)
                        self.assertEqual(r["refused"], [], r)
                        expected = RV.select_validation_fallback(_facts_for(world))
                        self.assertEqual(r["sent"], [expected.text], r)
                        self.assertIsNone(r["pending_fuzzy"])
                        self.assertEqual(r["revisions"], 0)
                        self.assertEqual(r["candidates"], [("Toyota", "Corolla", "current_focus")]
                                         if world == "corolla" else [], r)


if __name__ == "__main__":
    unittest.main()
