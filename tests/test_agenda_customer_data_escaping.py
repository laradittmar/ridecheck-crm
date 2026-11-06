"""PRELAUNCH-MINIMUM-LAUNCH — agenda customer-data escaping (finding F-1).

A customer's name and inspection address come from the Booking Flow. The week, month and slot
cards of the operator Agenda interpolated them with `_txt`, which trims but does not escape, so
stored markup reached the operator's page. The day card already escaped.

Escaping belongs to the HTML sink, once. Nothing is stored escaped, and `_aname`, `_aveh` and
`_aprof` stay plain text because `data-search` and the day card escape them themselves.

The proofs parse the page and do not search it for strings:

  * the parser decodes entities exactly once, so a value that reads back equal to the original
    was escaped exactly once: unescaped would have become markup, double would show `&lt;`;
  * a hostile page must have the same elements and attribute names as a benign page, in the
    same order. An injected tag or event handler cannot pass that, whatever it is spelled like.

AE-01..09 rendered surfaces · AE-10..14 exactly once and Spanish text · AE-15..18 sink inventory
and modal · AE-19..22 route, storage and lead card · AE-23..26 address / professional line
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import textwrap
import types
import unittest
from datetime import date, time
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (str(BACKEND), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)
for _m in ("resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"):
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = sqlalchemy.JSON
_pgj.JSONB = sqlalchemy.JSON

from fastapi.testclient import TestClient            # noqa: E402
from sqlalchemy import create_engine, event, text    # noqa: E402
from sqlalchemy.orm import sessionmaker              # noqa: E402

import app.models                                    # noqa: E402
import app.main                                      # noqa: E402,F401
from app.models import Lead, Profesional, Revision   # noqa: E402
from app.ui import kanban_view                       # noqa: E402
from app.ui.kanban_view import (                     # noqa: E402
    _friendly_tipo_vehiculo, render_calendar_page, render_lead_card,
)

CONFIGURED = {"AUTH_SECRET_KEY": "test-signing-key-for-the-agenda-escaping-suite",
              "ADMIN_PASSWORD": "a-configured-admin-password"}
OPERATOR = "operator@ridecheck.local"

# A fixed Wednesday: the Agenda closes on Sunday, so `date.today()` would hide the day card one
# day in seven.
DAY = date(2026, 11, 4)
WEEK = "2026-11-02"
REV_ID = 901
LEAD_ID = 77

PAYLOADS = {
    "tag": "<b>negrita</b>",
    "img_onerror": "<img src=x onerror=alert(1)>",
    "script": "<script>alert(2)</script>",
    "svg_onload": "<svg/onload=alert(3)>",
    "double_quote_breakout": '" onmouseover="alert(4)" x="',
    "single_quote_breakout": "' onfocus='alert(5)' autofocus='",
    "double_quotes": 'Taller "El Rápido"',
    "single_quote": "O'Higgins",
    "ampersand": "Pérez & Hijos",
    "text_that_looks_like_an_entity": "a &lt;b&gt; &amp; c &#60;",
    "close_the_card": "</div></a></span><script>alert(6)</script>",
    "close_a_comment": "--><img src=x onerror=alert(7)><!--",
    "close_a_textarea": "</textarea><script>alert(8)</script>",
    "javascript_url": "javascript:alert(9)",
}
BENIGN = "texto"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}


# ── the page as a parse tree ──────────────────────────────────────────────────

class Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs = tag, dict(attrs)
        self.children: list = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


class _Tree(HTMLParser):
    """`convert_charrefs` decodes text and attribute values once, as a browser does."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", [])
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse(markup: str) -> Node:
    tree = _Tree()
    tree.feed(markup)
    tree.close()
    return tree.root


def shape(root: Node) -> list[tuple]:
    return [(n.tag, tuple(sorted(n.attrs))) for n in root.walk()]


def handlers(root: Node) -> list[tuple]:
    return [(n.tag, k, v) for n in root.walk() for k, v in sorted(n.attrs.items())
            if k.startswith("on")]


def by_id(root: Node, value: str) -> Node:
    return next(n for n in root.walk() if n.attrs.get("id") == value)


def by_class(root: Node, cls: str) -> list[Node]:
    return [n for n in root.walk() if cls in n.classes()]


# ── the world under test ──────────────────────────────────────────────────────

def _values(p: str) -> dict:
    """One distinct prefix per field, so a value cannot pass on another field's behalf."""
    return dict(nombre=f"N{p}", apellido=f"A{p}", telefono=f"11{p}", marca=f"M{p}",
                modelo=f"O{p}", tipo_vehiculo=f"T{p}", zone_group=f"G{p}", zone_detail=f"D{p}",
                direccion_texto=f"C{p}", estado_revision=f"E{p}", prof_nombre=f"P{p}",
                prof_apellido=f"Q{p}", cargo=f"R{p}")


def _world(p: str, **over):
    v = _values(p)
    rev = NS(id=REV_ID, turno_fecha=DAY, turno_hora=time(10, 0),
             precio_total=over.get("precio_total", 130000), precio_base=120000, viaticos=10000,
             cobrado=over.get("cobrado"), fecha_cobro=over.get("fecha_cobro"),
             pago=over.get("pago"), estado_revision=over.get("estado", v["estado_revision"]),
             marca=v["marca"], modelo=v["modelo"], anio=2020, tipo_vehiculo=v["tipo_vehiculo"],
             zone_group=v["zone_group"], zone_detail=v["zone_detail"],
             direccion_texto=over.get("direccion_texto", v["direccion_texto"]),
             link_maps=over.get("link_maps"), profesional_id=over.get("profesional_id", 5),
             lead_id=LEAD_ID,
             resultado=None, cliente_presente=None, turno_notas=None)
    lead = NS(id=LEAD_ID, nombre=v["nombre"], apellido=v["apellido"], telefono=v["telefono"],
              revisions=[rev], estado="CONSULTA_NUEVA", flag="ACEPTADO", canal=None, email=None)
    prof = NS(id=5, nombre=v["prof_nombre"], apellido=v["prof_apellido"], cargo=v["cargo"])
    return lead, prof


def _page(p: str, **over) -> str:
    lead, prof = _world(p, **over)
    return render_calendar_page([lead], profesionales=[prof], week=WEEK, initial_date=DAY.isoformat(),
                                thread_by_lead={LEAD_ID: 321},
                                schedule_svc=over.get("schedule_svc"))


def _expected(p: str) -> dict:
    v = _values(p)
    tipo = _friendly_tipo_vehiculo(v["tipo_vehiculo"])
    return dict(
        name=f"{v['nombre']} {v['apellido']}",
        vehicle=f"{tipo} / {v['marca']} / {v['modelo']} / 2020",
        zone=f"{v['zone_detail']} · {v['zone_group']}",
        address=v["direccion_texto"],
        prof=f"{v['prof_nombre']} {v['prof_apellido']} ({v['cargo']})",
        estado=v["estado_revision"],
        phone=v["telefono"],
    )


def _panels(root: Node) -> dict:
    return {
        "day": by_id(root, "cal-view-day"),
        "dayslot": by_id(root, f"cal-dayslots-{DAY.isoformat()}"),
    }


class HostileStructure(unittest.TestCase):
    """A hostile value may change text. It may not change what the page is made of."""

    def _same_structure(self, **over):
        benign = parse(_page(BENIGN, **over))
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                hostile = parse(_page(payload, **over))
                self.assertEqual(shape(hostile), shape(benign))
                self.assertEqual(handlers(hostile), handlers(benign))

    def test_ae01_payable_card(self):
        self._same_structure()

    def test_ae02_paid_card(self):
        self._same_structure(cobrado="SI", fecha_cobro=DAY)

    def test_ae03_legacy_payment_card(self):
        self._same_structure(pago=True)

    def test_ae04_card_without_a_quote(self):
        self._same_structure(precio_total=None)

    def test_ae05_cancelled_card(self):
        self._same_structure(estado="CANCELADO")

    def test_ae06_card_without_an_address(self):
        self._same_structure(direccion_texto=None)

    def test_ae07_no_script_body_carries_customer_data(self):
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                root = parse(_page(payload))
                for node in root.walk():
                    if node.tag == "script":
                        self.assertNotIn(f"N{payload}", node.text())
                        self.assertNotIn(f"C{payload}", node.text())

    def test_ae08_a_stored_map_link_cannot_become_a_script_url(self):
        plain = parse(_page(BENIGN))
        for link in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>x</script>",
                     '" onclick="alert(1)'):
            with self.subTest(link=link):
                root = parse(_page(BENIGN, link_maps=link))
                self.assertEqual(shape(root), shape(plain))
                self.assertEqual(handlers(root), handlers(plain))
                card = by_class(by_id(root, "cal-view-week"), "calWeekApptRow")[0]
                self.assertIn(link, card.text())
        anchored = parse(_page(BENIGN, link_maps="https://ex.invalid/ok"))
        root = parse(_page(BENIGN, link_maps='https://ex.invalid/"><script>alert(1)</script>'))
        self.assertEqual(shape(root), shape(anchored))

    def test_ae09_day_start_values_are_escaped_too(self):
        class Hostile:
            def __init__(self, p): self.p = p

            def get_day_start_info(self, _day):
                return {"is_closed": False, "business_hours_str": self.p, "start_time": self.p,
                        "end_time": "18:00", "zero_zone_group": self.p, "zero_zone_detail": self.p}

        benign = parse(_page(BENIGN, schedule_svc=Hostile(BENIGN)))
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                root = parse(_page(BENIGN, schedule_svc=Hostile(payload)))
                self.assertEqual(shape(root), shape(benign))
                day = by_id(root, "cal-view-day")
                self.assertEqual(by_class(day, "agendaStartTime")[0].text(), payload)
                self.assertEqual(by_class(day, "agendaStartDetail")[0].text(), payload)
                self.assertEqual(by_class(day, "agendaDayHours")[0].text(), payload)


class ExactlyOnce(unittest.TestCase):
    """Decoded once, every sink reads back as the original: never markup, never `&lt;`."""

    def test_ae10_day_card_and_its_prerendered_copy(self):
        for name, payload in PAYLOADS.items():
            want = _expected(payload)
            for panel, node in _panels(parse(_page(payload))).items():
                with self.subTest(payload=name, panel=panel):
                    self.assertEqual(by_class(node, "agendaApptName")[0].text(), want["name"])
                    self.assertEqual(by_class(node, "agendaApptVehicle")[0].text(), want["vehicle"])
                    self.assertEqual(by_class(node, "agendaApptZone")[0].text(), want["zone"])
                    self.assertEqual(by_class(node, "agendaApptStatus")[0].text(), want["estado"])
                    self.assertEqual(by_class(node, "agendaAddrLink")[0].text().split(" ", 1)[1],
                                     want["address"])
                    self.assertEqual(by_class(node, "agendaCallBtn")[0].attrs["href"],
                                     f"tel:{want['phone']}")

    def test_ae11_week_card_and_its_search_index(self):
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                want = _expected(payload)
                row = by_class(by_id(parse(_page(payload)), "cal-view-week"), "calWeekApptRow")[0]
                self.assertEqual(by_class(row, "calWeekApptName")[0].text(), want["name"])
                meta = [n.text() for n in by_class(row, "calWeekApptMeta")]
                self.assertEqual(meta, [want["vehicle"],
                                        f"{want['address']} \xa0·\xa0 {want['prof']}",
                                        want["estado"]])
                self.assertEqual(row.attrs["data-search"],
                                 " ".join([want["name"], want["vehicle"], want["address"],
                                           want["prof"], want["estado"]]))

    def test_ae12_month_card(self):
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                want = _expected(payload)
                holder = by_id(parse(_page(payload)), f"cal-mday-{DAY.isoformat()}")
                card = by_class(holder, "calMonthApptCard")[0]
                title = by_class(card, "calMonthApptName")[0]
                self.assertEqual(title.children[0], want["name"] + " ")
                meta = [n.text() for n in by_class(card, "calMonthApptMeta")]
                self.assertEqual(meta, [want["vehicle"],
                                        f"{want['address']} \xa0·\xa0 {want['prof']}",
                                        want["estado"]])

    def test_ae13_modal_data_attributes_and_navigation_urls(self):
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                want = _expected(payload)
                day = by_id(parse(_page(payload)), "cal-view-day")
                button = by_class(day, "agendaCobrarBtn")[0]
                self.assertEqual(button.attrs["data-rev-name"], want["name"])
                self.assertEqual(button.attrs["data-rev-veh"], want["vehicle"])
                self.assertEqual(button.attrs["aria-label"], f"Cobrar $ 130.000 — {want['name']}")
                menu = by_class(day, "agendaGpsDropdown")[0]
                maps, waze = [n.attrs["href"] for n in menu.walk() if n.tag == "a"]
                self.assertEqual(parse_qs(urlsplit(maps).query)["query"],
                                 [f"{want['address']}, {want['zone']}"])
                self.assertEqual(parse_qs(urlsplit(waze).query)["q"], [want["address"]])

    def test_ae14_spanish_text_is_written_literally(self):
        lead, prof = _world(BENIGN)
        lead.nombre, lead.apellido = "José María", "Ñandú O'Higgins"
        rev = lead.revisions[0]
        rev.direccion_texto = "Av. Córdoba 1234, Peña & Cía"
        rev.marca, rev.modelo = "Citroën", "C3 Aircross"
        rev.zone_group, rev.zone_detail = "Núñez", "Güemes"
        rev.estado_revision = "REVISIÓN"
        prof.nombre, prof.apellido, prof.cargo = "Inés", "Ibáñez", "Técnica"
        markup = render_calendar_page([lead], profesionales=[prof], week=WEEK,
                                      initial_date=DAY.isoformat())
        root = parse(markup)
        row = by_class(by_id(root, "cal-view-week"), "calWeekApptRow")[0]
        self.assertEqual(by_class(row, "calWeekApptName")[0].text(), "José María Ñandú O'Higgins")
        self.assertIn("Av. Córdoba 1234, Peña & Cía", row.text())
        self.assertIn("Inés Ibáñez (Técnica)", row.text())
        for literal in ("José María", "Ñandú", "Av. Córdoba 1234, Peña &amp; Cía", "Citroën",
                        "Núñez", "Güemes", "REVISIÓN", "Inés Ibáñez (Técnica)"):
            self.assertIn(literal, markup)
        self.assertIn("Ñandú O&#x27;Higgins", markup)
        self.assertNotIn("&amp;amp;", markup)
        self.assertNotIn("&amp;#x27;", markup)
        for entity in ("&eacute;", "&ntilde;", "&Ntilde;", "&oacute;", "&#233;", "&#xe9;"):
            self.assertNotIn(entity, markup)


class MetaRow(unittest.TestCase):
    """`_ameta_row` keeps the separator rules. The older tests of them (test_m12_calendar_ui,
    TestMetadataSeparatorClean) die on a MagicMock in the day view before reaching an assertion,
    identically on 198ed4d, so they prove nothing about this line."""

    def _rows(self, **over) -> list[str]:
        root = parse(_page(PAYLOADS["ampersand"], **over))
        week = [n.text() for n in by_class(by_id(root, "cal-view-week"), "calWeekApptMeta")]
        month = [n.text() for n in by_class(by_id(root, f"cal-mday-{DAY.isoformat()}"),
                                            "calMonthApptMeta")]
        self.assertEqual(week, month)
        return week

    def test_ae23_address_and_professional(self):
        want = _expected(PAYLOADS["ampersand"])
        self.assertEqual(self._rows()[1], f"{want['address']} \xa0·\xa0 {want['prof']}")

    def test_ae24_address_without_a_professional(self):
        want = _expected(PAYLOADS["ampersand"])
        self.assertEqual(self._rows(profesional_id=None),
                         [want["vehicle"], want["address"], want["estado"]])

    def test_ae25_professional_without_an_address(self):
        want = _expected(PAYLOADS["ampersand"])
        self.assertEqual(self._rows(direccion_texto=None),
                         [want["vehicle"], want["prof"], want["estado"]])

    def test_ae26_neither_leaves_no_row_and_no_orphan_separator(self):
        want = _expected(PAYLOADS["ampersand"])
        self.assertEqual(self._rows(direccion_texto=None, profesional_id=None),
                         [want["vehicle"], want["estado"]])


# Every expression interpolated into an f-string inside `render_calendar_page` that is NOT passed
# through an escaper, with the reason it is safe. A new interpolation fails AE-15 until someone
# has classified it here.
_ENDS_AS_TEXT_BUILT_BY_THE_SERVER = {
    # numbers, counters and flags computed from them
    "turnos", "unpaid", "free_gaps", "travel_mins", "avail_mins", "margin_mins", "-margin_mins",
    "remaining", "h", "day.day", "day.month", "_gd.day", "_gd2.day", "week_start.day",
    "week_end.day", "week_start.year", "month_start.year",
    "'s' if turnos != 1 else ''", "'s' if free_gaps != 1 else ''",
    "' confirmed' if _any_ok else ''",
    # integer primary keys
    "l.id", "r.id", "wa_tid",
    # dates and times formatted by the server
    "build_stamp", "tstr", "tend", "_fc", "_cobro_today", "d.strftime('%d/%m')",
    "week_start.strftime('%d/%m')", "week_end.strftime('%d/%m')",
    "prev_end_dt.strftime('%H:%M')", "biz_end_t.strftime('%H:%M')", "today.isoformat()",
    "initial_day.isoformat()", "prev_monday.isoformat()", "next_monday.isoformat()",
    "prev_month_monday.isoformat()", "next_month_monday.isoformat()", "_gd.isoformat()",
    "_gd2.isoformat()", "_gd3.isoformat()",
    # labels from constant tables
    "labels[d.weekday()]", "_DAY_NAMES[day.weekday()]", "_MONTHS_ES_CAP[month_start.month - 1]",
    "_MONTHS_CAP[week_start.month - 1]", "_MONTHS_CAP[week_end.month - 1]",
    "_MONTHS_CAP[month_start.month - 1]", "_MONTHS_CAP[day.month - 1]",
    "_MONTHS_CAP[_gd2.month - 1]", "ICON_SEARCH", "ICON_CLOSE", "day_label", "dn", "dm",
    "today_sfx", "_dh", "_dlabel",
    # class names picked from fixed sets
    "cls", "scls", "m_cls", "m_txt", "hl_cls", "hl_id", "has_cls", "_tcls", "_ocls",
    # markup assembled above from escaped parts
    "stat_html", "pago_chip", "_dot", "estado_raw", "search_text", "href",
    "_meta_row", "_meta_row_nr", "_meta_mc", "_meta_wk",
    # percent-encoded with safe="", so only unreserved characters and %XX remain
    "q_addr", "_urlquote(raw_addr, safe='')", "maps_url", "waze_url",
    # the two halves of `_aname`, composed into plain text that its sinks escape
    "(_get(l, 'nombre') or '').strip()", "(_get(l, 'apellido') or '').strip()",
}
_ESCAPERS = ("html_lib.escape(", "_esc_txt(", "_esc_val(")


def _calendar_source() -> ast.FunctionDef:
    source = textwrap.dedent(inspect.getsource(render_calendar_page))
    return ast.parse(source).body[0]


class SinkInventory(unittest.TestCase):
    """The parse-tree proofs cannot reach `_day_slots`, which has no caller; these can."""

    def test_ae15_every_interpolation_is_escaped_or_classified(self):
        unclassified = set()
        for node in ast.walk(_calendar_source()):
            if isinstance(node, ast.FormattedValue):
                expr = ast.unparse(node.value)
                if not expr.startswith(_ESCAPERS) and expr not in _ENDS_AS_TEXT_BUILT_BY_THE_SERVER:
                    unclassified.add(expr)
        self.assertEqual(unclassified, set())

    def test_ae16_no_value_is_escaped_twice_in_the_source(self):
        for node in ast.walk(_calendar_source()):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("html_lib.escape", "_esc_txt"):
                inner = ast.unparse(node.args[0])
                self.assertFalse(inner.startswith(_ESCAPERS), inner)
                # finished HTML must never go through an escaper
                for finished in ("_aaddr(", "_ameta(", "_ameta_row(", "_url_link("):
                    self.assertNotIn(finished, inner)

    def test_ae17_the_plain_text_helpers_stay_plain(self):
        """Escaping inside them would double-escape `data-search` and the day card."""
        helpers = {n.name: n for n in ast.walk(_calendar_source())
                   if isinstance(n, ast.FunctionDef)}
        for name in ("_aname", "_aveh", "_aprof"):
            self.assertNotIn("escape", ast.unparse(helpers[name]), name)
            self.assertNotIn("_esc_", ast.unparse(helpers[name]), name)

    def test_ae18_the_modal_writes_customer_values_as_text(self):
        markup = _page(PAYLOADS["script"])
        for sink in ('getElementById("cobroWho").textContent=btn.dataset.revName',
                     'getElementById("cobroVeh").textContent=btn.dataset.revVeh'):
            self.assertIn(sink, markup)
        assignments = re.findall(r"innerHTML\s*=\s*([^;]+);", markup)
        self.assertTrue(assignments)
        for rhs in assignments:
            self.assertNotIn("dataset", rhs)


# ── through the real route, the real tables ───────────────────────────────────

def _client_and_db():
    """As in test_l4_7w5_agenda_cobro_action: other suites reload `app.db`, so the session is
    patched at `SessionLocal` and the override keyed on the callable the route really holds."""
    from sqlalchemy.pool import StaticPool
    from app.main import app as fastapi_app, SESSION_COOKIE
    from app.auth import sign_session
    import app.db as app_db
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    event.listen(engine, "connect", lambda c, r: c.execute("PRAGMA foreign_keys=OFF"))
    app.models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    patcher = patch.object(app_db, "SessionLocal", lambda: db)
    patcher.start()
    route = next(r for r in fastapi_app.routes if getattr(r, "path", "") == "/calendar")
    keys = {d.call for d in route.dependant.dependencies
            if getattr(d.call, "__name__", "").endswith("get_db")
            or getattr(d.call, "__name__", "") == "_get_db_gen"}
    keys.add(app_db.get_db)
    for key in keys:
        fastapi_app.dependency_overrides[key] = lambda: db
    client = TestClient(fastapi_app)
    with patch.dict(os.environ, CONFIGURED, clear=False):
        client.cookies.set(SESSION_COOKIE, sign_session({"email": OPERATOR}))
    return client, db, fastapi_app, patcher


class ThroughTheRoute(unittest.TestCase):

    def setUp(self):
        self.client, self.db, self.app, self._patcher = _client_and_db()

    def tearDown(self):
        self._patcher.stop()
        for dep in list(self.app.dependency_overrides):
            if getattr(dep, "__name__", "").endswith("get_db") or \
                    getattr(dep, "__name__", "") == "_get_db_gen":
                self.app.dependency_overrides.pop(dep, None)
        self.db.close()

    def _seed(self, p: str, telefono: str) -> tuple[int, int]:
        v = _values(p)
        lead = Lead(estado="CONSULTA_NUEVA", flag="ACEPTADO", nombre=v["nombre"],
                    apellido=v["apellido"], telefono=telefono)
        self.db.add(lead)
        self.db.flush()
        rev = Revision(lead_id=lead.id, turno_fecha=DAY, turno_hora=time(10, 0),
                       precio_total=130000, estado_revision="CONFIRMADO", marca=v["marca"],
                       modelo=v["modelo"], anio=2020, tipo_vehiculo=v["tipo_vehiculo"],
                       zone_group=v["zone_group"], zone_detail=v["zone_detail"],
                       direccion_texto=v["direccion_texto"])
        self.db.add(rev)
        self.db.commit()
        return lead.id, rev.id

    def _get(self) -> str:
        with patch.dict(os.environ, CONFIGURED, clear=False):
            response = self.client.get(f"/calendar?week={WEEK}&date={DAY.isoformat()}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("calWeekApptRow", response.text)
        return response.text

    def _wipe(self):
        self.db.execute(text("DELETE FROM revisions"))
        self.db.execute(text("DELETE FROM leads"))
        self.db.commit()
        self.db.expire_all()

    def test_ae19_a_stored_payload_is_inert_on_the_served_page(self):
        self._seed(BENIGN, "1100000001")
        benign = shape(parse(self._get()))
        self._wipe()
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                self._seed(payload, "1100000002")
                try:
                    served = parse(self._get())
                    self.assertEqual(shape(served), benign)
                    row = by_class(by_id(served, "cal-view-week"), "calWeekApptRow")[0]
                    self.assertEqual(by_class(row, "calWeekApptName")[0].text(),
                                     f"N{payload} A{payload}")
                finally:
                    self._wipe()

    def test_ae20_the_database_keeps_the_customer_text_unescaped(self):
        payload = PAYLOADS["ampersand"] + PAYLOADS["img_onerror"]
        lead_id, rev_id = self._seed(payload, "1100000003")
        self._get()
        self.db.expire_all()
        lead = self.db.execute(text("SELECT nombre, apellido FROM leads WHERE id=:i"),
                               {"i": lead_id}).one()
        rev = self.db.execute(text("SELECT direccion_texto, marca FROM revisions WHERE id=:i"),
                              {"i": rev_id}).one()
        self.assertEqual(tuple(lead), (f"N{payload}", f"A{payload}"))
        self.assertEqual(tuple(rev), (f"C{payload}", f"M{payload}"))

    def test_ae21_rendering_does_not_change_the_objects_it_reads(self):
        lead, prof = _world(PAYLOADS["script"])
        before = (dict(vars(lead)), dict(vars(lead.revisions[0])), dict(vars(prof)))
        render_calendar_page([lead], profesionales=[prof], week=WEEK, initial_date=DAY.isoformat())
        self.assertEqual((vars(lead), vars(lead.revisions[0]), vars(prof)), before)

    def test_ae22_the_lead_card_is_still_escaped_once(self):
        lead_id, _ = self._seed(BENIGN, "1100000004")
        benign = shape(parse(render_lead_card(self.db.get(Lead, lead_id))))
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                lead = self.db.get(Lead, lead_id)
                rev = lead.revisions[0]
                lead.nombre, lead.apellido = f"N{payload}", f"A{payload}"
                rev.marca, rev.direccion_texto = f"M{payload}", f"C{payload}"
                self.db.commit()
                card = parse(render_lead_card(self.db.get(Lead, lead_id)))
                self.assertEqual(shape(card), benign)
                self.assertIn(f"N{payload} A{payload}", card.text())
                self.assertIn(f"C{payload}", card.text())


if __name__ == "__main__":
    unittest.main()
