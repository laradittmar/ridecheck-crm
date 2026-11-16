"""R6-C4 — default-deny temporal validation, unit level (pure validator + CE producer gate).

The R6-C3 review (…R6-C3-FINAL-TEMPORAL-BOUNDARY-INDEPENDENT-REVIEW_AUDIT_001.md) found that
the residual scan was a list of positions and the negative-availability exemption a list of
positive markers, so natural wording still delivered wrong dates (R6C3RV-S01) and times
(R6C3RV-S02), and nine reviewer mutants survived (R6C3RV-T01). R6-C4 makes the analysis
default-deny:

* every digit token, spelled cardinal, weekday/month fragment and abbreviation, am/pm/hs and
  approximator is consumed by a recogniser, classified as explicitly inert content (prices,
  counts, durations, phones, addresses, model designators, enumerations), or recorded as an
  unresolved fragment;
* a negative availability core exempts only the day/time placeholders inside its scope (glue
  words only); anything else joins the proposition, and a dayless positive time beside a
  negated other day is refused;
* qualified weekdays ("otro", "siguiente", "que viene") and today's own weekday are
  unresolved; spelled hours/minutes are read compositionally after any preposition;
* `stated_day_is` / `stated_times_of` (the CE producer gate) read the same analysis.

Clock: business today = Wednesday 2026-11-11 unless a case says otherwise; evaluated Monday
2026-11-16; slots 10:00 / 11:00 / 15:00 / 15:30; a confirmed booking is Monday 16 10:00–10:45.
The full production path (four postures, SQLite and PostgreSQL) is
`test_r6_c4_default_deny_production_path.py`.
"""
from __future__ import annotations

import ast
import itertools
import pathlib
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.response_validator as RV  # noqa: E402
import test_r6_c3_temporal_class as T  # noqa: E402  (F, B, outcome, _Assert)
from app.services.conversation_engine import ConversationEngine, _parse_scheduling_text  # noqa: E402
from app.services.response_validator import (  # noqa: E402
    TemporalState, analyze_temporal, stated_day_is, stated_times_of, temporal_verdict,
    validate_response)

WED, THU, MON, THU19, MON23 = T.WED, T.THU, T.MON, T.THU19, T.MON23
F, B = T.F, T.B
NOON_WED = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)      # BA Wed 12:00


class _Assert(T._Assert):
    pass


# ── 1. The audit's S01 / S02 counterexamples (every one), each with its control ──

#: (id, wrong reply, canonical control, facts) — evaluated Monday 16 unless stated.
S01 = (
    ("N1", "No hay turnos el jueves, sí el lunes 23 a las 10:00.",
           "No hay turnos el jueves, sí el lunes 16 a las 10:00.", "offer"),
    ("N2", "No tenemos disponibilidad el jueves; el lunes 23 tenemos a las 10:00 y 11:00.",
           "No tenemos disponibilidad el jueves; el lunes 16 tenemos a las 10:00 y 11:00.", "offer"),
    ("N3", "No hay horarios libres el jueves excepto a las 10:00.",
           "No hay horarios libres el jueves, sí el lunes 16 a las 10:00.", "offer"),
    ("N4", "No hay turnos el jueves, sí el lunes 16 a las 12:00.",
           "No hay turnos el jueves, sí el lunes 16 a las 11:00.", "offer"),
    ("N5", "No hay turnos el jueves, solo el lunes 23 a las 10:00.",
           "No hay turnos el jueves, solo el lunes 16 a las 10:00.", "offer"),
    ("N6", "No hay turnos el jueves, el lunes 23 hay a las 10:00.",
           "No hay turnos el jueves, el lunes 16 hay a las 10:00.", "offer"),
    ("N7", "No quedan turnos el lunes 16 salvo el jueves 19 a las 10:00.",
           "No quedan turnos el jueves 19 salvo el lunes 16 a las 10:00.", "offer"),
    ("N8", "No hay turnos el jueves, en cambio el lunes 23 a las 10:00.",
           "No hay turnos el jueves, en cambio el lunes 16 a las 10:00.", "offer"),
    ("N9", "No hay turnos el jueves. Excepto a las 10:00.",
           "No hay turnos el jueves. El lunes 16 te esperamos a las 10:00.", "offer"),
    ("N10", "No hay turnos el jueves salvo a las 10:00.",
            "No hay turnos el jueves.", "offer"),
    ("W1", "Te esperamos el lunes veintitrés a las 10:00.",
           "Te esperamos el lunes 16 a las 10:00.", "offer"),
    ("W1q", "¿Te esperamos el lunes veintitrés a las 10:00?",
            "¿Te esperamos el lunes 16 a las 10:00?", "offer"),
    ("W1l", "Tengo disponibilidad el lunes veintitrés a las 10:00.",
            "Tengo disponibilidad el lunes 16 a las 10:00.", "offer"),
    ("W2", "Te esperamos el lunes (23) a las 10:00.",
           "Te esperamos el lunes (16) a las 10:00.", "offer"),
    ("W2b", "Te esperamos el lunes [23] a las 10:00.",
            "Te esperamos el lunes [16] a las 10:00.", "offer"),
    ("W2n", "Te esperamos el lunes nº 23 a las 10:00.",
            "Te esperamos el lunes nº 16 a las 10:00.", "offer"),
    ("W3", "Te esperamos el 2026/11/23 a las 10:00.",
           "Te esperamos el 2026/11/16 a las 10:00.", "offer"),
    ("W3d", "Te esperamos el 2026.11.23 a las 10:00.",
            "Te esperamos el 2026.11.16 a las 10:00.", "offer"),
    ("W4", "Te esperamos en 12 días a las 10:00.",
           "Te esperamos el lunes 16 a las 10:00.", "offer"),
    ("W4b", "Te esperamos en 3 días a las 10:00.", "Te esperamos el 16 a las 10:00.", "offer"),
    ("W4c", "Te esperamos dentro de 5 días a las 10:00.",
            "Te esperamos el 16/11 a las 10:00.", "offer"),
    ("W4d", "Te esperamos en 3 días hábiles a las 10:00.",
            "Te esperamos el lunes a las 10:00.", "offer"),
    ("W5", "Te esperamos el lun 23 a las 10:00.", "Te esperamos el lun 16 a las 10:00.", "offer"),
    ("W6", "Te esperamos el otro lunes a las 10:00.",
           "Te esperamos el lunes a las 10:00.", "offer"),
    ("W6b", "Te esperamos el lunes siguiente a las 10:00.",
            "Te esperamos el próximo lunes a las 10:00.", "offer"),
    ("W6c", "Te esperamos el lunes que viene no, el siguiente, a las 10:00.",
            "Te esperamos el lunes 16 a las 10:00.", "offer"),
    ("M1", "Te esperamos el lunes 16 o el jueves 19 a las 10:00.",
           "Te esperamos el lunes 16 a las 10:00 o a las 11:00.", "offer"),
    ("M2", "Te esperamos el lunes 16 a las 10:00. O mejor 23.",
           "Te esperamos el lunes 16 a las 10:00. O mejor a las 11:00.", "offer"),
    ("M3", "Te esperamos el lunes 16 a las 10:00. O mejor veintitrés.",
           "Te esperamos el lunes 16 a las 10:00.", "offer"),
)
S02 = (
    ("T1", "Te esperamos el lunes 16 a las dieciséis.",
           "Te esperamos el lunes 16 a las quince.", "offer"),
    ("T1b", "Te esperamos el lunes 16 a las trece y treinta.",
            "Te esperamos el lunes 16 a las quince y treinta.", "offer"),
    ("T2", "Te esperamos el lunes 16 a las diez y veinte.",
           "Te esperamos el lunes 16 a las diez.", "offer"),
    ("T2b", "Te esperamos el lunes 16 a las diez menos diez.",
            "Te esperamos el lunes 16 a las once.", "offer"),
    ("T3", "Te reservo el lunes 16 para las doce.", "Te reservo el lunes 16 para las once.",
           "offer"),
    ("T3b", "Te reservo el lunes 16 desde las nueve.", "Te reservo el lunes 16 desde las diez.",
            "offer"),
    ("T3c", "Te reservo el lunes 16 de nueve a doce.",
            "Te reservo el lunes 16 de 10:00 a 11:00.", "offer"),
    ("T3d", "Te reservo el lunes 16, a eso de las nueve.",
            "Te reservo el lunes 16, a las diez.", "offer"),
    ("T3e", "Te esperamos el lunes 16 hasta las doce.",
            "Te esperamos el lunes 16 hasta las once.", "offer"),
    ("A1", "Te esperamos el lunes 16 a las 10, más o menos.",
           "Te esperamos el lunes 16 a las 10.", "offer"),
    ("A2", "Te esperamos el lunes 16 a las 10 aprox.", "Te esperamos el lunes 16 a las 10.",
           "offer"),
    ("A3", "Te esperamos el lunes 16 sobre las 10.", "Te esperamos el lunes 16 a las 10.",
           "offer"),
    ("A4", "Te esperamos el lunes 16 después de las 10.", "Te esperamos el lunes 16 a las 10.",
           "offer"),
    ("A5", "Te esperamos el lunes 16 antes de las 11.", "Te esperamos el lunes 16 a las 11.",
           "offer"),
    ("A6", "Te esperamos el lunes 16 a primera hora.", "Te esperamos el lunes 16 a las 10.",
           "offer"),
)
BOOKING = (
    ("B1", "Tu turno es el lunes veintitrés a las 10:00.", "Tu turno es el lunes 16 a las 10:00."),
    ("B1b", "Tu turno es el lunes (23) a las 10:00.", "Tu turno es el lunes (16) a las 10:00."),
    ("B2", "Tu turno es el 2026/11/23 a las 10:00.", "Tu turno es el 2026/11/16 a las 10:00."),
    ("B3", "Tu turno es el lunes 16 a las dieciséis.", "Tu turno es el lunes 16 a las diez."),
    ("B3b", "Tu turno es el lunes 16 para las doce.", "Tu turno es el lunes 16 para las diez."),
    ("B3c", "Tu turno es el lunes 16 a las diez y veinte.",
            "Tu turno es el lunes 16 a las diez y cuarto.", None),
    ("B4", "Tu turno es el lun 23 a las 10:00.", "Tu turno es el lun 16 a las 10:00."),
    ("B5", "Tu turno es el otro lunes a las 10:00.", "Tu turno es el lunes a las 10:00."),
    ("B6", "Tu turno es en 5 días a las 10:00.", "Tu turno es el lunes 16 a las 10:00."),
    ("B7", "No tenemos turnos el jueves; tu turno es el lunes 23 a las 10:00.",
           "Tu turno es el lunes 16 a las 10:00."),
    ("B8", "No tenemos turnos el jueves; tu turno es el lunes 16 a las 10:00.",
           "Tu turno es el lunes 16 a las 10:00."),
)


def _facts(kind):
    return F() if kind == "offer" else B()


class C4_01_AuditCounterexamples(_Assert):

    def test_s01_every_counterexample_refused_and_its_control_kept(self):
        for cid, wrong, right, kind in S01:
            with self.subTest(cid=cid):
                self.refused(wrong, _facts(kind))
                self.kept(right, _facts(kind))

    def test_s02_every_counterexample_refused_and_its_control_kept(self):
        for cid, wrong, right, kind in S02:
            with self.subTest(cid=cid):
                self.refused(wrong, _facts(kind))
                self.kept(right, _facts(kind))

    def test_h1_qualified_or_bare_weekday_on_that_weekday(self):
        # said ON Monday 16 with Monday 16 evaluated: the customer reads next Monday (CE's own
        # parser resolves a weekday that is today to +7) — never silently today
        for text in ("Te esperamos el próximo lunes a las 10:00.",
                     "Te esperamos el lunes que viene a las 10:00.",
                     "Te esperamos el lunes a las 10:00.", "Te esperamos este lunes a las 10:00."):
            with self.subTest(text=text):
                self.refused(text, F(evaluated=MON, today=MON))
                self.refused(text, F(evaluated=MON23, today=MON))
        self.kept("Te esperamos hoy lunes a las 10:00.", F(evaluated=MON, today=MON))
        self.kept("Te esperamos hoy a las 10:00.", F(evaluated=MON, today=MON))
        self.refused("Te esperamos hoy martes a las 10:00.", F(evaluated=MON, today=MON))
        # on Wednesday "el próximo lunes" is the next Monday, Monday 16
        self.kept("Te esperamos el próximo lunes a las 10:00.", F())

    def test_booking_restatements(self):
        for cid, wrong, right, *rest in BOOKING:
            facts = B() if not rest else B(booked_times=("10:15", "11:00"))
            with self.subTest(cid=cid):
                self.refused(wrong, facts)
                self.kept(right, facts)

    def test_booking_has_no_negative_exemption(self):
        # R18: a negative sentence is part of the confirmed booking's proposition
        self.refused("No tenemos turnos el jueves. Tu turno es el lunes 16 a las 10:00.", B())
        self.refused("No hay turnos el jueves 19.", B())
        self.kept("No hay turnos el lunes 16 a las 11:00.", B(booked_times=("11:00", "11:45")))


# ── 2. Negative scope: by structure, never by a positive-marker list ─────────────

class C4_02_NegativeScope(_Assert):
    CONNECTORS = (",", ";", ":", " —", ", sí", ", pero", " pero", ", salvo", " salvo", " excepto",
                  ", excepto", " solo", ", solo", ", en cambio", " y", " o", ", aunque",
                  ", sino", " aparte", ", fuera de eso", ", eso sí", ". Igual", ", igual")

    def test_any_connector_joins_the_positive_part(self):
        for c in self.CONNECTORS:
            with self.subTest(c=c):
                self.refused(f"No hay turnos el jueves{c} el lunes 23 a las 10:00.", F())
                self.refused(f"No hay turnos el jueves{c} a las 10:00.", F())
                self.refused(f"No tenemos disponibilidad el jueves{c} el lunes 16 a las 12:00.",
                             F())

    def test_an_offer_before_the_negation_joins_too(self):
        # G01: the scope extends left only over glue words
        for text in ("Te esperamos el lunes 23 a las 10:00, no hay turnos el jueves.",
                     "El lunes 23 sí tenemos a las 10:00 pero no hay turnos el jueves.",
                     "Te reservo el lunes 23 a las 10:00 porque no hay turnos el jueves."):
            with self.subTest(text=text):
                self.refused(text, F())
        # the canonical offer before a negated other day is delivered
        self.kept("Te esperamos el lunes 16 a las 10:00, no hay turnos el jueves.", F())

    def test_availability_wording_or_a_fragment_outside_the_scope_joins(self):
        # G04: availability lexicon outside the negated core; G05: a vague/approximate fragment
        for text in ("No hay turnos el jueves, pero hay horarios libres.",
                     "No hay turnos el jueves, pero tengo disponibilidad.",
                     "No hay turnos el jueves, sí el finde.",
                     "No hay turnos el jueves, mejor la semana que viene.",
                     "No hay turnos el jueves, más o menos."):
            with self.subTest(text=text):
                self.refused(text, F())

    def test_plain_negatives_remain_valid(self):
        for text in ("No tenemos turnos disponibles el jueves.",
                     "Para el jueves no hay horarios disponibles.",
                     "Los domingos no trabajamos.", "El domingo no trabajamos.",
                     "Lamentablemente no tenemos turnos disponibles el jueves 19.",
                     "Para el jueves 19 a las 10:00 no tenemos disponibilidad.",
                     "No hay turnos el jueves ni el viernes.",
                     "Ya no quedan horarios libres para el jueves por la tarde.",
                     "No tengo nada el jueves."):
            with self.subTest(text=text):
                self.kept(text, F())

    def test_ce_rejection_templates_stay_valid(self):
        # the deterministic CE sentences that explain a rejected request
        for text in (
                "Para lunes 16/11 a las 08:00 no tenemos disponibilidad (ese día trabajamos "
                "de 9.30 a 14 hs). Horarios disponibles: 10:00, 11:00 o 15:00. ¿Alguno te viene bien?",
                "Para martes 17/11 a las 10:00 no tengo disponibilidad (ese horario ya está "
                "reservado). Para lunes 16/11 tengo: 10:00 o 11:00. ¿Alguno te viene bien?",
                "Para domingo 15/11 no tengo disponibilidad (los domingos no trabajamos) y no me "
                "queda ningún horario libre ese día. Para lunes 16/11 tengo 10:00. ¿Te sirve?",
                "Para mañana jueves 12/11 no hay horarios disponibles (sin disponibilidad). "
                "¿Tenés otro día preferido?",
                "Para lunes 16/11 no hay horarios disponibles por la mañana. ¿Podría ser por la "
                "tarde?"):
            with self.subTest(text=text[:40]):
                self.kept(text, F())

    def test_a_dayless_time_beside_a_negated_other_day_is_refused(self):
        self.refused("No hay turnos el jueves. ¿Te sirve a las 10:00?", F())
        self.refused("No tengo el jueves; te ofrezco a las 11:00.", F())
        self.refused("No hay turnos el jueves, sí por la tarde.", F())
        # …but beside the evaluated date itself it is that date (CE's own template)
        self.kept("Para lunes 16/11 a las 08:00 no tenemos disponibilidad. "
                  "Horarios disponibles: 10:00 u 11:00.", F())

    def test_no_positive_marker_list_exists(self):
        self.assertFalse(hasattr(RV, "_POSITIVE_OFFER_RE"))


# ── 3. Default-deny token coverage ───────────────────────────────────────────────

class C4_03_DefaultDeny(_Assert):

    def test_every_leftover_number_word_or_fragment_is_unresolved(self):
        for frag in ("23", "(23)", "[23]", "nº 23", "#23", "veintitrés", "dieciséis",
                     "treinta y uno", "hs", "pm", "am", "p.m.", "lun", "jue", "mié", "vie",
                     "sáb", "nov", "dic", "noviembre", "23.11.2026.1", "1-2-3"):
            text = f"Te esperamos el lunes a las 10:00, {frag}."
            with self.subTest(frag=frag):
                self.refused(text, F())
                an = analyze_temporal(text, WED)
                self.assertEqual(an.parse_state,
                                 TemporalState.TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED)
        # these are READ (a time or a date) and then refused as another day/time
        for frag in ("23 hs", "2026/11/23", "23/11/2026", "23-11", "23 de noviembre"):
            text = f"Te esperamos el lunes a las 10:00, {frag}."
            with self.subTest(read=frag):
                self.refused(text, F())
                self.assertNotEqual(analyze_temporal(text, WED).parse_state,
                                    TemporalState.NO_TEMPORAL_EXPRESSION)

    def test_weekday_abbreviations(self):
        for abbr, wd in (("lun", 0), ("mar", 1), ("mié", 2), ("mier", 2), ("jue", 3), ("vie", 4),
                         ("sáb", 5), ("dom", 6)):
            day = WED + timedelta(days=(wd - WED.weekday()) % 7 or 7)
            for sep in ("", "."):
                with self.subTest(abbr=abbr, sep=sep):
                    if abbr == "mar":
                        # "mar 17" is also March 17: read as the month and refused (disclosed,
                        # fail-closed)
                        self.refused(f"Te esperamos el {abbr}{sep} {day.day} a las 10:00.",
                                     F(evaluated=day))
                    else:
                        self.kept(f"Te esperamos el {abbr}{sep} {day.day} a las 10:00.",
                                  F(evaluated=day))
                    self.refused(f"Te esperamos el {abbr}{sep} {day.day + 7} a las 10:00.",
                                 F(evaluated=day))
                    other = day + timedelta(days=1)
                    self.refused(f"Te esperamos el {abbr}{sep} {other.day} a las 10:00.",
                                 F(evaluated=other))

    def test_year_first_dates_are_read(self):
        for sep in ("/", ".", "-"):
            with self.subTest(sep=sep):
                self.kept(f"Te esperamos el 2026{sep}11{sep}16 a las 10:00.", F())
                self.refused(f"Te esperamos el 2026{sep}11{sep}23 a las 10:00.", F())
                self.refused(f"Te esperamos el 2026{sep}13{sep}16 a las 10:00.", F())

    def test_relative_offsets_are_unresolved(self):
        for form in ("en 12 días", "en 3 días", "dentro de 5 días", "en 3 días hábiles",
                     "en unos días", "dentro de un par de días", "en 5 días corridos",
                     "en dos días", "en 2 horas", "dentro de 30 minutos", "de acá a 3 días"):
            with self.subTest(form=form):
                self.refused(f"Te esperamos {form} a las 10:00.", F())
                self.refused(f"Te esperamos {form}.", F())
                self.assertFalse(stated_day_is(f"¿Te reservo {form} a las 10:00?", MON, WED))

    def test_qualified_weekdays_are_unresolved(self):
        for form in ("el otro lunes", "la otra semana el lunes", "el lunes siguiente",
                     "el lunes que viene", "el lunes entrante", "el lunes próximo"):
            with self.subTest(form=form):
                self.refused(f"Te esperamos {form} a las 10:00.", F())
                self.refused(f"Te esperamos {form} a las 10:00.", F(evaluated=MON23))

    def test_multiple_dates_where_only_one_is_canonical(self):
        for text in ("Te esperamos el lunes 16 o el jueves 19 a las 10:00.",
                     "Te esperamos el lunes 16 o el 23 a las 10:00.",
                     "Te esperamos el 16/11 o el 23/11 a las 10:00.",
                     "Te esperamos el lunes 16 a las 10:00 o el lunes 23 a la misma hora.",
                     "Te esperamos el lunes 16 a las 10:00. También el 2026/11/23."):
            with self.subTest(text=text):
                self.refused(text, F())
                self.assertFalse(stated_day_is(text, MON, WED))


# ── 4. The compositional Spanish number parser (independent oracle) ──────────────

_ORACLE = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce "
           "quince dieciséis diecisiete dieciocho diecinueve veinte veintiuno veintidós "
           "veintitrés veinticuatro veinticinco veintiséis veintisiete veintiocho veintinueve "
           "treinta").split()


def _oracle_word(n):
    if n <= 30:
        return _ORACLE[n]
    tens, unit = divmod(n, 10)
    name = {3: "treinta", 4: "cuarenta", 5: "cincuenta"}[tens]
    return name if unit == 0 else f"{name} y {_ORACLE[unit]}"


class C4_04_SpanishNumbers(_Assert):

    def test_cardinal_values_0_to_59(self):
        for n in range(60):
            with self.subTest(n=n):
                self.assertEqual(RV._card_value(RV._norm(_oracle_word(n))), n)

    def test_spelled_hours_0_to_24(self):
        for h in range(0, 25):
            word = "una" if h == 1 else _oracle_word(h)
            hh = h + 12 if 1 <= h <= 7 else h
            expect = f"{hh:02d}:00" if hh <= 23 else None
            for prep in ("a", "para", "desde", "hasta"):
                text = f"Te esperamos el lunes 16 {prep} {'la' if h == 1 else 'las'} {word}."
                with self.subTest(text=text):
                    an = analyze_temporal(text, WED)
                    if expect is None:
                        self.assertTrue(an.unresolved, text)
                    else:
                        self.assertEqual([t.value for t in an.times], [expect], text)
                    facts = F(offered_slots=(expect,)) if expect else F()
                    (self.kept if expect else self.refused)(text, facts)
                    if expect:
                        self.refused(text, F(offered_slots=("10:00",) if expect != "10:00"
                                             else ("11:00",)))

    def test_spelled_minutes(self):
        # "y uno" / "menos uno" are not read as a minute ("y un técnico" is not 10:01): the
        # leftover "uno" after a clock is unresolved (fail-closed)
        for minute in range(1, 60):
            text = f"Te esperamos el lunes 16 a las diez y {_oracle_word(minute)}."
            with self.subTest(minute=minute):
                an = analyze_temporal(text, WED)
                if minute == 1:
                    self.assertTrue(an.unresolved, text)
                else:
                    self.assertEqual([t.value for t in an.times], [f"10:{minute:02d}"])
        for minute in range(1, 31):
            text = f"Te esperamos el lunes 16 a las diez menos {_oracle_word(minute)}."
            with self.subTest(menos=minute):
                an = analyze_temporal(text, WED)
                if minute == 1:
                    self.assertTrue(an.unresolved, text)
                else:
                    self.assertEqual([t.value for t in an.times], [f"09:{60 - minute:02d}"])
        self.assertEqual([t.value for t in analyze_temporal(
            "a las diez y media, a las diez y cuarto, a las diez menos cuarto, a las diez en "
            "punto", WED).times], ["10:30", "10:15", "09:45", "10:00"])
        self.assertEqual([t.value for t in analyze_temporal(
            "a las 8 menos cuarto; a las 2 menos cuarto; a las 2 y veinte de la tarde", WED).times],
            ["07:45", "13:45", "14:20"])
        self.assertTrue(analyze_temporal("a las diez menos cuarenta", WED).unresolved)

    def test_spelled_windows(self):
        an = analyze_temporal("Te esperamos el lunes 16 de las diez a las once.", WED)
        self.assertEqual([t.value for t in an.times], ["10:00", "11:00"])
        an = analyze_temporal("Te esperamos el lunes 16 entre las tres y las tres y media.", WED)
        self.assertEqual([t.value for t in an.times], ["15:00", "15:30"])
        self.refused("Te esperamos el lunes 16 de las nueve a las doce.", F())
        self.kept("Te esperamos el lunes 16 de las diez a las once.", F())


# ── 5. The reviewer's surviving mutants, each killed by name ─────────────────────

class C4_05_ReviewerMutants(_Assert):

    def test_r03_yearless_date_compares_its_month(self):
        self.refused("Te esperamos el 16/12 a las 10:00.", F())
        self.refused("Te esperamos el 16 de diciembre a las 10:00.", F())
        self.kept("Te esperamos el 16/11 a las 10:00.", F())

    def test_r05_pero_is_not_needed_to_end_the_negative_scope(self):
        self.refused("No hay turnos el jueves, pero el lunes 23 hay a las 10:00.", F())
        self.refused("No hay turnos el jueves pero el lunes 23 hay a las 10:00.", F())

    def test_r07_a_number_attached_to_a_month_word(self):
        for form in ("23-nov", "23/nov", "23 nov", "nov 23", "23 de nov.", "23.nov"):
            with self.subTest(form=form):
                self.refused(f"Te esperamos {form} a las 10:00.", F())
        self.kept("Te esperamos 16-nov a las 10:00.", F())

    def test_r08_leftover_am_pm_hs(self):
        # a meridiem a clock reads is applied ("once pm" is 23:00, not the 11:00 slot); one no
        # clock consumed is an unresolved fragment
        for text in ("Te esperamos el lunes a las once pm.", "Te esperamos el lunes a las 10:00 hs pm.",
                     "Te esperamos el lunes 16, 10 hs hs.", "Te esperamos el lunes a las once p.m.",
                     "Te esperamos el lunes 16 a las 10:00. Pm."):
            with self.subTest(text=text):
                self.refused(text, F())
        self.kept("Te esperamos el lunes a las diez am.", F())
        self.kept("Te esperamos el lunes a las tres pm.", F())

    def test_r09_menos_cuarto_takes_the_hour_back(self):
        self.refused("Te esperamos el lunes 16 a las 10 menos cuarto.", F(offered_slots=("10:45",)))
        self.kept("Te esperamos el lunes 16 a las 10 menos cuarto.", F(offered_slots=("09:45",)))
        self.kept("Te esperamos el lunes 16 a las once menos cuarto.", F(offered_slots=("10:45",)))

    def test_r16_producer_gate_needs_every_day_canonical(self):
        self.assertFalse(stated_day_is("¿Te reservo el lunes 16 o el jueves 19 a las 10:00?",
                                       MON, WED))
        self.assertFalse(stated_day_is("¿Te reservo el lunes 16 a las 10:00? Si no, el jueves 19.",
                                       MON, WED))
        self.assertTrue(stated_day_is("¿Te reservo el lunes 16 a las 10:00?", MON, WED))

    def test_r18_negative_under_a_booking(self):
        self.refused("No tenemos turnos el jueves; tu turno es el lunes 23 a las 10:00.", B())
        self.refused("No tenemos turnos el jueves. Tu turno es el lunes 16 a las 10:00.", B())

    def test_r20_iso_datetime_time_is_read(self):
        self.refused("Te esperamos el 2026-11-16T12:00.", F())
        self.refused("Te esperamos el 2026-11-16 12:00 hs.", F())
        self.kept("Te esperamos el 2026-11-16T10:00.", F())

    def test_r14_a_declared_claim_with_a_stale_date_is_refused(self):
        # masked on the production path (the builder never passes a stale date, audit §7); the
        # validator refuses it again as defence in depth
        claim = RV.StructuredClaim(RV.DomainClaim.AVAILABILITY, ("2026-11-11", "10:00"))
        stale = F(evaluated=WED, today=THU)
        self.assertEqual(RV._validate_structured_claim(claim, stale),
                         "declared date is not the evaluated date")
        self.assertIsNone(RV._validate_structured_claim(claim, F(evaluated=WED, today=WED)))

    def test_r22_esta_tarde_is_today(self):
        self.refused("Te esperamos esta tarde a las 15:00.", F())
        self.refused("Te esperamos esta mañana a las 10:00.", F())
        self.kept("Te esperamos esta tarde a las 15:00.", F(evaluated=WED))


# ── 6. The CE producer gate reads the same analysis (R6C3RV-P01) ─────────────────

class C4_06_ProducerGate(unittest.TestCase):

    def state(self):
        return SimpleNamespace(active_requested_date="2026-11-16",
                               last_offered_slots='["10:00", "11:00", "15:00", "15:30"]',
                               last_visible_slots='["10:00", "11:00"]')

    def gate(self, text, time="10:00"):
        with mock.patch.object(RV, "_utcnow", lambda: NOON_WED):
            return ConversationEngine._ai_time_day_is_evaluated(
                self.state(), text, {"10:00", "11:00", "15:00", "15:30"}, time)

    def test_stated_day_is_false_for_every_s01_s02_form(self):
        for cid, wrong, _right, kind in S01 + S02:
            if kind != "offer":
                continue
            with self.subTest(cid=cid):
                proposal = "¿" + wrong.rstrip(".") + "?" if not wrong.startswith("¿") else wrong
                ok_day = stated_day_is(proposal, MON, WED)
                times = stated_times_of(proposal, WED)
                # a wrong DAY → stated_day_is False; a wrong TIME → not among the stated
                # times, or the analysis is unresolved
                self.assertTrue(not ok_day or not set(times) <= {"10:00", "11:00", "15:00",
                                                                 "15:30"} or not times,
                                (cid, ok_day, times))

    def test_ce_gate_on_audit_forms(self):
        for text, time in (("¿Te reservo el lunes veintitrés a las 10:00?", "10:00"),
                           ("¿Te reservo el lunes (23) a las 10:00?", "10:00"),
                           ("¿Te reservo el 2026/11/23 a las 10:00?", "10:00"),
                           ("¿Te reservo en 12 días a las 10:00?", "10:00"),
                           ("¿Te reservo el otro lunes a las 10:00?", "10:00"),
                           ("¿Te reservo el lun 23 a las 10:00?", "10:00"),
                           ("¿Te reservo el lunes 16 o el jueves 19 a las 10:00?", "10:00"),
                           ("No hay turnos el jueves, ¿te reservo a las 10:00?", "10:00"),
                           ("¿Te reservo el lunes 16 a las diez y veinte?", "10:00"),
                           ("¿Te reservo el lunes 16 a las diez menos diez?", "10:00"),
                           ("¿Te reservo el lunes 16 a las 10:00, más o menos?", "10:00"),
                           ("¿Te reservo el lunes 16 a las 11:00?", "10:00")):
            with self.subTest(text=text):
                self.assertFalse(self.gate(text, time))
        for text in ("¿Te reservo el lunes 16 a las 10:00?", "¿Te reservo a las 10:00?",
                     "¿Te reservo el lunes 16 a las diez?", "¿Te reservo el 16/11 a las 10:00?",
                     "¿Te reservo el lunes a las 10:00 o a las 11:00?"):
            with self.subTest(kept=text):
                self.assertTrue(self.gate(text))

    def test_ce_parser_misreading_cannot_store_a_time(self):
        # whatever CE's own parser extracts, the gate holds a time only when the analysis
        # reads it: "diez y veinte" is 10:20
        text = "¿Te reservo el lunes 16 a las diez y veinte?"
        _day, ce_time = _parse_scheduling_text([text], WED)
        self.assertFalse(self.gate(text, ce_time or "10:00"))
        self.assertEqual(stated_times_of(text, WED), ("10:20",))


# ── 7. Preserved controls: ordinary prose, inert numbers, business hours ─────────

class C4_07_Controls(_Assert):
    PROSE = (
        "Tu Corolla 2018 lo revisamos el lunes 16 a las 10:00.",
        "Revisamos tu Renault 12 el lunes 16 a las 10:00.",
        "Revisamos tu Peugeot 208 y el 3008 el lunes 16 a las 10:00.",
        "Revisamos tu Fiat 500 el lunes 16 a las 10:00.",
        "Revisamos tu Gol Trend 1.6 del 2015 el lunes 16 a las 10:00.",
        "Revisamos tu camioneta 4x4 el lunes 16 a las 10:00.",
        "La revisión cubre 150 puntos y tu auto tiene 80.000 km.",
        "Tenemos 2 técnicos y el auto tuvo 3 dueños.",
        "Te esperamos el lunes 16 a las 10:00; la revisión cubre 150 puntos.",
        "Te esperamos el lunes 16 a las 10:00 en Av. 9 de Julio 1500.",
        "Te esperamos el lunes 16 a las 10:00 en Calle 25 de Mayo 300.",
        "Te esperamos el lunes 16 a las 10:00 en Corrientes 1500.",
        "La revisión dura unas 2 horas. Te esperamos el lunes 16 a las 10:00.",
        "La revisión dura entre 1 y 2 horas.",
        "Te enviamos el informe en 24 hs.",
        "Escribinos al 11 2345-6789 y te esperamos el lunes 16 a las 10:00.",
        "Escribinos al +54 9 11 2345-6789.",
        "Para el lunes 16 tengo 3 horarios disponibles. Elegí el que te sirva y confirmá el turno acá:",
        "Tengo 10:00 y 11:00. ¿Cuál de las dos te sirve?",
        "Tengo 10:00 y 11:00, ¿te sirven las dos?",
        "Uno, dos y tres: revisamos todo.",
        "Opción 1: el lunes 16 a las 10:00.",
        "Podés pagar en 3 cuotas; te esperamos el lunes 16 a las 10:00.",
        "La revisión tiene un 20% de descuento.",
        "¡Buen fin de semana!",
    )
    HOURS = ("Atendemos de lunes a sábado de 9 a 18 hs.", "Los sábados atendemos de 9:30 a 14 hs.",
             "Respondemos todos los días de 8 a 22.", "Nuestro horario de atención es de 9 a 18.",
             "Trabajamos de lunes a viernes de 9:00 a 18:00.")
    CANONICAL = ("Te esperamos el lunes 16 a las 10:00.", "¿Te reservo el lunes 16 a las 11:00?",
                 "Te esperamos el lunes 16/11 de 10:00 a 11:00.", "Te esperamos el 16 a las 15:30.",
                 "Para el lunes 16. Te esperamos a las 10:00.",
                 "Turno:\n- lunes 16/11\n- 10:00", "Te esperamos el lunes a las diez.",
                 "Te esperamos el lunes a las tres y media de la tarde.")

    def test_prose_and_inert_numbers(self):
        for text in self.PROSE:
            with self.subTest(text=text):
                self.kept(text, F())

    def test_general_hours_with_and_without_an_evaluation(self):
        for text in self.HOURS:
            with self.subTest(text=text):
                self.kept(text, F())
                self.kept(text, F(availability_checked=False, offered_slots=(),
                                  availability_date=None))

    def test_canonical_offers_and_bookings(self):
        for text in self.CANONICAL:
            with self.subTest(text=text):
                self.kept(text, F())
        for text in ("Tu turno es el lunes 16 a las 10:00.", "Tu turno es el lunes a las 10.",
                     "Listo, tu turno está confirmado.", "Tu turno es el lunes 16 de 10:00 a 10:45."):
            with self.subTest(booking=text):
                self.kept(text, B())

    def test_date_named_street_without_a_prefix_is_still_refused(self):
        # disclosed limit (fail-closed): "9 de Julio" with no street prefix reads as a date
        self.refused("Te esperamos el lunes 16 a las 10:00 en 9 de Julio 1500.", F())


# ── 8. Generated token-coverage corpus ───────────────────────────────────────────

#: day forms → (text, canonical for the Monday-16 evaluation / Monday-16 booking)
DAY_FORMS = (
    ("el lunes 16", True), ("el lun 16", True), ("el lun. 16", True), ("el lunes (16)", True),
    ("el lunes [16]", True), ("el lunes nº 16", True), ("el 2026/11/16", True),
    ("el 2026.11.16", True), ("el 16-nov", True), ("el lunes 16/11", True),
    ("el 16 de noviembre", True), ("el próximo lunes", True), ("el lunes", True), ("el 16", True),
    ("el lunes 23", False), ("el lun 23", False), ("el lunes (23)", False), ("el lunes [23]", False),
    ("el lunes nº 23", False), ("el 2026/11/23", False), ("el 2026.11.23", False),
    ("el 23-nov", False), ("el lunes veintitrés", False), ("el lunes dieciséis", False),
    ("en 12 días", False), ("dentro de 5 días", False), ("en 3 días hábiles", False),
    ("el otro lunes", False), ("el lunes siguiente", False), ("el lunes que viene", False),
    ("el jueves", False), ("el jue 19", False), ("el mar 17", False), ("el mié 18", False),
    ("el vie 20", False), ("el sáb 21", False), ("el dom 22", False),
    ("el lunes 16 o el jueves 19", False), ("el lunes 16 y el 23", False), ("el 16/12", False),
)
#: time forms → value ("HH:MM") or None (unresolved)
TIME_FORMS = (
    ("a las 10:00", "10:00"), ("a las 10", "10:00"), ("a las diez", "10:00"),
    ("para las diez", "10:00"), ("a las once", "11:00"), ("desde las 11", "11:00"),
    ("a las tres de la tarde", "15:00"), ("a las quince", "15:00"), ("a las 15 hs", "15:00"),
    ("a las quince y treinta", "15:30"), ("a las tres y media", "15:30"),
    ("a las diez y cuarenta y cinco", "10:45"), ("a las once menos cuarto", "10:45"),
    ("a las dieciséis", "16:00"), ("a las doce", "12:00"), ("para las doce", "12:00"),
    ("a las diez y veinte", "10:20"), ("a las diez menos diez", "09:50"),
    ("a las trece y treinta", "13:30"), ("a las 10, más o menos", None), ("a las 10 aprox", None),
    ("sobre las 10", None), ("después de las 10", None), ("antes de las 11", None),
    ("a primera hora", None), ("a las once pm", "23:00"), ("de nueve a doce", None),
    ("a las veinticinco", None),
)
#: frames: (name, template, kind) — kind "offer" / "booking" / "negative"
FRAMES = (
    ("statement", "Te esperamos {d} {t}.", "offer"),
    ("question", "¿Te reservo {d} {t}?", "offer"),
    ("cross", "Para {d}. Te esperamos {t}.", "offer"),
    ("newline", "Turno:\n{d}\n{t}", "offer"),
    ("bullets", "• Día: {d}\n• Hora: {t}", "offer"),
    ("clause", "Perfecto, {d}; te esperamos {t}.", "offer"),
    ("negpos", "No hay turnos el jueves, sí {d} {t}.", "offer"),
    ("negsemi", "No tenemos disponibilidad el jueves; {d} tenemos {t}.", "offer"),
    ("booking", "Tu turno es {d} {t}.", "booking"),
    ("bookcross", "Tu turno quedó para {d}. Te esperamos {t}.", "booking"),
    ("bookneg", "No hay turnos el jueves; tu turno es {d} {t}.", "booking"),
)
OFFER_SLOTS = {"10:00", "11:00", "15:00", "15:30"}
BOOKED_TIMES = {"10:00", "10:45"}


def generate_corpus():
    """(text, facts kind, expected, label). Deterministic. A reply is kept only when its
    day is the canonical date, its time is canonical for the facts (an evaluated slot, or
    the committed start/end), and the frame carries no other day (the "bookneg" frame names
    Thursday under a booking, so it is always refused)."""
    out = []
    for (fname, tpl, kind), (d, d_ok), (t, t_val) in itertools.product(FRAMES, DAY_FORMS,
                                                                        TIME_FORMS):
        allowed = BOOKED_TIMES if kind == "booking" else OFFER_SLOTS
        good = d_ok and t_val is not None and t_val in allowed and fname != "bookneg"
        out.append((tpl.format(d=d, t=t), kind, "kept" if good else "refused",
                    f"{fname}/{d}/{t}"))
    return out


class C4_08_GeneratedCorpus(_Assert):

    def test_generated_token_coverage(self):
        corpus = generate_corpus()
        self.assertGreater(len(corpus), 12000)
        counts = {"kept": [0, 0], "refused": [0, 0]}
        bad = []
        for text, kind, want, label in corpus:
            facts = B() if kind == "booking" else F()
            out, av = T.outcome(text, facts)
            delivered = " ".join(out.text.split()) == " ".join(text.split())
            ok = delivered if want == "kept" else (not delivered and any(not f.allowed for f in av))
            counts[want][0 if ok else 1] += 1
            if not ok:
                bad.append((label, out.text[:60]))
        self.assertEqual(bad, [], f"{len(bad)} generated cases misjudged; first: {bad[:8]}")
        print(f"\nR6-C4 generated token-coverage corpus: {len(corpus)} cases; "
              f"wrong refused {counts['refused'][0]}/{sum(counts['refused'])}; "
              f"canonical kept {counts['kept'][0]}/{sum(counts['kept'])}")

    def test_the_producer_gate_agrees_with_the_validator(self):
        # every offer case the validator refuses for its DAY is refused by stated_day_is
        for (fname, tpl, kind), (d, d_ok) in itertools.product(FRAMES[:6], DAY_FORMS):
            text = tpl.format(d=d, t="a las 10:00")
            with self.subTest(text=text):
                self.assertEqual(stated_day_is(text, MON, WED), d_ok, text)


# ── 9. Structure ─────────────────────────────────────────────────────────────────

class C4_09_Structure(unittest.TestCase):

    def test_validator_and_producer_gate_share_one_analysis(self):
        src = (_ROOT / "backend/app/services/response_validator.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in ("stated_day_is", "stated_times_of", "validate_response"):
            calls = {getattr(c.func, "id", None) for c in ast.walk(fns[name])
                     if isinstance(c, ast.Call)}
            self.assertIn("analyze_temporal", calls, name)
        ce = (_ROOT / "backend/app/services/conversation_engine.py").read_text(encoding="utf-8")
        self.assertIn("_rv_stated_times_of(reply_text, today)", ce)
        # R6-C4A: the producer gate is no longer called — the AI-proposed-time producer is
        # removed; the gate stays (and is unit-tested above) as defence in depth only.
        self.assertNotIn("state.preferred_time = ai_proposed_time", ce)

    def test_verdict_state_for_a_partial_parse_is_never_no_expression(self):
        for text in ("Te esperamos el lunes veintitrés.", "Te esperamos en 12 días.",
                     "Te esperamos el otro lunes.", "Te esperamos el lun 23."):
            with self.subTest(text=text):
                v = temporal_verdict(analyze_temporal(text, WED), F())
                self.assertIsNotNone(v)
                self.assertFalse(v.allowed)
                self.assertNotEqual(v.state, TemporalState.NO_TEMPORAL_EXPRESSION)


# ── 10. Implementer's own adversarial pass (beyond the audit) ────────────────────

class C4_10_FurtherShapes(_Assert):

    def test_past_days_interval_bounds_and_ordinal_weekdays(self):
        for text in ("Te esperamos ayer a las 10:00.", "Te esperamos anteayer a las 10:00.",
                     "Te esperamos el lunes pasado a las 10:00.",
                     "Te esperamos el lunes anterior a las 10:00.",
                     "Te esperamos el último lunes a las 10:00.",
                     "Te esperamos el primer lunes a las 10:00.",
                     "Te esperamos el lunes de la semana próxima a las 10:00.",
                     "Te esperamos antes del lunes a las 10:00.",
                     "Te esperamos hasta el lunes a las 10:00.",
                     "Te esperamos desde el lunes a las 10:00.",
                     "Te esperamos después del 16 a las 10:00."):
            with self.subTest(text=text):
                self.refused(text, F())

    def test_numbers_beside_day_words_stay_temporal(self):
        for text in ("Te esperamos el día 1611 a las 10:00.", "Te esperamos el lunes 1611 a las 10:00.",
                     "Tengo el lunes 23 opciones a las 10:00.",
                     "Te esperamos el lunes Navidad 25 a las 10:00.",
                     "Te esperamos para Navidad 25 a las 10:00.",
                     "Te esperamos el lunes 16 a las dies.", "Te esperamos el lunes 16 a las 10 y 11.",
                     "Te esperamos el lunes 16 — el 23 a las 10:00.",
                     "No hay turnos el jueves — el lunes 23 a las 10:00.",
                     "No hay turnos el jueves… el lunes 23 a las 10:00.",
                     "• Día: el lunes 16\n• Hora: a primera hora",
                     "• Día: el jueves"):
            with self.subTest(text=text):
                self.refused(text, F())
        self.kept("Revisamos tu auto del 2015 el lunes 16 a las 10:00.", F())

    def test_a_label_line_number_is_temporal(self):
        # E16: a spelled number on a label-only line ("Turno: doce hora") states a time by layout
        for text in ("Te esperamos el lunes 16.\nTurno: doce hora",
                     "Te esperamos el lunes 16.\nTurno: dos para",
                     "Te esperamos el lunes 16 a las 10:00.\nTurno: tres la"):
            with self.subTest(text=text):
                self.refused(text, F())

    def test_trailing_approximators(self):
        for tail in ("en adelante", "o antes", "o después", "como mucho", "o por ahí", "y pico",
                     "o algo así"):
            with self.subTest(tail=tail):
                self.refused(f"Te esperamos el lunes 16 a las 10:00 {tail}.", F())

    def test_a_parenthetical_with_a_day_is_not_information(self):
        self.refused("Te esperamos el lunes 16 (o el 23) a las 10:00.", F())
        self.refused("Te esperamos el lunes 16 a las 10:00 (atendemos el jueves 19).", F())
        self.kept("Te esperamos el lunes 16 a las 10:00 (atendemos de 9 a 18 hs).", F())
        self.kept("Para mañana jueves 12/11 no hay horarios disponibles (sin disponibilidad). "
                  "¿Tenés otro día preferido?", F())


if __name__ == "__main__":
    unittest.main()
