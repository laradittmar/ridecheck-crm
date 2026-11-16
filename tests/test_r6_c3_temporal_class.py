"""R6-C3 — R6C2RV-S01: the temporal CLASS, unit level (pure validator + CE builder rules).

R6-C2 bound an enumerated list of day/date forms; anything it did not fully read silently
became "no anchor", and the day-blind endpoint rule then accepted matching times on another
date. R6-C3 replaces the list with one temporal analysis of the COMPLETE reply:

* every temporal-looking expression ends FULLY_PARSED_AND_CANONICAL, FULLY_PARSED_BUT_
  UNSUPPORTED, TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED or NO_TEMPORAL_EXPRESSION, and only
  the first authorises a concrete date/time proposition;
* the reply is one proposition: a day in one sentence binds a time in another, and every
  anchor must agree with every other and with the canonical evaluated date;
* deterministic slot replies carry a structured AVAILABILITY claim (exact ISO date + HH:MM)
  checked directly against CanonicalFacts.

The full production path (four postures, SQLite and PostgreSQL) is
`test_r6_c3_temporal_class_production_path.py`. Clock: business today = Wednesday
2026-11-11 (America/Argentina/Buenos_Aires); the evaluated date is Monday 2026-11-16 unless
a case says otherwise; evaluated slots 10:00 / 11:00 / 15:00 / 15:30.
"""
from __future__ import annotations

import ast
import itertools
import pathlib
import sys
import unittest
from datetime import date, timedelta

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.response_validator as RV  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.response_validator import (  # noqa: E402
    CLAIM_AVAILABILITY, CanonicalFacts, DomainClaim, ResponseProvenance, StructuredClaim,
    TemporalState, analyze_temporal, composed_text, temporal_verdict, validate_envelope,
    validate_response)

WED = date(2026, 11, 11)
THU, MON, MON23, THU19 = date(2026, 11, 12), date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 19)
SLOTS = ("10:00", "11:00", "15:00", "15:30")


def F(evaluated=MON, today=WED, **kw):
    base = dict(vehicle_marca="Toyota", vehicle_modelo="Corolla",
                inspection_zone_detail="Palermo", inspection_zone_group="CABA",
                availability_checked=True, offered_slots=SLOTS, availability_date=evaluated,
                business_today=today)
    base.update(kw)
    return CanonicalFacts(**base)


def B(**kw):
    """A confirmed booking: committed Monday 16 at 10:00 (end 10:45 = SERVICE_MINUTES)."""
    base = dict(booking_confirmed=True, booked_date=MON, booked_times=("10:00", "10:45"),
                availability_date=THU19)
    base.update(kw)
    return F(**base)


def outcome(text, facts):
    out = validate_response(text, facts)
    av = [f for f in out.findings if f.claim == CLAIM_AVAILABILITY]
    return out, av


class _Assert(unittest.TestCase):
    def refused(self, text, facts, msg=""):
        out, av = outcome(text, facts)
        self.assertTrue(av and any(not f.allowed for f in av), f"{text!r} {msg}: {av}")
        self.assertNotEqual(" ".join(out.text.split()), " ".join(text.split()),
                            f"{text!r} delivered {msg}")
        return out

    def kept(self, text, facts, msg=""):
        # verbatim up to whitespace (the validator re-joins kept lines with one space)
        out, av = outcome(text, facts)
        self.assertEqual(" ".join(out.text.split()), " ".join(text.split()),
                         f"{text!r} {msg}: {out.findings}")
        self.assertTrue(all(f.allowed for f in av), f"{text!r}: {av}")
        return out


# ── 1. The temporal-analysis contract ──────────────────────────────────────────

class T01_AnalysisContract(unittest.TestCase):

    def an(self, text, today=WED):
        return analyze_temporal(text, today)

    def test_four_states_are_a_closed_set(self):
        self.assertEqual({s.name for s in TemporalState},
                         {"NO_TEMPORAL_EXPRESSION", "FULLY_PARSED_AND_CANONICAL",
                          "FULLY_PARSED_BUT_UNSUPPORTED",
                          "TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED"})

    def test_result_fields(self):
        a = self.an("Te esperamos el lunes 16/11 de 10:00 a 11:00. También el 16 a las 15 hs.")
        self.assertEqual(set(a.dates), {MON})
        self.assertEqual([d.weekday for d in a.weekdays], [0])
        self.assertEqual([d.day for d in a.days_of_month], [16])
        self.assertEqual([t.value for t in a.times], ["10:00", "11:00", "15:00"])
        self.assertEqual([t.value for t in a.window_endpoints], ["10:00", "11:00"])
        self.assertEqual(a.unresolved, ())
        self.assertFalse(a.general_hours)
        self.assertTrue(a.transactional)

    def test_parse_states(self):
        self.assertIs(self.an("Gracias, cualquier duda me escribís.").parse_state,
                      TemporalState.NO_TEMPORAL_EXPRESSION)
        self.assertIs(self.an("Te esperamos el lunes a las 10:00.").parse_state,
                      TemporalState.FULLY_PARSED_BUT_UNSUPPORTED)          # not yet compared
        for t in ("Te esperamos el finde a las 10:00.", "Te esperamos el 31/11 a las 10:00.",
                  "Te esperamos la semana que viene.", "Te esperamos el diecinueve."):
            with self.subTest(t=t):
                self.assertIs(self.an(t).parse_state,
                              TemporalState.TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED)

    def test_verdict_states(self):
        def v(text, facts=None):
            return temporal_verdict(analyze_temporal(text, WED), facts or F())
        self.assertIsNone(v("Gracias, cualquier duda me escribís."))
        self.assertIs(v("Te esperamos el lunes a las 10:00.").state,
                      TemporalState.FULLY_PARSED_AND_CANONICAL)
        self.assertIs(v("Te esperamos el jueves a las 10:00.").state,
                      TemporalState.FULLY_PARSED_BUT_UNSUPPORTED)
        self.assertIs(v("Te esperamos el finde a las 10:00.").state,
                      TemporalState.TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED)
        self.assertTrue(v("Te esperamos el lunes a las 10:00.").allowed)
        for t in ("Te esperamos el jueves a las 10:00.", "Te esperamos el finde a las 10:00."):
            self.assertFalse(v(t).allowed)

    def test_weekday_and_number_are_both_read(self):
        a = self.an("el lunes 23 a las 10:00")
        (d,) = a.days
        self.assertEqual((d.kind, d.weekday, d.day, d.resolved), ("weekday_date", 0, 23, MON23))
        # a weekday that disagrees with its own day number is a conflict, never one of them
        self.assertTrue(self.an("el martes 16 a las 10:00").unresolved)

    def test_regexes_do_not_partially_consume(self):
        # each piece of the sentence is accounted for exactly once
        a = self.an("Para el jueves 19/11. Te esperamos a las 10:00.")
        self.assertEqual([(d.kind, d.month, d.day) for d in a.days], [("monthday", 11, 19)])
        self.assertEqual([t.value for t in a.times], ["10:00"])
        self.assertEqual(a.unresolved, ())

    def test_never_raises(self):
        for t in ("", " ", "¿?", "::::", "1/1/1/1", "\x00", "a las", "el", "99:99", "31/02/2026",
                  "2026-99-99T99:99", "las " * 50, "lunes " * 40, "😀 10hs 😀"):
            with self.subTest(t=t):
                analyze_temporal(t, WED)
                validate_response(t or "x", F())


# ── 2/3/10. Required forms — refused when wrong, kept when canonical ───────────

class T02_ReviewerForms(_Assert):

    WRONG = [
        # A. weekday + day number
        ("Te esperamos el lunes 23 de 10:00 a 11:00.", MON),
        ("Te esperamos el jueves 19 a las 10:00.", THU),
        ("Te esperamos este jueves 19 a las 10:00.", THU),
        ("Te esperamos el próximo lunes 23 a las 10:00.", MON),
        # B/C/D. bare day, alternative numeric, abbreviated / reversed month, ISO datetime
        ("Te esperamos el 19 a las 10:00.", MON),
        ("Te esperamos día 19 a las 10:00.", MON),
        ("Te esperamos para el 19 a las 10:00.", MON),
        ("Te esperamos el 19-11 a las 10:00.", MON),
        ("Te esperamos el 19-11-2026 a las 10:00.", MON),
        ("Te esperamos el 19.11 a las 10:00.", MON),
        ("Te esperamos el 19.11.2026 a las 10:00.", MON),
        ("Te esperamos el 19 nov a las 10:00.", MON),
        ("Te esperamos el 19 nov. a las 10:00.", MON),
        ("Te esperamos el 19 de nov a las 10:00.", MON),
        ("Te esperamos el 19 de nov. a las 10:00.", MON),
        ("Te esperamos noviembre 19 a las 10:00.", MON),
        ("Te esperamos nov 19 a las 10:00.", MON),
        ("Te esperamos el 2026-11-19T10:00.", MON),
        ("Te esperamos el 2026-11-19 10:00.", MON),
        # E. vague periods next to a concrete proposition
        ("Te esperamos el finde a las 10:00.", MON),
        ("Te esperamos el fin de semana a las 10:00.", MON),
        ("Te esperamos la semana que viene a las 10:00.", MON),
        ("Te esperamos la próxima semana a las 10:00.", MON),
        ("Podemos ir durante la semana a las 10:00.", MON),
        ("Podemos ir a fin de mes a las 10:00.", MON),
        ("Podemos ir a principios de mes a las 10:00.", MON),
        ("Podemos ir después del feriado a las 10:00.", MON),
        ("Te esperamos el finde.", MON),
        ("Te reservo la semana que viene.", MON),
    ]
    KEPT = [
        ("Te esperamos el lunes 16 de 10:00 a 11:00.", MON),
        ("Te esperamos el jueves 12 a las 10:00.", THU),
        ("Te esperamos este lunes 16 a las 10:00.", MON),
        ("Te esperamos el 16 a las 10:00.", MON),
        ("Te esperamos día 16 a las 10:00.", MON),
        ("Te esperamos para el 16 a las 10:00.", MON),
        ("Te esperamos el 16-11 a las 10:00.", MON),
        ("Te esperamos el 16-11-2026 a las 10:00.", MON),
        ("Te esperamos el 16.11 a las 10:00.", MON),
        ("Te esperamos el 16.11.2026 a las 10:00.", MON),
        ("Te esperamos el 16 nov a las 10:00.", MON),
        ("Te esperamos el 16 nov. a las 10:00.", MON),
        ("Te esperamos el 16 de nov. a las 10:00.", MON),
        ("Te esperamos noviembre 16 a las 10:00.", MON),
        ("Te esperamos el 2026-11-16T10:00.", MON),
        ("Te esperamos el 16 de noviembre a las 10:00.", MON),
        ("Te esperamos el lunes 16/11 a las 10:00.", MON),
    ]

    def test_wrong_date_forms_are_refused(self):
        for text, evaluated in self.WRONG:
            with self.subTest(text=text):
                self.refused(text, F(evaluated=evaluated))

    def test_canonical_date_forms_are_kept(self):
        for text, evaluated in self.KEPT:
            with self.subTest(text=text):
                self.kept(text, F(evaluated=evaluated))

    def test_weekday_matches_but_number_does_not(self):
        # "lunes 23" for canonical Monday 16: the weekday alone never decides
        self.refused("Te esperamos el lunes 23 a las 10:00.", F())
        self.refused("¿Te reservo el lunes 23 a las 10:00?", F())

    def test_weekday_disagrees_with_its_date(self):
        for t in ("Te esperamos el martes 16 a las 10:00.", "Te esperamos el jueves 16/11 a las 10:00.",
                  "Te esperamos el viernes 16 de noviembre a las 10:00."):
            with self.subTest(t=t):
                self.refused(t, F())

    def test_impossible_dates(self):
        for t in ("Te esperamos el 31-11 a las 10:00.", "Te esperamos el 31.11 a las 10:00.",
                  "Te esperamos el 31 nov a las 10:00.", "Te esperamos el 40 a las 10:00.",
                  "Te esperamos el 2026-11-31T10:00.", "Te esperamos el lunes 40 a las 10:00."):
            with self.subTest(t=t):
                self.refused(t, F())

    def test_every_month_word_and_abbreviation(self):
        months = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                  "septiembre", "setiembre", "octubre", "noviembre", "diciembre"]
        abbrs = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "sept", "set",
                 "oct", "nov", "dic"]
        for word in months + abbrs + [a + "." for a in abbrs] + [a.upper() for a in abbrs]:
            n = RV._MONTH_WORDS[word.rstrip(".").lower()]
            canonical = date(2026 if n == 12 else 2027, n, 3)
            for text in (f"Te esperamos el 3 de {word} a las 10:00.", f"Te esperamos {word} 3 a las 10:00.",
                         f"Te esperamos el 3 {word} a las 10:00."):
                with self.subTest(text=text):
                    self.kept(text, F(evaluated=canonical))
                    self.refused(text, F())


# ── 4. Weekday horizon ───────────────────────────────────────────────────────

class T03_WeekdayHorizon(_Assert):

    def test_bare_weekday_is_its_next_occurrence_only(self):
        names = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
        for k in range(0, 14):
            canonical = WED + timedelta(days=k)
            name = names[canonical.weekday()]
            for det in ("el", "este", "el próximo", ""):
                text = f"Te esperamos {det} {name} a las 10:00.".replace("  ", " ")
                with self.subTest(k=k, text=text):
                    # R6-C4 (R6C3RV-S01 H1): today's own weekday (k = 0, 7) is ambiguous —
                    # today, or the same weekday next week (CE's own reading) — and refused;
                    # the next occurrence is [today + 1, today + 6]. Was: k <= 6 kept.
                    if 1 <= k <= 6:
                        self.kept(text, F(evaluated=canonical))
                    else:
                        self.refused(text, F(evaluated=canonical))

    def test_a_past_evaluation_is_stale_even_when_the_reply_names_its_date(self):
        # the validator's own staleness rule: the builder drops a past date, and the validator
        # refuses it again even if the reply names exactly that (past) date
        for t in ("Te esperamos el 11/11 a las 10:00.", "Te esperamos el 2026-11-11 a las 10:00.",
                  "Te esperamos el 11 de noviembre a las 10:00."):
            with self.subTest(t=t):
                self.refused(t, F(evaluated=WED, today=THU))
                self.kept(t, F(evaluated=WED, today=WED))

    def test_this_monday_when_the_evaluation_is_eight_or_twelve_days_away(self):
        for canonical in (MON23, date(2026, 11, 23)):
            self.refused("Te esperamos este lunes a las 10:00.", F(evaluated=canonical))
            self.refused("Te esperamos el lunes a las 10:00.", F(evaluated=canonical))
        # an explicit date is authoritative and the weekday must agree with it
        self.kept("Te esperamos el lunes 23/11 a las 10:00.", F(evaluated=MON23))
        self.kept("Te esperamos el lunes 23 a las 10:00.", F(evaluated=MON23))


# ── 5. Time normalisation ──────────────────────────────────────────────────────

class T04_TimeNormalisation(_Assert):
    FORMS = {
        "a las 10": "10:00", "a las 10:00": "10:00", "a las 10.00": "10:00",
        "10 hs": "10:00", "10hs": "10:00", "10 h": "10:00", "10:00 hs": "10:00",
        "10am": "10:00", "10 am": "10:00", "10 a.m.": "10:00", "a las 10 en punto": "10:00",
        "a las diez": "10:00", "a las diez de la mañana": "10:00", "diez de la mañana": "10:00",
        "10h00": "10:00",
        "3pm": "15:00", "3 pm": "15:00", "a las 3 de la tarde": "15:00", "a las 15": "15:00",
        "a las 3": "15:00", "15 hs": "15:00",
        "a las 15:30": "15:30", "3:30 pm": "15:30", "a las 3 y media": "15:30",
        "a las 15 y media": "15:30", "a las tres y media de la tarde": "15:30",
        "a las 11": "11:00", "11am": "11:00", "a las 11 en punto": "11:00",
    }

    def test_forms_normalise_to_24_hour_hhmm(self):
        for form, want in self.FORMS.items():
            with self.subTest(form=form):
                a = analyze_temporal(f"Te esperamos el lunes {form}.", WED)
                self.assertEqual([t.value for t in a.times], [want], a)
                self.assertEqual(a.unresolved, (), a)

    def test_forms_bind_to_the_day(self):
        for form in self.FORMS:
            with self.subTest(form=form):
                self.kept(f"Te esperamos el lunes {form}.", F())
                self.refused(f"Te esperamos el jueves {form}.", F())

    def test_non_slot_quarter_times(self):
        for form in ("a las 10 y cuarto", "a las 10 y media", "a las 11 menos cuarto",
                     "a las 12", "al mediodía", "a las 9", "9am"):
            with self.subTest(form=form):
                self.refused(f"Te esperamos el lunes {form}.", F())

    def test_invalid_clock_wording_fails_closed(self):
        for form in ("a las 25", "a las 10:75", "13pm", "a las 16 de la tarde", "a las 1030",
                     "a las 10 y pico", "10:00 PM hs."):
            with self.subTest(form=form):
                self.refused(f"Te esperamos el lunes {form}.", F())

    def test_every_time_must_be_an_evaluated_slot(self):
        self.refused("Te esperamos el lunes a las 10 o a las 12.", F())
        self.kept("Te esperamos el lunes a las 10 o a las 11.", F())


# ── 2. Complete-message scope ───────────────────────────────────────────────────

class T05_MessageScope(_Assert):
    WRONG = [
        "Para el jueves 19/11. Te esperamos a las 10:00.",
        "El jueves te esperamos. Horario: 10:00.",
        "La fecha sería el 19. Podemos ir a las 10.",
        "Para el jueves 19/11.\nTe esperamos a las 10:00.",
        "Turno:\n- jueves 19/11\n- 10:00 hs",
        "• Día: jueves\n• Hora: 10:00",
        "1. Jueves 19/11\n2. 10:00",
        "Te esperamos a las 10:00. El día sería el jueves.",
        "¡Perfecto! El jueves. Te espero a las 10.",
    ]
    KEPT = [
        "Para el lunes 16/11. Te esperamos a las 10:00.",
        "El lunes te esperamos. Horario: 10:00.",
        "La fecha sería el 16. Podemos ir a las 10.",
        "Para el lunes 16/11.\nTe esperamos a las 10:00.",
        "Turno:\n- lunes 16/11\n- 10:00 hs",
        "• Día: lunes\n• Hora: 10:00",
    ]

    def test_day_in_one_sentence_time_in_another(self):
        for t in self.WRONG:
            with self.subTest(t=t):
                out = self.refused(t, F())
                self.assertNotIn("10", out.text)
                self.assertNotIn("jueves", out.text.lower())

    def test_correct_cross_sentence_controls(self):
        for t in self.KEPT:
            with self.subTest(t=t):
                self.kept(t, F())

    def test_one_valid_anchor_cannot_hide_another(self):
        for t in ("Te esperamos el lunes 16/11 a las 10:00. Si preferís, el jueves 19 también.",
                  "Te esperamos el lunes a las 10:00 o el jueves a las 11:00.",
                  "El lunes 16 te esperamos a las 10:00. Confirmame para el 19.",
                  "Te esperamos el lunes 16/11 a las 10:00. Si no, el finde."):
            with self.subTest(t=t):
                self.refused(t, F())

    def test_question_and_statement_share_the_message_rule(self):
        self.refused("¿Te reservo el jueves? Sería a las 10:00.", F())
        self.kept("¿Te reservo el lunes? Sería a las 10:00.", F())

    def test_ordinary_sentences_around_a_correct_offer_survive(self):
        self.kept("¡Genial! Te esperamos el lunes 16/11 a las 10:00. La revisión dura unos 45 "
                  "minutos y vamos con todo el equipo.", F())

    def test_refusal_removes_every_sentence_of_the_proposition(self):
        out = self.refused("Gracias por escribirnos. Turno: jueves 19/11. Te esperamos a las 10:00.",
                           F())
        self.assertEqual(out.text, "Gracias por escribirnos.")


# ── 6. Class-level fail-closed rule; ordinary prose unchanged ────────────────────

class T06_FailClosed(_Assert):

    def test_any_unresolved_temporal_wording_in_a_proposition_is_refused(self):
        for t in ("Te esperamos el lunes a eso de las diez y pico.", "Te esperamos el lunes 16 hs 10.",
                  "Te esperamos en noviembre a las 10:00.", "Te esperamos los lunes a las 10:00.",
                  "Te esperamos el lunes, o el martes, a las 10:00.", "Turno el lunes 16/11/20266.",
                  "Te esperamos el dieciséis a las 10:00.", "Te esperamos a partir de mañana a las 10.",
                  "Te esperamos el 16/11 al 20/11 a las 10:00.", "Te esperamos las mañanas a las 10:00.",
                  "Te esperamos de 10 a 16 de noviembre a las 10:00."):
            with self.subTest(t=t):
                self.refused(t, F())

    def test_an_approximate_clock_reading_is_not_an_exact_slot(self):
        for t in ("Te esperamos el lunes a eso de las 10.", "Te esperamos el lunes tipo 10 hs.",
                  "Te esperamos el lunes alrededor de las 10:00.",
                  "Te esperamos el lunes más o menos a las 10."):
            with self.subTest(t=t):
                self.refused(t, F())                  # 10:00 IS evaluated: the reading is approximate

    def test_a_negative_phrase_cannot_shelter_a_positive_offer(self):
        for t in ("No tenemos más turnos el jueves, pero te esperamos el jueves 19 a las 10:00.",
                  "No hay problema, te esperamos el jueves 19 a las 10:00.",
                  "No tengo disponibilidad el martes; te espero el jueves a las 10."):
            with self.subTest(t=t):
                self.refused(t, F())
        # a genuine negative statement is still allowed after an evaluation
        self.kept("No tengo disponibilidad el jueves a las 18:00.", F())

    def test_a_part_of_the_day_must_contain_its_times(self):
        self.refused("Te esperamos el lunes por la tarde a las 10:00.", F())
        self.kept("Te esperamos el lunes por la mañana a las 10:00.", F())
        self.kept("Te esperamos el lunes por la tarde, a las 15:30.", F())
        self.refused("Te esperamos el lunes por la noche.", F())          # no evaluated slot then
        self.kept("Te esperamos el lunes por la tarde.", F())

    def test_ordinary_prose_has_no_temporal_proposition(self):
        for t in ("Gracias, cualquier duda me escribís.", "¡Buen fin de semana!",
                  "Que tengas un buen día.", "Somos 2 técnicos y revisamos el Peugeot 208.",
                  "¡Hasta mañana!", "Trabajamos desde 2008.", "La revisión dura 45 minutos.",
                  "Hacemos revisiones a domicilio en CABA y GBA."):
            with self.subTest(t=t):
                self.kept(t, CanonicalFacts())

    def test_no_evaluation_refuses_any_concrete_proposition(self):
        for t in ("Te esperamos el lunes a las 10.", "Te esperamos el 16 nov.",
                  "Te esperamos a las 10 hs.", "El horario sería a las 10."):
            with self.subTest(t=t):
                self.refused(t, CanonicalFacts())


# ── 8. General business hours ───────────────────────────────────────────────────

class T07_GeneralHours(_Assert):
    GENERAL = ("Trabajamos de lunes a viernes de 9:00 a 18:00.",
               "Nuestro horario de atención es de 9 a 18.",
               "Atendemos los sábados de 9 a 13.",
               "Atendemos de 9 a 18 hs.",
               "Por las mañanas atendemos de 9 a 12.",
               "Te cuento que atendemos de lunes a viernes de 9 a 18.")

    def test_general_hours_are_information(self):
        for t in self.GENERAL:
            for facts in (CanonicalFacts(), F()):
                with self.subTest(t=t, facts=facts.availability_checked):
                    self.kept(t, facts)

    def test_dotted_business_hours_are_hours_not_a_date(self):
        # `schedule.format_business_hours_es` renders "de 9.30 a 14 hs": hours, not the 9th of
        # month 30 — inside CE's own negative reason and in general hours alike
        self.kept("Para lunes 16/11 a las 18:00 no tengo disponibilidad (ese día trabajamos de "
                  "9.30 a 14 hs). Para lunes 16/11 tengo: 10:00 o 11:00. ¿Alguno te viene bien?", F())
        self.kept("Atendemos de 9.30 a 14 hs.", F())
        self.kept("Te esperamos el lunes a las 15.30 hs.", F())

    def test_specific_offer_overrides_the_frame(self):
        for t in ("Te esperamos de lunes a viernes de 9 a 18.", "El jueves atendemos de 10:00 a 11:00.",
                  "Atendemos el jueves 19 a las 10:00.", "Atendemos a las 12."):
            with self.subTest(t=t):
                self.refused(t, F())

    def test_general_hours_beside_an_offer_validate_the_offer(self):
        self.kept("Trabajamos de lunes a viernes de 9:00 a 18:00. Te esperamos el lunes a las 10:00.", F())
        self.refused("Trabajamos de lunes a viernes de 9:00 a 18:00. Te esperamos el jueves a las 10:00.",
                     F())


# ── 9. Confirmed bookings ───────────────────────────────────────────────────────

class T08_ConfirmedBooking(_Assert):

    def test_committed_values_only(self):
        self.kept("Tu turno es el lunes 16 a las 10:00.", B())
        self.kept("Tu turno es el lunes 16/11 de 10:00 a 10:45.", B())
        self.kept("Te esperamos el lunes a las 10.", B())
        self.kept("Listo, tu turno está confirmado.", B())

    def test_required_refusals(self):
        cases = {
            "booked Monday 16, reply Monday 23": ("Tu turno es el lunes 23 a las 10:00.", B()),
            "right weekday, wrong day number": ("Tu turno es el lunes 9 a las 10:00.", B()),
            "alternative date form, wrong date": ("Tu turno es el 19-11 a las 10:00.", B()),
            "abbreviated month, wrong date": ("Tu turno es el 19 nov. a las 10:00.", B()),
            "date and time in two sentences": ("Tu turno es el jueves 19/11. Te esperamos a las 10:00.", B()),
            "unsupported non-HH:MM time": ("Tu turno es el lunes a las 11.", B()),
            "invented end": ("Tu turno es el lunes de 10 a 11 hs.", B()),
            "BOOKED with no booked revision": ("Tu turno es el lunes a las 10:00.",
                                               B(booked_date=None, booked_times=())),
            "revision that is not booked": ("Tu turno es el lunes 16 a las 10:00.",
                                            B(booked_date=None, booked_times=())),
            "vague period": ("Tu turno es el finde a las 10:00.", B()),
        }
        for name, (text, facts) in cases.items():
            with self.subTest(name=name):
                self.refused(text, facts)


# ── 7. Structured deterministic availability ──────────────────────────────────

class T09_StructuredAvailability(unittest.TestCase):

    def why(self, value, facts=None):
        return RV._validate_structured_claim(StructuredClaim(DomainClaim.AVAILABILITY, value),
                                             facts or F())

    def test_exact_iso_date_and_hhmm(self):
        self.assertIsNone(self.why(("2026-11-16", "10:00", "15:30")))
        for bad in (("10:00",), ("2026-11-16",), ("2026-11-19", "10:00"),
                    ("2026-11-16", "2026-11-16", "10:00"), ("2026-11-16", "10:00", "12:00"),
                    ("2026-11-16", "10"), ("2026-11-16", "10:00hs"), ("2026-11-16T10:00",),
                    ("2026-11-31", "10:00"), ("16/11/2026", "10:00"), ("2026-11-16", 1000)):
            with self.subTest(bad=bad):
                self.assertIsNotNone(self.why(bad))

    def test_exactly_one_iso_date_is_the_stated_reason(self):
        # the refusal is the declared-date rule itself, not an incidental failure
        self.assertEqual(self.why(("10:00",)), "availability claim must declare exactly one ISO date")
        self.assertEqual(self.why(("2026-11-16", "2026-11-16", "10:00")),
                         "availability claim must declare exactly one ISO date")

    def test_against_canonical_facts(self):
        v = ("2026-11-16", "10:00")
        self.assertIsNotNone(self.why(v, F(evaluated=None)))
        self.assertIsNotNone(self.why(v, F(evaluated=date(2026, 11, 9))))      # stale
        self.assertIsNotNone(self.why(v, F(offered_slots=())))
        self.assertIsNotNone(self.why(v, F(availability_checked=False)))

    def test_composed_offer_is_validated_on_its_declaration(self):
        ok = ConversationEngine._composed_slot_offer(
            "Para lunes 16/11 tengo disponible: 10:00 o 11:00. ¿A qué hora te viene bien?",
            "2026-11-16", ["10:00", "11:00"])
        env = validate_envelope(ok, F())
        self.assertIs(env.provenance, ResponseProvenance.DETERMINISTIC_COMPOSED)
        self.assertEqual(env.text, str(ok))
        for day, slots in (("2026-11-19", ["10:00"]), ("2026-11-16", ["12:00"]),
                           ("2026-11-16", [])):
            with self.subTest(day=day, slots=slots):
                bad = ConversationEngine._composed_slot_offer(
                    "Para lunes 16/11 tengo disponible: 10:00. ¿Te sirve?", day, slots)
                env = validate_envelope(bad, F())
                self.assertIs(env.provenance, ResponseProvenance.VALIDATION_FALLBACK, env)

    def test_the_rendered_words_are_still_analysed(self):
        # a correct declaration does not license different words
        bad = ConversationEngine._composed_slot_offer(
            "Para jueves 19/11 tengo disponible: 10:00. ¿Te sirve?", "2026-11-16", ["10:00"])
        self.assertNotEqual(validate_envelope(bad, F()).text, str(bad))

    def test_ai_prose_earns_no_structured_trust(self):
        env = validate_envelope("Para jueves 19/11 tengo disponible: 10:00.", F())
        self.assertIs(env.provenance, ResponseProvenance.VALIDATION_FALLBACK)

    def test_every_deterministic_slot_offer_is_declared(self):
        src = (_ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()
        tree = ast.parse(src)
        offers = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
                names = {n.id for v in node.values if isinstance(v, ast.FormattedValue)
                         for n in ast.walk(v.value) if isinstance(n, ast.Name)}
                if names & {"slot_list", "sl", "shown"} and ("tengo" in text.lower()
                                                            or "horarios" in text.lower()):
                    offers += 1
        self.assertGreaterEqual(offers, 6)
        # 6 ScheduleService handler offers + the R6-C4B-R1 canonical SCHEDULING
        # availability block (`_scheduling_next_step`), declared the same way
        self.assertEqual(src.count("self._composed_slot_offer("), 7)
        # Each CE-method slot offer is wrapped (`_regenerated_offer_es` is the plan's
        # state-sourced re-statement, rendered after validation; closeout §7 documents it).
        start = src.index("class ConversationEngine")
        for marker in ("Horarios disponibles: {slot_list}", "tengo: {slot_list}. ¿Alguno",
                       "por la {period_label} tengo: {slot_list}. ¿Cuál",
                       "Tengo estos horarios: {shown}", "tengo disponible: {slot_list}",
                       "Tengo disponible: {sl}"):
            i = src.index(marker, start)
            window = src[max(0, i - 300):i + 1300]
            with self.subTest(marker=marker):
                self.assertIn("_composed_slot_offer(", window)


# ── CE producer gate (AI-proposed preferred time) uses the same analysis ──────────

class T10_ProducerGate(unittest.TestCase):

    def test_stated_day_is_reads_the_complete_reply(self):
        self.assertTrue(RV.stated_day_is("¿Te parece bien el lunes 16 a las 10?", MON, WED))
        for t in ("¿Te parece bien el lunes 23 a las 10?", "¿Te parece el 19-11 a las 10?",
                  "Para el jueves. ¿Te parece a las 10:00?", "¿Te parece el finde a las 10?"):
            with self.subTest(t=t):
                self.assertFalse(RV.stated_day_is(t, MON, WED))


# ── 12. Generated grammar/product matrix (unit level; full path samples it) ───────

_WD = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre")
_ABBR = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def day_forms(d: date):
    """Every supported written form of one calendar date (article handled by the frame)."""
    m, a = _MONTHS[d.month - 1], _ABBR[d.month - 1]
    return [
        f"{d.day}/{d.month}", f"{d.day:02d}/{d.month:02d}", f"{d.day}/{d.month}/{d.year}",
        f"{d.day}-{d.month}", f"{d.day}-{d.month}-{d.year}", f"{d.day}.{d.month}",
        f"{d.day}.{d.month}.{d.year}", d.isoformat(), f"{d.day} de {m}",
        f"{d.day} de {m} de {d.year}", f"{d.day} {a}", f"{d.day} {a}.", f"{d.day} de {a}.",
        f"{m} {d.day}", f"{a} {d.day}", f"día {d.day}", f"{d.day}",
    ]


TIME_FORMS = {"10:00": ["a las 10:00", "a las 10", "10 hs", "10hs", "10am", "a las 10 en punto",
                        "de 10:00 a 11:00", "entre las 10 y las 11"],
              "15:30": ["a las 15:30", "3:30 pm", "a las 3 y media de la tarde", "15:30 hs"]}

FRAMES = (
    ("statement", "Te esperamos el {day} {time}."),
    ("question", "¿Te reservo el {day} {time}?"),
    ("cross", "Para el {day}. Te esperamos {time}."),
    ("newline", "Turno:\n{day}\n{time}"),
    ("bullets", "- Día: {day}\n- Hora: {time}"),
)


def _case(frame, day, time):
    return frame.format(day=day, time=time)


def generate_corpus():
    """(text, evaluated date, expected 'kept'|'refused', label). Deterministic, bounded.

    For the canonical date MON (Monday 16) and two wrong dates — Thursday 19 (other
    weekday) and Monday 23 (same weekday, other day number) — every day form × an optional
    weekday prefix (correct for the stated date, or mismatched) × time forms × frames ×
    capitalisation. A reply is kept only when its stated date IS the canonical date, its
    weekday (if any) agrees with it, and its times are evaluated slots."""
    out = []
    for stated, prefix_kind, frame, caps in itertools.product(
            (MON, THU19, MON23), ("none", "right", "wrong"), FRAMES, ("lower", "title")):
        for i, form in enumerate(day_forms(stated)):
            if prefix_kind == "right":
                day = f"{_WD[stated.weekday()]} {form}"
            elif prefix_kind == "wrong":
                day = f"{_WD[(stated.weekday() + 2) % 7]} {form}"
            else:
                day = form
            for slot, forms in TIME_FORMS.items():
                time = forms[i % len(forms)]
                text = _case(frame[1], day, time)
                if caps == "title":
                    text = text[0].upper() + text[1:].replace(" el ", " El ").replace(" de ", " De ")
                # a bare number alone on its own line is unresolved (fail closed)
                bare_line = frame[0] == "newline" and form.isdigit() and prefix_kind == "none"
                good = stated == MON and prefix_kind != "wrong" and not bare_line
                out.append((text, MON, "kept" if good else "refused",
                            f"{frame[0]}/{prefix_kind}/{stated.isoformat()}/{form}/{time}/{caps}"))
    # wrong times on the canonical date are refused
    for frame, form in itertools.product(FRAMES, day_forms(MON)):
        out.append((_case(frame[1], form, "a las 12"), MON, "refused", f"{frame[0]}/badtime/{form}"))
    return out


class T11_GeneratedCorpus(_Assert):

    def test_generated_corpus(self):
        corpus = generate_corpus()
        self.assertGreater(len(corpus), 3000)
        counts = {"kept": [0, 0], "refused": [0, 0]}
        bad = []
        for text, evaluated, want, label in corpus:
            out, av = outcome(text, F(evaluated=evaluated))
            delivered = " ".join(out.text.split()) == " ".join(text.split())
            ok = delivered if want == "kept" else (not delivered and any(not f.allowed for f in av))
            counts[want][0 if ok else 1] += 1
            if not ok:
                bad.append((label, text, out.text))
        self.assertEqual(bad, [], f"{len(bad)} generated cases misjudged; first: {bad[:5]}")
        print(f"\nR6-C3 generated unit corpus: {len(corpus)} cases; "
              f"wrong-date refused {counts['refused'][0]}/{sum(counts['refused'])}; "
              f"correct-date kept {counts['kept'][0]}/{sum(counts['kept'])}")


if __name__ == "__main__":
    unittest.main()
