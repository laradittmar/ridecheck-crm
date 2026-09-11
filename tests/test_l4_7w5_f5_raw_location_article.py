"""L4.7W5-F5 — locality recognition from RAW customer text.

Two live Wilds died on the same sentence shape: "Un 3008 en paternal" and "quiero revisar
un auto en paternal" both produced a location Flow instead of a zone.

L4.7W5-F4 made the CATALOG lookup article-aware and I certified it by calling that lookup
directly. The live path never reaches it: `_extract_zone_from_text` turns raw text into a
zone first, by substring containment, and "la paternal" does not occur in the customer's
sentence. I tested the layer I had changed instead of the behaviour that was reported.

So every test here enters through `_extract_zone_from_text` — the same function WhatsApp
traffic uses — and never through the repository.

The opposite error existed too, and predates this work: "me mordí la boca" resolved to
La Boca, because the canonical name occurs literally in ordinary prose. An article-prefixed
locality is a common noun wearing a proper name, so both directions need the same rule.

LOC-ART-01..10 plus role safety and the pre-existing false positives.
"""
from __future__ import annotations

import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _m in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    sys.modules.setdefault(_m, types.ModuleType(_m))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = _sa.JSON
_pgj.JSONB = _sa.JSON

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import app.models
from app.models import ViaticosZone
from app.services.conversation_engine import (ConversationEngine, _ARTICLE_PREFIX_RE,
                                              _locality_in_context)

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# Catalog shaped like the live one: article localities whose bare form is a common word,
# alongside ordinary proper-noun localities.
CATALOG = [("CABA", "La Paternal", 0), ("CABA", "La Boca", 0), ("CABA", "Palermo", 0),
           ("CABA", "Villa Urquiza", 0), ("CABA", "CABA", 0),
           ("Sur", "La Plata", 90000), ("Sur", "Berazategui", 90000),
           ("Norte", "El Talar", 50000), ("Norte", "Tigre", 50000),
           ("Oeste", "Los Polvorines", 60000)]


class _Raw(unittest.TestCase):
    """Every assertion goes through the raw-text entry point."""

    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        for g, d, v in CATALOG:
            self.db.add(ViaticosZone(zone_group=g, zone_detail=d, viaticos=v))
        self.db.commit()
        self.eng = ConversationEngine.__new__(ConversationEngine)
        self.eng.db = self.db

    def tearDown(self):
        self.db.close()

    def zone(self, text):
        z = self.eng._extract_zone_from_text(text)
        return (z.zone_group, z.zone_detail) if z else None


class TestArticleDroppedLocalities(_Raw):

    def test_loc_art_01_the_first_wild_sentence(self):
        """LOC-ART-01 — "Un 3008 en paternal", verbatim from the audit."""
        self.assertEqual(self.zone("Un 3008 en paternal"), ("CABA", "La Paternal"))

    def test_loc_art_02_the_second_wild_sentence(self):
        """LOC-ART-02 — "quiero revisar un auto en paternal", verbatim."""
        self.assertEqual(self.zone("quiero revisar un auto en paternal"),
                         ("CABA", "La Paternal"))

    def test_loc_art_03_the_canonical_form_still_works(self):
        self.assertEqual(self.zone("el auto está en la paternal"), ("CABA", "La Paternal"))

    def test_loc_art_04_10_non_article_localities_are_unchanged(self):
        """LOC-ART-04/10 — proper-noun localities keep plain containment."""
        for text, expected in (
            ("el auto está en palermo", ("CABA", "Palermo")),
            ("el auto está en villa urquiza", ("CABA", "Villa Urquiza")),
            ("el auto esta en berazategui", ("Sur", "Berazategui")),
            ("estoy en tigre", ("Norte", "Tigre")),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.zone(text), expected)

    def test_loc_art_05_every_article_family(self):
        """LOC-ART-05 — La / El / Los, article dropped, resolved from raw text."""
        for text, expected in (
            ("el auto esta en boca", ("CABA", "La Boca")),
            ("el auto esta en plata", ("Sur", "La Plata")),
            ("esta en talar", ("Norte", "El Talar")),
            ("el auto esta en polvorines", ("Oeste", "Los Polvorines")),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.zone(text), expected)

    def test_a_bare_locality_answer_resolves(self):
        """"¿en qué zona está el auto?" → "paternal" is a location, not prose."""
        self.assertEqual(self.zone("paternal"), ("CABA", "La Paternal"))
        self.assertEqual(self.zone("la paternal"), ("CABA", "La Paternal"))


class TestFalsePositiveSafety(_Raw):

    def test_loc_art_07_la_plata_is_not_money(self):
        """LOC-ART-07 — "plata" is the commonest word for cash in Argentina."""
        for text in ("tengo plata para pagar", "no tengo plata", "quiero pagar",
                     "cuanta plata es?"):
            with self.subTest(text=text):
                self.assertIsNone(self.zone(text))

    def test_loc_art_08_el_talar_is_not_a_verb(self):
        """LOC-ART-08 — "talar" is the verb "to fell"."""
        for text in ("hay que talar un árbol", "van a talar todo"):
            with self.subTest(text=text):
                self.assertIsNone(self.zone(text))

    def test_loc_art_09_la_boca_is_not_a_mouth(self):
        """LOC-ART-09 — these two resolved to La Boca BEFORE this milestone; the canonical
        name occurs literally in ordinary prose."""
        for text in ("me mordí la boca", "la boca del tanque", "abrí la boca"):
            with self.subTest(text=text):
                self.assertIsNone(self.zone(text))

    def test_unrelated_prose_writes_no_locality(self):
        for text in ("el auto está en la puerta", "estoy en casa",
                     "quiero revisar un auto", "hola buenas tardes"):
            with self.subTest(text=text):
                self.assertIsNone(self.zone(text))

    def test_word_boundaries_are_enforced(self):
        """An alias must never fire from inside a longer word."""
        self.assertIsNone(self.zone("el auto está en paternalismo"))
        self.assertIsNone(self.zone("es una plataforma"))


class TestAliasAmbiguity(_Raw):

    def test_loc_art_06_an_ambiguous_alias_resolves_to_neither(self):
        """LOC-ART-06 — two canonical localities reducing to the same bare form must not
        silently pick one. The canonical forms keep working."""
        self.db.add(ViaticosZone(zone_group="Oeste", zone_detail="El Paternal", viaticos=70000))
        self.db.commit()
        self.assertIsNone(self.zone("el auto está en paternal"),
                          "ambiguous alias must not auto-resolve")
        self.assertEqual(self.zone("el auto está en la paternal"), ("CABA", "La Paternal"))
        self.assertEqual(self.zone("el auto está en el paternal"), ("Oeste", "El Paternal"))

    def test_the_alias_index_is_built_from_the_catalog_not_the_customer(self):
        self.assertTrue(_ARTICLE_PREFIX_RE.match("la paternal"))
        self.assertIsNone(_ARTICLE_PREFIX_RE.match("paternal"))
        self.assertTrue(_locality_in_context("el auto esta en paternal", "paternal"))
        self.assertFalse(_locality_in_context("tengo plata para pagar", "plata"))


class TestRoleSafety(_Raw):

    def test_part_6_inspection_location_not_customer_origin(self):
        """The vehicle's location is what matters; both are named in one sentence."""
        self.assertEqual(self.zone("el auto está en paternal pero yo soy de tigre"),
                         ("CABA", "La Paternal"))

    def test_the_reverse_order_still_prefers_the_vehicle_clause(self):
        z = self.zone("yo soy de tigre, el auto está en paternal")
        self.assertIn(z, [("CABA", "La Paternal"), ("Norte", "Tigre")])
        # documented: this function returns ONE zone from free text; the inspection-vs-origin
        # decision is the reconciler's job (RISK-03), not this extractor's. Asserting the
        # stronger claim here would overstate what this layer does.


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


# ─────────────────────────────────────────────────────────────────────────────
# PART 7 — SAME-DAY SCHEDULING (owner business decision, 2026-09-11)
#
# Same-day booking is allowed and is roughly 60% of demand. The previous behaviour started
# NEXT_AVAILABLE at today + 1 and the Flow picker began at delta 1, so today could never be
# offered and "para hoy tenes?" had no path to an answer.
#
# Today is now searched first — but a day already under way does not begin at opening time,
# so ScheduleService seeds from now plus a lead buffer. A slot that has passed, or that
# cannot be reached, is never offered.
# ─────────────────────────────────────────────────────────────────────────────

from datetime import date as _date, datetime as _dt, time as _time, timedelta as _td
from unittest.mock import patch as _patch

from app.schemas.schedule import ScheduleCheckIn as _CheckIn
from app.services.schedule import (SAME_DAY_LEAD_MINUTES, ScheduleService as _Sched,
                                   business_hours_for_weekday as _hours)


class TestSameDayScheduling(unittest.TestCase):
    """Every assertion drives ScheduleService with a frozen local clock."""

    def setUp(self):
        with _engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _Session()
        for g, d, v in CATALOG:
            self.db.add(ViaticosZone(zone_group=g, zone_detail=d, viaticos=v))
        self.db.commit()
        self.svc = _Sched(self.db)
        # A Wednesday: 09:00-18:00, the widest ordinary day.
        self.day = _date(2026, 9, 16)
        self.assertFalse(_hours(self.day.weekday())[2], "fixture day must be open")

    def tearDown(self):
        self.db.close()

    def _at(self, hh, mm=0):
        """Freeze the service's notion of local now on the fixture day."""
        frozen = _dt(self.day.year, self.day.month, self.day.day, hh, mm)
        return _patch.multiple(_Sched,
                               _local_now=staticmethod(lambda: frozen),
                               _local_today=classmethod(lambda cls: frozen.date()))

    def _today_slots(self):
        out = self.svc.list_slots(_CheckIn(
            preferred_day=self.day, preferred_time=_time(9, 0),
            address="Palermo, CABA, Buenos Aires", zone_group="CABA",
            zone_detail="Palermo", is_holiday=False))
        return out.slots or []

    def test_today_01_10_a_morning_request_gets_remaining_today_slots(self):
        """TODAY-01/10 — early in an open day, today has real capacity and is offered,
        using the frozen local clock rather than the server's."""
        with self._at(9, 0):
            slots = self._today_slots()
        self.assertTrue(slots, "an open day at 09:00 must still have capacity")

    def test_today_03_a_slot_that_has_already_passed_is_never_offered(self):
        """TODAY-03 — the safety property. Seeding from opening time would offer 09:00 at
        four in the afternoon."""
        def earliest_allowed(hh, mm=0):
            return (_dt(2026, 9, 16, hh, mm)
                    + _td(minutes=SAME_DAY_LEAD_MINUTES)).strftime("%H:%M")

        with self._at(16, 0):
            late = self._today_slots()
        for s in late:
            self.assertGreaterEqual(s, earliest_allowed(16), f"{s} is in the past")

        with self._at(9, 0):
            early = self._today_slots()
        for s in early:
            self.assertGreaterEqual(s, earliest_allowed(9), f"{s} precedes the lead buffer")

        # asking earlier genuinely opens up more of the day
        self.assertGreater(len(early), len(late))

    def test_the_lead_buffer_keeps_the_first_offer_reachable(self):
        """A slot starting in five minutes is not a real offer."""
        with self._at(11, 0):
            slots = self._today_slots()
        if slots:
            earliest_allowed = (_dt(2026, 9, 16, 11, 0)
                                + _td(minutes=SAME_DAY_LEAD_MINUTES)).strftime("%H:%M")
            self.assertGreaterEqual(slots[0], earliest_allowed)

    def test_today_02_a_spent_day_yields_nothing_and_rolls_forward(self):
        """TODAY-02 — late in the day today is empty, and the search continues rather than
        dead-ending."""
        with self._at(17, 45):
            self.assertEqual(self._today_slots(), [])
            found = self.svc.find_next_available(
                zone_group="CABA", zone_detail="Palermo", start_day=self.day)
        self.assertIsNotNone(found)
        self.assertGreater(found.day, self.day, "an exhausted today must roll to a later day")

    def test_today_05_06_the_search_begins_at_today_not_tomorrow(self):
        """TODAY-05/06 — "lo antes posible" and delegated choice must consider today.

        Asserted two ways: the service returns today when today has room, and CE seeds the
        search with date.today() rather than today + 1.
        """
        with self._at(9, 0):
            found = self.svc.find_next_available(
                zone_group="CABA", zone_detail="Palermo", start_day=self.day)
        self.assertEqual(found.day, self.day, "today must win when today has capacity")
        self.assertEqual(found.days_checked, 1)

        ce = (ROOT / "backend" / "app" / "services"
              / "conversation_engine.py").read_text(encoding="utf-8-sig")
        self.assertIn("start = date.today()", ce)
        self.assertNotIn("start = date.today() + timedelta(days=1)", ce)

    def test_today_04_travel_feasibility_still_governs_same_day(self):
        """TODAY-04 — urgency changes the ordering objective, never the rules. Same-day
        slots come from the same travel-validated path as any other day."""
        import inspect
        src = inspect.getsource(_Sched._suggest_slots)
        self.assertIn("_is_travel_valid_slot", src)
        self.assertIn("jornada_start=effective_start", src,
                      "travel must be measured from the effective start, not opening time")

    def test_the_flow_picker_can_offer_today(self):
        """TODAY-07 — the Flow's own date list began at delta 1, so today was unreachable
        even when the conversation had agreed on it."""
        bfs = (ROOT / "backend" / "app" / "services"
               / "booking_flow_service.py").read_text(encoding="utf-8-sig")
        self.assertIn("for delta in range(0, BOOKING_HORIZON_DAYS + 1):", bfs)

    def test_today_09_no_capacity_still_reaches_the_human_rescue(self):
        """TODAY-09 — when nothing is bookable the escalation path is still the exit."""
        ce = (ROOT / "backend" / "app" / "services"
              / "conversation_engine.py").read_text(encoding="utf-8-sig")
        import ast as _ast
        fn = next(_ast.unparse(n) for n in _ast.walk(_ast.parse(ce))
                  if isinstance(n, _ast.FunctionDef)
                  and n.name == "_handle_next_available_request")
        self.assertIn("_handle_scheduling_escalation", fn)
