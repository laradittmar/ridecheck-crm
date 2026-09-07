"""L4.7W4-F1 — an endpoint-backed Flow must be launched as one.

Wild W4: the Booking Flow was dispatched with `flow_action: "navigate"`. The message was
delivered and read, the customer opened it, and Meta never contacted the endpoint — 64
data-exchange requests that session, every one a health-check `ping`, not a single INIT. The
Flow could not render because nothing had asked the back end for anything.

The screen name was already right (`APPOINTMENT`). Only the launch mode was wrong, and the
shared helper defaulted it silently — which is the defect these tests hold shut.

BF-DX-01 booking uses data-exchange   BF-DX-09 INIT returns APPOINTMENT
BF-DX-02 initial screen APPOINTMENT   BF-DX-10 Flow open != booked
BF-DX-03 vehicle Flow stays navigate  BF-DX-11 confirm_booking is the sole writer
BF-DX-04 location Flow stays navigate BF-DX-12 path attribution
BF-DX-05 mode is explicit             BF-DX-13 acceptance regression
BF-DX-06 flow-first still dispatches  BF-DX-14 FAQ regression
BF-DX-07 no prose dump on success     BF-DX-15 vehicle/location regression
BF-DX-08 opening the Flow hits the endpoint
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from app.ui.whatsapp_ui import (  # noqa: E402
    FLOW_MODE_DATA_EXCHANGE,
    FLOW_MODE_NAVIGATE,
    _flow_launch_fields,
)

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")
UI_SOURCE = (ROOT / "backend" / "app" / "ui" / "whatsapp_ui.py").read_text(encoding="utf-8-sig")
DX_SOURCE = (ROOT / "backend" / "app" / "routes"
             / "flow_data_exchange.py").read_text(encoding="utf-8-sig")
BFS_SOURCE = (ROOT / "backend" / "app" / "services"
              / "booking_flow_service.py").read_text(encoding="utf-8-sig")


def flow_button_calls():
    """Every _send_flow_button call site, with its screen and declared mode."""
    out = []
    for node in ast.walk(ast.parse(CE_SOURCE)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_send_flow_button"):
            kw = {k.arg: ast.unparse(k.value) for k in node.keywords}
            out.append((node.lineno, kw.get("initial_screen"), kw.get("mode"),
                        kw.get("flow_id"), kw.get("path_id")))
    return out


class TestLaunchMode(unittest.TestCase):

    def test_bf_dx_05_the_mode_decides_the_payload_and_nothing_else(self):
        """BF-DX-05 — and an unknown mode is refused rather than defaulted."""
        navigate = _flow_launch_fields(FLOW_MODE_NAVIGATE, "VEHICLE_DETAILS")
        self.assertEqual(navigate["flow_action"], "navigate")
        self.assertEqual(navigate["flow_action_payload"], {"screen": "VEHICLE_DETAILS"})

        exchange = _flow_launch_fields(FLOW_MODE_DATA_EXCHANGE, "APPOINTMENT")
        self.assertEqual(exchange["flow_action"], "data_exchange")
        self.assertNotIn("flow_action_payload", exchange,
                         "the endpoint's INIT names the first screen; sending one "
                         "here would contradict it")

        for bad in (None, "", "NAVIGATE", "exchange", "data-exchange"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                _flow_launch_fields(bad, "APPOINTMENT")

    def test_the_helper_has_no_hardcoded_launch_action_left(self):
        code = UI_SOURCE
        self.assertNotIn('"flow_action": "navigate",\n                    '
                         '"flow_action_payload"', code,
                         "the launch fields must come from _flow_launch_fields")
        self.assertIn("_flow_launch_fields(mode, initial_screen)", code)


class TestBookingFlowDispatch(unittest.TestCase):

    def booking_call(self):
        calls = [c for c in flow_button_calls() if c[1] == "'APPOINTMENT'"]
        self.assertEqual(len(calls), 1, f"expected exactly one booking dispatch, got {calls}")
        return calls[0]

    def test_bf_dx_01_booking_flow_uses_data_exchange(self):
        """BF-DX-01 — the fix, asserted at the call site."""
        _, screen, mode, _flow, path = self.booking_call()
        self.assertEqual(mode, "FLOW_MODE_DATA_EXCHANGE")

    def test_bf_dx_02_booking_initial_screen_is_appointment(self):
        """BF-DX-02 — this was already correct in W4; it must stay correct."""
        _, screen, _mode, _flow, _path = self.booking_call()
        self.assertEqual(screen, "'APPOINTMENT'")

    def test_bf_dx_12_booking_flow_declares_the_booking_path(self):
        """BF-DX-12 — attribution is set at dispatch, not inferred later."""
        _, _screen, _mode, _flow, path = self.booking_call()
        self.assertEqual(path, "OutboundPathId.BOOKING_FLOW.value")

    def test_bf_dx_03_04_every_other_flow_stays_navigate(self):
        """BF-DX-03/04 — the endpoint-less Flows must not be converted."""
        others = [c for c in flow_button_calls() if c[1] != "'APPOINTMENT'"]
        self.assertGreaterEqual(len(others), 4, "vehicle, location and website Flows exist")
        for lineno, screen, mode, _flow, _path in others:
            self.assertNotEqual(mode, "FLOW_MODE_DATA_EXCHANGE",
                                f"line {lineno} screen={screen} must remain navigate")
        screens = {c[1] for c in others}
        self.assertIn("'VEHICLE_DETAILS'", screens)
        self.assertIn("'LOCATION_DETAILS'", screens)


class TestEndpointContract(unittest.TestCase):

    def test_bf_dx_08_09_init_is_implemented_and_returns_appointment(self):
        """BF-DX-08/09 — what Meta will now call, and what it gets back."""
        self.assertIn('if action == "INIT"', DX_SOURCE)
        self.assertIn("svc.handle_init(booking_token)", DX_SOURCE)
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "handle_init")
        self.assertIn("'screen': 'APPOINTMENT'", fn.replace('"', "'"))
        self.assertIn("_appointment_screen_data", fn)

    def test_the_endpoint_still_answers_ping_and_the_three_triggers(self):
        for marker in ('action == "ping"', "date_selected", "prepare_summary",
                       "confirm_booking"):
            self.assertIn(marker, DX_SOURCE, marker)

    def test_bf_dx_10_opening_the_flow_books_nothing(self):
        """BF-DX-10 — INIT and date_selected are read-only screens."""
        for name in ("handle_init", "handle_date_selected", "handle_prepare_summary"):
            fn = next(ast.unparse(n) for n in ast.walk(ast.parse(BFS_SOURCE))
                      if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertNotIn('status="booked"', fn.replace("'", '"'), name)

    def test_bf_dx_11_confirm_booking_is_the_sole_booking_writer(self):
        """BF-DX-11 — in the service AND in the engine."""
        writers = {n.name for n in ast.walk(ast.parse(BFS_SOURCE))
                   if isinstance(n, ast.FunctionDef)
                   and 'status="booked"' in ast.unparse(n).replace("'", '"')}
        self.assertTrue(writers <= {"handle_confirm_booking", "_create_booking"},
                        f"unexpected booking writer(s) in the service: {writers}")
        ce_writers = {n.name for n in ast.walk(ast.parse(CE_SOURCE))
                      if isinstance(n, ast.FunctionDef)
                      and 'status="booked"' in ast.unparse(n).replace("'", '"')}
        self.assertTrue(ce_writers <= {"_process_flow_response"}, ce_writers)


class TestNoRegression(unittest.TestCase):

    def test_bf_dx_06_07_flow_first_is_untouched(self):
        """BF-DX-06/07 — the W3-F1 dispatch and its no-prose-dump rule still stand."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_dispatch_booking_flow_for_day")
        self.assertIn("_send_booking_flow", fn)
        self.assertIn("_authorize_scheduling_progression", fn)
        for name in ("_handle_day_only_request", "_handle_period_request"):
            caller = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                          if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertIn("_dispatch_booking_flow_for_day", caller, name)
            self.assertIn("if flow_out is not None" if name == "_handle_day_only_request"
                          else "if _flow_out is not None", caller)

    def test_bf_dx_13_acceptance_path_untouched(self):
        self.assertIn("_semantic_acceptance_claims", CE_SOURCE)
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_authorize_acceptance")
        self.assertIn("self._semantic_acceptance_claims(state, texts)", fn)
        self.assertIn("authorize_quote_acceptance", fn)

    def test_bf_dx_14_faq_reconciliation_untouched(self):
        from app.services.conversation_engine import _FAQ_TOPIC_ANSWERS
        self.assertEqual(set(_FAQ_TOPIC_ANSWERS),
                         {"business_hours", "report", "presence", "payment", "service_scope"})
        self.assertIn("_topics_already_answered_this_cycle", CE_SOURCE)

    def test_bf_dx_15_vehicle_and_location_governance_untouched(self):
        from app.services.locality_resolver import LocationFragment
        from app.services.vehicle_catalog import fuzzy_lookup_vehicle
        self.assertEqual(fuzzy_lookup_vehicle(
            "Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?").outcome,
            "UNRESOLVED")
        self.assertIsNone(LocationFragment.from_evidence(
            "está", "INSPECTION_LOCATION", "t", "el auto está"))
        names = {n.name for n in ast.walk(ast.parse(CE_SOURCE))
                 if isinstance(n, ast.FunctionDef)}
        self.assertNotIn("_handle_fuzzy_confirm", names)


class TestPayloadShape(unittest.TestCase):
    """The bytes that actually go to Meta, for both kinds of Flow."""

    def send(self, mode, screen):
        captured = {}

        class _Resp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *a):
                return False
            def read(self_inner):
                return b'{"messages":[{"id":"wamid.TEST"}]}'

        def _fake_urlopen(req, timeout=None):
            import json as _json
            captured.update(_json.loads(req.data.decode("utf-8")))
            return _Resp()

        from app.ui import whatsapp_ui
        env = {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "1",
               "OUTBOUND_ENABLED": "true"}
        import os
        with patch.dict(os.environ, env, clear=False), \
             patch.object(whatsapp_ui, "get_settings",
                          return_value=SimpleNamespace(whatsapp_token="t",
                                                       whatsapp_phone_number_id="1")), \
             patch.object(whatsapp_ui.urlrequest, "urlopen", _fake_urlopen):
            whatsapp_ui._send_whatsapp_cloud_flow(
                to_wa_id="549110000", flow_id="F1", flow_token="tok",
                body_text="hola", initial_screen=screen, mode=mode)
        return captured["interactive"]["action"]["parameters"]

    def test_data_exchange_payload_has_no_screen(self):
        params = self.send(FLOW_MODE_DATA_EXCHANGE, "APPOINTMENT")
        self.assertEqual(params["flow_action"], "data_exchange")
        self.assertNotIn("flow_action_payload", params)
        self.assertEqual(params["flow_message_version"], "3")

    def test_navigate_payload_still_names_its_screen(self):
        params = self.send(FLOW_MODE_NAVIGATE, "VEHICLE_DETAILS")
        self.assertEqual(params["flow_action"], "navigate")
        self.assertEqual(params["flow_action_payload"], {"screen": "VEHICLE_DETAILS"})


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
