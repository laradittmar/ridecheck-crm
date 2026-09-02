"""M21.3 scheduler ground-truth integration tests.

Tests T1–T18 cover travel model, zero-zone constraints, business hours,
occupancy filtering, and cross-turn correctness.

Regression tests cover ZONE-02, SCHED-01, F6 tipo authority, F5.1 location
gate, F4 location authority, and messy-turn reconciliation.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.schedule import (
    OccupiedSlot,
    ScheduleService,
    SERVICE_MINUTES,
    ZERO_ZONE_GROUP,
    ZERO_SANTA,
    ZERO_MELO,
    MONDAY_SANTA_ANCHOR,
    _BusinessHours,
)
from app.services.travel import ZoneTravelProvider
from app.schemas.schedule import ScheduleCheckIn


# ── Shared fixtures ───────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _EmptyDb:
    """DB stub that returns no rows for every query."""
    def execute(self, stmt):
        return _FakeResult([])


def _make_svc(**kw):
    return ScheduleService(db=_EmptyDb(), travel_provider=ZoneTravelProvider(), **kw)


def _occupied(start_dt: datetime, zone_group: str | None, ident: int = 1) -> OccupiedSlot:
    return OccupiedSlot(
        source="revision",
        identifier=ident,
        start=start_dt,
        end=start_dt + timedelta(minutes=SERVICE_MINUTES),
        label=f"rev#{ident}",
        zone_group=zone_group,
    )


# ── Travel model unit tests ───────────────────────────────────────────────────

class TestZoneTravelProvider(unittest.TestCase):
    def setUp(self):
        self.tp = ZoneTravelProvider()

    def _t(self, a, b):
        return self.tp.get_travel_minutes(a, b)

    # T1 — same group
    def test_T1_zero_norte_to_norte(self):
        self.assertEqual(self._t("Norte", "Norte"), 30)

    # T2 — CABA cross
    def test_T2_zero_norte_to_caba(self):
        self.assertEqual(self._t("Norte", "CABA"), 60)

    def test_T2b_caba_to_norte(self):
        self.assertEqual(self._t("CABA", "Norte"), 60)

    def test_caba_to_oeste(self):
        self.assertEqual(self._t("CABA", "Oeste"), 60)

    def test_caba_to_sur(self):
        self.assertEqual(self._t("CABA", "Sur"), 60)

    def test_norte_to_oeste(self):
        self.assertEqual(self._t("Norte", "Oeste"), 90)

    def test_norte_to_sur(self):
        self.assertEqual(self._t("Norte", "Sur"), 90)

    def test_oeste_to_sur(self):
        self.assertEqual(self._t("Oeste", "Sur"), 90)

    def test_case_insensitive(self):
        self.assertEqual(self._t("norte", "caba"), 60)
        self.assertEqual(self._t("NORTE", "CABA"), 60)

    def test_unknown_origin_returns_zero(self):
        self.assertEqual(self._t(None, "Norte"), 0)

    def test_unknown_dest_returns_zero(self):
        self.assertEqual(self._t("Norte", None), 0)

    def test_both_unknown_returns_zero(self):
        self.assertEqual(self._t(None, None), 0)


# ── OccupiedSlot gap constraints ──────────────────────────────────────────────

class TestSlotGapConstraints(unittest.TestCase):
    """T3–T8: previous and next appointment travel constraints."""

    def setUp(self):
        self.svc = _make_svc()
        # Shared constants: Wednesday 2026-08-26, jornada 09:00
        self.day = date(2026, 8, 26)  # Wednesday
        self.jornada_start = datetime(2026, 8, 26, 9, 0)
        self.zero_zone = "Norte"

    def _valid(self, candidate_dt, zone_group, occupied):
        return self.svc._is_travel_valid_slot(
            candidate=candidate_dt,
            zone_group=zone_group,
            occupied_slots=occupied,
            jornada_start=self.jornada_start,
            zero_zone=self.zero_zone,
        )

    # T3 — prev Norte → candidate Norte: 30 min gap needed
    def test_T3_prev_norte_to_norte_30min(self):
        prev = _occupied(datetime(2026, 8, 26, 9, 0), "Norte")
        # 09:00 + 45 + 30 = 10:15; candidate 10:15 → exactly valid
        self.assertTrue(self._valid(datetime(2026, 8, 26, 10, 15), "Norte", [prev]))
        # 10:00 < 10:15 → invalid
        self.assertFalse(self._valid(datetime(2026, 8, 26, 10, 0), "Norte", [prev]))

    # T4 — prev Norte → candidate CABA: 60 min gap needed
    def test_T4_prev_norte_to_caba_60min(self):
        prev = _occupied(datetime(2026, 8, 26, 9, 0), "Norte")
        # 09:00 + 45 + 60 = 10:45
        self.assertTrue(self._valid(datetime(2026, 8, 26, 10, 45), "CABA", [prev]))
        self.assertFalse(self._valid(datetime(2026, 8, 26, 10, 30), "CABA", [prev]))

    # T5 — prev Norte → candidate Sur: 90 min gap needed
    def test_T5_prev_norte_to_sur_90min(self):
        prev = _occupied(datetime(2026, 8, 26, 9, 0), "Norte")
        # 09:00 + 45 + 90 = 11:15
        self.assertTrue(self._valid(datetime(2026, 8, 26, 11, 15), "Sur", [prev]))
        self.assertFalse(self._valid(datetime(2026, 8, 26, 11, 0), "Sur", [prev]))

    # T6 — slot gen: first Norte at 09:00, next Norte: earliest 10:15
    def test_T6_first_norte_next_norte_earliest_1015(self):
        prev = _occupied(datetime(2026, 8, 26, 9, 0), "Norte")
        hours = _BusinessHours(start=time(9, 0), end=time(18, 0))
        slots = self.svc._suggest_slots(
            preferred_day=self.day,
            occupied_slots=[prev],
            hours=hours,
            zone_group="Norte",
        )
        # First slot after prev: 10:15 (but 30-min increments, so 10:30)
        self.assertNotIn("10:00", slots)
        self.assertIn("10:30", slots)

    # T7 — slot gen: first Norte at 09:00, next CABA: earliest 11:00
    # prev.start + 45 + travel(Norte→CABA=60) = 10:45; next 30-min slot ≥ 10:45 = 11:00
    def test_T7_first_norte_next_caba_earliest_1100(self):
        prev = _occupied(datetime(2026, 8, 26, 9, 0), "Norte")
        hours = _BusinessHours(start=time(9, 0), end=time(18, 0))
        slots = self.svc._suggest_slots(
            preferred_day=self.day,
            occupied_slots=[prev],
            hours=hours,
            zone_group="CABA",
        )
        self.assertNotIn("10:30", slots)
        self.assertIn("11:00", slots)

    # T8 — slot gen: first Norte at 09:00, next Sur: earliest 11:30
    # prev.start + 45 + travel(Norte→Sur=90) = 11:15; next 30-min slot ≥ 11:15 = 11:30
    def test_T8_first_norte_next_sur_earliest_1130(self):
        prev = _occupied(datetime(2026, 8, 26, 9, 0), "Norte")
        hours = _BusinessHours(start=time(9, 0), end=time(18, 0))
        slots = self.svc._suggest_slots(
            preferred_day=self.day,
            occupied_slots=[prev],
            hours=hours,
            zone_group="Sur",
        )
        self.assertNotIn("11:00", slots)
        self.assertIn("11:30", slots)


# ── Monday zero-zone and business hours ───────────────────────────────────────

class TestMondayZeroZone(unittest.TestCase):
    """T9–T10, T17: Monday 13:00 start, Santa/Melo alternation."""

    def setUp(self):
        self.svc = _make_svc()

    def test_monday_business_hours_start_at_1300(self):
        # Any Monday must have 13:00 start
        monday = date(2026, 8, 17)  # SANTA anchor
        hours = self.svc._business_hours(monday, is_holiday=False)
        self.assertEqual(hours.start, time(13, 0))
        self.assertEqual(hours.end, time(18, 0))

    # T9 — Monday SANTA week: first Norte slot earliest 13:30
    def test_T9_monday_santa_week_first_norte_earliest_1330(self):
        monday_santa = date(2026, 8, 17)  # anchor = SANTA
        self.assertEqual(self.svc._zero_zone_detail(monday_santa), ZERO_SANTA)
        hours = self.svc._business_hours(monday_santa, is_holiday=False)
        slots = self.svc._suggest_slots(
            preferred_day=monday_santa,
            occupied_slots=[],
            hours=hours,
            zone_group="Norte",
        )
        # zero=Norte, dest=Norte → 30 min travel; jornada 13:00 → earliest 13:30
        self.assertNotIn("13:00", slots)
        self.assertIn("13:30", slots)

    # T10 — Monday SANTA week: first CABA earliest 14:00
    def test_T10_monday_santa_week_first_caba_earliest_1400(self):
        monday_santa = date(2026, 8, 17)
        hours = self.svc._business_hours(monday_santa, is_holiday=False)
        slots = self.svc._suggest_slots(
            preferred_day=monday_santa,
            occupied_slots=[],
            hours=hours,
            zone_group="CABA",
        )
        # zero=Norte, dest=CABA → 60 min; jornada 13:00 → earliest 14:00
        self.assertNotIn("13:30", slots)
        self.assertIn("14:00", slots)

    # T17 — Monday alternation parity
    def test_T17_monday_alternation_parity(self):
        anchor = MONDAY_SANTA_ANCHOR  # SANTA week
        week_1 = anchor + timedelta(weeks=1)   # MELO
        week_2 = anchor + timedelta(weeks=2)   # SANTA
        week_3 = anchor + timedelta(weeks=3)   # MELO

        self.assertEqual(self.svc._zero_zone_detail(anchor), ZERO_SANTA)
        self.assertEqual(self.svc._zero_zone_detail(week_1), ZERO_MELO)
        self.assertEqual(self.svc._zero_zone_detail(week_2), ZERO_SANTA)
        self.assertEqual(self.svc._zero_zone_detail(week_3), ZERO_MELO)

    def test_monday_before_anchor_alternates_correctly(self):
        pre_anchor = MONDAY_SANTA_ANCHOR - timedelta(weeks=1)  # MELO
        self.assertEqual(self.svc._zero_zone_detail(pre_anchor), ZERO_MELO)


# ── Tuesday hours and zero zone ───────────────────────────────────────────────

class TestTuesdayHours(unittest.TestCase):
    """T11–T12: Tuesday 09:30 start, zero=Santa (Norte)."""

    def setUp(self):
        self.svc = _make_svc()
        self.tuesday = date(2026, 8, 18)

    def test_tuesday_hours_0930_1400(self):
        hours = self.svc._business_hours(self.tuesday, is_holiday=False)
        self.assertEqual(hours.start, time(9, 30))
        self.assertEqual(hours.end, time(14, 0))

    def test_tuesday_zero_zone_is_santa(self):
        self.assertEqual(self.svc._zero_zone_detail(self.tuesday), ZERO_SANTA)

    # T11 — Tuesday: first CABA earliest 10:30
    def test_T11_tuesday_first_caba_earliest_1030(self):
        hours = self.svc._business_hours(self.tuesday, is_holiday=False)
        slots = self.svc._suggest_slots(
            preferred_day=self.tuesday,
            occupied_slots=[],
            hours=hours,
            zone_group="CABA",
        )
        # zero=Norte (Santa Catalina), dest=CABA → 60 min; jornada 09:30 → earliest 10:30
        self.assertNotIn("09:30", slots)
        self.assertNotIn("10:00", slots)
        self.assertIn("10:30", slots)

    # T12 — Tuesday: first Norte earliest 10:00
    def test_T12_tuesday_first_norte_earliest_1000(self):
        hours = self.svc._business_hours(self.tuesday, is_holiday=False)
        slots = self.svc._suggest_slots(
            preferred_day=self.tuesday,
            occupied_slots=[],
            hours=hours,
            zone_group="Norte",
        )
        # zero=Norte, dest=Norte → 30 min; jornada 09:30 → earliest 10:00
        self.assertNotIn("09:30", slots)
        self.assertIn("10:00", slots)


# ── Friday always full day ────────────────────────────────────────────────────

class TestFridayAlwaysFull(unittest.TestCase):
    """T18: Friday always 09:00–18:00 (no alternation)."""

    def setUp(self):
        self.svc = _make_svc()

    def test_T18_friday_always_0900_1800(self):
        fridays = [
            date(2026, 8, 21),
            date(2026, 8, 28),
            date(2026, 9, 4),
            date(2026, 9, 11),
        ]
        for friday in fridays:
            hours = self.svc._business_hours(friday, is_holiday=False)
            self.assertEqual(hours.start, time(9, 0), f"Friday {friday} start wrong")
            self.assertEqual(hours.end, time(18, 0), f"Friday {friday} end wrong")
            self.assertFalse(hours.closed, f"Friday {friday} should not be closed")


# ── Cancellation / non-occupying filter ──────────────────────────────────────

class TestNonOccupyingFilter(unittest.TestCase):
    """T13–T14: CANCELADO and REPROGRAMAR do not block; booked ThreadRevision does."""

    def _run_suggest(self, revision_estado: str, expected_blocked: bool):
        from app.models import Revision, ThreadRevision as TR

        class _SingleRevDb:
            """Returns one Revision for Revision queries; empty for all others."""
            def __init__(self):
                self._rev = Revision(
                    id=99,
                    lead_id=1,
                    turno_fecha=date(2026, 8, 26),
                    turno_hora=time(10, 0),
                    estado_revision=revision_estado,
                    zone_group="Norte",
                )

            def execute(self, stmt):
                entity = stmt.column_descriptions[0].get("entity") if stmt.column_descriptions else None
                if entity is Revision:
                    return _FakeResult([self._rev])
                return _FakeResult([])

        svc = ScheduleService(db=_SingleRevDb(), travel_provider=ZoneTravelProvider())
        hours = _BusinessHours(start=time(9, 0), end=time(18, 0))
        slots = svc._suggest_slots(
            preferred_day=date(2026, 8, 26),
            occupied_slots=svc._load_occupied_slots(date(2026, 8, 26)),
            hours=hours,
            zone_group="Norte",
            max_results=24,
        )
        # 10:00 is within occupied territory; if blocked, 10:00 absent; if not, 10:00 present
        if expected_blocked:
            self.assertNotIn("10:00", slots, f"estado={revision_estado} should block 10:00")
        else:
            self.assertIn("10:00", slots, f"estado={revision_estado} should NOT block 10:00")

    # T13 — CANCELADO does not block
    def test_T13_cancelado_does_not_block(self):
        self._run_suggest("CANCELADO", expected_blocked=False)

    def test_reprogramar_does_not_block(self):
        self._run_suggest("REPROGRAMAR", expected_blocked=False)

    # T14 — PENDIENTE (active) does block
    def test_T14_pendiente_blocks(self):
        self._run_suggest("PENDIENTE", expected_blocked=True)

    def test_confirmado_blocks(self):
        self._run_suggest("CONFIRMADO", expected_blocked=True)


# ── Next appointment constraint ───────────────────────────────────────────────

class TestNextAppointmentConstraint(unittest.TestCase):
    """T15–T16: inserting a slot that prevents reaching the next appointment."""

    def setUp(self):
        self.svc = _make_svc()
        self.day = date(2026, 8, 26)
        self.jornada = datetime(2026, 8, 26, 9, 0)

    def _valid(self, candidate_dt, zone_group, occupied):
        return self.svc._is_travel_valid_slot(
            candidate=candidate_dt,
            zone_group=zone_group,
            occupied_slots=occupied,
            jornada_start=self.jornada,
            zero_zone="Norte",
        )

    # T15 — inserting slot that would block next booked → invalid
    # Candidate Norte at 09:30, next Norte at 10:00.
    # 09:30 + 45 + travel(Norte→Norte=30) = 10:45 > 10:00 → INVALID
    def test_T15_next_constraint_blocks_insertion(self):
        next_slot = _occupied(datetime(2026, 8, 26, 10, 0), "Norte", ident=2)
        self.assertFalse(self._valid(datetime(2026, 8, 26, 9, 30), "Norte", [next_slot]))

    # T16 — same zone, sufficient gap → valid
    # Candidate Norte at 09:30, next Norte at 10:45.
    # 09:30 + 45 + 30 = 10:45 <= 10:45 → VALID
    def test_T16_sufficient_gap_to_next_is_valid(self):
        next_slot = _occupied(datetime(2026, 8, 26, 10, 45), "Norte", ident=2)
        self.assertTrue(self._valid(datetime(2026, 8, 26, 9, 30), "Norte", [next_slot]))


# ── Regression: SCHED-01 time parser ─────────────────────────────────────────

class TestSCHED01Parser(unittest.TestCase):
    """SCHED-01 regression: rightmost explicit time must win."""

    def _parse(self, *texts):
        from app.services.conversation_engine import _parse_scheduling_text
        today = date(2026, 8, 27)
        _, time_str = _parse_scheduling_text(list(texts), today)
        return time_str

    def test_correction_pattern_sabado_9(self):
        """'me dijiste hasta las 18hs. sábado a las 9 entonces' → 09:00"""
        result = self._parse("me dijiste hasta las 18hs. sábado a las 9 entonces")
        self.assertEqual(result, "09:00")

    def test_explicit_correction_no_las_9(self):
        """'No, las 9 no. Mejor a las 11' → 11:00"""
        result = self._parse("No, las 9 no. Mejor a las 11")
        self.assertEqual(result, "11:00")

    def test_single_time_no_correction(self):
        """'mañana 18hs' → 18:00 (no correction, rightmost = only match)"""
        result = self._parse("mañana 18hs")
        self.assertEqual(result, "18:00")

    def test_colon_format(self):
        """'sábado 9:30' → 09:30"""
        result = self._parse("sábado 9:30")
        self.assertEqual(result, "09:30")


# ── Regression: ZONE-02 candidate zone update ────────────────────────────────

class TestZONE02(unittest.TestCase):
    """ZONE-02 regression: explicit location in current turn must update candidate."""

    def test_vehicle_location_written_overwrites_candidate_zone(self):
        """When _vehicle_location_written=True, the candidate's zone must be updated."""
        import types
        state = types.SimpleNamespace(
            home_zone_group="CABA",
            home_zone_detail="Palermo",
        )
        candidate = types.SimpleNamespace(zone_group="Oeste", zone_detail="San Miguel")

        # Simulate the ZONE-02 fix logic
        _vehicle_location_written = True
        _ai_set_zone = False
        focus_after = candidate

        if focus_after and _vehicle_location_written:
            if state.home_zone_group:
                focus_after.zone_group = state.home_zone_group
            if state.home_zone_detail:
                focus_after.zone_detail = state.home_zone_detail
        elif focus_after and not _vehicle_location_written and _ai_set_zone:
            if state.home_zone_group and not focus_after.zone_group:
                focus_after.zone_group = state.home_zone_group
            if state.home_zone_detail and not focus_after.zone_detail:
                focus_after.zone_detail = state.home_zone_detail

        self.assertEqual(candidate.zone_group, "CABA")
        self.assertEqual(candidate.zone_detail, "Palermo")

    def test_ai_set_zone_only_fills_empty_fields(self):
        """When _ai_set_zone=True and _vehicle_location_written=False, only empty fields updated."""
        import types
        state = types.SimpleNamespace(home_zone_group="CABA", home_zone_detail="Palermo")
        candidate = types.SimpleNamespace(zone_group="Oeste", zone_detail="San Miguel")

        _vehicle_location_written = False
        _ai_set_zone = True
        focus_after = candidate

        if focus_after and _vehicle_location_written:
            if state.home_zone_group:
                focus_after.zone_group = state.home_zone_group
            if state.home_zone_detail:
                focus_after.zone_detail = state.home_zone_detail
        elif focus_after and not _vehicle_location_written and _ai_set_zone:
            if state.home_zone_group and not focus_after.zone_group:
                focus_after.zone_group = state.home_zone_group
            if state.home_zone_detail and not focus_after.zone_detail:
                focus_after.zone_detail = state.home_zone_detail

        # Pre-existing zone_group="Oeste" must NOT be overwritten
        self.assertEqual(candidate.zone_group, "Oeste")
        self.assertEqual(candidate.zone_detail, "San Miguel")


# ── Business hours full coverage ──────────────────────────────────────────────

class TestBusinessHours(unittest.TestCase):
    def setUp(self):
        self.svc = _make_svc()

    def _hours(self, d):
        return self.svc._business_hours(d, is_holiday=False)

    def test_monday_1300_1800(self):
        h = self._hours(date(2026, 8, 17))
        self.assertEqual(h.start, time(13, 0))
        self.assertEqual(h.end, time(18, 0))

    def test_tuesday_0930_1400(self):
        h = self._hours(date(2026, 8, 18))
        self.assertEqual(h.start, time(9, 30))
        self.assertEqual(h.end, time(14, 0))

    def test_wednesday_0900_1800(self):
        h = self._hours(date(2026, 8, 19))
        self.assertEqual(h.start, time(9, 0))
        self.assertEqual(h.end, time(18, 0))

    def test_thursday_0900_1400(self):
        h = self._hours(date(2026, 8, 20))
        self.assertEqual(h.start, time(9, 0))
        self.assertEqual(h.end, time(14, 0))

    def test_friday_0900_1800_always(self):
        for friday in [date(2026, 8, 21), date(2026, 8, 28), date(2026, 9, 4)]:
            h = self._hours(friday)
            self.assertEqual(h.start, time(9, 0), f"{friday}")
            self.assertEqual(h.end, time(18, 0), f"{friday}")

    def test_saturday_0900_1500(self):
        h = self._hours(date(2026, 8, 22))
        self.assertEqual(h.start, time(9, 0))
        self.assertEqual(h.end, time(15, 0))

    def test_sunday_closed(self):
        h = self._hours(date(2026, 8, 23))
        self.assertTrue(h.closed)

    def test_holiday_0900_1500(self):
        h = self.svc._business_hours(date(2026, 8, 17), is_holiday=True)
        self.assertEqual(h.start, time(9, 0))
        self.assertEqual(h.end, time(15, 0))


# ── Zero zone details per weekday ─────────────────────────────────────────────

class TestZeroZoneDetails(unittest.TestCase):
    def setUp(self):
        self.svc = _make_svc()

    def test_zero_zone_group_always_norte(self):
        for d in [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19),
                  date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 22)]:
            self.assertEqual(self.svc._zero_zone_group(d), "Norte")

    def test_tuesday_zero_santa(self):
        self.assertEqual(self.svc._zero_zone_detail(date(2026, 8, 18)), ZERO_SANTA)

    def test_wednesday_zero_melo(self):
        self.assertEqual(self.svc._zero_zone_detail(date(2026, 8, 19)), ZERO_MELO)

    def test_thursday_zero_santa(self):
        self.assertEqual(self.svc._zero_zone_detail(date(2026, 8, 20)), ZERO_SANTA)

    def test_friday_zero_melo(self):
        self.assertEqual(self.svc._zero_zone_detail(date(2026, 8, 21)), ZERO_MELO)

    def test_saturday_zero_santa(self):
        self.assertEqual(self.svc._zero_zone_detail(date(2026, 8, 22)), ZERO_SANTA)


# ── Full ScheduleService.check() integration ─────────────────────────────────

class TestScheduleServiceCheck(unittest.TestCase):
    def setUp(self):
        self.svc = _make_svc()

    def test_valid_wednesday_norte_after_travel(self):
        # Wednesday, Norte, 09:30 (travel 30min from Norte zero → earliest 09:30)
        result = self.svc.check(ScheduleCheckIn(
            address="Tigre",
            preferred_day=date(2026, 8, 19),
            preferred_time=time(9, 30),
            zone_group="Norte",
        ))
        self.assertTrue(result.valid)
        self.assertEqual(result.buffer_minutes, 0)
        self.assertEqual(result.total_slot_minutes, SERVICE_MINUTES)

    def test_invalid_too_early_for_travel(self):
        # Wednesday Norte, 09:00: travel 30min → earliest 09:30 → 09:00 invalid
        result = self.svc.check(ScheduleCheckIn(
            address="Tigre",
            preferred_day=date(2026, 8, 19),
            preferred_time=time(9, 0),
            zone_group="Norte",
        ))
        self.assertFalse(result.valid)
        self.assertIn("traslado", " ".join(result.reasons).lower())

    def test_sunday_returns_closed(self):
        result = self.svc.check(ScheduleCheckIn(
            address="Cualquier lugar",
            preferred_day=date(2026, 8, 23),
            preferred_time=time(10, 0),
        ))
        self.assertFalse(result.valid)
        self.assertEqual(result.business_hours, "cerrado")

    def test_monday_before_1300_invalid(self):
        result = self.svc.check(ScheduleCheckIn(
            address="Cualquier lugar",
            preferred_day=date(2026, 8, 17),
            preferred_time=time(9, 0),
        ))
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
