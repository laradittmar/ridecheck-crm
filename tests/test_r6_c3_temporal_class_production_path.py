"""R6-C3 — R6C2RV-S01: the temporal class on the FULL production path.

The R6 / R6-C1 / R6-C2 harness (`handle_message` → CE → reply model with a canned provider
answer → reply admission → `_send_text_to_wa` → `_validate_outbound_text` → `plan.compose` →
OutboundSafetyGate → Meta recorder), R1/R2/R3 interlock armed, in every posture:
  CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED false / true × SMART_BOOKING_CE_ENABLED false / true.

Canonical state is seeded exactly as ScheduleService writes it (`active_requested_date`
together with `last_offered_slots`; `last_visible_slots` a subset). Business clock frozen at
Wednesday 2026-11-11 12:00 America/Argentina/Buenos_Aires. Evaluated slots 10:00 / 11:00 /
15:00 / 15:30.

Every refused case asserts (R6-C2 harness `refused`): exactly one message; the false content
is not in it; the plan-appropriate VALIDATION_FALLBACK with AVAILABILITY refused; no stored
preferred day/time; no revision or booking; any outstanding question is the fallback's
declared one; no pending vehicle, no candidate change. Every kept case is delivered
verbatim (whitespace aside), AI_COMPOSED, with no outstanding question.

Database: SQLite in memory; set R6_PG_URL to run the identical cases on PostgreSQL.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from datetime import date, datetime, time, timezone

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_r6_c2_day_date_production_path as DD  # noqa: E402  (the R6-C2 harness)
import test_r6_c3_temporal_class as T  # noqa: E402  (the generated grammar)
import test_r6_full_engine_prose_corpus as C  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (Lead, ThreadRevision, WhatsAppContact, WhatsAppThread,  # noqa: E402
                        WhatsAppThreadCandidate, WhatsAppThreadState)

MON, THU, MON23 = DD.MON, DD.THU, "2026-11-23"
BOOKED = (date(2026, 11, 16), time(10, 0))


class _Base(DD._Base):
    """`refused` / `kept` from the R6-C2 harness; whitespace-normalised verbatim check."""


# ── R6C2RV-S01 (a): weekday + day number ───────────────────────────────────────

class TC_A_WeekdayAndDayNumber(_Base):

    def test_a1_monday_23_while_monday_16_evaluated(self):
        self.refused("Te esperamos el lunes 23 de 10:00 a 11:00.", ["23", "10:00"], date_iso=MON)

    def test_a2_thursday_19_while_thursday_12_evaluated(self):
        self.refused("Te esperamos el jueves 19 a las 10:00.", ["19", "10:00"], date_iso=THU)

    def test_a3_booking_restates_monday_23(self):
        self.refused("Tu turno es el lunes 23 a las 10:00.", ["23", "10:00"], booked=BOOKED,
                     date_iso=MON)

    def test_a_controls(self):
        self.kept("Te esperamos el lunes 16 de 10:00 a 11:00.", date_iso=MON)
        self.kept("Te esperamos el jueves 12 a las 10:00.", date_iso=THU)
        self.kept("Tu turno es el lunes 16 a las 10:00.", booked=BOOKED, date_iso=MON)


# ── (b) alternative / unrecognised forms ─────────────────────────────────────────

class TC_B_AlternativeForms(_Base):
    WRONG = ("el 19", "el 19-11", "el 19.11", "el 19 nov", "el noviembre 19", "el 19 de nov.",
             "el 2026-11-19T10:00", "el finde", "la semana que viene")
    RIGHT = ("el 16", "el 16-11", "el 16.11", "el 16 nov", "el noviembre 16", "el 16 de nov.",
             "el 2026-11-16T10:00")

    def test_b_wrong_forms_refused(self):
        for form in self.WRONG:
            reply = (f"Te esperamos {form}." if "T10" in form
                     else f"Te esperamos {form} a las 10:00.")
            self.refused(reply, ["10:00"], date_iso=MON)

    def test_b_canonical_forms_kept(self):
        for form in self.RIGHT:
            reply = (f"Te esperamos {form}." if "T10" in form
                     else f"Te esperamos {form} a las 10:00.")
            self.kept(reply, date_iso=MON)


# ── (c) bare weekday horizon ─────────────────────────────────────────────────────

class TC_C_WeekdayHorizon(_Base):

    def test_c_this_monday_beyond_six_days(self):
        self.refused("Te esperamos este lunes a las 10:00.", ["lunes", "10:00"], date_iso=MON23,
                     offered=DD.SLOTS, visible=DD.SLOTS)
        self.refused("Te esperamos el lunes a las 10:00.", ["lunes", "10:00"], date_iso=MON23)

    def test_c_controls(self):
        self.kept("Te esperamos este lunes a las 10:00.", date_iso=MON)
        self.kept("Te esperamos el lunes 23 a las 10:00.", date_iso=MON23)


# ── (d) non-HH:MM times ──────────────────────────────────────────────────────────

class TC_D_ClockForms(_Base):
    FORMS = ("a las 10", "10 hs", "10hs", "10am", "a las 3pm", "a las 3 y media",
             "a las 10 y cuarto")

    def test_d_wrong_day_with_any_clock_form(self):
        for form in self.FORMS:
            self.refused(f"Te esperamos el jueves {form}.", ["jueves"], date_iso=MON)

    def test_d_canonical_controls(self):
        for form in ("a las 10", "10 hs", "10hs", "10am", "a las 3pm", "a las 3 y media"):
            self.kept(f"Te esperamos el lunes {form}.", date_iso=MON)

    def test_d_non_slot_quarter_on_the_right_day(self):
        self.refused("Te esperamos el lunes a las 10 y cuarto.", ["10 y cuarto"], date_iso=MON)


# ── (e) complete-message scope ──────────────────────────────────────────────────

class TC_E_MessageScope(_Base):

    def test_e_day_and_time_in_different_sentences(self):
        for reply in ("Para el jueves 19/11. Te esperamos a las 10:00.",
                      "El jueves te esperamos. Horario: 10:00.",
                      "Para el jueves 19/11.\nTe esperamos a las 10:00.",
                      "Turno:\n- jueves 19/11\n- 10:00",
                      "• Día: jueves\n• Hora: 10:00"):
            self.refused(reply, ["jueves", "10:00"], date_iso=MON)

    def test_e_controls(self):
        for reply in ("Para el lunes 16/11. Te esperamos a las 10:00.",
                      "El lunes te esperamos. Horario: 10:00.",
                      "Para el lunes 16/11.\nTe esperamos a las 10:00.",
                      "Turno:\n- lunes 16/11\n- 10:00"):
            self.kept(reply, date_iso=MON)


# ── General business hours and ordinary prose (preserved) ──────────────────────

class TC_F_GeneralHours(_Base):

    def test_general_hours_kept_with_and_without_an_evaluation(self):
        for reply in ("Trabajamos de lunes a viernes de 9:00 a 18:00.",
                      "Nuestro horario de atención es de 9 a 18.",
                      "Atendemos los sábados de 9 a 13."):
            self.kept(reply, date_iso=MON)
            self.kept(reply, date_iso=None, offered=None, visible=None)

    def test_general_hours_beside_a_wrong_offer(self):
        # the customer-specific offer is validated on its own and removed; the general-hours
        # information around it is still delivered (no false text, no state)
        hours = "Trabajamos de lunes a viernes de 9:00 a 18:00."
        for sb, direct in C.POSTURES:
            with self.subTest(sb=sb, direct=direct):
                r = DD.run_case(self.eng, hours + " Te esperamos el jueves a las 10:00.", sb, direct,
                                date_iso=MON)
                self._common(r)
                # R6-C4A: QUOTED is transactional — the whole model body is discarded
                DD.assert_ai_body_discarded(self, r, hours + " Te esperamos el jueves a las 10:00.",
                                            ["jueves", "10:00", hours])
                self.assertEqual(r["oq"], [], r)


# ── Confirmed bookings: only the committed revision ──────────────────────────────

def _seed_booking(eng, *, status, stage="BOOKED", link=True):
    """A thread in stage BOOKED whose `current_revision_id` points to a revision of
    `status` (None: no revision at all)."""
    n = next(C._SEQ)
    with Session(eng) as db:
        c = WhatsAppContact(wa_id=f"54900{84000000 + n:08d}", display_name="R6C3 Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False, flag="PRESUPUESTO_ENVIADO")
        db.add_all([c, lead]); db.flush()
        th = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
        db.add(th); db.flush()
        cand = WhatsAppThreadCandidate(thread_id=th.id, tipo_vehiculo="AUTO", marca="Toyota",
                                       modelo="Corolla", anio=2018, status="current_focus",
                                       source_text="Toyota Corolla 2018")
        db.add(cand); db.flush()
        rev_id = None
        if status is not None:
            rev = ThreadRevision(thread_id=th.id, candidate_id=cand.id, status=status,
                                 scheduled_date=BOOKED[0], scheduled_time=BOOKED[1],
                                 marca="Toyota", modelo="Corolla", anio=2018, zone_group="CABA")
            db.add(rev); db.flush()
            rev_id = rev.id if link else None
        db.add(WhatsAppThreadState(
            thread_id=th.id, last_stage=stage, needs_human=False,
            current_focus_candidate_id=cand.id, current_revision_id=rev_id,
            home_zone_group="CABA", home_zone_detail="Palermo", active_requested_date=MON,
            last_offered_slots=json.dumps(DD.SLOTS), last_visible_slots=json.dumps(DD.SLOTS),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        db.commit()
        return th.id, c.wa_id


class TC_G_ConfirmedBooking(_Base):

    def test_required_refusals(self):
        for reply in ("Tu turno es el lunes 23 a las 10:00.",          # Monday 23 for Monday 16
                      "Tu turno es el lunes 9 a las 10:00.",           # right weekday, wrong number
                      "Tu turno es el 19-11 a las 10:00.",             # alternative form, wrong date
                      "Tu turno es el jueves 19/11. Te esperamos a las 10:00.",   # two sentences
                      "Tu turno es el lunes a las 11.",                # non-HH:MM, not committed
                      "Tu turno es el lunes de 10 a 11 hs."):          # invented end
            self.refused(reply, ["Tu turno"], booked=BOOKED, date_iso=MON)

    def test_committed_values_kept(self):
        for reply in ("Tu turno es el lunes 16 a las 10:00.", "Te esperamos el lunes a las 10.",
                      "Tu turno es el lunes 16/11 de 10:00 a 10:45."):
            self.kept(reply, booked=BOOKED, date_iso=MON)

    def _booking_case(self, status, link=True):
        for sb, direct in C.POSTURES:
            with self.subTest(status=status, link=link, sb=sb, direct=direct):
                tid, wa = _seed_booking(self.eng, status=status, link=link)
                r = DD.turn(self.eng, tid, wa, "Tu turno es el lunes 16 a las 10:00.", sb, direct)
                self.assertEqual(len(r["sent"]), 1, r)
                self.assertNotIn("10:00", r["sent"][0], r)
                # R6-C4A: BOOKED without a committed slot — the model body is discarded and
                # the neutral handoff line is sent (the booking facts are incomplete)
                DD.assert_ai_body_discarded(self, r, "Tu turno es el lunes 16 a las 10:00.", ["lunes"])
                self.assertNotIn("Tu turno", r["sent"][0], r)
                self.assertIsNone(r["preferred_time"], r)
                with Session(self.eng) as db:
                    n = db.execute(select(func.count()).select_from(ThreadRevision).where(
                        ThreadRevision.thread_id == tid)).scalar_one()
                self.assertEqual(n, 0 if status is None else 1, r)

    def test_booked_stage_without_a_booked_revision(self):
        self._booking_case(None)

    def test_current_revision_points_to_a_non_booked_revision(self):
        self._booking_case("provisional")
        self._booking_case("cancelled")


# ── Generated grammar corpus on the full path (bounded, deterministic sample) ─────

def full_path_sample(step=None):
    corpus = T.generate_corpus()
    step = step or max(1, len(corpus) // 56)
    picked = corpus[::step]
    # every frame and both outcomes are present
    assert {c[3].split("/")[0] for c in picked} >= {f[0] for f in T.FRAMES} | {"statement"}
    return picked


class TC_H_GeneratedCorpus(_Base):

    def test_generated_sample_all_postures(self):
        sample = full_path_sample()
        kept = refused = 0
        for text, _evaluated, want, label in sample:
            with self.subTest(label=label):
                if want == "kept":
                    self.kept(text, date_iso=MON)
                    kept += 1
                else:
                    self.refused(text, [], date_iso=MON)
                    refused += 1
        print(f"\nR6-C3 generated full-path sample: {len(sample)} cases × {len(C.POSTURES)} "
              f"postures; refused {refused}, kept {kept}")
        self.assertGreater(kept, 5)
        self.assertGreater(refused, 20)


if __name__ == "__main__":
    unittest.main()
