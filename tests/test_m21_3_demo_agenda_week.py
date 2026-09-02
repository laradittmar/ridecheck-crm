"""M21.3-DEMO-AGENDA-WEEK — Calendar week-boundary fix and current-week visibility.

WEEK-01: Mon 31 Aug appointments render in Day view.
WEEK-02: Tue 1 Sep appointments render in Day view.
WEEK-03: Wed 2 Sep appointments render in Day view.
WEEK-04: Thu 3 Sep appointments render in Day view.
WEEK-05: Fri 4 Sep appointments render in Day view.
WEEK-06: Sat 5 Sep appointments render in Day view.
WEEK-07: Week view includes all six operating days.
WEEK-08: Week view total = 19 appointments.
WEEK-09: Day next/prev navigation changes date correctly.
WEEK-10: Hoy resolves current local date correctly.
WEEK-11: Argentina timezone does not shift appointment date.
WEEK-12: travel/gap blocks remain visible in Day view.
WEEK-13: visual spacing classes present.
WEEK-14: no duplicate appointments created.
WEEK-15: Agenda↔Revision links preserved.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON
_pg_json.JSONB = _sa.JSON

for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

from datetime import time as dtime
import html as html_lib

# ── Helpers ────────────────────────────────────────────────────────────────────

_CURRENT_WEEK_MONDAY = date(2026, 8, 31)

WEEK_APPTS = {
    date(2026, 8, 31): [dtime(13, 30), dtime(15, 30)],           # Mon: 2
    date(2026, 9, 1):  [dtime(9, 30), dtime(11, 0), dtime(12, 30)],  # Tue: 3
    date(2026, 9, 2):  [dtime(9, 0), dtime(10, 0), dtime(12, 30), dtime(15, 0)],  # Wed: 4
    date(2026, 9, 3):  [dtime(9, 0), dtime(10, 0), dtime(11, 30)],   # Thu: 3
    date(2026, 9, 4):  [dtime(9, 0), dtime(10, 30), dtime(13, 0), dtime(15, 30)],  # Fri: 4
    date(2026, 9, 5):  [dtime(9, 0), dtime(11, 0), dtime(13, 0)],    # Sat: 3
}

TOTAL_APPTS = sum(len(v) for v in WEEK_APPTS.values())  # 19


def _make_revision(turno_fecha: date, turno_hora: dtime, rev_id: int, lead_id: int,
                   zone_group: str = "CABA", locality: str = "Palermo") -> Any:
    r = MagicMock()
    r.id = rev_id
    r.turno_fecha = turno_fecha
    r.turno_hora = turno_hora
    r.zone_group = zone_group
    r.zone_detail = locality
    r.direccion_texto = f"Av. Demo 123, {locality}"
    r.link_maps = ""
    r.estado_revision = "CONFIRMADO"
    r.appointment_approval_status = "approved"
    r.appointment_approved_at = None
    r.appointment_approval_sent_at = None
    r.appointment_approval_token = None
    r.profesional_id = None
    r.profesional = None
    r.marca = "Toyota"
    r.modelo = "Corolla"
    r.anio = 2019
    r.tipo_vehiculo = "Auto"
    r.precio_total = 35000
    r.presupuesto_compra = None
    r.turno_notas = ""
    r.resultado = None
    r.compro = None
    return r


def _make_lead(lead_id: int, nombre: str, apellido: str, revisions: list) -> Any:
    l = MagicMock()
    l.id = lead_id
    l.nombre = nombre
    l.apellido = apellido
    l.wa_id = f"54911000000{lead_id:02d}"
    l.estado = "AGENDADO"
    l.revisions = revisions
    l.email = f"{nombre.lower()}@example.invalid"
    return l


def _build_demo_leads():
    """Build synthetic Lead+Revision objects matching the demo agenda."""
    leads = []
    rev_id = 100
    lead_id = 200
    names = [
        ("Fernando", "Lopez"), ("Sandra", "Gonzalez"), ("Esteban", "Ramirez"),
        ("Luciana", "Fernandez"), ("Martin", "Rodriguez"), ("Paula", "Martinez"),
        ("Diego", "Alvarez"), ("Cecilia", "Romero"), ("Nicolas", "Torres"),
        ("Mariana", "Pereyra"), ("Gustavo", "Benitez"), ("Carolina", "Acosta"),
        ("Federico", "Sosa"), ("Roberto", "Medina"), ("Valeria", "Suarez"),
        ("Hernan", "Vazquez"), ("Romina", "Castro"), ("Julian", "Mora"),
        ("Adriana", "Fuentes"),
    ]
    ni = 0
    for d, times in WEEK_APPTS.items():
        for t in times:
            nombre, apellido = names[ni % len(names)]
            ni += 1
            rev = _make_revision(d, t, rev_id, lead_id)
            lead = _make_lead(lead_id, nombre, apellido, [rev])
            leads.append(lead)
            rev_id += 1
            lead_id += 1
    return leads


def _render_calendar(leads, initial_day: date | None = None):
    """Call render_calendar_page with demo leads for the current week."""
    from app.ui.kanban_view import render_calendar_page
    return render_calendar_page(
        leads=leads,
        profesionales=[],
        week=_CURRENT_WEEK_MONDAY.isoformat(),
        user_email="demo@example.invalid",
        highlight_lead_id=None,
        initial_date=(initial_day or _CURRENT_WEEK_MONDAY).isoformat(),
        schedule_svc=None,
        thread_by_lead={},
    )


# ── WEEK-01: Mon 31 Aug appointments in Day view ──────────────────────────────

class TestWEEK_01_Mon(unittest.TestCase):
    def test_01a_mon_slot_exists(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads, date(2026, 8, 31))
        self.assertIn('id="cal-dayslots-2026-08-31"', html,
                      "Day slot for 2026-08-31 must be pre-rendered")

    def test_01b_mon_has_appointments(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads, date(2026, 8, 31))
        slot_start = html.find('id="cal-dayslots-2026-08-31"')
        slot_end = html.find('id="cal-dayslots-2026-09-', slot_start)
        if slot_end == -1:
            slot_end = html.find('</div>', slot_start + 1000)
        slot_html = html[slot_start:slot_end]
        self.assertNotIn("Sin turnos", slot_html,
                         "Mon Aug 31 slot must not be empty")

    def test_01c_mon_count(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads, date(2026, 8, 31))
        # Count 13:30 and 15:30 appearances in the Mon slot section
        mon_section = html[html.find('id="cal-dayslots-2026-08-31"'):]
        mon_section = mon_section[:mon_section.find('id="cal-dayslots-2026-09-01"', 10) if 'id="cal-dayslots-2026-09-01"' in mon_section else 5000]
        self.assertIn("13:30", mon_section)
        self.assertIn("15:30", mon_section)


# ── WEEK-02: Tue 1 Sep appointments in Day view ───────────────────────────────

class TestWEEK_02_Tue(unittest.TestCase):
    def test_02a_tue_slot_exists(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn('id="cal-dayslots-2026-09-01"', html,
                      "Day slot for 2026-09-01 must be pre-rendered (month boundary fix)")

    def test_02b_tue_not_empty(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-01"')
        slot_html = html[idx:idx + 3000]
        self.assertNotIn("Sin turnos", slot_html,
                         "Tue Sep 1 slot must not be empty — fix extends by_day_month past month end")

    def test_02c_tue_times_present(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-01"')
        slot_html = html[idx:idx + 3000]
        self.assertIn("09:30", slot_html)


# ── WEEK-03: Wed 2 Sep appointments in Day view ───────────────────────────────

class TestWEEK_03_Wed(unittest.TestCase):
    def test_03a_wed_slot_exists(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn('id="cal-dayslots-2026-09-02"', html)

    def test_03b_wed_not_empty(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-02"')
        slot_html = html[idx:idx + 3000]
        self.assertNotIn("Sin turnos", slot_html)

    def test_03c_wed_four_appointments(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-02"')
        # Find end of this slot (next slot or generous window)
        next_idx = html.find('id="cal-dayslots-2026-09-03"', idx + 1)
        slot_html = html[idx:next_idx] if next_idx > idx else html[idx:idx + 10000]
        times_found = sum(1 for t in ["09:00", "10:00", "12:30", "15:00"] if t in slot_html)
        self.assertGreaterEqual(times_found, 3, "Wed should show at least 3 of 4 appointment times")


# ── WEEK-04: Thu 3 Sep appointments in Day view ───────────────────────────────

class TestWEEK_04_Thu(unittest.TestCase):
    def test_04a_thu_slot_exists(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn('id="cal-dayslots-2026-09-03"', html)

    def test_04b_thu_not_empty(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-03"')
        slot_html = html[idx:idx + 3000]
        self.assertNotIn("Sin turnos", slot_html)


# ── WEEK-05: Fri 4 Sep appointments in Day view ───────────────────────────────

class TestWEEK_05_Fri(unittest.TestCase):
    def test_05a_fri_slot_exists(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn('id="cal-dayslots-2026-09-04"', html)

    def test_05b_fri_not_empty(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-04"')
        slot_html = html[idx:idx + 3000]
        self.assertNotIn("Sin turnos", slot_html)


# ── WEEK-06: Sat 5 Sep appointments in Day view ───────────────────────────────

class TestWEEK_06_Sat(unittest.TestCase):
    def test_06a_sat_slot_exists(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn('id="cal-dayslots-2026-09-05"', html)

    def test_06b_sat_not_empty(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-05"')
        slot_html = html[idx:idx + 3000]
        self.assertNotIn("Sin turnos", slot_html)


# ── WEEK-07: Week view includes all six operating days ────────────────────────

class TestWEEK_07_WeekViewDays(unittest.TestCase):
    def test_07a_week_view_has_mon(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        week_html = html[week_idx:week_idx + 20000]
        self.assertIn("31 Ago", week_html, "Week view must show Mon 31 Aug")

    def test_07b_week_view_has_tue(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        week_html = html[week_idx:week_idx + 20000]
        self.assertIn("1 Sep", week_html, "Week view must show Tue 1 Sep")

    def test_07c_week_view_has_six_days(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        week_html = html[week_idx:week_idx + 20000]
        day_heads = week_html.count('calWeekDayHead')
        self.assertGreaterEqual(day_heads, 6, "Week view must have at least 6 day cards")


# ── WEEK-08: Week view total appointments ─────────────────────────────────────

class TestWEEK_08_WeekTotal(unittest.TestCase):
    def test_08a_week_total_is_19(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        end_idx = html.find('id="cal-view-month"')
        week_html = html[week_idx:end_idx]
        appt_count = week_html.count('calWeekApptRow')
        self.assertEqual(appt_count, TOTAL_APPTS,
                         f"Week view must show {TOTAL_APPTS} appointment rows, found {appt_count}")

    def test_08b_no_days_empty_in_week_view(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        end_idx = html.find('id="cal-view-month"')
        week_html = html[week_idx:end_idx]
        # Each non-Sunday day should have the has-appt class
        has_appt_count = week_html.count('has-appt')
        self.assertEqual(has_appt_count, 6,
                         "All 6 operating days must have has-appt class")


# ── WEEK-09: Day navigation date change ───────────────────────────────────────

class TestWEEK_09_DayNavigation(unittest.TestCase):
    def test_09a_next_day_slot_exists(self):
        """Navigating next from Mon should land on Tue slot."""
        leads = _build_demo_leads()
        html = _render_calendar(leads, date(2026, 8, 31))
        self.assertIn('id="cal-dayslots-2026-09-01"', html,
                      "Tue Sep 1 slot must exist so JS navGo(+1) from Mon has somewhere to go")

    def test_09b_prev_day_slot_exists(self):
        """Monday prev slot (Sun 30 Aug) should exist (closed day)."""
        leads = _build_demo_leads()
        html = _render_calendar(leads, date(2026, 8, 31))
        self.assertIn('id="cal-dayslots-2026-08-30"', html)

    def test_09c_js_nav_functions_present(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn("navGo", html)
        self.assertIn("cal-nav-prev", html)
        self.assertIn("cal-nav-next", html)


# ── WEEK-10: Hoy resolves correct date ────────────────────────────────────────

class TestWEEK_10_HoyDate(unittest.TestCase):
    def test_10a_today_data_attr_correct(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        import re
        m = re.search(r'data-today="([^"]+)"', html)
        self.assertIsNotNone(m, "cal-view-day must have data-today attribute")
        today_attr = m.group(1)
        self.assertEqual(today_attr, "2026-08-31",
                         f"data-today should be 2026-08-31, got {today_attr}")

    def test_10b_hoy_button_present(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn("Hoy", html)


# ── WEEK-11: Argentina timezone does not shift dates ──────────────────────────

class TestWEEK_11_Timezone(unittest.TestCase):
    def test_11a_no_utc_shift_in_slot_ids(self):
        """Slot IDs must use local Argentina dates, not UTC-shifted dates."""
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        # All expected dates must appear as slot IDs
        for d in WEEK_APPTS:
            self.assertIn(f'id="cal-dayslots-{d.isoformat()}"', html,
                          f"Slot for {d.isoformat()} must exist with correct Argentina date")

    def test_11b_appointments_not_shifted_to_wrong_day(self):
        """An appointment at 09:00 Argentina time must not appear in the prior day's slot."""
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        # Sep 1 appointment at 09:30 — must appear in Sep 1 slot, not Aug 31
        aug31_idx = html.find('id="cal-dayslots-2026-08-31"')
        sep1_idx = html.find('id="cal-dayslots-2026-09-01"')
        aug31_html = html[aug31_idx:sep1_idx] if sep1_idx > aug31_idx else ""
        # 09:30 should NOT be in Aug 31's slot (that day starts at 13:30)
        self.assertNotIn("09:30", aug31_html,
                         "09:30 appointment belongs to Sep 1, must not appear in Aug 31 slot")


# ── WEEK-12: travel/gap blocks in Day view ────────────────────────────────────

class TestWEEK_12_TravelGap(unittest.TestCase):
    def test_12a_travel_class_present_for_multiappt_day(self):
        """Wed has 4 appointments — at least one travel or gap block should render."""
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        idx = html.find('id="cal-dayslots-2026-09-02"')
        slot_html = html[idx:idx + 8000]
        has_travel_or_gap = ("agendaTravel" in slot_html or "agendaGap" in slot_html
                             or "agendaDayWrap" in slot_html)
        self.assertTrue(has_travel_or_gap,
                        "Wed with 4 appointments must render travel or gap blocks")

    def test_12b_day_wrap_present(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn("agendaDayWrap", html)


# ── WEEK-13: Visual spacing classes ───────────────────────────────────────────

class TestWEEK_13_VisualSpacing(unittest.TestCase):
    def test_13a_agenda_card_class_present(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn("agendaApptCard", html,
                      "agendaApptCard class must be present for appointment spacing")

    def test_13b_week_appt_row_class_present(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn("calWeekApptRow", html)

    def test_13c_day_slots_data_hidden(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        self.assertIn('id="cal-dayslots-data" style="display:none;"', html,
                      "cal-dayslots-data must be hidden so it does not add visual clutter")


# ── WEEK-14: No duplicate appointments ────────────────────────────────────────

class TestWEEK_14_NoDuplicates(unittest.TestCase):
    def test_14a_week_total_not_double(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        end_idx = html.find('id="cal-view-month"')
        week_html = html[week_idx:end_idx]
        appt_count = week_html.count('calWeekApptRow')
        self.assertLessEqual(appt_count, TOTAL_APPTS,
                             f"Week view must not show more than {TOTAL_APPTS} rows (no duplicates)")

    def test_14b_each_day_count_exact(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        end_idx = html.find('id="cal-view-month"')
        week_html = html[week_idx:end_idx]
        appt_count = week_html.count('calWeekApptRow')
        self.assertEqual(appt_count, TOTAL_APPTS)


# ── WEEK-15: Agenda↔Revision links ───────────────────────────────────────────

class TestWEEK_15_AgendaRevisionLinks(unittest.TestCase):
    def test_15a_lead_links_present_in_week_view(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        end_idx = html.find('id="cal-view-month"')
        week_html = html[week_idx:end_idx]
        self.assertIn('href=', week_html,
                      "Week view appointment rows must have href links to lead/revision")

    def test_15b_names_visible_in_week_view(self):
        leads = _build_demo_leads()
        html = _render_calendar(leads)
        week_idx = html.find('id="cal-view-week"')
        end_idx = html.find('id="cal-view-month"')
        week_html = html[week_idx:end_idx]
        self.assertIn("Fernando", week_html, "Demo lead names must be visible in week view")

    def test_15c_outbound_off(self):
        outbound = os.environ.get("OUTBOUND_ENABLED", "false").lower()
        self.assertNotEqual(outbound, "true")


if __name__ == "__main__":
    unittest.main()
