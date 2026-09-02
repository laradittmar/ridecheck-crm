"""M21.3-UX3 — AGENDA CONTACT ACTIONS + REVISION/APPOINTMENT LINKAGE

UX3-01  Agenda WA action points to real Inbox route (/whatsapp/thread/)
UX3-02  Agenda WA action resolves correct Thread for Lead/Revision
UX3-03  No WhatsApp Thread → WA action safely absent
UX3-04  Agenda WA navigation does NOT create/send message
UX3-05  Llamar uses canonical customer phone (Lead.telefono)
UX3-06  Llamar renders tel: URI
UX3-07  Missing customer phone handled safely (no Llamar action)
UX3-08  Seller phone cannot silently replace customer phone
UX3-09  Agenda → Revision link opens correct Revision (/kanban?open_lead=&open_rev=)
UX3-10  Revision with scheduled appointment renders "Ver en agenda"
UX3-11  Revision → Agenda opens correct appointment date (?date=YYYY-MM-DD)
UX3-12  highlight_lead_id present in Revision → Agenda link
UX3-13  No hard-coded thread/lead/revision IDs
UX3-14  UX2 Maps/GPS regression passes
UX3-15  UX1 newest-message Inbox behavior preserved (WA button goes to thread, not raw inbox)
UX3-16  Mobile action layout: actions wrap, no overflow
"""
from __future__ import annotations

import sys
import types
from datetime import date, time, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON
_pg_json.JSONB = _sa.JSON

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

import unittest
from app.ui.kanban_view import render_calendar_page, render_page

_WED = date(2026, 8, 26)
_MON = _WED - timedelta(days=2)


def _rev(
    *,
    id: int = 1,
    lead_id: int = 1,
    turno_fecha: date = _WED,
    turno_hora: time | None = time(10, 0),
    zone_group: str | None = "Norte",
    zone_detail: str | None = "Santa Catalina",
    direccion_texto: str | None = "Av. Maipú 1234",
    estado_revision: str = "PENDIENTE",
    pago: bool | None = None,
    cobrado: str | None = None,
    tipo_vehiculo: str | None = "SEDAN",
    marca: str | None = "Toyota",
    modelo: str | None = "Corolla",
    anio: int | None = 2020,
    profesional_id: int | None = None,
    link_maps: str | None = None,
    appointment_approval_status: str | None = None,
) -> types.SimpleNamespace:
    from datetime import datetime
    return types.SimpleNamespace(
        id=id, lead_id=lead_id, turno_fecha=turno_fecha, turno_hora=turno_hora,
        zone_group=zone_group, zone_detail=zone_detail,
        direccion_texto=direccion_texto, estado_revision=estado_revision,
        pago=pago, cobrado=cobrado, tipo_vehiculo=tipo_vehiculo,
        marca=marca, modelo=modelo, anio=anio,
        profesional_id=profesional_id, link_maps=link_maps,
        appointment_approval_status=appointment_approval_status,
        # fields referenced by render_page / render_lead_card
        created_at=datetime(2026, 8, 1),
        precio_base=None, viaticos=None, precio_total=None,
        medio_pago=None, tipo_vendedor=None, vendedor_tipo=None,
        agencia_id=None, agencia=None, link_compra=None,
        presupuesto_compra=None, compro=None, comision=None,
        fecha_cobro=None, approval_tag=None,
        cliente_presente=None, turno_notas=None,
        resultado=None, motivo_rechazo=None, resultado_link=None,
    )


def _lead(
    *,
    id: int = 1,
    nombre: str = "Juan",
    apellido: str = "Pérez",
    telefono: str | None = "+5491122334455",
    estado: str = "AGENDADO",
    flag: str | None = None,
    revisions: list | None = None,
) -> types.SimpleNamespace:
    revs = revisions or []
    return types.SimpleNamespace(
        id=id, nombre=nombre, apellido=apellido,
        telefono=telefono, estado=estado, flag=flag,
        revisions=revs,
        # fields referenced by render_page
        email=None, canal=None, necesita_humano=False,
        created_at=None, motivo_perdida=None, feedback=None,
    )


class _FakeSched:
    def get_day_start_info(self, day: date) -> dict:
        from app.services.schedule import ScheduleService
        svc = ScheduleService.__new__(ScheduleService)
        return svc.get_day_start_info(day)


def _render_cal(leads=None, thread_by_lead=None):
    svc = _FakeSched()
    return render_calendar_page(
        leads or [],
        week=_MON.isoformat(),
        initial_date=_WED.isoformat(),
        schedule_svc=svc,
        thread_by_lead=thread_by_lead or {},
    )


class TestUX3WAButton(unittest.TestCase):
    """UX3-01 to UX3-04 — Agenda WA navigation."""

    def _html_with_thread(self):
        r = _rev(id=1)
        l = _lead(id=1, revisions=[r])
        return _render_cal(leads=[l], thread_by_lead={1: 42})

    def _html_no_thread(self):
        r = _rev(id=1)
        l = _lead(id=1, revisions=[r])
        return _render_cal(leads=[l], thread_by_lead={})

    # UX3-01 — WA action points to /whatsapp/thread/ (real route)
    def test_ux3_01_wa_route_correct(self):
        html = self._html_with_thread()
        self.assertIn("/whatsapp/thread/42", html)
        self.assertNotIn("/integrations/whatsapp/inbox", html)

    # UX3-02 — WA action contains the resolved thread_id (42)
    def test_ux3_02_wa_correct_thread_id(self):
        html = self._html_with_thread()
        self.assertIn('href="/whatsapp/thread/42"', html)

    # UX3-03 — No thread → WA button element absent (CSS class def OK)
    def test_ux3_03_no_thread_wa_absent(self):
        html = self._html_no_thread()
        self.assertNotIn('<a class="agendaActionBtn agendaWaBtn"', html)
        self.assertNotIn("/whatsapp/thread/", html)

    # UX3-04 — WA button is a navigation link, not a form/fetch/POST
    def test_ux3_04_wa_is_navigation_no_send(self):
        html = self._html_with_thread()
        # Must be an <a> tag, not a form POST
        self.assertIn('<a class="agendaActionBtn agendaWaBtn"', html)
        # No send action triggered server-side
        self.assertNotIn('method="post"', html.lower().split("agendaWaBtn")[0].split(
            '<a class="agendaActionBtn agendaWaBtn"')[-1][:200])


class TestUX3Llamar(unittest.TestCase):
    """UX3-05 to UX3-08 — Llamar action."""

    def _html_with_phone(self, phone="+5491122334455"):
        r = _rev(id=1)
        l = _lead(id=1, telefono=phone, revisions=[r])
        return _render_cal(leads=[l])

    def _html_no_phone(self):
        r = _rev(id=1)
        l = _lead(id=1, telefono=None, revisions=[r])
        return _render_cal(leads=[l])

    # UX3-05 — Llamar uses Lead.telefono (canonical customer phone)
    def test_ux3_05_llamar_uses_lead_telefono(self):
        html = self._html_with_phone("+5491122334455")
        self.assertIn("+5491122334455", html)
        self.assertIn("agendaCallBtn", html)

    # UX3-06 — Llamar renders tel: URI
    def test_ux3_06_llamar_tel_uri(self):
        html = self._html_with_phone("+5491122334455")
        self.assertIn('href="tel:', html)
        self.assertIn("tel:+5491122334455", html)

    # UX3-07 — Missing phone: no Llamar button element rendered (CSS class def OK)
    def test_ux3_07_missing_phone_no_llamar(self):
        html = self._html_no_phone()
        self.assertNotIn('<a class="agendaActionBtn agendaCallBtn"', html)

    # UX3-08 — Seller phone (agencia.telefono) does NOT appear as customer Llamar
    def test_ux3_08_seller_phone_not_in_llamar(self):
        # Revision has agencia with a phone; lead has a different phone
        r = _rev(id=1)
        agencia = types.SimpleNamespace(nombre_agencia="AutoX", telefono="+54911VENDEDOR")
        r.agencia = agencia
        l = _lead(id=1, telefono="+54911CLIENTE", revisions=[r])
        html = _render_cal(leads=[l])
        # Only customer phone appears in tel: URI
        self.assertIn("tel:+54911CLIENTE", html)
        # Seller phone must NOT appear in a tel: href
        self.assertNotIn("tel:+54911VENDEDOR", html)


class TestUX3AgendaRevisionLink(unittest.TestCase):
    """UX3-09 — Agenda → Revision link."""

    # UX3-09 — Agenda card "Ver revisión" links to /kanban?open_lead=&open_rev=
    def test_ux3_09_agenda_revision_link(self):
        r = _rev(id=7)
        l = _lead(id=3, revisions=[r])
        html = _render_cal(leads=[l])
        self.assertIn("/kanban?open_lead=3&open_rev=7", html)
        self.assertIn("Ver revisión", html)


class TestUX3RevisionAgendaLink(unittest.TestCase):
    """UX3-10 to UX3-12 — Revision → Agenda."""

    def _render_kanban(self, lead, profesionales=None):
        return render_page(
            [lead],
            profesionales=profesionales or [],
            user_email="test@test.com",
        )

    # UX3-10 — Revision with turno_fecha renders "Ver en agenda"
    def test_ux3_10_revision_ver_en_agenda(self):
        r = _rev(id=1, turno_fecha=_WED, appointment_approval_status="APPROVED")
        l = _lead(id=1, revisions=[r])
        html = self._render_kanban(l)
        self.assertIn("Ver en agenda", html)

    # UX3-11 — Revision → Agenda link contains appointment date
    def test_ux3_11_revision_agenda_correct_date(self):
        r = _rev(id=1, turno_fecha=_WED, appointment_approval_status="APPROVED")
        l = _lead(id=1, revisions=[r])
        html = self._render_kanban(l)
        self.assertIn(f"date={_WED.isoformat()}", html)

    # UX3-12 — highlight_lead_id present in Revision → Agenda link
    def test_ux3_12_revision_agenda_highlight_lead(self):
        r = _rev(id=1, turno_fecha=_WED, appointment_approval_status="APPROVED")
        l = _lead(id=5, revisions=[r])
        html = self._render_kanban(l)
        self.assertIn("highlight_lead_id=5", html)
        self.assertIn(f"date={_WED.isoformat()}", html)


class TestUX3Invariants(unittest.TestCase):
    """UX3-13 to UX3-16 — Cross-cutting invariants."""

    # UX3-13 — No hard-coded IDs in source (WA, lead, revision IDs from domain)
    def test_ux3_13_no_hardcoded_ids(self):
        r1 = _rev(id=11)
        l1 = _lead(id=7, revisions=[r1])
        html = _render_cal(leads=[l1], thread_by_lead={7: 99})
        self.assertIn("/whatsapp/thread/99", html)
        self.assertIn("/kanban?open_lead=7&open_rev=11", html)
        self.assertNotIn("/whatsapp/thread/1", html)
        self.assertNotIn("open_lead=1", html)

    # UX3-14 — UX2 Maps/GPS regression: Maps link + agendaGpsDropdown still present
    def test_ux3_14_ux2_maps_gps_regression(self):
        r = _rev(id=1, direccion_texto="Florida 800", zone_group="CABA")
        l = _lead(id=1, revisions=[r])
        html = _render_cal(leads=[l])
        self.assertIn("google.com/maps/search", html)
        self.assertIn("waze.com", html)
        self.assertIn("agendaGpsDropdown", html)

    # UX3-15 — Agenda WA button goes to /whatsapp/thread/ not broken /integrations route
    def test_ux3_15_wa_navigates_to_thread_not_inbox(self):
        r = _rev(id=1)
        l = _lead(id=1, revisions=[r])
        html = _render_cal(leads=[l], thread_by_lead={1: 77})
        self.assertIn("/whatsapp/thread/77", html)
        # The broken old route must not appear as a WA action button
        self.assertNotIn("/integrations/whatsapp/inbox?thread_id=", html)

    # UX3-16 — Mobile: agendaApptActions has flex-wrap in rendered calendar HTML
    def test_ux3_16_mobile_actions_wrap(self):
        html = _render_cal(leads=[])
        self.assertIn("agendaApptActions", html)
        self.assertIn("flex-wrap", html)


if __name__ == "__main__":
    unittest.main()
