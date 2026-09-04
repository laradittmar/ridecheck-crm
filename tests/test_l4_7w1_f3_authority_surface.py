"""L4.7W1-F3 — one way to send, one way to write, one way to accept.

The independent audits found many overlapping authority surfaces. These tests hold the
invariants that closed the BLOCKER ones, and they are written as *architectural*
assertions (AST over the repository) rather than as checks on individual call sites, so a
new bypass fails the suite instead of quietly joining the surface.

AUTH-01 no outbound record outside the gate   AUTH-06 AI cannot grant acceptance
AUTH-02 the UI sender is gated + attributed   AUTH-07 acceptance sites are governed
AUTH-03 every send carries a path_id          AUTH-08 F2 fuzzy invariants preserved
AUTH-04 canonical vehicle writes go via C2     AUTH-09 booking stays transactional
AUTH-05 canonical location writes go via C2    AUTH-10 live n8n provenance is recorded
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

BACKEND = ROOT / "backend" / "app"
GATE_MODULE = "outbound_safety_gate.py"


def py_files():
    return [p for p in BACKEND.rglob("*.py") if "__pycache__" not in p.parts]


def executable_code(path: pathlib.Path) -> str:
    """Strip docstrings: a comment describing a bypass is not a bypass."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                # A class or function whose entire body IS the docstring still needs a
                # body, or the stripped tree will not parse.
                body[0] = ast.Pass() if len(body) == 1 else body[0]
                if len(body) > 1:
                    body.pop(0)
    return ast.unparse(ast.fix_missing_locations(tree))


class TestOutboundAuthority(unittest.TestCase):

    def test_auth_01_no_outbound_record_is_constructed_outside_the_gate(self):
        """AUTH-01 — the repo-wide invariant, on executable code only."""
        offenders = []
        for path in py_files():
            if path.name == GATE_MODULE:
                continue
            code = executable_code(path)
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "WhatsAppMessage"):
                    continue
                for kw in node.keywords:
                    if (kw.arg == "direction" and isinstance(kw.value, ast.Constant)
                            and kw.value.value == "out"):
                        offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(sorted(set(offenders)), [],
                         f"outbound records built outside OutboundSafetyGate: {offenders}")

    def test_auth_02_the_ui_sender_is_gated_and_attributed(self):
        """AUTH-02 — the human CRM send is a gate client, not a parallel authority."""
        source = (BACKEND / "ui" / "whatsapp_ui.py").read_text(encoding="utf-8-sig")
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef) and n.name == "whatsapp_thread_send")
        self.assertIn("OutboundSafetyGate(db)", fn)
        self.assertIn("gate.attempt(", fn)
        self.assertIn("OutboundPathId.MANUAL_CRM.value", fn)
        self.assertIn("BLOCKED_KILL_SWITCH", fn)
        self.assertIn("gate.mark_sent(", fn)
        self.assertNotIn("db.add(outbound)", fn)

    def test_auth_03_every_gate_attempt_declares_a_path_id(self):
        """AUTH-03 — an unattributed send is blocked at step -1; none may be written."""
        offenders = []
        for path in py_files():
            if path.name == GATE_MODULE:
                continue
            tree = ast.parse(executable_code(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "attempt"):
                    continue
                if not any(kw.arg == "path_id" for kw in node.keywords):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [], f"gate.attempt without path_id: {offenders}")


class TestCanonicalWrites(unittest.TestCase):

    ALLOWED_WRITERS = {
        "_apply_vehicle_identity", "_apply_inspection_zone",      # C2 chokepoints
        "ui_revision_latest_update",                              # authenticated human CRM
    }
    FIELDS = {"marca", "modelo", "tipo_vehiculo", "anio", "zone_group", "zone_detail"}

    def test_auth_04_and_05_canonical_identity_fields_have_one_writer_each(self):
        """AUTH-04/05 — automated vehicle and location writes go through C2."""
        offenders = []
        for path in py_files():
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef) or fn.name in self.ALLOWED_WRITERS:
                    continue
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Assign):
                        continue
                    for target in node.targets:
                        if (isinstance(target, ast.Attribute) and target.attr in self.FIELDS
                                and isinstance(target.value, ast.Name)
                                and target.value.id in ("target", "candidate", "cand", "focus")):
                            offenders.append(
                                f"{path.relative_to(ROOT)}:{node.lineno} in {fn.name}")
        self.assertEqual(offenders, [],
                         f"canonical identity written outside the C2 chokepoints: {offenders}")


class TestAcceptanceAuthority(unittest.TestCase):

    def test_auth_06_the_ai_cannot_grant_acceptance(self):
        """AUTH-06 — an AI-proposed ACEPTADO passes the C3B authorizer, or is dropped."""
        source = (BACKEND / "services" / "conversation_engine.py").read_text(encoding="utf-8-sig")
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef) and n.name == "_process_text")
        self.assertIn("new_flag == 'ACEPTADO'", fn.replace('"', "'"))
        self.assertIn("_authorize_acceptance(ctx, state, ai_input_messages)", fn)
        # the guard must sit between the membership check and the assignment
        idx_member = fn.index("_ALLOWED_FLAGS")
        idx_guard = fn.index("L4.7W1-F3 AI ACEPTADO blocked")
        idx_assign = fn.index("lead.flag = new_flag")
        self.assertLess(idx_member, idx_guard)
        self.assertLess(idx_guard, idx_assign)

    def test_auth_07_every_automated_aceptado_site_is_governed(self):
        """AUTH-07 — enumerate the ACEPTADO writers and classify every one."""
        source = (BACKEND / "services" / "conversation_engine.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        sites = {}
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Attribute) and t.attr == "flag"
                                for t in node.targets)
                        and "ACEPTADO" in ast.unparse(node)):
                    sites.setdefault(fn.name, 0)
                    sites[fn.name] += 1
        governed = {
            "_process_flow_response",        # transactional Booking Flow (exception B)
            "_handle_quoted_acceptance",     # behind _authorize_acceptance (C3B)
            "_handle_scheduling_escalation",  # behind _progression_allowed (C3B)
            "_process_text",                 # behind _progression_allowed / C3B guard
        }
        self.assertTrue(set(sites) <= governed,
                        f"ungoverned ACEPTADO writer(s): {sorted(set(sites) - governed)}")


class TestPreservedInvariants(unittest.TestCase):

    def test_auth_08_f2_fuzzy_invariants_are_untouched(self):
        """AUTH-08 — F3 must not reopen F2."""
        from app.services.vehicle_catalog import (extract_vehicle_fragments,
                                                  fuzzy_lookup_vehicle)
        self.assertEqual(extract_vehicle_fragments("Hola, buen día. Bueno, ¿cómo andás?"), ())
        self.assertEqual(
            fuzzy_lookup_vehicle("Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?")
            .outcome, "UNRESOLVED")
        self.assertEqual(fuzzy_lookup_vehicle("toyota corola").outcome, "AUTO_ACCEPT")
        ce = (BACKEND / "services" / "conversation_engine.py").read_text(encoding="utf-8-sig")
        names = {n.name for n in ast.walk(ast.parse(ce)) if isinstance(n, ast.FunctionDef)}
        self.assertNotIn("_handle_fuzzy_confirm", names)

    def test_auth_09_booking_remains_one_transactional_writer(self):
        """AUTH-09 — status="booked" is still written from the Flow path alone."""
        ce = (BACKEND / "services" / "conversation_engine.py").read_text(encoding="utf-8-sig")
        writers = set()
        for fn in ast.walk(ast.parse(ce)):
            if isinstance(fn, ast.FunctionDef) and 'status="booked"' in ast.unparse(fn).replace(
                    "'", '"'):
                writers.add(fn.name)
        self.assertTrue(writers <= {"_process_flow_response"}, f"booking writers: {writers}")

    def test_auth_10_live_n8n_provenance_is_recorded(self):
        """AUTH-10 — the runtime workflow is exported, and the divergence is visible."""
        live = ROOT / "N8N workflows" / "RUNTIME_LIVE_EXPORT_2026-09-04.json"
        self.assertTrue(live.exists(), "the live workflow export is committed")
        doc = json.loads(live.read_text(encoding="utf-8"))
        self.assertTrue(doc["active"])
        self.assertGreater(len(doc["nodes"]), 0)
        blob = json.dumps(doc)
        self.assertIn("/api/conversation/handle", blob, "the live path calls CE")
        # every live sender is a gate client, not a direct Meta call
        for node in doc["nodes"]:
            url = str((node.get("parameters") or {}).get("url") or "")
            if "graph.facebook.com" in url:
                self.fail(f"live workflow sends to Meta directly: {node['name']}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
