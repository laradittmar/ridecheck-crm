"""R6-C2 — R6C1RV-S01: a stated day/date is bound to the canonical EVALUATED date.

Full ConversationEngine production path (the R6 / R6-C1 harness: `handle_message` → CE →
reply model with a canned provider answer → reply admission → `_send_text_to_wa` →
`_validate_outbound_text` → `plan.compose` → OutboundSafetyGate → Meta recorder), with the
R1/R2/R3 interlock armed, in every posture:
  CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED false / true × SMART_BOOKING_CE_ENABLED false / true.

Canonical state (seeded exactly as ScheduleService writes it: `active_requested_date`
TOGETHER with `last_offered_slots`, `last_visible_slots` a subset of that evaluation):
Toyota Corolla 2018, CABA / Palermo, quote already sent (stage QUOTED), evaluated slots
10:00 / 11:00 / 15:00 / 15:30 on the evaluated date.

The business clock is frozen (`response_validator._utcnow`) at 2026-11-11T15:00Z =
Wednesday 2026-11-11 12:00 in America/Argentina/Buenos_Aires, so "hoy" = Wed 11, "mañana" =
Thu 12, "pasado mañana" = Fri 13, and the reviewer's dates are literal: Mon 2026-11-16,
Sat 2026-11-14. The timezone classes move that clock across the UTC and the Buenos Aires
midnights. The database session timezone is never the business-date authority (on
PostgreSQL the disposable server runs `TimeZone=Etc/UTC`).

Database: SQLite in memory; set R6_PG_URL to run the identical cases on PostgreSQL.

Every refused case asserts: exactly one message; the wrong day and the times are not in it;
it is the plan-appropriate validation fallback (AVAILABILITY refused); no pending vehicle, no
candidate change, no booking/revision, no stored preferred time; any outstanding question is
the fallback's DECLARED one. Every kept case asserts the reply is verbatim, AI_COMPOSED.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import date, datetime, time, timezone
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (the shared harness module)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, WhatsAppContact,  # noqa: E402
                        WhatsAppMessage, WhatsAppOutstandingQuestion, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import response_validator as RV  # noqa: E402

H = C.H
SLOTS = ["10:00", "11:00", "15:00", "15:30"]
#: Wednesday 2026-11-11 12:00 in Buenos Aires.
NOON_WED = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
#: UTC has crossed midnight, Buenos Aires has not: Wed 2026-11-11 22:30 local.
UTC_CROSSED = datetime(2026, 11, 12, 1, 30, tzinfo=timezone.utc)
#: Buenos Aires has crossed midnight too: Thu 2026-11-12 00:30 local.
BA_CROSSED = datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)

MON, SAT, WED, THU, FRI = "2026-11-16", "2026-11-14", "2026-11-11", "2026-11-12", "2026-11-13"
_CUSTOMER = "Perfecto, gracias. Lo voy a pensar y te aviso"


def _seed(eng, *, date_iso, offered=SLOTS, visible=SLOTS, booked=None):
    n = next(C._SEQ)
    with Session(eng) as db:
        c = WhatsAppContact(wa_id=f"54900{83000000 + n:08d}", display_name="R6C2 Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False, flag="PRESUPUESTO_ENVIADO")
        db.add_all([c, lead]); db.flush()
        th = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
        db.add(th); db.flush()
        cand = WhatsAppThreadCandidate(thread_id=th.id, tipo_vehiculo="AUTO", marca="Toyota",
                                       modelo="Corolla", anio=2018, status="current_focus",
                                       source_text="Toyota Corolla 2018")
        db.add(cand); db.flush()
        rev_id = None
        if booked is not None:
            rev = ThreadRevision(thread_id=th.id, candidate_id=cand.id, status="booked",
                                 scheduled_date=booked[0], scheduled_time=booked[1],
                                 marca="Toyota", modelo="Corolla", anio=2018,
                                 zone_group="CABA")
            db.add(rev); db.flush(); rev_id = rev.id
        db.add(WhatsAppThreadState(
            thread_id=th.id, last_stage=("BOOKED" if booked else "QUOTED"), needs_human=False,
            current_focus_candidate_id=cand.id, current_revision_id=rev_id,
            home_zone_group="CABA", home_zone_detail="Palermo",
            active_requested_date=date_iso,
            last_offered_slots=(json.dumps(offered) if offered is not None else None),
            last_visible_slots=(json.dumps(visible) if visible is not None else None),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        db.commit()
        return th.id, c.wa_id


def _inbound(eng, tid, wa):
    n = next(C._SEQ)
    wamid = f"wamid.R6C2.IN.{n:07d}"
    with Session(eng) as db:
        db.add(WhatsAppMessage(thread_id=tid, direction="in", wa_message_id=wamid,
                               message_type="text", text=_CUSTOMER, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=tid, wa_message_id=wamid, wa_id=wa, status="triggered"))
        db.commit()
    return ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                                message_type="text", text=_CUSTOMER)


def turn(eng, tid, wa, reply, sb, direct, now=NOON_WED) -> dict:
    ev = _inbound(eng, tid, wa)

    def hook(ce):
        ce._call_openai_inner = lambda messages: C._decision(reply)
    with mock.patch.object(RV, "_utcnow", lambda: now), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                       lambda *a, **k: (f"wamid.R6C2.FLOW.{next(C._FSEQ):06d}", 200)), \
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
            ai_calls=getattr(ce, "_reply_calls", 0),
            armed=bool(ce._integrated_features_armed()),
            sent=[t for _w, t in m.sent], egress=len(m.egress),
            pending_fuzzy=st.pending_fuzzy_catalog_key, needs_human=st.needs_human,
            preferred_time=st.preferred_time, preferred_day=st.preferred_day,
            active_date=st.active_requested_date,
            oq=[(q.question_kind, q.asked_by) for q in oqs],
            candidates=[(x.marca, x.modelo, x.status) for x in cands],
            revisions=db.execute(select(func.count()).select_from(ThreadRevision).where(
                ThreadRevision.thread_id == tid)).scalar_one(),
            route=getattr(getattr(ce, "_turn_route", None), "name", None),
            discarded=getattr(ce, "_discarded_ai_reply", None),
            provenance=getattr(getattr(env, "provenance", None), "name", None),
            fallback=getattr(getattr(env, "fallback_id", None), "name", None),
            findings=[(f.claim, f.allowed, f.detail) for f in getattr(env, "findings", ())],
            refused=sorted({f.claim for f in getattr(env, "findings", ())
                            if not f.allowed and f.action != RV.ACTION_ALLOWED}))


def run_case(eng, reply, sb, direct, *, now=NOON_WED, **seed) -> dict:
    tid, wa = _seed(eng, **seed)
    return turn(eng, tid, wa, reply, sb, direct, now=now)


def _same(a, b):
    return " ".join(a.split()) == " ".join(b.split())


#: R6-C4A — the transactional routes (`transactional_boundary.TransactionalRoute`).
TRANSACTIONAL = frozenset({"QUOTATION", "SCHEDULING", "FLOW_PENDING", "BOOKED"})
#: R6-C4B-R1 (owner decision: AI bridge + canonical fact block + canonical action) — model
#: prose never reaches the customer on ANY route: the interpreter body is discarded at the
#: boundary and a historical body is never a claim-free bridge. Every harness turn therefore
#: expects the canonical deterministic message, whatever the body says; the R6-C1..C4
#: analysis of undeclared prose stays covered by the unit suites and by the bridge's own
#: defence in depth (`test_r6_c4b_verified_generation`).
PROSE_NEVER_DELIVERED = frozenset({"NON_TRANSACTIONAL", "QUOTATION", "SCHEDULING",
                                   "FLOW_PENDING", "BOOKED", None})


#: what a BOOKED turn sends for the fixture booking (Mon 16/11 10:00): the committed revision's
#: own date and start time, then the generic handoff's approved line
BOOKED_STATUS_TEXT = ("Tu turno está confirmado para el lunes 16/11 a las 10:00.\n\n"
                      "Para no equivocarme con tu caso, lo sigue un asesor de RideCheck. "
                      "Te contactamos a la brevedad.")


def assert_ai_body_discarded(tc, r, reply, forbidden=(), booked=False):
    """R6-C4A expectation for a harness turn on a TRANSACTIONAL route (QUOTED / BOOKED seed).

    Before R6-C4A these turns validated the model's prose and delivered it when the temporal
    analyser accepted it. Now the model body — wrong or canonical — is discarded at the reply
    boundary and the canonical deterministic renderer is sent: the quote (QUOTATION), the
    committed booking's own date/time plus a human handoff (BOOKED), or the neutral handoff
    line when the booking facts are incomplete. Nothing the model wrote reaches the customer
    or the state."""
    tc.assertIn(r["route"], PROSE_NEVER_DELIVERED, r)
    tc.assertEqual(len(r["sent"]), 1, r)
    sent = r["sent"][0]
    tc.assertFalse(_same(sent, reply), f"model body delivered on a transactional route: {r}")
    tc.assertNotIn(" ".join(reply.split()).casefold(), " ".join(sent.split()).casefold(), r)
    canonical = (BOOKED_STATUS_TEXT if r["route"] == "BOOKED" and booked else "")
    for frag in forbidden:
        if frag not in canonical:          # the committed values themselves may be restated
            tc.assertNotIn(frag, sent, f"model content delivered: {r}")
    tc.assertIsNone(r["preferred_time"], r)
    tc.assertIsNone(r["preferred_day"], r)
    tc.assertIsNone(r["pending_fuzzy"], r)
    for _kind, by in r["oq"]:
        tc.assertNotEqual(by, "REPLY_MODEL", r)
    if r["route"] == "BOOKED":
        tc.assertTrue(r["needs_human"], r)
        if booked:
            tc.assertEqual(sent, BOOKED_STATUS_TEXT, r)
            tc.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
    else:
        tc.assertFalse(r["needs_human"], r)
        tc.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)


class _Base(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = C._engine()

    def _common(self, r, booked=False):
        self.assertTrue(r["armed"], "R1/R2/R3 interlock must be armed")
        self.assertEqual(r["ai_calls"], 1, f"the turn must reach the reply model: {r}")
        self.assertEqual(r["egress"], 0)
        self.assertEqual(len(r["sent"]), 1, r)
        self.assertIsNone(r["pending_fuzzy"], r)
        self.assertEqual(r["candidates"], [("Toyota", "Corolla", "current_focus")], r)
        self.assertEqual(r["revisions"], 1 if booked else 0, r)
        self.assertEqual(r["needs_human"], r.get("route") == "BOOKED", r)
        self.assertIsNone(r["preferred_time"], f"prose became a stored time: {r}")
        self.assertIsNone(r["preferred_day"], r)

    def refused(self, reply, forbidden, *, booked=None, now=NOON_WED, **seed):
        for sb, direct in C.POSTURES:
            with self.subTest(reply=reply, seed=seed, booked=booked, sb=sb, direct=direct,
                              now=now.isoformat()):
                r = run_case(self.eng, reply, sb, direct, now=now, booked=booked, **seed)
                self._common(r, booked=bool(booked))
                if r["route"] in PROSE_NEVER_DELIVERED:  # R6-C4A / R6-C4B-R1
                    assert_ai_body_discarded(self, r, reply, forbidden, booked=bool(booked))
                    continue
                self.assertFalse(_same(r["sent"][0], reply), f"false day delivered: {r}")
                for frag in forbidden:
                    self.assertNotIn(frag, r["sent"][0], f"false content delivered: {r}")
                self.assertIn("AVAILABILITY", r["refused"], r)
                self.assertEqual(r["provenance"], "VALIDATION_FALLBACK", r)
                for _kind, by in r["oq"]:
                    self.assertEqual(by, "DETERMINISTIC_TEMPLATE", r)
                self.assertNotIn("qué zona", r["sent"][0])
                self.assertNotIn("qué vehículo", r["sent"][0])

    def kept(self, reply, *, booked=None, now=NOON_WED, **seed):
        for sb, direct in C.POSTURES:
            with self.subTest(reply=reply, seed=seed, booked=booked, sb=sb, direct=direct,
                              now=now.isoformat()):
                r = run_case(self.eng, reply, sb, direct, now=now, booked=booked, **seed)
                self._common(r, booked=bool(booked))
                if r["route"] in PROSE_NEVER_DELIVERED:  # R6-C4A / R6-C4B-R1
                    assert_ai_body_discarded(self, r, reply, booked=bool(booked))
                    continue
                self.assertTrue(_same(r["sent"][0], reply), f"reply altered: {r}")
                self.assertIsNone(r["fallback"], r)
                self.assertEqual(r["provenance"], "AI_COMPOSED", r)
                self.assertEqual(r["oq"], [], r)


# ── Required mismatches (DD-M01 … DD-M12): all refused ─────────────────────────────

class DD_MismatchedDayRefused(_Base):

    def test_m01_weekday_differs_window(self):
        self.refused("Te esperamos el jueves de 10:00 a 11:00.", ["jueves", "10:00"], date_iso=MON)

    def test_m02_month_name_date_differs(self):
        self.refused("Te esperamos el 20 de noviembre de 15:00 a 15:30.",
                     ["20 de noviembre", "15:00"], date_iso=SAT)

    def test_m03_today_evaluated_tomorrow_stated(self):
        self.refused("Podemos ir mañana entre 10:00 y 11:00.", ["mañana", "10:00"], date_iso=WED)

    def test_m04_numeric_date_differs(self):
        self.refused("Te esperamos el 19/11 de 10:00 a 11:00.", ["19/11", "10:00"], date_iso=MON)

    def test_m05_single_time_wrong_weekday(self):
        self.refused("Te ofrezco el jueves a las 10:00.", ["jueves", "10:00"], date_iso=MON)

    def test_m06_question_reserves_wrong_weekday(self):
        self.refused("¿Te reservo el jueves de 10:00 a 11:00?", ["jueves", "10:00"], date_iso=MON)

    def test_m07_pasado_manana_is_another_day(self):
        self.refused("Pasado mañana te esperamos de 15:00 a 15:30.", ["Pasado mañana", "15:00"],
                     date_iso=MON)

    def test_m08_same_weekday_wrong_calendar_date(self):
        for reply in ("Te esperamos el lunes 23/11 de 10:00 a 11:00.",
                      "Te esperamos el lunes 23 de noviembre a las 10:00.",
                      "Te esperamos el 2026-11-23 de 10:00 a 11:00.",
                      "Te esperamos el 16/11/2027 de 10:00 a 11:00."):
            self.refused(reply, ["10:00"], date_iso=MON)

    def test_m09_correct_date_unsupported_time(self):
        self.refused("Te esperamos el lunes a las 12:00.", ["12:00"], date_iso=MON)

    def test_m10_correct_date_one_unsupported_endpoint(self):
        self.refused("Te esperamos el lunes de 10:00 a 12:00.", ["12:00"], date_iso=MON)

    def test_m11_anchor_with_no_canonical_date(self):
        self.refused("Te esperamos el lunes de 10:00 a 11:00.", ["lunes", "10:00"], date_iso=None)

    def test_m12_invalid_or_stale_canonical_date(self):
        for bad in ("2026-13-40", "lunes", "16/11/2026", "2026-11-16T10:00", "", "2026-02-30"):
            self.refused("Te esperamos el lunes de 10:00 a 11:00.", ["10:00"], date_iso=bad)
        # stale: an evaluation of a day already past on the business calendar
        self.refused("Te esperamos el lunes de 10:00 a 11:00.", ["10:00"], date_iso="2026-11-09")

    def test_m13_unresolvable_day(self):
        for reply in ("Te esperamos el 31/11 de 10:00 a 11:00.",
                      "Te esperamos el día 40 a las 10:00."):
            self.refused(reply, ["10:00"], date_iso=MON)


# ── Required valid cases (DD-V01 … DD-V08): all delivered verbatim ──────────────────

class DD_CorrectDayKept(_Base):

    def test_v01_matching_weekday_and_times(self):
        self.kept("Te esperamos el lunes de 10:00 a 11:00.", date_iso=MON)
        self.kept("Te esperamos el sábado de 15:00 a 15:30.", date_iso=SAT)

    def test_v02_matching_explicit_date(self):
        self.kept("Te esperamos el 14 de noviembre de 15:00 a 15:30.", date_iso=SAT)
        self.kept("Te esperamos el lunes 16 de noviembre de 2026 a las 10:00.", date_iso=MON)
        self.kept("Te esperamos el 2026-11-16 de 10:00 a 11:00.", date_iso=MON)

    def test_v03_matching_numeric_date(self):
        self.kept("Te esperamos el 16/11 de 10:00 a 11:00.", date_iso=MON)
        self.kept("Te esperamos el lunes 16/11/2026 a las 10:00.", date_iso=MON)

    def test_v04_relative_days(self):
        self.kept("Podemos ir hoy entre 15:00 y 15:30.", date_iso=WED)
        self.kept("Podemos ir mañana entre 10:00 y 11:00.", date_iso=THU)
        self.kept("Pasado mañana te esperamos de 15:00 a 15:30.", date_iso=FRI)
        self.kept("El día 16 te esperamos a las 10:00.", date_iso=MON)

    def test_v05_supported_single_time(self):
        self.kept("Te ofrezco el lunes a las 10:00.", date_iso=MON)
        self.kept("¿Te reservo el lunes a las 15:30?", date_iso=MON)

    def test_v06_general_business_hours(self):
        for reply in ("Trabajamos de lunes a viernes de 9:00 a 18:00.",
                      "Los sábados atendemos de 9:00 a 13:00."):
            self.kept(reply, date_iso=MON)
            self.kept(reply, date_iso=None, offered=None, visible=None)

    def test_v08_no_anchor_keeps_the_endpoint_rule(self):
        self.kept("Te ofrezco las 10:00 o las 15:30.", date_iso=MON)
        self.kept("Te ofrezco las 10:00 o las 15:30.", date_iso=None)
        self.refused("Te ofrezco las 12:00.", ["12:00"], date_iso=MON)


# ── Confirmed booking (DD-V07 / mutation 11) ──────────────────────────────────────

class DD_ConfirmedBooking(_Base):
    BOOKED = (date(2026, 11, 16), time(10, 0))

    def test_v07_booking_restates_its_committed_date_and_window(self):
        self.kept("Te confirmamos: te esperamos el lunes 16/11 a las 10:00.",
                  booked=self.BOOKED, date_iso=MON)
        self.kept("Te confirmamos: te esperamos el lunes de 10:00 a 10:45.",
                  booked=self.BOOKED, date_iso=MON)

    def test_booking_uses_its_committed_date_not_the_last_requested(self):
        # the customer last ASKED about Thursday 19; the committed booking is Monday 16
        self.kept("Te esperamos el lunes a las 10:00.", booked=self.BOOKED, date_iso="2026-11-19")
        self.refused("Te esperamos el jueves a las 10:00.", ["jueves"], booked=self.BOOKED,
                     date_iso="2026-11-19")

    def test_booking_may_not_restate_another_time(self):
        self.refused("Te esperamos el lunes a las 11:00.", ["11:00"], booked=self.BOOKED,
                     date_iso=MON)


# ── State consistency: the date and the slots belong to ONE evaluation ───────────

class DD_StateConsistency(_Base):

    def test_slots_without_a_date(self):
        self.refused("Te esperamos el lunes a las 10:00.", ["lunes"], date_iso=None)

    def test_date_without_slots(self):
        self.refused("Te esperamos el lunes a las 10:00.", ["lunes"], date_iso=MON,
                     offered=None, visible=None)
        self.refused("Te esperamos el lunes a las 10:00.", ["lunes"], date_iso=MON,
                     offered=[], visible=None)

    def test_visible_slots_from_another_evaluation(self):
        # the date and `last_offered_slots` are one evaluation (10:00 / 11:00); the visible
        # list is not a subset of it, so it is not that evaluation's and names no day.
        self.refused("Te esperamos el lunes de 15:00 a 15:30.", ["15:00"], date_iso=MON,
                     offered=["10:00", "11:00"], visible=["15:00", "15:30"])

    def test_visible_slots_without_the_evaluation_record(self):
        self.refused("Te esperamos el lunes a las 10:00.", ["lunes"], date_iso=MON,
                     offered=None, visible=SLOTS)

    def test_the_canonical_date_follows_a_new_evaluation(self):
        for sb, direct in C.POSTURES:
            with self.subTest(sb=sb, direct=direct):
                tid, wa = _seed(self.eng, date_iso=MON)
                r1 = turn(self.eng, tid, wa, "Te esperamos el lunes a las 10:00.", sb, direct)
                # R6-C4A / R6-C4B-R1 (review B-1): every model body is discarded — including
                # "Te esperamos …", an implied confirmation on a thread that is not booked
                assert_ai_body_discarded(self, r1, "Te esperamos el lunes a las 10:00.")
                # a NEW request: ScheduleService evaluates Thursday 12 — date and slots are
                # written together, exactly as every CE evaluation site writes them.
                # (R6-C3: a bare "el jueves" names only the NEXT Thursday, so this control
                # uses Thursday 12; Thursday 19 is refused by the weekday horizon.)
                with Session(self.eng) as db:
                    st = db.execute(select(WhatsAppThreadState).where(
                        WhatsAppThreadState.thread_id == tid)).scalar_one()
                    st.active_requested_date = "2026-11-12"
                    st.last_offered_slots = json.dumps(["10:00", "11:00"])
                    st.last_visible_slots = json.dumps(["10:00", "11:00"])
                    db.commit()
                r2 = turn(self.eng, tid, wa, "Te esperamos el lunes a las 10:00.", sb, direct)
                assert_ai_body_discarded(self, r2, "Te esperamos el lunes a las 10:00.")
                r3 = turn(self.eng, tid, wa, "Te esperamos el jueves a las 10:00.", sb, direct)
                assert_ai_body_discarded(self, r3, "Te esperamos el jueves a las 10:00.")
                for r in (r1, r2, r3):
                    self.assertIsNone(r["preferred_time"], r)
                    self.assertEqual(r["revisions"], 0, r)


# ── Business timezone across the UTC and the Buenos Aires midnights ───────────────

class DD_BusinessTimezoneRollover(_Base):

    def test_utc_crossed_midnight_buenos_aires_has_not(self):
        # 2026-11-12T01:30Z = Wed 11 22:30 in Buenos Aires: hoy = Wed 11, mañana = Thu 12
        self.kept("Podemos ir hoy entre 15:00 y 15:30.", date_iso=WED, now=UTC_CROSSED)
        self.kept("Podemos ir mañana entre 10:00 y 11:00.", date_iso=THU, now=UTC_CROSSED)
        self.kept("Pasado mañana te esperamos a las 10:00.", date_iso=FRI, now=UTC_CROSSED)
        self.refused("Podemos ir hoy entre 10:00 y 11:00.", ["hoy"], date_iso=THU,
                     now=UTC_CROSSED)
        self.refused("Podemos ir mañana entre 10:00 y 11:00.", ["mañana"], date_iso=FRI,
                     now=UTC_CROSSED)

    def test_buenos_aires_crossed_midnight(self):
        # 2026-11-12T03:30Z = Thu 12 00:30 in Buenos Aires: hoy = Thu 12, mañana = Fri 13
        self.kept("Podemos ir hoy entre 10:00 y 11:00.", date_iso=THU, now=BA_CROSSED)
        self.kept("Podemos ir mañana entre 10:00 y 11:00.", date_iso=FRI, now=BA_CROSSED)
        self.refused("Podemos ir mañana entre 10:00 y 11:00.", ["mañana"], date_iso=THU,
                     now=BA_CROSSED)
        self.refused("Pasado mañana te esperamos a las 10:00.", ["Pasado"], date_iso=FRI,
                     now=BA_CROSSED)
        # the evaluation of Wednesday 11 is now in the past on the business calendar
        self.refused("Te esperamos el miércoles a las 10:00.", ["miércoles"], date_iso=WED,
                     now=BA_CROSSED)

    def test_weekday_at_the_boundary(self):
        self.kept("Te esperamos el jueves a las 10:00.", date_iso=THU, now=UTC_CROSSED)
        # R6-C4 (R6C3RV-S01 H1): on BA Thursday a bare "el jueves" is ambiguous (today, or
        # next Thursday — CE's own reading) and refused; "hoy jueves" is today. Was: kept.
        self.refused("Te esperamos el jueves a las 10:00.", ["jueves"], date_iso=THU,
                     now=BA_CROSSED)
        self.kept("Te esperamos hoy jueves a las 10:00.", date_iso=THU, now=BA_CROSSED)
        self.refused("Te esperamos el miércoles a las 10:00.", ["miércoles"], date_iso=THU,
                     now=UTC_CROSSED)


if __name__ == "__main__":
    unittest.main()
