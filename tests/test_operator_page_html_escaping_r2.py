"""PRELAUNCH-MINIMUM-LAUNCH R2 — same-class audit of /kanban, /control and /whatsapp.

Confirmed and corrected here:

  * /kanban zones block: `</` was neutralised but `<!--<script>` was not, and that name swallows
    the rest of the document. `<` is now `\\u003c`, which `JSON.parse` restores;
  * the revision edit form wrote stored zone names into `<option>` unescaped;
  * three client pickers wrote customer names through `innerHTML`: a WhatsApp profile name is
    chosen by the customer, so it reached the operator's page with no operator action;
  * /control put a stored path id inside an inline handler. HTML-escaping cannot protect that:
    the attribute parser decodes it back before the JavaScript runs. It now travels in `data-path`.

`render_table_page` had no caller and is deleted; OP-R2-14..15 hold that.

Execution in a real browser is proved by tests/escaping_browser_proof/run_proof.py, over a sealed
local HTTP origin. These tests pin the same boundaries in the sealed runner, without a browser.

R2-01..05b /kanban · R2-06..10 /control · R2-11..13 /whatsapp · R2-14..15 dead renderer ·
R2-16 storage
"""
from __future__ import annotations

import ast
import glob
import inspect
import json
import os
import re
import sys
import textwrap
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_agenda_customer_data_escaping import (     # noqa: E402  (also sets up the import path)
    CONFIGURED, _client_and_db, by_class, by_id, handlers, parse, shape,
)
from test_operator_page_html_escaping import (       # noqa: E402
    BENIGN, PAYLOADS as _R1_PAYLOADS, SPANISH, _named,
)
from sqlalchemy import text                          # noqa: E402

from app.main import app as fastapi_app              # noqa: E402
from app.models import (                             # noqa: E402
    Lead, Profesional, Revision, ViaticosZone, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
)
from app.ui import control_view, kanban_view, whatsapp_ui   # noqa: E402
from app.ui.hybrid_trace_view import render_turn_trace_page     # noqa: E402
from app.ui.kanban_view import render_edit_latest_revision_form, render_page   # noqa: E402
from app.ui.offer_authority_view import (            # noqa: E402
    render_offer_detail_page, render_offer_list_page,
)

ROOT = Path(__file__).resolve().parents[1]
PAYLOADS = {**_R1_PAYLOADS, "leave_a_quoted_js_string": "');alert(15);//",
            "open_a_comment": "<!--<script>", "close_the_block": "</script><img src=x onerror=alert(16)>"}
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def _deep(value, token: str):
    """Every string in a JSON value replaced, so hostile and benign take the same code path."""
    if isinstance(value, dict):
        return {k: _deep(v, token) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep(v, token) for v in value]
    return token if isinstance(value, str) else value


class _Routes(unittest.TestCase):

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
        self.assertEqual(response.status_code, 200, url)
        return response.text

    def _wipe(self):
        for table in ("whatsapp_messages", "whatsapp_threads", "whatsapp_contacts", "revisions",
                      "leads", "profesionales", "viaticos_zones"):
            self.db.execute(text(f"DELETE FROM {table}"))
        self.db.commit()
        self.db.expire_all()

    def _seed(self, p: str) -> int:
        lead = Lead(estado="AGENDADO", flag=f"F{p}", nombre=f"N{p}", apellido=f"A{p}",
                    telefono=f"11{p}", email=f"m{p}", motivo_perdida=f"X{p}")
        self.db.add_all([lead, ViaticosZone(zone_group=f"G{p}", zone_detail=f"D{p}", viaticos=1)])
        self.db.flush()
        self.db.add(Revision(lead_id=lead.id, turno_fecha=date(2026, 11, 4), turno_hora=time(10, 0),
                             precio_total=1, estado_revision=f"E{p}", marca=f"M{p}", modelo=f"O{p}",
                             anio=2020, tipo_vehiculo=f"T{p}", zone_group=f"G{p}",
                             zone_detail=f"D{p}", direccion_texto=f"C{p}", resultado=f"R{p}"))
        contact = WhatsAppContact(wa_id=f"W{p}", display_name=f"P{p}")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, last_message_at=NOW)
        self.db.add(thread)
        self.db.flush()
        first = WhatsAppMessage(thread_id=thread.id, wa_message_id="a", direction="in", timestamp=NOW,
                                message_type="text", text=f"T{p}", status="received")
        self.db.add(first)
        self.db.flush()
        self.db.add_all([
            WhatsAppMessage(thread_id=thread.id, wa_message_id="b", direction="out", timestamp=NOW,
                            message_type="text", text=f"U{p}", status="sent",
                            raw_payload={"reply_to_message_id": first.id, "local_reaction": f"L{p}"}),
            WhatsAppMessage(thread_id=thread.id, wa_message_id="c", direction="in", timestamp=NOW,
                            message_type="flow_response", text="-", status="received",
                            raw_payload={"interactive": {"nfm_reply": {"response_json": json.dumps(
                                {"zona_general": f"Z{p}", "marca": f"B{p}", "anio": f"Y{p}"})}}}),
            WhatsAppMessage(thread_id=thread.id, wa_message_id="d", direction="out", timestamp=NOW,
                            message_type="flow", text=f"V{p}", status="delivered"),
            WhatsAppMessage(thread_id=thread.id, wa_message_id="e", direction="in", timestamp=NOW,
                            message_type="audio", media_id=f"I{p}", text=f"Q{p}", status="received"),
        ])
        self.db.commit()
        return thread.id

    def _same_structure(self, urls):
        """`urls` maps the seeded thread id to the pages to compare."""
        tid = self._seed(BENIGN)
        benign = {name: shape(parse(self._get(url(tid)))) for name, url in urls.items()}
        self._wipe()
        for payload_name, payload in PAYLOADS.items():
            tid = self._seed(payload)
            try:
                for name, url in urls.items():
                    with self.subTest(payload=payload_name, page=name):
                        self.assertEqual(shape(parse(self._get(url(tid)))), benign[name])
            finally:
                self._wipe()


# ── /kanban ───────────────────────────────────────────────────────────────────

def _edit_form(p: str, zones: dict) -> str:
    from types import SimpleNamespace as NS
    rev = NS(id=9, zone_group=f"G{p}", zone_detail=f"D{p}", turno_fecha=None, turno_hora=None,
             profesional_id=None, tipo_vehiculo=None, marca=None, modelo=None, anio=None,
             link_compra=None, presupuesto_compra=None, vendedor_tipo=None, compro=None,
             direccion_texto=None, link_maps=None, precio_base=None, viaticos=None,
             precio_total=None, pago=None, medio_pago=None, turno_notas=None,
             cliente_presente=None, estado_revision=None, resultado=None, motivo_rechazo=None)
    return render_edit_latest_revision_form(5, rev, 1, zones, [], [])


class Kanban(_Routes):

    def test_r2_01_the_zones_block_cannot_leave_its_script(self):
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                zones = {f"G{p}": [f"D{p}"]}
                block = by_id(parse(render_page([], zones_map=zones)), "zones-data").text()
                self.assertNotIn("<", block)
                self.assertEqual(json.loads(block), zones)

    def test_r2_02_edit_form_zone_options_read_back_exactly_once(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            with self.subTest(payload=name):
                root = parse(_edit_form(p, {f"G{p}": [f"D{p}", f"H{p}"], f"K{p}": []}))
                for field, values in (("zone_group", [f"G{p}", f"K{p}"]),
                                      ("zone_detail", [f"D{p}", f"H{p}"])):
                    options = [n for n in _named(root, "select", field)[0].walk()
                               if n.tag == "option" and n.attrs["value"]]
                    self.assertEqual([o.attrs["value"] for o in options], values)
                    self.assertEqual([o.text() for o in options], values)
                    self.assertTrue(any("selected" in o.attrs for o in options))

    def test_r2_03_a_stored_payload_is_inert_on_the_served_board(self):
        self._same_structure({"kanban": lambda tid: "/kanban"})

    def test_r2_04_reflected_parameters_are_inert_and_only_q_is_shown(self):
        names = ("q", "marca", "modelo", "tipo_vehiculo", "anio", "zone_group", "zone_detail",
                 "estado_revision", "canal", "estado", "flag")
        benign = parse(self._get("/kanban", params={n: BENIGN for n in names}))
        self.assertEqual(by_class(benign, "chip"), [], "/kanban renders no filter chips")
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                served = parse(self._get("/kanban", params={n: p for n in names}))
                self.assertEqual(shape(served), shape(benign))
                self.assertEqual(handlers(served), handlers(benign))
                self.assertEqual(by_id(served, "kanban-search-input").attrs["value"], p)

    def test_r2_05_the_whatsapp_picker_escapes_customer_values(self):
        page = render_page([])
        line = next(x for x in page.splitlines() if "data-thread-id=\"' + thread.thread_id" in x)
        self.assertIn('waEsc(thread.display_name || thread.wa_id || "-")', line)
        self.assertIn('waEsc(thread.wa_id || "-")', line)
        _assert_full_escaper(self, page, "waEsc")


_KANBAN_SAFE = {
    # integer primary keys, counters and numeric columns
    "lead_id", "last_rev_num", "last_rev.id", "p.id", "a.id", "i", "len(col)",
    "len(buckets.get(k, []))", "last_rev.anio or ''", "last_rev.presupuesto_compra or ''",
    "last_rev.precio_base or ''", "last_rev.viaticos or ''", "last_rev.precio_total or ''",
    "getattr(last_rev, 'comision', None) or ''",
    "getattr(last_rev, 'booking_version', '') if getattr(last_rev, 'booking_version', None) is not None else ''",
    # dates and times formatted by the server
    "build_stamp", "tf_val", "th_val",
    "getattr(last_rev, 'fecha_cobro', None).isoformat() if getattr(last_rev, 'fecha_cobro', None) else ''",
    # constants: icons, fixed lists, their labels
    "ICON_SEARCH", "ICON_CLOSE", "ICON_PLUS_THIN", "k", "c", "val", "estado_k", "estado_label",
    "KANBAN_LABELS.get(k, k)",
    # markup assembled above from escaped parts
    "search_val", "zones_json", "_mobile_tab_items", "create_lead_form_html", "header_actions",
    "profesional_options", "agencia_options", "zone_inputs_html", "zone_group_options",
    "zone_detail_options",
    "_render_revision_approval_ui(last_rev, include_actions=True, lead_id=lead_id)",
}
_ESCAPERS = ("html_lib.escape(", "_esc_txt(", "_esc_val(")


def _escaped(node) -> bool:
    """An escaper call, or a conditional whose branches are each a constant or an escaper."""
    if isinstance(node, ast.IfExp):
        return all(isinstance(b, ast.Constant) or _escaped(b) for b in (node.body, node.orelse))
    return ast.unparse(node).startswith(_ESCAPERS)


class KanbanInventory(unittest.TestCase):

    def test_r2_05b_every_interpolation_is_escaped_or_classified(self):
        for fn in (render_page, render_edit_latest_revision_form):
            with self.subTest(fn=fn.__name__):
                unclassified = set()
                for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
                    if not isinstance(node, ast.FormattedValue):
                        continue
                    expr = ast.unparse(node.value)
                    fixed_word = expr.startswith(("'selected' if", "'display:none;' if"))
                    joined = expr.startswith("''.join(")
                    built = expr.startswith(('f\'<option', '"<div class=\'grid-1'))
                    if not (_escaped(node.value) or expr in _KANBAN_SAFE
                            or fixed_word or joined or built):
                        unclassified.add(expr)
                self.assertEqual(unclassified, set())


def _assert_full_escaper(case, page: str, name: str):
    body = re.search(r"function %s\(v\)\s*\{(.*?)\n\s*\}" % name, page, re.S).group(1)
    for source, target in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"),
                           ("'", "&#39;")):
        case.assertIn(f"/{source}/g", body)
        case.assertIn(target, body)
    case.assertLess(body.index("/&/g"), body.index("/</g"), "& first, or the rest double-escape")


# ── /control ──────────────────────────────────────────────────────────────────

def _offer(p: str) -> dict:
    return dict(id=7, thread_id=3, cycle_key=f"Y{p}", candidate_id=f"V{p}", tipo_vehiculo=f"T{p}",
                zone_group=f"G{p}", zone_detail=f"D{p}", offered_date=f"I{p}", offered_time=f"J{p}",
                flow_id=f"W{p}", contract_version=f"K{p}", token_fingerprint_prefix=f"f{p}",
                status=f"S{p}", issued_at=f"A{p}", expires_at=f"B{p}", terminal_at=f"C{p}",
                accepted_at=f"E{p}", terminal_reason=f"R{p}", refused_count=1, last_refusal=f"L{p}",
                last_refused_at=f"M{p}", replay_count=1, last_replay_at=f"N{p}",
                accepted_revision_id=5, accepted_thread_revision_id=6)


class Control(_Routes):

    def test_r2_06_esc_covers_all_five_characters(self):
        page = control_view.render_control_page("operator@ridecheck.invalid")
        body = re.search(r"function esc\(s\) \{(.*?)\n    \}", page, re.S).group(1)
        for source, target in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"),
                               ("'", "&#39;")):
            self.assertIn(f"/{source}/g", body)
            self.assertIn(target, body)

    def test_r2_07_no_stored_string_is_placed_inside_an_inline_handler(self):
        page = control_view.render_control_page("operator@ridecheck.invalid")
        self.assertIn('data-path="\' + esc(r.path_id || r.path) + \'"', page)
        self.assertIn('onclick="filterTraceByPath(this.dataset.path);return false;"', page)
        built = re.findall(r"""onclick=\\?"[^"]*'\s*(?:\n\s*)?\+""", page)
        self.assertEqual(len(built), 1, built)
        self.assertIn("filterTraceByCustomer(", built[0], "contact_id is an integer primary key")

    def test_r2_08_offer_pages_keep_their_structure(self):
        benign_detail = shape(parse(render_offer_detail_page(_offer(BENIGN))))
        benign_list = shape(parse(render_offer_list_page([_offer(BENIGN)])))
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                self.assertEqual(shape(parse(render_offer_detail_page(_offer(p)))), benign_detail)
                self.assertEqual(shape(parse(render_offer_list_page([_offer(p)]))), benign_list)

    def test_r2_09_every_string_of_a_real_trace_can_be_hostile(self):
        fixture = ROOT / "tests" / "fixtures" / "hybrid_trace_live_partial_reconciliation.json"
        trace = json.loads(fixture.read_text(encoding="utf-8"))

        def page(token):
            return parse(render_turn_trace_page({"captured": True, "trace": _deep(trace, token)}))

        benign = shape(page(BENIGN))
        self.assertGreater(len(benign), 100)
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                self.assertEqual(shape(page(p)), benign)

    def test_r2_10_a_turn_id_in_the_path_is_inert(self):
        benign = shape(parse(self._get("/control/turn/texto")))
        for raw in ("%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E", "%22%20onfocus%3D%22alert(1)",
                    "%253Cscript%253E"):
            with self.subTest(raw=raw):
                self.assertEqual(shape(parse(self._get(f"/control/turn/{raw}"))), benign)


# ── /whatsapp ─────────────────────────────────────────────────────────────────

class WhatsApp(_Routes):

    def test_r2_11_stored_payloads_are_inert_on_the_inbox_and_the_thread(self):
        self._same_structure({"inbox": lambda tid: "/whatsapp/inbox",
                              "thread": lambda tid: f"/whatsapp/thread/{tid}"})

    def test_r2_12_values_read_back_exactly_once_and_spanish_is_literal(self):
        for name, p in {**PAYLOADS, "spanish": SPANISH}.items():
            tid = self._seed(p)
            try:
                with self.subTest(payload=name):
                    root = parse(self._get(f"/whatsapp/thread/{tid}"))
                    row = by_class(root, "wa-chat-row")[0]
                    self.assertEqual(row.attrs["data-display-name"], f"P{p}")
                    self.assertEqual(row.attrs["data-wa-id"], f"W{p}")
                    self.assertEqual(by_class(root, "wa-chat-name")[0].text(), f"P{p}")
                    self.assertEqual(by_class(root, "wa-thread-title")[0].text(), f"P{p}")
                    bubbles = [n.text() for n in by_class(root, "wa-msg-text")]
                    self.assertIn(f"T{p}", bubbles)
                    self.assertIn(f"Zona: Z{p} · Marca: B{p} · Año: Y{p}", bubbles)
                    self.assertEqual(by_class(root, "wa-msg-reaction")[0].text(), f"L{p}")
                    self.assertEqual(by_class(root, "wa-msg-quote-text")[0].text(), f"T{p}")
            finally:
                self._wipe()
        tid = self._seed(SPANISH)
        self.assertIn(f">P{SPANISH}<", self._get(f"/whatsapp/thread/{tid}"))

    def test_r2_12b_a_thread_id_in_the_path_is_inert(self):
        benign = shape(parse(self._get("/whatsapp/thread/texto")))
        for raw in ("%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E", "%22%20onfocus%3D%22alert(1)"):
            with self.subTest(raw=raw):
                self.assertEqual(shape(parse(self._get(f"/whatsapp/thread/{raw}"))), benign)

    def test_r2_12c_a_thread_without_messages_renders_its_own_branch_safely(self):
        def page(p):
            contact = WhatsAppContact(wa_id=f"W{p}", display_name=f"P{p}")
            self.db.add(contact)
            self.db.flush()
            thread = WhatsAppThread(contact_id=contact.id, last_message_at=NOW)
            self.db.add(thread)
            self.db.commit()
            try:
                return parse(self._get(f"/whatsapp/thread/{thread.id}"))
            finally:
                self._wipe()

        benign = shape(page(BENIGN))
        for name, p in PAYLOADS.items():
            with self.subTest(payload=name):
                root = page(p)
                self.assertEqual(shape(root), benign)
                self.assertEqual(by_class(root, "wa-contact-phone")[0].text(), f"W{p}")
                self.assertEqual(by_class(root, "wa-contact-name")[0].text(), f"P{p}")

    def test_r2_13_both_pickers_escape_customer_values(self):
        source = inspect.getsource(whatsapp_ui._render_whatsapp_shell)
        for sink in ("waEsc(lead.nombre || '-')", "waEsc(lead.apellido || '')",
                     "waEsc(lead.telefono || '-')",
                     'waEsc(row.getAttribute("data-display-name") || "-")',
                     'waEsc(row.getAttribute("data-wa-id") || "-")'):
            self.assertIn(sink, source)
        body = re.search(r"function waEsc\(v\)\{(.*?)\n\s*\}", source, re.S).group(1)
        for source_char, target in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"),
                                    ("'", "&#39;")):
            self.assertIn(f"/{source_char}/g", body)
            self.assertIn(target, body)


# ── the dead renderer ─────────────────────────────────────────────────────────

class DeadRenderer(unittest.TestCase):

    def test_r2_14_nothing_defines_imports_or_names_it(self):
        self.assertFalse(hasattr(kanban_view, "render_table_page"))
        this = Path(__file__).resolve()
        found = []
        for pattern in ("backend/**/*.py", "tests/**/*.py", "scripts/**/*.py", "meta/**/*.py"):
            for path in glob.glob(str(ROOT / pattern), recursive=True):
                if Path(path).resolve() == this:
                    continue
                tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
                for node in ast.walk(tree):
                    named = (getattr(node, "id", None), getattr(node, "attr", None),
                             getattr(node, "name", None), getattr(node, "value", None))
                    if any(isinstance(n, str) and "render_table_page" in n for n in named):
                        found.append(path)
        self.assertEqual(found, [])

    def test_r2_15_no_registered_route_uses_it_and_table_keeps_its_renderer(self):
        routes = {r.path: r for r in fastapi_app.routes if hasattr(r, "endpoint")}
        for path, route in routes.items():
            self.assertNotEqual(route.endpoint.__name__, "render_table_page", path)
            names = route.endpoint.__code__.co_names if hasattr(route.endpoint, "__code__") else ()
            self.assertNotIn("render_table_page", names, path)
        self.assertIn("render_revisions_table_page", routes["/table"].endpoint.__code__.co_names)


# ── storage ───────────────────────────────────────────────────────────────────

class Storage(_Routes):

    def test_r2_16_rendering_leaves_the_stored_text_as_written(self):
        p = PAYLOADS["ampersand"] + PAYLOADS["img_onerror"]
        tid = self._seed(p)
        for url in ("/kanban", "/whatsapp/inbox", f"/whatsapp/thread/{tid}"):
            self._get(url)
        self.db.expire_all()
        self.assertEqual(tuple(self.db.execute(text(
            "SELECT nombre, apellido, telefono FROM leads")).one()), (f"N{p}", f"A{p}", f"11{p}"))
        self.assertEqual(tuple(self.db.execute(text(
            "SELECT display_name, wa_id FROM whatsapp_contacts")).one()), (f"P{p}", f"W{p}"))
        self.assertEqual(self.db.execute(text(
            "SELECT text FROM whatsapp_messages WHERE wa_message_id='a'")).scalar(), f"T{p}")
        self.assertEqual(tuple(self.db.execute(text(
            "SELECT zone_group, zone_detail FROM viaticos_zones")).one()), (f"G{p}", f"D{p}"))


if __name__ == "__main__":
    unittest.main()
