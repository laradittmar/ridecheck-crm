"""R6-C2 — R6C1RV-S01: day/date binding, unit level (pure validator + CE builder rules).

The full production path is `test_r6_c2_day_date_production_path.py`; this file pins the
pieces it is built from:

* `canonical_date` — strict typed/ISO canonical date, never free text or a datetime;
* `_day_anchors` — deterministic resolution of every supported day/date form;
* the business timezone (America/Argentina/Buenos_Aires) across the UTC and local midnights,
  never UTC and never a database session;
* `_check_availability` — statements and questions share one day rule; matching times on
  another day are refused; a confirmed booking restates only its committed date and times;
* declared (structured) AVAILABILITY claims — a declared ISO date must be the evaluated one;
* `ConversationEngine._canonical_availability_date` — the date and the slot set belong to
  ONE evaluation, else no date;
* every CE site that writes `last_offered_slots` writes `active_requested_date` with it.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app.services.response_validator as RV  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.response_validator import (  # noqa: E402
    CLAIM_AVAILABILITY, CanonicalFacts, DomainClaim, StructuredClaim, validate_response)

WED = date(2026, 11, 11)
THU, FRI, SAT, MON = date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 14), date(2026, 11, 16)
SLOTS = ("10:00", "11:00", "15:00", "15:30")


def F(evaluated=MON, today=WED, **kw):
    base = dict(vehicle_marca="Toyota", vehicle_modelo="Corolla", availability_checked=True,
                offered_slots=SLOTS, availability_date=evaluated, business_today=today)
    base.update(kw)
    return CanonicalFacts(**base)


def avail(text, facts):
    out = validate_response(text, facts)
    found = [f for f in out.findings if f.claim == CLAIM_AVAILABILITY]
    return out, found


class _Assert(unittest.TestCase):
    def refused(self, text, facts):
        out, found = avail(text, facts)
        self.assertTrue(found and not found[0].allowed, f"{text!r}: {found}")
        self.assertNotEqual(out.text, text)

    def kept(self, text, facts):
        out, found = avail(text, facts)
        self.assertEqual(out.text, text, f"{text!r}: {out.findings}")
        self.assertTrue(all(f.allowed for f in found), found)


class CanonicalDateStrictness(unittest.TestCase):

    def test_typed_and_iso_only(self):
        self.assertEqual(RV.canonical_date(MON), MON)
        self.assertEqual(RV.canonical_date("2026-11-16"), MON)
        for bad in ("16/11/2026", "2026-11-16T10:00", "2026-13-40", "2026-02-30", "lunes", "",
                    " 2026-11-16", "2026-11-16 ", None, 20261116,
                    datetime(2026, 11, 16, 10, 0), b"2026-11-16"):
            with self.subTest(bad=bad):
                self.assertIsNone(RV.canonical_date(bad))

    def test_facts_accept_only_an_exact_date(self):
        # a str (even a valid ISO one) or a datetime in the facts field names NO date
        for bad in ("2026-11-16", datetime(2026, 11, 16, 10, 0)):
            with self.subTest(bad=bad):
                facts = F(evaluated=bad)
                _AssertProxy.refused("Te esperamos el lunes a las 10:00.", facts)

    def test_validator_zone_is_the_schedule_zone(self):
        from app.services import schedule
        self.assertEqual(RV.BUSINESS_TIMEZONE, schedule.BUSINESS_TIMEZONE)
        self.assertEqual(RV.BUSINESS_TIMEZONE, "America/Argentina/Buenos_Aires")


class DayAnchorResolution(unittest.TestCase):

    def anchors(self, text, today=WED):
        return RV._day_anchors(text, today)

    def test_every_supported_form(self):
        cases = {
            "el 2026-11-16": [("date", MON)],
            "el 16/11": [("monthday", 11, 16)],
            "el 16/11/2026": [("date", MON)],
            "el 16/11/26": [("date", MON)],
            "el 20 de noviembre": [("monthday", 11, 20)],
            "el 16 de noviembre de 2026": [("date", MON)],
            "el día 14": [("date", SAT)],
            "el día 3": [("date", date(2026, 12, 3))],      # next occurrence, not the past
            "hoy": [("date", WED)],
            "mañana": [("date", THU)],
            "pasado mañana": [("date", FRI)],
            "el lunes": [("weekday", 0)],
            "este jueves": [("weekday", 3)],
            "el próximo viernes": [("weekday", 4)],
            "Te espero jueves de 15:00 a 15:30.": [("weekday", 3)],
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                got, malformed = self.anchors(text)
                self.assertFalse(malformed)
                self.assertEqual(sorted(got, key=str), sorted(want, key=str))

    def test_morning_is_not_tomorrow(self):
        for text in ("por la mañana", "a la mañana", "de mañana"):
            with self.subTest(text=text):
                self.assertEqual(self.anchors(text), ([], False))
        # R6-C3: the habitual "las mañanas" outside a general-hours frame names no single
        # day and no single time: it is unresolved, never "no anchor".
        self.assertEqual(self.anchors("las mañanas"), ([], True))

    def test_general_hours_name_no_day(self):
        for text in ("Trabajamos de lunes a viernes de 9:00 a 18:00.",
                     "Los sábados atendemos de 9:00 a 13:00."):
            with self.subTest(text=text):
                self.assertEqual(self.anchors(text), ([], False))

    def test_impossible_dates_are_malformed(self):
        for text in ("el 31/11", "el 30 de febrero", "el 2026-02-30", "el día 40",
                     "el 16/11/202", "el 0/11"):
            with self.subTest(text=text):
                self.assertTrue(self.anchors(text)[1], text)

    def test_month_day_without_a_year_is_not_moved_to_another_year(self):
        # 16/11 matches 2027-11-16 by month/day; it is never re-dated to make it pass
        self.assertTrue(RV._anchor_is(("monthday", 11, 16), date(2027, 11, 16)))
        self.assertFalse(RV._anchor_is(("monthday", 11, 16), date(2026, 11, 17)))
        self.assertFalse(RV._anchor_is(("date", date(2027, 11, 16)), MON))


class BusinessTimezone(_Assert):
    UTC_CROSSED = datetime(2026, 11, 12, 1, 30, tzinfo=timezone.utc)   # BA Wed 22:30
    BA_CROSSED = datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)    # BA Thu 00:30

    def test_business_date_is_buenos_aires_not_utc(self):
        self.assertEqual(RV.business_date_at(self.UTC_CROSSED), WED)
        self.assertEqual(RV.business_date_at(self.BA_CROSSED), THU)
        with self.assertRaises(ValueError):
            RV.business_date_at(datetime(2026, 11, 12, 1, 30))
        with mock.patch.object(RV, "_utcnow", lambda: self.UTC_CROSSED):
            self.assertEqual(RV.business_today(), WED)
        with mock.patch.object(RV, "_utcnow", lambda: self.BA_CROSSED):
            self.assertEqual(RV.business_today(), THU)

    def test_relative_days_around_both_midnights(self):
        for now, hoy, manana, pasado in ((self.UTC_CROSSED, WED, THU, FRI),
                                         (self.BA_CROSSED, THU, FRI, SAT)):
            with mock.patch.object(RV, "_utcnow", lambda now=now: now):
                for word, day in (("hoy", hoy), ("mañana", manana), ("pasado mañana", pasado)):
                    with self.subTest(now=now.isoformat(), word=word):
                        s = f"Podemos ir {word} entre 10:00 y 11:00."
                        # business_today=None: the validator reads its own business clock
                        self.kept(s, F(evaluated=day, today=None))
                        for other in (hoy, manana, pasado):
                            if other != day:
                                self.refused(s, F(evaluated=other, today=None))

    def test_weekday_at_the_boundary(self):
        for now in (self.UTC_CROSSED, self.BA_CROSSED):
            with mock.patch.object(RV, "_utcnow", lambda now=now: now):
                with self.subTest(now=now.isoformat()):
                    # R6-C4 (R6C3RV-S01 H1): a weekday that is the business day's own is
                    # ambiguous and refused; in apposition to "hoy" it is today. Was: the bare
                    # "el jueves" kept on BA Thursday.
                    if RV.business_today() == THU:
                        self.refused("Te esperamos el jueves a las 10:00.",
                                     F(evaluated=THU, today=None))
                        self.kept("Te esperamos hoy jueves a las 10:00.",
                                  F(evaluated=THU, today=None))
                    else:
                        self.kept("Te esperamos el jueves a las 10:00.",
                                  F(evaluated=THU, today=None))
                    self.refused("Te esperamos el miércoles a las 10:00.",
                                 F(evaluated=THU, today=None))

    def test_a_past_evaluation_is_stale(self):
        with mock.patch.object(RV, "_utcnow", lambda: self.BA_CROSSED):
            self.refused("Te esperamos el miércoles a las 10:00.", F(evaluated=WED, today=None))
        with mock.patch.object(RV, "_utcnow", lambda: self.UTC_CROSSED):
            # R6-C4: "hoy miércoles" — the bare "el miércoles" on a Wednesday is ambiguous
            self.kept("Te esperamos hoy miércoles a las 10:00.", F(evaluated=WED, today=None))
            self.refused("Te esperamos el miércoles a las 10:00.", F(evaluated=WED, today=None))

    def test_the_utc_date_would_have_been_wrong(self):
        """Control: at 01:30Z the UTC date is already Thursday; resolving 'hoy' in UTC
        would accept a Thursday evaluation for 'hoy' — the business calendar refuses it."""
        self.assertEqual(self.UTC_CROSSED.date(), THU)
        with mock.patch.object(RV, "_utcnow", lambda: self.UTC_CROSSED):
            self.refused("Podemos ir hoy entre 10:00 y 11:00.", F(evaluated=THU, today=None))


class AvailabilityDayRule(_Assert):

    def test_reviewer_mismatches(self):
        for text, evaluated in (("Te esperamos el jueves de 10:00 a 11:00.", MON),
                                ("Te esperamos el 20 de noviembre de 15:00 a 15:30.", SAT),
                                ("Podemos ir mañana entre 10:00 y 11:00.", WED),
                                ("Te esperamos el 19/11 de 10:00 a 11:00.", MON),
                                ("Te ofrezco el jueves a las 10:00.", MON),
                                ("¿Te reservo el jueves de 10:00 a 11:00?", MON),
                                ("Pasado mañana te esperamos de 15:00 a 15:30.", MON),
                                ("Te esperamos el lunes 23/11 a las 10:00.", MON),
                                ("Tengo disponibilidad el jueves.", MON)):
            with self.subTest(text=text):
                self.refused(text, F(evaluated=evaluated))

    def test_reviewer_matches(self):
        for text, evaluated in (("Te esperamos el lunes de 10:00 a 11:00.", MON),
                                ("Te esperamos el 14 de noviembre de 15:00 a 15:30.", SAT),
                                ("Podemos ir hoy entre 10:00 y 11:00.", WED),
                                ("Te ofrezco el lunes a las 10:00.", MON),
                                ("¿Te reservo el lunes de 10:00 a 11:00?", MON),
                                ("Tengo disponibilidad el lunes.", MON)):
            with self.subTest(text=text):
                self.kept(text, F(evaluated=evaluated))

    def test_statement_and_question_share_the_rule(self):
        for verb in ("Te reservo", "¿Te reservo"):
            end = "?" if verb.startswith("¿") else "."
            with self.subTest(verb=verb):
                self.refused(f"{verb} el jueves a las 10:00{end}", F())
                self.kept(f"{verb} el lunes a las 10:00{end}", F())

    def test_an_impossible_date_beside_a_valid_weekday_is_refused(self):
        # "el lunes 31/11": the weekday matches the evaluated Monday, the date cannot exist
        for text in ("Te esperamos el lunes 31/11 a las 10:00.",
                     "Te esperamos el lunes 30 de febrero a las 10:00."):
            with self.subTest(text=text):
                self.refused(text, F())

    def test_no_canonical_date_refuses_any_named_day(self):
        for text in ("Te esperamos el lunes a las 10:00.", "¿Te reservo el lunes a las 10:00?",
                     "Te esperamos el 16/11 de 10:00 a 11:00."):
            with self.subTest(text=text):
                self.refused(text, F(evaluated=None))

    def test_no_anchor_keeps_the_existing_endpoint_rule(self):
        self.kept("Te ofrezco las 10:00 o las 15:30.", F(evaluated=None))
        self.refused("Te ofrezco las 12:00.", F(evaluated=None))

    def test_no_anchor_window_with_an_empty_evaluation_is_refused(self):
        # The R6-C1 window rule on its own (no day named, so the day rule never runs): a
        # concrete window is supported only by evaluated slots, and an evaluation that
        # produced none supports no window. (R6-C1 pinned this with a weekday-anchored
        # window, which the R6-C2 day rule now refuses first.)
        for text in ("El turno sería de 10:00 a 11:00.", "Agendamos la inspección de 10:00 a 11:00."):
            with self.subTest(text=text):
                self.refused(text, F(evaluated=None, offered_slots=()))
                self.kept(text, F(evaluated=None))          # the same window, evaluated slots

    def test_general_hours_survive_without_any_evaluation(self):
        for text in ("Trabajamos de lunes a viernes de 9:00 a 18:00.",
                     "Los sábados atendemos de 9:00 a 13:00."):
            with self.subTest(text=text):
                self.kept(text, CanonicalFacts())

    def test_negative_statement_unchanged(self):
        self.kept("No tengo disponibilidad a las 18:00.", F())


class ConfirmedBooking(_Assert):

    def B(self, **kw):
        base = dict(booking_confirmed=True, booked_date=MON, booked_times=("10:00", "10:45"),
                    availability_date=date(2026, 11, 19))
        base.update(kw)
        return F(**base)

    def test_restates_only_its_own_date_and_times(self):
        self.kept("Te esperamos el lunes 16/11 a las 10:00.", self.B())
        self.kept("Te esperamos el lunes de 10:00 a 10:45.", self.B())
        self.refused("Te esperamos el jueves a las 10:00.", self.B())        # requested date
        self.refused("Te esperamos el lunes a las 11:00.", self.B())         # another time
        self.refused("Te esperamos el lunes a las 10:00.", self.B(booked_date=None))
        self.refused("Te esperamos el lunes a las 10:00.", self.B(booked_date="2026-11-16"))

    def test_booking_without_day_or_time_still_restates(self):
        self.kept("Listo, tu turno está confirmado.", self.B())


class DeclaredAvailabilityClaim(unittest.TestCase):

    def why(self, value, facts):
        return RV._validate_structured_claim(StructuredClaim(DomainClaim.AVAILABILITY, value),
                                             facts)

    def test_declared_date_must_be_the_evaluated_date(self):
        self.assertIsNone(self.why(("10:00", "2026-11-16"), F()))
        self.assertIsNotNone(self.why(("10:00", "2026-11-19"), F()))
        self.assertIsNotNone(self.why(("10:00", "2026-11-16"), F(evaluated=None)))
        self.assertIsNotNone(self.why(("2026-11-16",), F()))           # a date with no slot
        self.assertIsNotNone(self.why(("12:00", "2026-11-16"), F()))
        self.assertIsNotNone(self.why(("10:00", "2026-11-31"), F()))
        # R6-C3: a deterministic availability claim must carry its exact ISO date.
        self.assertIsNotNone(self.why(("10:00",), F(evaluated=None)))


class StatedDayHelper(unittest.TestCase):

    def test_public_helper(self):
        self.assertTrue(RV.stated_day_is("Te parece a las 10:00?", None, WED))
        self.assertTrue(RV.stated_day_is("¿El lunes a las 10:00?", MON, WED))
        self.assertFalse(RV.stated_day_is("¿El jueves a las 10:00?", MON, WED))
        self.assertFalse(RV.stated_day_is("¿El lunes a las 10:00?", None, WED))
        self.assertFalse(RV.stated_day_is("¿El lunes a las 10:00?", "2026-11-16", WED))


def _state(date_iso, offered, visible=None):
    return SimpleNamespace(active_requested_date=date_iso,
                           last_offered_slots=None if offered is None else json.dumps(offered),
                           last_visible_slots=None if visible is None else json.dumps(visible))


class OneEvaluation(unittest.TestCase):
    """`_canonical_availability_date`: the canonical date and the slot set are one
    evaluation, or there is no date."""

    def date_of(self, state, offered, today=WED):
        return ConversationEngine._canonical_availability_date(state, tuple(offered), today)

    def test_consistent_evaluation(self):
        self.assertEqual(self.date_of(_state("2026-11-16", list(SLOTS)), SLOTS), MON)
        self.assertEqual(self.date_of(_state("2026-11-16", list(SLOTS), ["10:00"]), ("10:00",)),
                         MON)

    def test_slots_without_a_date(self):
        self.assertIsNone(self.date_of(_state(None, list(SLOTS)), SLOTS))

    def test_date_without_slots(self):
        self.assertIsNone(self.date_of(_state("2026-11-16", None), ()))
        self.assertIsNone(self.date_of(_state("2026-11-16", []), ()))

    def test_visible_list_of_another_evaluation(self):
        self.assertIsNone(self.date_of(_state("2026-11-16", ["10:00", "11:00"],
                                              ["15:00", "15:30"]), ("15:00", "15:30")))
        self.assertIsNone(self.date_of(_state("2026-11-16", None, ["10:00"]), ("10:00",)))

    def test_invalid_or_stale_date(self):
        for bad in ("2026-13-40", "16/11/2026", "lunes", "2026-11-16T10:00", ""):
            with self.subTest(bad=bad):
                self.assertIsNone(self.date_of(_state(bad, list(SLOTS)), SLOTS))
        self.assertIsNone(self.date_of(_state("2026-11-10", list(SLOTS)), SLOTS))
        self.assertEqual(self.date_of(_state("2026-11-11", list(SLOTS)), SLOTS), WED)

    def test_malformed_evaluation_record(self):
        for raw in ("not json", '{"a": 1}', '"10:00"'):
            with self.subTest(raw=raw):
                st = SimpleNamespace(active_requested_date="2026-11-16", last_offered_slots=raw,
                                     last_visible_slots=None)
                self.assertIsNone(self.date_of(st, SLOTS))

    def test_new_request_updates_the_date(self):
        st = _state("2026-11-16", list(SLOTS))
        self.assertEqual(self.date_of(st, SLOTS), MON)
        st.active_requested_date, st.last_offered_slots = "2026-11-19", json.dumps(["10:00"])
        self.assertEqual(self.date_of(st, ("10:00",)), date(2026, 11, 19))


class AIProposedTimeIsNotStoredOnAnotherDay(unittest.TestCase):
    """R6-C2: the SCHEDULING-stage producer holds an AI-proposed time as `preferred_time` only
    when it is an evaluated slot (R6) AND the day the reply names is the evaluated date."""
    NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)      # Wed 11 noon, Buenos Aires

    def gate(self, reply, date_iso="2026-11-16", slots=SLOTS):
        with mock.patch.object(RV, "_utcnow", lambda: self.NOW):
            return ConversationEngine._ai_time_day_is_evaluated(
                _state(date_iso, list(slots), list(slots)), reply, set(slots))

    def test_the_reply_day_must_be_the_evaluated_date(self):
        self.assertTrue(self.gate("¿Te parece bien el lunes a las 10:00?"))
        self.assertTrue(self.gate("¿Te parece bien a las 10:00?"))           # no day named
        self.assertFalse(self.gate("¿Te parece bien el jueves a las 10:00?"))
        self.assertFalse(self.gate("¿Te parece bien mañana a las 10:00?"))   # Thu 12, not Mon 16
        self.assertFalse(self.gate("¿Te parece bien el lunes a las 10:00?", date_iso=None))
        self.assertFalse(self.gate("¿Te parece bien el lunes a las 10:00?", date_iso="2026-11-09"))

    def test_the_producer_consults_the_gate_before_storing(self):
        src = (_ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()
        # R6-C4A: the producer no longer exists. No state is derived from model prose: the
        # body is discarded at `_apply_transactional_boundary` on the SCHEDULING route, and the
        # engine never parses the model's reply for a time or stores one.
        self.assertNotIn("state.preferred_time = ai_proposed_time", src)
        self.assertNotIn("_parse_scheduling_text([ai_reply_text]", src)
        self.assertIn("def _apply_transactional_boundary(", src)


class EvaluationWriteSites(unittest.TestCase):
    """Every CE statement block that writes a non-empty-or-empty `last_offered_slots`
    evaluation also writes `active_requested_date` in the same block (or clears both)."""

    def test_offered_slots_are_never_written_without_the_date(self):
        src = (_ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()
        tree = ast.parse(src)
        sites = 0
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                targets = []
                for stmt in block:
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                                    and t.value.id == "state":
                                targets.append(t.attr)
                if "last_offered_slots" in targets:
                    sites += 1
                    self.assertIn("active_requested_date", targets,
                                  f"line {block[0].lineno}: slots written without the date")
        self.assertGreaterEqual(sites, 6)


_AssertProxy = _Assert()

if __name__ == "__main__":
    unittest.main()
