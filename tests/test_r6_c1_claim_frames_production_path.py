"""R6-C1 — R6IRV-S01 / S02 / P01 through the FULL ConversationEngine production path.

Same harness as `test_r6_full_engine_prose_corpus` (one real turn: `handle_message` → CE →
reply model with a canned provider answer → reply admission → `_send_text_to_wa` →
`_validate_outbound_text` → `plan.compose` → OutboundSafetyGate → Meta recorder), with
the R1/R2/R3 interlock armed, in every posture:
  CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED false / true × SMART_BOOKING_CE_ENABLED false / true.

Canonical-state worlds:
  new       no vehicle, no inspection location
  corolla   Toyota Corolla 2018 current focus, location unknown
  located   no vehicle, inspection location CABA / Palermo
  both      Toyota Corolla 2018 + CABA / Palermo, quote already sent (stage QUOTED)
  gol       Volkswagen Gol 2015 + CABA / Palermo, quote already sent (S02 positive control)
  slots     as `both`, plus ScheduleService slots 15:00 / 15:30 offered for the evaluated
            date Thursday 2026-11-12 (R6-C2; the business clock is frozen on Wed 2026-11-11)
(The three located-with-vehicle worlds are seeded as already QUOTED: otherwise the M18
deterministic quote override replaces the reply-model prose and the validator never sees it.)

Database: SQLite in memory; set R6_PG_URL to run the identical cases on PostgreSQL.

Every refused case asserts: exactly one message; the false content is not in it; it is the
plan-appropriate validation fallback (or an earlier deterministic guard removed it); no
pending fuzzy vehicle; no candidate created or changed; no booking/revision; any outstanding
question is the fallback's DECLARED one. Every kept case asserts the reply is verbatim, with no
state change.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (the shared harness module)
import test_r6_c2_day_date_production_path as DD  # noqa: E402  (R6-C4A expectation helper)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, WhatsAppContact,  # noqa: E402
                        WhatsAppMessage, WhatsAppOutstandingQuestion, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import response_validator as RV  # noqa: E402

H = C.H
WORLDS = ("new", "corolla", "located", "both", "slots")
_VEHICLE = {"corolla": ("Toyota", "Corolla", 2018), "both": ("Toyota", "Corolla", 2018),
            "slots": ("Toyota", "Corolla", 2018), "gol": ("Volkswagen", "Gol", 2015),
            "gol_eval": ("Volkswagen", "Gol", 2015)}
_LOCATED = {"located", "both", "slots", "gol", "gol_eval"}
#: R6-C3 (R6C2RV-S01): worlds whose thread holds ScheduleService's evaluation of SLOTS_DATE.
#: "gol_eval" is "gol" with that evaluation: "revisamos tu gol mañana" proposes a day, so it
#: needs the day's evaluation to be delivered.
_EVALUATED_WORLDS = ("slots", "gol_eval")
_CUSTOMER = {"new": "Hola, buenas tardes, quería hacer una consulta",
             "located": "Hola, buenas tardes, quería hacer una consulta"}
_DEFAULT_CUSTOMER = "Perfecto, gracias. Lo voy a pensar y te aviso"


def _seed(eng, world: str, text: str):
    n = next(C._SEQ)
    with Session(eng) as db:
        c = WhatsAppContact(wa_id=f"54900{82000000 + n:08d}", display_name="R6C1 Fixture")
        quoted = world in _VEHICLE and world in _LOCATED
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False,
                    flag=("PRESUPUESTO_ENVIADO" if quoted else None))
        db.add_all([c, lead]); db.flush()
        th = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
        db.add(th); db.flush()
        focus = None
        if world in _VEHICLE:
            marca, modelo, anio = _VEHICLE[world]
            cand = WhatsAppThreadCandidate(thread_id=th.id, tipo_vehiculo="AUTO", marca=marca,
                                           modelo=modelo, anio=anio, status="current_focus",
                                           source_text=f"{marca} {modelo} {anio}")
            db.add(cand); db.flush(); focus = cand.id
        located = world in _LOCATED
        db.add(WhatsAppThreadState(
            thread_id=th.id, last_stage=("QUOTED" if quoted else "QUALIFYING"), needs_human=False,
            current_focus_candidate_id=focus,
            home_zone_group=("CABA" if located else None),
            home_zone_detail=("Palermo" if located else None),
            last_offered_slots=(json.dumps(["15:00", "15:30"]) if world in _EVALUATED_WORLDS
                                else None),
            # R6-C2 (R6C1RV-S01): the slots belong to ONE evaluated date, written with them.
            active_requested_date=(SLOTS_DATE if world in _EVALUATED_WORLDS else None),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        wamid = f"wamid.R6C1.IN.{n:07d}"
        db.add(WhatsAppMessage(thread_id=th.id, direction="in", wa_message_id=wamid,
                               message_type="text", text=text, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=th.id, wa_message_id=wamid, wa_id=c.wa_id, status="triggered"))
        db.commit()
        return th.id, c.wa_id, wamid


#: R6-C2: the business clock is frozen on Wednesday 2026-11-11 12:00 (Buenos Aires) and the
#: `slots` world's evaluation is Thursday 2026-11-12, so "el jueves" / "mañana" name it.
#: (R6-C1 seeded no date and so pinned day-blind acceptance.)
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
SLOTS_DATE = "2026-11-12"


def run_case(eng, world: str, reply: str, sb: bool, direct: bool) -> dict:
    text = _CUSTOMER.get(world, _DEFAULT_CUSTOMER)
    tid, wa, wamid = _seed(eng, world, text)
    ev = ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                              message_type="text", text=text)

    def hook(ce):
        ce._call_openai_inner = lambda messages: C._decision(reply)
    with mock.patch.object(RV, "_utcnow", lambda: NOW), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                       lambda *a, **k: (f"wamid.R6C1.FLOW.{next(C._FSEQ):06d}", 200)), \
            H.Meta() as m:
        out, ce = H.run_turn(eng, ev, cfg=C._cfg(sb, direct), engine_hook=hook)
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        oqs = db.execute(select(WhatsAppOutstandingQuestion).where(
            WhatsAppOutstandingQuestion.thread_id == tid)).scalars().all()
        cands = db.execute(select(WhatsAppThreadCandidate).where(
            WhatsAppThreadCandidate.thread_id == tid)).scalars().all()
        env = getattr(ce, "_turn_outbound_envelope", None)
        return dict(
            world=world, ai_calls=getattr(ce, "_reply_calls", 0),
            armed=bool(ce._integrated_features_armed()),
            sent=[t for _w, t in m.sent], egress=len(m.egress),
            pending_fuzzy=st.pending_fuzzy_catalog_key, needs_human=st.needs_human,
            preferred_time=getattr(st, "preferred_time", None),
            oq=[(q.question_kind, q.asked_by) for q in oqs],
            candidates=[(x.marca, x.modelo, x.status) for x in cands],
            revisions=db.execute(select(func.count()).select_from(ThreadRevision).where(
                ThreadRevision.thread_id == tid)).scalar_one(),
            route=getattr(getattr(ce, "_turn_route", None), "name", None),
            discarded=getattr(ce, "_discarded_ai_reply", None),
            provenance=getattr(getattr(env, "provenance", None), "name", None),
            preferred_day=st.preferred_day,
            fallback=getattr(getattr(env, "fallback_id", None), "name", None),
            refused=sorted({f.claim for f in getattr(env, "findings", ())
                            if not f.allowed and f.action != RV.ACTION_ALLOWED}))


#: With a focus vehicle and no zone, `_scrub_invented_price` (BUG-2) replaces an invented
#: amount with its own missing-zone request before the validator runs (as in the R6 corpus).
UPSTREAM_GUARD = set()   # R6-C4B: was {("PRICE", "corolla")}; model prose no longer reaches the BUG-2 scrub — the invented price is refused by verification (standard fallback)


def _expected_candidates(world):
    if world not in _VEHICLE:
        return []
    marca, modelo, _ = _VEHICLE[world]
    return [(marca, modelo, "current_focus")]


class _Base(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = C._engine()

    def _common(self, r):
        self.assertTrue(r["armed"], "R1/R2/R3 interlock must be armed")
        self.assertEqual(r["ai_calls"], 1, f"the turn must reach the reply model: {r}")
        self.assertEqual(r["egress"], 0)
        self.assertEqual(len(r["sent"]), 1, r)
        self.assertIsNone(r["pending_fuzzy"], f"prose armed a pending vehicle: {r}")
        self.assertEqual(r["candidates"], _expected_candidates(r["world"]), r)
        self.assertEqual(r["revisions"], 0)
        self.assertFalse(r["needs_human"])

    def refused(self, reply, forbidden, claim, worlds=WORLDS):
        for world in worlds:
            for sb, direct in C.POSTURES:
                with self.subTest(reply=reply, world=world, sb=sb, direct=direct):
                    r = run_case(self.eng, world, reply, sb, direct)
                    self._common(r)
                    if r["route"] in DD.PROSE_NEVER_DELIVERED:   # R6-C4A / R6-C4B-R1
                        DD.assert_ai_body_discarded(self, r, reply, forbidden)
                        continue
                    for frag in forbidden:
                        self.assertNotIn(frag, r["sent"][0], f"false content delivered: {r}")
                    self.assertNotEqual(" ".join(r["sent"][0].split()), " ".join(reply.split()), r)
                    if (claim, world) in UPSTREAM_GUARD:
                        # an EARLIER deterministic guard removed it before the validator
                        # (`_scrub_invented_price`, BUG-2): its own missing-zone request
                        self.assertEqual(r["refused"], [], r)
                        self.assertEqual(r["oq"], [("INSPECTION_LOCATION", "REPLY_MODEL")], r)
                        continue
                    self.assertIn(claim, r["refused"], r)
                    self.assertEqual(r["provenance"], "VALIDATION_FALLBACK", r)
                    # any outstanding question is the fallback's DECLARED one
                    for kind, by in r["oq"]:
                        self.assertEqual(by, "DETERMINISTIC_TEMPLATE", r)
                    if world in _LOCATED:
                        self.assertNotIn("qué zona", r["sent"][0])
                        self.assertNotIn("localidad o barrio", r["sent"][0])
                    if world in _VEHICLE:
                        self.assertNotIn("qué vehículo", r["sent"][0])

    def kept(self, reply, worlds=WORLDS):
        for world in worlds:
            for sb, direct in C.POSTURES:
                with self.subTest(reply=reply, world=world, sb=sb, direct=direct):
                    r = run_case(self.eng, world, reply, sb, direct)
                    self._common(r)
                    if r["route"] in DD.PROSE_NEVER_DELIVERED:   # R6-C4A / R6-C4B-R1
                        DD.assert_ai_body_discarded(self, r, reply)
                        continue
                    self.assertEqual(" ".join(r["sent"][0].split()), " ".join(reply.split()),
                                     f"reply altered: {r}")
                    self.assertIsNone(r["fallback"], r)
                    self.assertEqual(r["provenance"], "VERIFIED_GENERATED")  # R6-C4B: delivered model words are verified
                    for kind, by in r["oq"]:      # only a genuine missing-place question
                        self.assertEqual(kind, "INSPECTION_LOCATION", r)
                        self.assertNotIn(world, _LOCATED, r)


class S01_ConcreteWindowsFullEngine(_Base):

    def test_s01_01_reviewer_windows_are_refused(self):
        self.refused("Te esperamos el jueves de 15:00 a 15:30.", ["15:00", "15:30"], "AVAILABILITY",
                     worlds=("new", "corolla", "located", "both"))
        self.refused("Podemos ir mañana entre 10:00 y 11:00.", ["10:00", "11:00"], "AVAILABILITY")

    def test_s01_02_structural_variants_are_refused(self):
        for reply in ("Te ofrezco el viernes de 10:00 a 11:00.",
                      "Te esperamos el 14/11 de 15:00 a 16:00.",
                      "El turno sería de 16:00 a 16:30."):
            self.refused(reply, ["10:00 a 11:00", "15:00 a 16:00", "16:00 a 16:30"],
                         "AVAILABILITY", worlds=("new", "both", "slots"))

    def test_s01_03_general_hours_reach_the_customer(self):
        for reply in ("Trabajamos de lunes a viernes de 9:00 a 18:00.",
                      "De 9:00 a 18:00 atendemos consultas.",
                      "Los sábados atendemos de 9:00 a 13:00."):
            self.kept(reply)

    def test_s01_04_an_evaluated_window_is_supported(self):
        self.kept("Te esperamos el jueves de 15:00 a 15:30.", worlds=("slots",))


class S02_VehicleFramesFullEngine(_Base):

    def test_s02_01_reviewer_examples_are_refused(self):
        for reply in ("Perfecto, revisamos tu gol mañana.", "Gol, perfecto, lo revisamos."):
            self.refused(reply, ["gol", "Gol"], "VEHICLE")

    def test_s02_02_the_same_frames_with_the_canonical_gol_are_kept(self):
        # R6-C3: "mañana" makes the first a scheduling proposition, so it is kept in the world
        # whose thread holds tomorrow's evaluation; the second names no day.
        self.kept("Perfecto, revisamos tu gol mañana.", worlds=("gol_eval",))
        self.kept("Gol, perfecto, lo revisamos.", worlds=("gol", "gol_eval"))

    def test_s02_03_ordinary_prose_still_reaches_the_customer(self):
        for reply in ("Uno nunca sabe.", "Revisamos todo uno por uno.",
                      "Podemos revisar primero uno de los dos autos y después el otro.",
                      "Lo vemos punto por punto.", "¡Buena idea! Revisarlo antes de comprar te ahorra sorpresas."):
            self.kept(reply)


class P01_QuestionPropositionsFullEngine(_Base):

    def test_p01_01_invented_price_question(self):
        self.refused("¿Te parece bien $12.345 por la revisión?", ["12.345"], "PRICE")

    def test_p01_02_unevaluated_slot_question(self):
        self.refused("¿Te reservo el jueves a las 11:30?", ["11:30"], "AVAILABILITY")

    def test_p01_03_non_canonical_window_question(self):
        self.refused("¿Podemos ir mañana entre 10:00 y 11:00?", ["10:00", "11:00"], "AVAILABILITY")

    def test_p01_04_wrong_vehicle_booking_question(self):
        self.refused("¿Coordinamos la revisión de tu Fiat Uno 2015?", ["Fiat", "Uno"], "VEHICLE")

    def test_p01_05_wrong_place_visit_question(self):
        self.refused("¿Vamos hasta Pilar mañana?", ["Pilar"], "LOCATION",
                     worlds=("located", "both"))

    def test_p01_06_information_seeking_questions_survive(self):
        self.kept("¿Es un Peugeot 2008?", worlds=("new", "located"))
        self.kept("¿Querés que coordinemos la revisión?")
        self.kept("¿Te parece buena idea revisarlo antes de comprar?")
        self.kept("¿Te reservo el jueves a las 15:00?", worlds=("slots",))


if __name__ == "__main__":
    unittest.main()
