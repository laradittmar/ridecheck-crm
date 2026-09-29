"""L4.7W5-FAQ-HOURS-TRUTH-R2 — unproven hours prose is removed, not tolerated.

R1 removed only what its grammar recognised and appended the canonical answer regardless, so
an unrecognised phrasing survived NEXT TO the correct schedule. The independent review proved
five such shapes and one mirror-image defect (a genuine appointment offer deleted as a false
hours claim).

R2 changes the decision, not the grammar. While hours correction is armed, a response unit
carrying a weekday token AND a clock-like number must prove itself:

    proof A   its claims parse completely and every one agrees with canonical_weekday_hours()
    proof B   every clock token is a slot this thread holds in canonical state

Anything else is removed. Unrecognised is unproven, and unproven is removed.

These are OUTPUT probes. Not one is a customer phrasing, and none may enter production code.

ADV-*   the adversarial shapes the review proved, plus the cases the milestone requires
PROP-*  nine properties that hold regardless of wording
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import conversation_engine as ce           # noqa: E402
from app.services.conversation_engine import (               # noqa: E402
    ConversationEngine,
    _clock_tokens,
    _hours_patterns,
    _hours_truth_violations,
    _hours_unit_survives,
    _norm_lower,
    _protected_slot_minutes,
    _regenerated_offer_es,
    _stated_hours_claims,
    _UNIT_PROTECTED_SLOT,
    _UNIT_VERIFIED_HOURS,
)
from app.services.schedule import (                           # noqa: E402
    business_hours_for_weekday,
    business_hours_summary_es,
    canonical_weekday_hours,
)

CANONICAL = business_hours_summary_es()
SATURDAY, SUNDAY = 5, 6

LIVE_GENERATED = (
    "Para la revisión del Peugeot 208 2022, ¿qué día y horario te viene mejor? "
    "Trabajamos lunes de 13 a 18 hs, martes de 9.30 a 14 hs, miércoles de 9 a 18 hs, "
    "jueves de 9 a 14 hs, viernes de 9 a 18 hs y sábados de 9 a 13 hs."
)


def compose(primary: str, slots=None, armed: bool = True) -> str:
    """The real composition path, with a controlled topic set and canonical slot state."""
    raw = json.dumps(list(slots)) if slots else None
    state = SimpleNamespace(last_visible_slots=raw, last_offered_slots=raw)
    engine = ConversationEngine.__new__(ConversationEngine)
    engine._turn_ctx = SimpleNamespace(state=state, thread=SimpleNamespace(id=None))
    engine._contributing_sources = None
    engine._faq_topics_for_burst = (
        lambda text, ctx=None, st=None: {"business_hours"} if armed else set())
    return ConversationEngine._compose_secondary_answers(engine, primary, "irrelevant")


def hours_units(text: str) -> list[str]:
    """Units of `text` that carry a weekday token and a clock-like number."""
    _range_re, _single_re, day_re = _hours_patterns()
    found = []
    for line in text.split("\n"):
        for unit in re.split(r"(?<=[.!?])\s+", line):
            if not unit.strip():
                continue
            normalized = _norm_lower(unit)
            if day_re.search(normalized) and _clock_tokens(normalized):
                found.append(unit)
    return found


def stated_intervals(text: str) -> dict[int, set[tuple]]:
    """weekday -> every (start, end) the text states for it, across all its units."""
    per_day: dict[int, set[tuple]] = {}
    for unit in hours_units(text):
        claims, _closed, _open = _stated_hours_claims(_norm_lower(unit))
        for weekday, interval in claims.items():
            per_day.setdefault(weekday, set()).add(interval)
    return per_day


class ADV_ProvenReviewShapes(unittest.TestCase):
    """The five shapes that survived R1, each with the false value that must not remain."""

    CASES = (
        ("verb between day and hours", "El sábado trabajamos de 9 a 13 hs.", "de 9 a 13"),
        ("split opening and closing", "Los sábados abrimos a las 9 y cerramos a las 13 hs.",
         "cerramos a las 13"),
        ("a bound, not a range", "Atendemos los sábados hasta las 13 hs.", "hasta las 13"),
        ("colon list form", "Sábados: 9 a 13 hs.", "9 a 13"),
        ("closing only", "El sábado el taller cierra a las 13.", "cierra a las 13"),
    )

    def test_adv_01_the_false_value_never_survives(self):
        for label, prose, false_fragment in self.CASES:
            with self.subTest(shape=label):
                self.assertNotIn(false_fragment, compose(prose))

    def test_adv_02_the_canonical_schedule_replaces_it(self):
        for label, prose, _f in self.CASES:
            with self.subTest(shape=label):
                self.assertIn(CANONICAL, compose(prose))

    def test_adv_03_no_two_schedules_coexist(self):
        """The exact failure mode: a false sentence with the correction beneath it."""
        for label, prose, false_fragment in self.CASES:
            with self.subTest(shape=label):
                final = compose(prose)
                self.assertFalse(false_fragment in final and CANONICAL in final)


class ADV_RequiredCases(unittest.TestCase):

    def test_adv_04_the_live_failure_is_corrected(self):
        final = compose(LIVE_GENERATED)
        self.assertNotIn("9 a 13", final)
        self.assertIn("sábados de 9 a 15 hs", final)

    def test_adv_05_the_non_hours_part_of_the_live_reply_survives(self):
        self.assertIn("¿qué día y horario te viene mejor?", compose(LIVE_GENERATED))

    def test_adv_06_a_correct_saturday_statement_is_not_contradicted(self):
        final = compose("Trabajamos sábados de 9 a 15 hs.")
        self.assertFalse(_hours_truth_violations(_norm_lower(final)))
        for weekday, intervals in stated_intervals(final).items():
            with self.subTest(weekday=weekday):
                self.assertEqual(len(intervals), 1)

    def test_adv_07_a_wrong_opening_time_is_removed(self):
        final = compose("Trabajamos lunes de 9 a 18 hs.")      # Monday opens at 13
        self.assertNotIn("lunes de 9 a 18", final)
        self.assertIn("lunes de 13 a 18 hs", final)

    def test_adv_08_a_wrong_closing_time_is_removed(self):
        final = compose("Trabajamos jueves de 9 a 20 hs.")     # Thursday closes at 14
        self.assertNotIn("9 a 20", final)
        self.assertIn("jueves de 9 a 14 hs", final)

    def test_adv_09_a_sunday_open_assertion_is_removed(self):
        for prose in ("Los domingos abrimos de 9 a 13 hs.", "Los domingos también trabajamos."):
            with self.subTest(prose=prose[:28]):
                final = compose(prose)
                self.assertNotIn("domingos abrimos", final)
                self.assertNotIn("domingos tambien trabajamos", _norm_lower(final))

    def test_adv_10_a_correct_sunday_closed_assertion_is_kept(self):
        final = compose("Los domingos no trabajamos.")
        self.assertIn("Los domingos no trabajamos.", final)

    def test_adv_11_a_reply_with_no_hours_assertion_is_untouched(self):
        primary = "¿Qué día te viene mejor?"
        final = compose(primary)
        self.assertTrue(final.startswith(primary))

    def test_adv_12_multiple_false_sentences_are_all_removed(self):
        final = compose("Los sábados cerramos a las 13 hs. Trabajamos lunes de 9 a 18 hs.")
        self.assertNotIn("cerramos a las 13", final)
        self.assertNotIn("lunes de 9 a 18", final)
        self.assertIn(CANONICAL, final)

    def test_adv_13_a_reply_already_canonical_is_not_duplicated(self):
        primary = "Genial. " + CANONICAL
        final = compose(primary)
        self.assertEqual(final, primary)
        self.assertEqual(final.count(CANONICAL), 1)

    def test_adv_14_a_bulleted_list_is_judged_bullet_by_bullet(self):
        final = compose("Horarios:\n- lunes de 13 a 18 hs\n- sábados de 9 a 13 hs")
        self.assertNotIn("9 a 13 hs", final)
        self.assertIn("Horarios:", final)

    def test_adv_15_a_fully_removed_reply_does_not_start_with_blank_lines(self):
        final = compose("Trabajamos sábados de 9 a 13 hs.")
        self.assertFalse(final.startswith("\n"))
        self.assertTrue(final.startswith("Trabajamos lunes"))


class ADV_AppointmentSlots(unittest.TestCase):

    def test_adv_16_a_real_offered_slot_is_represented_in_the_output(self):
        final = compose("Tengo disponible el sábado de 13:00 a 14:00 hs. ¿Te sirve?",
                        slots=["13:00", "14:00"])
        for slot in ("13:00", "14:00"):
            with self.subTest(slot=slot):
                self.assertIn(slot, final)

    def test_adv_17_a_slot_like_time_absent_from_state_is_not_protected(self):
        final = compose("Tengo disponible el sábado de 13:00 a 14:00 hs.", slots=["09:30"])
        self.assertNotIn("13:00 a 14:00", final)

    def test_adv_18_a_mixed_unit_fails_closed_and_the_offer_is_regenerated(self):
        final = compose("Tengo disponible el sábado 13:00. Los sábados cerramos a las 13 hs.",
                        slots=["13:00"])
        self.assertNotIn("cerramos a las 13", final)
        self.assertIn("13:00", final)

    def test_adv_19_a_protected_unit_is_kept_by_proof_b(self):
        """A slot sentence asserting no hours canonical truth denies survives untouched."""
        protected = _protected_slot_minutes(
            SimpleNamespace(last_visible_slots=json.dumps(["13:30"]), last_offered_slots=None))
        disposition, _carried = _hours_unit_survives("El sábado tengo 13:30.", protected)
        self.assertEqual(disposition, _UNIT_PROTECTED_SLOT)

    def test_adv_20_the_regenerated_offer_comes_from_state_not_from_prose(self):
        self.assertEqual(_regenerated_offer_es((13 * 60, 14 * 60)),
                         "Horarios disponibles: 13:00 o 14:00. ¿Alguno te viene bien?")
        self.assertEqual(_regenerated_offer_es(()), "")


class PROP_Properties(unittest.TestCase):
    """Nine properties that must hold whatever the wording."""

    #: Output shapes, including ones no parser grammar covers. Never production phrases.
    CORPUS = (
        "El sábado trabajamos de 9 a 13 hs.",
        "Los sábados abrimos a las 9 y cerramos a las 13 hs.",
        "Atendemos los sábados hasta las 13 hs.",
        "Sábados: 9 a 13 hs.",
        "El sábado el taller cierra a las 13.",
        "Trabajamos lunes de 9 a 18 hs.",
        "Trabajamos jueves de 9 a 20 hs.",
        "Los domingos abrimos de 9 a 13 hs.",
        "Los domingos también trabajamos.",
        "Trabajamos sábados de 9 a 15 hs.",
        "Los domingos no trabajamos.",
        LIVE_GENERATED,
        "Horarios:\n- lunes de 13 a 18 hs\n- sábados de 9 a 13 hs",
        "Genial. " + CANONICAL,
        "¿Qué día te viene mejor?",
        "Los sábados cerramos a las 13 hs. Trabajamos lunes de 9 a 18 hs.",
        "El lunes de 8 a 19 hs y el martes de 9 a 14 hs.",
        "Abrimos todos los días de 9 a 18 hs.",
    )

    def finals(self, slots=None):
        return [(prose, compose(prose, slots=slots)) for prose in self.CORPUS]

    def test_prop_01_never_two_different_intervals_for_one_weekday(self):
        for prose, final in self.finals():
            for weekday, intervals in stated_intervals(final).items():
                with self.subTest(prose=prose[:34], weekday=weekday):
                    self.assertEqual(len(intervals), 1,
                                     f"conflicting schedules for weekday {weekday}: {intervals}")

    def test_prop_02_no_unproven_hours_unit_survives(self):
        for prose, final in self.finals():
            for unit in hours_units(final):
                disposition, _carried = _hours_unit_survives(unit, frozenset())
                with self.subTest(prose=prose[:34], unit=unit[:40]):
                    self.assertIn(disposition, (_UNIT_VERIFIED_HOURS, _UNIT_PROTECTED_SLOT))

    def test_prop_03_the_canonical_answer_appears_at_most_once(self):
        for prose, final in self.finals():
            with self.subTest(prose=prose[:34]):
                self.assertLessEqual(final.count(CANONICAL), 1)

    def test_prop_04_surviving_facts_come_from_canonical_weekday_hours(self):
        for prose, final in self.finals():
            with self.subTest(prose=prose[:34]):
                self.assertFalse(_hours_truth_violations(_norm_lower(final)))
            for weekday, intervals in stated_intervals(final).items():
                start, end, closed = business_hours_for_weekday(weekday)
                with self.subTest(prose=prose[:34], weekday=weekday):
                    self.assertFalse(closed)
                    self.assertEqual(intervals,
                                     {((start.hour, start.minute), (end.hour, end.minute))})

    def test_prop_05_every_protected_slot_stays_represented(self):
        slots = ["13:00", "14:00"]
        for prose in ("Tengo disponible el sábado de 13:00 a 14:00 hs.",
                      "Te ofrezco el sábado 13:00. Los sábados cerramos a las 14 hs.",
                      "El sábado tengo 13:00 o 14:00."):
            final = compose(prose, slots=slots)
            present = [s for s in slots if s in _norm_lower(prose)]
            for slot in present:
                with self.subTest(prose=prose[:34], slot=slot):
                    self.assertIn(slot, final)

    def test_prop_06_protection_requires_canonical_state_not_resemblance(self):
        prose = "Tengo disponible el sábado de 13:00 a 14:00 hs."
        self.assertIn("13:00", compose(prose, slots=["13:00", "14:00"]))
        self.assertNotIn("13:00 a 14:00", compose(prose, slots=None))
        self.assertNotIn("13:00 a 14:00", compose(prose, slots=["08:00"]))

    def test_prop_07_the_original_live_failure_is_corrected(self):
        final = compose(LIVE_GENERATED)
        self.assertNotIn("sábados de 9 a 13", final)
        self.assertIn("sábados de 9 a 15 hs", final)

    def test_prop_08_sunday_is_never_represented_as_open(self):
        for prose, final in self.finals():
            normalized = _norm_lower(final)
            _claims, _closed, opened = _stated_hours_claims(normalized)
            with self.subTest(prose=prose[:34]):
                self.assertNotIn(SUNDAY, opened)
                self.assertNotIn(SUNDAY, stated_intervals(final))

    def test_prop_09_the_protection_is_scoped_to_the_business_hours_path(self):
        """Unrelated numeric prose is untouched when the topic is not armed, and an
        armed turn does not strip numbers that carry no weekday."""
        unrelated = "Revisamos más de 250 puntos y el auto es un 208 2022."
        self.assertEqual(compose(unrelated, armed=False), unrelated)
        self.assertIn("250 puntos", compose(unrelated))
        self.assertIn("13:30", compose("Tengo estos horarios: 13:30, 14:00 y 14:30."))


class SCOPE_Boundaries(unittest.TestCase):
    """R2 stays inside its milestone."""

    HOURS_DETECTION_AT_B5F1675 = frozenset({
        "horarios", "horario", "en que horarios", "que horarios",
        "cuando laburan", "cuando trabajan", "a que hora", "horario de atencion",
    })

    def source(self) -> str:
        return (BACKEND / "app/services/conversation_engine.py").read_text(encoding="utf-8")

    def test_scope_01_the_customer_phrase_detector_is_unchanged(self):
        self.assertEqual(set(ce._HOURS_FAQ_DETECTION), set(self.HOURS_DETECTION_AT_B5F1675))

    def test_scope_02_no_provider_call_was_added(self):
        # G3-5F §4.3 put both consumers behind ONE provider boundary
        # (`_reply_decision`), so the number of places that can issue a reply
        # request fell from two to one. The guarantee this asserts — that no
        # milestone added a provider call — is unchanged and now stricter: the
        # two consumers are counted separately, so a third would still fail.
        self.assertEqual(len(re.findall(r"self\._call_openai\(", self.source())), 1)
        self.assertEqual(len(re.findall(r"self\._reply_decision\(", self.source())), 2)
        self.assertEqual(len(re.findall(r"self\._ai_invoked = True", self.source())), 1)

    def test_scope_03_the_schedule_remains_the_sole_authority(self):
        """No weekday table is duplicated outside schedule.py, and the guard reads it."""
        engine_source = self.source()
        self.assertNotIn("_WEEKDAY" + "_HOURS", engine_source)
        self.assertIn("business_hours_for_weekday", engine_source)
        self.assertIn("canonical_weekday_hours", engine_source)
        table = "_WEEKDAY" + "_HOURS"        # not spelled out: this file is scanned too
        for path in ((BACKEND / "app").rglob("*.py")):
            if path.name == "schedule.py":
                continue
            with self.subTest(path=path.name):
                self.assertNotIn(table, path.read_text(encoding="utf-8"), path.name)

    def test_scope_04_scheduling_intent_representation_is_untouched(self):
        from app.schemas.turn_evidence import SchedulingRequestEvidence
        self.assertEqual(SchedulingRequestEvidence()._MEANINGFUL_FIELDS,
                         ("day_expression", "resolved_date", "time"))

    def test_scope_05_no_customer_text_entered_production_code(self):
        for path in (BACKEND / "app").rglob("*.py"):
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertNotIn("disponiblees", body)
                self.assertNotIn("Que horarios tienen", body)

    def test_scope_06_no_phrase_grammar_was_added_for_the_review_shapes(self):
        """The fix is the day+hour predicate, not recognition of these wordings.

        Scanned over the functions R2 introduced rather than the whole module: one
        pre-existing comment elsewhere quotes a customer saying "hasta las", and a
        whole-file scan would fail on it while proving nothing about this change.
        """
        import inspect
        added = "\n".join(inspect.getsource(getattr(ce, name)) for name in (
            "_clock_tokens", "_hours_claim_spans", "_hours_unit_is_fully_verified",
            "_protected_slot_minutes", "_hours_unit_survives",
            "_strip_unverified_hours", "_regenerated_offer_es"))
        for phrase in ("cerramos", "hasta", "cierra", "abrimos", "atendemos", "taller"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, added)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
