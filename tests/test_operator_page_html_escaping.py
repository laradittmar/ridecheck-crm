"""PRELAUNCH-MINIMUM-LAUNCH — dynamic HTML on the /table, /profesionales and /agencias pages.

These pages interpolated request and database values with `_txt`, which trims but does not
escape. `/table` reflected every filter into its chips, so a crafted link ran script in an
operator's session, and all three wrote stored names, phones and addresses as markup.

Each value is escaped once, at the sink, for the context it lands in:

  * HTML text and attributes: `_esc_txt`, `_esc_val`, `html.escape(quote=True)`;
  * URLs: `urlencode` for the query, then `html.escape` for the `href` that holds it;
  * `<script type="application/json">`: `<` as `\\u003c`, undone by `JSON.parse`.

The proofs reuse the agenda suite's parse tree. A value that reads back equal to the original was
escaped exactly once, and a hostile page must have the same elements and attribute names as a
benign page, so an injected tag or handler cannot pass however it is spelled.

OP-01..09 /table · OP-10..11 /profesionales · OP-12..17 /agencias · OP-18..21 sink inventory ·
OP-22..26 through the routes, storage included
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import re
import sys
import textwrap
import unittest
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agenda_customer_data_escaping import (     # noqa: E402  (also sets up the import path)
    CONFIGURED, _client_and_db, by_class, by_id, handlers, parse, shape,
)
from sqlalchemy import text                          # noqa: E402

from app.models import Agencia, Lead, Profesional, Revision, Vendedor, ViaticosZone  # noqa: E402
from app.ui import kanban_view                       # noqa: E402
from app.ui.kanban_view import (                     # noqa: E402
    _filters_form_html, render_agencias_page, render_profesionales_page,
    render_revisions_table_page,
)

PAYLOADS = {
    "tag": "<b>negrita</b>",
    "img_onerror": "<img src=x onerror=alert(1)>",
    "script": "<script>alert(2)</script>",
    "svg_onload": "<svg/onload=alert(3)>",
    "double_quote_breakout": '" onmouseover="alert(4)" x="',
    "single_quote_breakout": "' onfocus='alert(5)' autofocus='",
    "attribute_then_tag": '"><img src=x onerror=alert(6)>',
    "single_attribute_then_tag": "'><svg onload=alert(7)>",
    "double_quotes": 'Taller "El Rápido"',
    "single_quote": "O'Higgins",
    "ampersand": "Pérez & Hijos",
    "query_separators": "a&b=c #d+e?f",
    "text_that_looks_like_an_entity": "a &lt;b&gt; &amp; c &#60;",
    "close_the_cell": "</td></tr></table><script>alert(8)</script>",
    "close_a_comment": "--><img src=x onerror=alert(9)><!--",
    "close_a_select": "</option></select><img src=x onerror=alert(10)>",
    "url_encoded": "%3Cscript%3Ealert(11)%3C%2Fscript%3E",
    "double_url_encoded": "%253Cimg%2520src%253Dx%2520onerror%253Dalert(12)%253E",
    "javascript_url": "javascript:alert(13)",
}
BENIGN = "texto"
SPANISH = "José Ñandú Peñalver — Córdoba, Año 2ª ¿güeña?"
STAMP = datetime(2026, 11, 4, 9, 30)


def _canal_label(value: str) -> str:
    return value.replace("_", " ").title()


# ── /table world ──────────────────────────────────────────────────────────────

def _prof(p: str):
    return NS(id=5, nombre=f"P{p}", apellido=f"L{p}", email=f"e{p}", telefono=f"t{p}", cargo=f"R{p}",
              created_at=STAMP)


def _revision(p: str):
    lead = NS(id=77, nombre=f"N{p}", apellido=f"A{p}", telefono=f"11{p}", email=f"m{p}",
              estado="AGENDADO", flag=f"F{p}")
    return NS(id=901, lead=lead, profesional=_prof(p), agencia=NS(nombre_agencia=f"B{p}"),
              created_at=STAMP, turno_fecha=date(2026, 11, 4), turno_hora=time(10, 0),
              precio_total=130000, tipo_vehiculo=f"T{p}", marca=f"M{p}", modelo=f"O{p}",
              anio=f"Y{p}", zone_group=f"G{p}", zone_detail=f"D{p}", estado_revision=f"E{p}")


def _filters(p: str) -> dict:
    return dict(q=f"Q{p}", estado=[f"S{p}"], flag=[f"X{p}"], profesional_id="5", canal=f"C{p}",
                tipo_vehiculo=f"T{p}", marca=f"M{p}", modelo=f"O{p}", anio=f"Y{p}",
                zone_group=f"G{p}", zone_detail=f"D{p}", estado_revision=f"E{p}",
                from_date=f"I{p}", to_date=f"J{p}", date_field="turno")


def _zones(p: str) -> dict:
    return {f"G{p}": [f"D{p}", f"H{p}"], f"K{p}": []}


def _table(p: str, **over) -> str:
    args = {**_filters(p), "zones_map": _zones(p)}
    args.update(over)
    return render_revisions_table_page([_revision(p)], profesionales=[_prof(p)],
                                       user_email="operator@ridecheck.local", **args)


def _chips(root) -> list[str]:
    return [c.text().removesuffix("-") for c in by_class(root, "chip")]


def _expected_chips(p: str) -> list[str]:
    return [f"Estado: S{p}", f"Flag: X{p}", f"Profesional: P{p} L{p} (R{p})",
            f"Canal: {_canal_label(f'C{p}')}", f"Marca: M{p}", f"Modelo: O{p}",
            f"Tipo vehículo: T{p}", f"Año: Y{p}", f"Zona grupo: G{p}", f"Zona detalle: D{p}",
            f"Estado revisión: E{p}", f"Turno: I{p} ? J{p}", f"Buscar: Q{p}", "Limpiar todo"]


def _row(root):
    return next(n for n in root.walk() if n.tag == "tr" and "data-search" in n.attrs)


def _cells(row) -> list[str]:
    return [n.text() for n in row.walk() if n.tag == "td"]


def _named(root, tag: str, name: str):
    return [n for n in root.walk() if n.tag == tag and n.attrs.get("name") == name]


class TablePage(unittest.TestCase):

    def test_op01_no_payload_changes_the_structure_of_the_page(self):
        benign = parse(_table(BENIGN))
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                hostile = parse(_table(payload))
                self.assertEqual(shape(hostile), shape(benign))
                self.assertEqual(handlers(hostile), handlers(benign))

    def test_op02_rows_read_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                cells = _cells(_row(parse(_table(p))))
                self.assertEqual(
                    cells[3:6] + cells[7:14] + cells[15:16],
                    [f"N{p} A{p}", f"11{p}", f"m{p}", f"F{p}", f"T{p}", f"M{p}", f"O{p}", f"Y{p}",
                     f"G{p}", f"D{p}", f"E{p}"])

    def test_op03_the_row_search_index_reads_back_exactly_once(self):
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                self.assertEqual(
                    _row(parse(_table(p))).attrs["data-search"],
                    f"N{p} A{p} 11{p} m{p} M{p} O{p} Y{p} P{p} L{p} (R{p}) E{p} B{p}")

    def test_op04_every_filter_chip_reads_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                self.assertEqual(_chips(parse(_table(p))), _expected_chips(p))

    def test_op05_one_sided_dates_and_known_labels(self):
        p = PAYLOADS["img_onerror"]
        only_from = dict(_filters(p), to_date="")
        only_to = dict(_filters(p), from_date="")
        self.assertIn(f"Turno desde: I{p}", _chips(parse(_table(p, **only_from))))
        self.assertIn(f"Turno hasta: J{p}", _chips(parse(_table(p, **only_to))))
        known = _chips(parse(_table(p, estado=["AGENDADO"], flag=["ACEPTADO"], canal="IG_DM")))
        self.assertEqual(known[0], f"Estado: {kanban_view.KANBAN_LABELS['AGENDADO']}")
        self.assertEqual(known[1], f"Flag: {kanban_view.FLAG_LABELS['ACEPTADO']}")
        self.assertEqual(known[3], "Canal: Instagram DM")

    def test_op06_links_carry_each_value_through_the_url_exactly_once(self):
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                root = parse(_table(p))
                want = {k: (v if isinstance(v, list) else [v]) for k, v in _filters(p).items()}
                links = [by_class(root, "backLink")[0].attrs["href"]]
                links += [n.attrs["href"] for n in by_class(root, "btn-sm") if n.tag == "a"]
                self.assertEqual(len(links), 2)
                for href in links:
                    parts = urlsplit(href)
                    self.assertEqual(parts.path, "/kanban")
                    self.assertEqual(parse_qs(parts.query), want)
                self.assertEqual(urlsplit(links[1]).fragment, "lead-77")
                marca_chip = next(c for c in by_class(root, "chip")
                                  if c.text().startswith("Marca:"))
                cleared = dict(want)
                cleared.pop("marca")
                self.assertEqual(parse_qs(urlsplit(marca_chip.attrs["href"]).query), cleared)

    def test_op07_an_href_holds_no_bare_ampersand(self):
        """A parser reads `&` and `&amp;` alike, so only the source shows the attribute context."""
        markup = _table(PAYLOADS["ampersand"])
        hrefs = [h for h in re.findall(r'href="([^"]*)"', markup) if "?" in h]
        self.assertGreater(len(hrefs), 10)
        for href in hrefs:
            self.assertIsNone(re.search(r"&(?!amp;)", href), href)

    def test_op08_filter_inputs_and_zone_options_read_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                root = parse(_table(p))
                for field, value in (("q", f"Q{p}"), ("marca", f"M{p}"), ("modelo", f"O{p}"),
                                     ("anio", f"Y{p}"), ("from_date", f"I{p}"),
                                     ("to_date", f"J{p}")):
                    self.assertEqual(_named(root, "input", field)[0].attrs["value"], value)
                self.assertEqual(by_id(root, "table-search-input").attrs["value"], f"Q{p}")
                for field, values in (("zone_group", [f"G{p}", f"K{p}"]),
                                      ("zone_detail", [f"D{p}", f"H{p}"])):
                    options = [n for n in _named(root, "select", field)[0].walk()
                               if n.tag == "option" and n.attrs["value"]]
                    self.assertEqual([o.attrs["value"] for o in options], values)
                    self.assertEqual([o.text() for o in options], values)
                    self.assertTrue(any("selected" in o.attrs for o in options))

    def test_op09_the_zones_block_cannot_leave_its_script_and_parses_to_the_same_map(self):
        for name, p in {**PAYLOADS, "open_a_comment": "<!--<script>",
                        "close_the_block": "</script><img src=x onerror=alert(14)>"}.items():
            with self.subTest(payload=name):
                zones = _zones(p)
                block = by_id(parse(_table(p, zones_map=zones)), "zones-data").text()
                self.assertNotIn("<", block)
                self.assertEqual(json.loads(block), zones)

    def test_op09b_spanish_is_written_literally_and_url_escapes_are_not_decoded(self):
        markup = _table(SPANISH)
        self.assertIn(f"<td>M{SPANISH}</td>", markup)
        self.assertIn(f"Marca: M{SPANISH}<", markup)
        self.assertIn(f'value="M{SPANISH}"', markup)
        self.assertNotIn("&eacute;", markup)
        self.assertNotIn("&#", markup.split("<tbody>")[1].split("</tbody>")[0])
        encoded = PAYLOADS["url_encoded"]
        root = parse(_table(encoded))
        self.assertIn(f"Buscar: Q{encoded}", _chips(root))
        self.assertIn("%253Cscript%253E", by_class(root, "backLink")[0].attrs["href"])


# ── /profesionales ────────────────────────────────────────────────────────────

def _prof_page(p: str) -> str:
    return render_profesionales_page([_prof(p)], user_email="operator@ridecheck.local")


class ProfesionalesPage(unittest.TestCase):

    def test_op10_no_payload_changes_the_structure_of_the_page(self):
        benign = parse(_prof_page(BENIGN))
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                hostile = parse(_prof_page(payload))
                self.assertEqual(shape(hostile), shape(benign))
                self.assertEqual(handlers(hostile), handlers(benign))

    def test_op11_every_column_reads_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                root = parse(_prof_page(p))
                scope = next(n for n in root.walk()
                             if n.attrs.get("data-search-scope") == "prof")
                self.assertEqual(_cells(scope)[:5], [f"P{p}", f"L{p}", f"e{p}", f"t{p}", f"R{p}"])
        self.assertIn(f"<td>P{SPANISH}</td>", _prof_page(SPANISH))


# ── /agencias ─────────────────────────────────────────────────────────────────

def _agencia(p: str, **over):
    fields = dict(id=31, nombre_agencia=f"B{p}", direccion=f"C{p}", gmaps=f"https://maps.example/{p}",
                  mail=f"m{p}", telefono=f"t{p}", vendedor_id=8, vendedor=NS(nombre=f"V{p}"),
                  file_name=f"f{p}", file_path="/uploads/stored.xlsx", fecha_subido=STAMP)
    fields.update(over)
    return NS(**fields)


def _ag_page(p: str, **over) -> str:
    return render_agencias_page([_agencia(p, **over)], vendedores=[NS(id=8, nombre=f"V{p}")],
                                user_email="operator@ridecheck.local")


class AgenciasPage(unittest.TestCase):

    def test_op12_no_payload_changes_the_structure_of_the_page(self):
        benign = parse(_ag_page(BENIGN))
        for name, payload in PAYLOADS.items():
            with self.subTest(payload=name):
                hostile = parse(_ag_page(payload))
                self.assertEqual(shape(hostile), shape(benign))
                self.assertEqual(handlers(hostile), handlers(benign))

    def test_op13_every_column_reads_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                cells = _cells(_row(parse(_ag_page(p))))
                self.assertEqual(cells[1:3] + cells[4:8],
                                 [f"B{p}", f"C{p}", f"m{p}", f"V{p}", f"t{p}", f"f{p}"])

    def test_op14_the_modal_and_both_vendor_lists_read_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                root = parse(_ag_page(p))
                modal = by_id(root, "ag-modal-31")
                for field, value in (("nombre_agencia", f"B{p}"), ("direccion", f"C{p}"),
                                     ("gmaps", f"https://maps.example/{p}"), ("mail", f"m{p}"),
                                     ("telefono", f"t{p}")):
                    self.assertEqual(_named(modal, "input", field)[0].attrs["value"], value)
                selects = _named(root, "select", "vendedor_id")
                self.assertEqual(len(selects), 2)
                for select in selects:
                    options = [n for n in select.walk() if n.tag == "option" and n.attrs["value"]]
                    self.assertEqual([o.text() for o in options], [f"V{p}"])

    def test_op15_the_download_link_and_a_row_without_a_file(self):
        p = PAYLOADS["script"]
        link = next(n for n in _row(parse(_ag_page(p))).walk() if n.tag == "a"
                    and n.attrs["href"] == "/ui/agencia_file/31")
        self.assertEqual(link.text(), f"f{p}")
        self.assertEqual(_cells(_row(parse(_ag_page(p, file_path=None))))[7], "-")

    def test_op16_a_stored_map_link_cannot_become_a_script_url(self):
        for gmaps in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>"):
            with self.subTest(gmaps=gmaps):
                row = _row(parse(_ag_page(BENIGN, gmaps=gmaps)))
                self.assertEqual(_cells(row)[3], gmaps)
                self.assertEqual([n for n in row.walk() if n.tag == "a"
                                  and not n.attrs["href"].startswith("/ui/")], [])
        breakout = 'https://maps.example/"><img src=x onerror=alert(1)>'
        row = _row(parse(_ag_page(BENIGN, gmaps=breakout)))
        self.assertEqual(next(n for n in row.walk() if n.tag == "a").attrs["href"], breakout)

    def test_op17_the_search_index_is_escaped_once_and_spanish_is_literal(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                self.assertEqual(_row(parse(_ag_page(p))).attrs["data-search"],
                                 f"B{p} C{p} m{p} V{p} t{p} f{p}")
        markup = _ag_page(SPANISH)
        self.assertIn(f"<td>B{SPANISH}</td>", markup)
        self.assertIn(f'value="B{SPANISH}"', markup)
        self.assertNotIn("&ntilde;", markup)


# ── sink inventory ────────────────────────────────────────────────────────────

_SAFE_BY_CONSTRUCTION = {
    # integer primary keys, and numbers formatted by the server
    "r.id", "l.id", "a.id", "p.id", "v.id", "len(revisions)", "raw_total:.2f", "raw_total",
    "_fmt_money(r.precio_total)", "_fmt_money(total_precio)", "len(selected_labels) - 1",
    "len(flag_selected_labels) - 1",
    # dates and times formatted by the server
    "build_stamp", "created_txt", "turno_txt", "tf", "th",
    "r.created_at.strftime('%Y-%m-%d %H:%M') if r.created_at else '-'",
    "a.fecha_subido.strftime('%Y-%m-%d %H:%M') if a.fecha_subido else '-'",
    # the labels written into `label`, then escaped at the chip that shows them
    "field_label", "from_date", "to_date",
    # constants: icons, fixed option lists and their labels; the callers' literal `action`
    "ICON_SEARCH", "ICON_CLOSE", "ICON_MENU_HAMBURGER", "ICON_ARROW_LEFT", "ICON_CHEVRON_DOWN",
    "action", "k", "c", "t", "s", "KANBAN_LABELS.get(k, k)", "FLAG_LABELS.get(k, k)",
    "estado_label", "flag_label", "selected_labels[0]", "flag_selected_labels[0]",
    # booleans that pick a fixed word
    "'selected' if g == zone_group_val else ''", "'selected' if d == zone_detail_val else ''",
    "'checked' if k in estado_set else ''", "'checked' if k in flag_set else ''",
    "'selected' if str(p.id) == prof_val else ''", "'selected' if canal == c else ''",
    "'selected' if tipo_vehiculo == t else ''", "'selected' if estado_revision == s else ''",
    "'selected' if date_field == 'turno' else ''",
    "'selected' if date_field == 'created_at' else ''",
    "'selected' if a.vendedor_id == v.id else ''",
    "'<input type=\"hidden\" name=\"open_filters\" value=\"1\"/>' if include_open_filters else ''",
    # finished markup or query assembled above from escaped parts
    "qstr", "query", "search_text", "search_val", "zones_json", "_make_table_link(p)",
    "_url_link(a.gmaps, 'Maps')", "file_cell", "vend_opts", "estado_html", "flag_html",
    "profesional_html", "zone_inputs_html", "estado_checks", "flag_checks", "back_link_html",
    "zone_group_options", "zone_detail_options", "prof_options",
}
_ESCAPERS = ("html_lib.escape(", "_esc_txt(", "_esc_val(")
_RENDERERS = (_filters_form_html, render_revisions_table_page, render_profesionales_page,
              render_agencias_page)


def _tree(fn) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(fn)))


def _joined_calls(node) -> bool:
    return isinstance(node, ast.Call) and ast.unparse(node.func).endswith(".join")


class SinkInventory(unittest.TestCase):

    def test_op18_every_interpolation_is_escaped_or_classified(self):
        for fn in _RENDERERS:
            with self.subTest(fn=fn.__name__):
                unclassified = set()
                for node in ast.walk(_tree(fn)):
                    if not isinstance(node, ast.FormattedValue) or _joined_calls(node.value):
                        continue
                    expr = ast.unparse(node.value)
                    if not expr.startswith(_ESCAPERS) and expr not in _SAFE_BY_CONSTRUCTION:
                        unclassified.add(expr)
                self.assertEqual(unclassified, set())

    def test_op19_no_value_is_escaped_twice_in_the_source(self):
        for fn in _RENDERERS:
            for node in ast.walk(_tree(fn)):
                if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                        "html_lib.escape", "_esc_txt", "_esc_val"):
                    inner = ast.unparse(node.args[0])
                    self.assertFalse(inner.startswith(_ESCAPERS), inner)
                    for finished in ("_make_table_link(", "_url_link(", "search_text", "search_val"):
                        self.assertNotIn(finished, inner)

    def test_op20_the_plain_values_stay_plain(self):
        """Each feeds both a sink and an index or a URL, so escaping it early escapes it twice."""
        for fn in (kanban_view._txt, kanban_view._val, kanban_view._profesional_label,
                   kanban_view._build_query_string):
            self.assertNotIn("escape", inspect.getsource(fn), fn.__name__)
        source = inspect.getsource(render_agencias_page)
        self.assertIn("vend_name = _txt(a.vendedor.nombre if a.vendedor else None)", source)
        self.assertIn('kanban_href = f"/kanban?{query}" if query else "/kanban"',
                      inspect.getsource(render_revisions_table_page))

    def test_op21_inline_handlers_hold_only_integer_ids_and_scripts_hold_no_value(self):
        p = PAYLOADS["script"]
        fixed = re.compile(r"^(openAgenciaEdit\(\d+\)|closeAgenciaEdit\(\d+(, event)?\)|"
                           r"event\.stopPropagation\(\);|toggleSidebar\(\)|openFilters\(\)|"
                           r"closeFilters\(\))$")
        for markup in (_table(p), _prof_page(p), _ag_page(p)):
            root = parse(markup)
            for _, _, value in handlers(root):
                self.assertRegex(value, fixed)
            for node in root.walk():
                if node.tag == "script" and node.attrs.get("id") != "zones-data":
                    self.assertNotIn("alert(2)", node.text())


# ── through the routes, the real tables ───────────────────────────────────────

class ThroughTheRoutes(unittest.TestCase):

    def setUp(self):
        self.client, self.db, self.app, self._patcher = _client_and_db()

    def tearDown(self):
        self._patcher.stop()
        for dep in list(self.app.dependency_overrides):
            if getattr(dep, "__name__", "").endswith("get_db") or \
                    getattr(dep, "__name__", "") == "_get_db_gen":
                self.app.dependency_overrides.pop(dep, None)
        self.db.close()

    def _get(self, url: str, **kwargs) -> str:
        with patch.dict(os.environ, CONFIGURED, clear=False):
            response = self.client.get(url, **kwargs)
        self.assertEqual(response.status_code, 200)
        return response.text

    def _wipe(self):
        for table in ("revisions", "leads", "profesionales", "agencias", "vendedores",
                      "viaticos_zones"):
            self.db.execute(text(f"DELETE FROM {table}"))
        self.db.commit()
        self.db.expire_all()

    def _seed(self, p: str) -> None:
        prof = Profesional(nombre=f"P{p}", apellido=f"L{p}", email=f"e{p}", telefono=f"t{p}",
                           cargo=f"R{p}")
        vend = Vendedor(nombre=f"V{p}")
        lead = Lead(estado="AGENDADO", flag=f"F{p}", nombre=f"N{p}", apellido=f"A{p}",
                    telefono=f"11{p}", email=f"m{p}")
        self.db.add_all([prof, vend, lead, ViaticosZone(zone_group=f"G{p}", zone_detail=f"D{p}",
                                                        viaticos=1)])
        self.db.flush()
        agencia = Agencia(nombre_agencia=f"B{p}", direccion=f"C{p}", gmaps=f"https://m.example/{p}",
                          mail=f"m{p}", telefono=f"t{p}", vendedor_id=vend.id, file_name=f"f{p}",
                          file_path="/uploads/stored.xlsx", fecha_subido=STAMP)
        self.db.add(agencia)
        self.db.flush()
        self.db.add(Revision(lead_id=lead.id, profesional_id=prof.id, agencia_id=agencia.id,
                             turno_fecha=date(2026, 11, 4), turno_hora=time(10, 0),
                             precio_total=130000, estado_revision=f"E{p}", marca=f"M{p}",
                             modelo=f"O{p}", anio=2020, tipo_vehiculo=f"T{p}",
                             zone_group=f"G{p}", zone_detail=f"D{p}"))
        self.db.commit()

    @staticmethod
    def _query(p: str) -> dict:
        return {k: v for k, v in _filters(p).items() if k != "profesional_id"}

    def test_op22_reflected_parameters_are_inert_on_the_served_table(self):
        benign = parse(self._get("/table", params=self._query(BENIGN)))
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                served = parse(self._get("/table", params=self._query(p)))
                self.assertEqual(shape(served), shape(benign))
                self.assertEqual(handlers(served), handlers(benign))
                self.assertIn(f"Buscar: Q{p}", _chips(served))
                self.assertIn(f"Marca: M{p}", _chips(served))

    def test_op23_a_percent_encoded_link_is_decoded_once_by_the_server_and_no_more(self):
        benign = shape(parse(self._get("/table?q=texto&marca=texto")))
        for raw, shown in (
                ("%3Cscript%3Ealert(1)%3C%2Fscript%3E", "<script>alert(1)</script>"),
                ("%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E", "<img src=x onerror=alert(1)>"),
                ("%22%20onmouseover%3D%22alert(1)", '" onmouseover="alert(1)'),
                ("%253Cscript%253E", "%3Cscript%3E"),
                ("Pe%C3%B1a%20%26%20Hijos", "Peña & Hijos")):
            with self.subTest(raw=raw):
                root = parse(self._get(f"/table?q={raw}&marca={raw}"))
                self.assertEqual(shape(root), benign)
                self.assertEqual(_chips(root), [f"Marca: {shown}", f"Buscar: {shown}",
                                                "Limpiar todo"])
                self.assertEqual(_named(root, "input", "q")[0].attrs["value"], shown)

    def test_op24_a_stored_payload_is_inert_on_all_three_served_pages(self):
        self._seed(BENIGN)
        urls = ("/table", "/profesionales", "/agencias")
        benign = {u: shape(parse(self._get(u))) for u in urls}
        self._wipe()
        for name, p in PAYLOADS.items():
            self._seed(p)
            try:
                for url in urls:
                    with self.subTest(payload=name, url=url):
                        self.assertEqual(shape(parse(self._get(url))), benign[url])
            finally:
                self._wipe()

    def test_op25_the_database_keeps_the_text_as_typed_after_writing_and_rendering(self):
        p = PAYLOADS["ampersand"] + PAYLOADS["img_onerror"]
        with patch.dict(os.environ, CONFIGURED, clear=False):
            for url, data in (
                    ("/ui/profesional_create", dict(nombre=f"P{p}", apellido=f"L{p}", email=f"e{p}",
                                                    telefono=f"t{p}", cargo=f"R{p}")),
                    ("/ui/agencia_create", dict(nombre_agencia=f"B{p}", direccion=f"C{p}",
                                                gmaps="https://m.example/", mail=f"m{p}",
                                                telefono=f"t{p}", vendedor_nuevo=f"V{p}"))):
                response = self.client.post(url, data=data, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
        prof_cells = _cells(next(n for n in parse(self._get("/profesionales")).walk()
                                 if n.attrs.get("data-search-scope") == "prof"))
        self.assertEqual(prof_cells[:5], [f"P{p}", f"L{p}", f"e{p}", f"t{p}", f"R{p}"])
        self.assertEqual(_cells(_row(parse(self._get("/agencias"))))[1], f"B{p}")
        self.db.expire_all()
        self.assertEqual(tuple(self.db.execute(text(
            "SELECT nombre, apellido, email, telefono, cargo FROM profesionales")).one()),
            (f"P{p}", f"L{p}", f"e{p}", f"t{p}", f"R{p}"))
        self.assertEqual(tuple(self.db.execute(text(
            "SELECT a.nombre_agencia, a.direccion, a.mail, a.telefono, v.nombre FROM agencias a "
            "JOIN vendedores v ON v.id = a.vendedor_id")).one()),
            (f"B{p}", f"C{p}", f"m{p}", f"t{p}", f"V{p}"))

    def test_op26_rendering_does_not_change_what_it_reads(self):
        p = PAYLOADS["script"]
        rev, prof, ag = _revision(p), _prof(p), _agencia(p)
        filters, zones = _filters(p), _zones(p)
        before = json.dumps([vars(rev.lead), vars(prof), filters, zones], default=str)
        ag_before, rev_before = dict(vars(ag)), dict(vars(rev))
        render_revisions_table_page([rev], profesionales=[prof], zones_map=zones, **filters)
        render_profesionales_page([prof])
        render_agencias_page([ag], vendedores=[])
        self.assertEqual(json.dumps([vars(rev.lead), vars(prof), filters, zones], default=str), before)
        self.assertEqual((vars(ag), vars(rev)), (ag_before, rev_before))


if __name__ == "__main__":
    unittest.main()
