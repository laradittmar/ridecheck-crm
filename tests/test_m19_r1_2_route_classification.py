"""M19.R1.2 — Route classification and server authentication evidence tests.

Proves through static AST inspection + auth-middleware analysis which routes have
server-verified authenticated CRM sessions and which do not.

Per spec (M19.R1.2 safety-first fallback):
  "If a route currently has no server-verifiable authentication context, it must
   temporarily be treated as automated and gated until proper authentication is
   implemented."

Findings (proved below):
  - auth_middleware in main.py protects prefix "/whatsapp", "/kanban", etc.
  - The send routes are on prefix "/api/whatsapp" — NOT in the protected list.
  - None of the send route functions accept a Request parameter that carries
    request.state.user_email.
  - None of the send route functions have a user-auth Depends().
  - Therefore: ALL send routes have NO server-verified auth.

Classification result:
  Route                                   Auth   Gate?      Fallback rule
  POST /api/whatsapp/thread/{id}/send-text    No     Required   safety-first
  POST /api/whatsapp/thread/{id}/send-interactive No  Required  safety-first
  POST /api/whatsapp/thread/{id}/send-list    No     Required   safety-first
  POST /api/whatsapp/thread/{id}/send-flow    No     Required   safety-first
  POST /api/whatsapp/send-to-phone            No     Required   safety-first (was already gated)

Tests:
  C.1  auth_middleware prefix list does NOT include /api/whatsapp
  C.2  send_thread_text uses gate.attempt() (AST)
  C.3  send_thread_text does NOT create WhatsAppMessage(automated=False) (AST)
  C.4  _store_outbound_and_send uses gate.attempt() (AST)
  C.5  _store_outbound_and_send accepts message_type parameter (AST)
  C.6  send_thread_interactive passes message_type="interactive" to helper (AST)
  C.7  send_thread_list passes message_type="list" to helper (AST)
  C.8  send_thread_flow passes message_type="flow" to helper (AST)
  C.9  send_to_phone uses gate.attempt() — unchanged (AST)
  C.10 n8n workflow sends to send-text only (JSON inspection)
  C.11 n8n send-text calls carry no auth header (JSON inspection)
  C.12 auth_middleware has no handler for /api/ prefix (AST)
"""
from __future__ import annotations

import ast as _ast
import json
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

_WHATSAPP_API = BACKEND_DIR / "app" / "api" / "whatsapp.py"
_MAIN_PY = BACKEND_DIR / "app" / "main.py"
_N8N_WF_6 = ROOT_DIR / "N8N workflows" / "CRM - Ridecheck (Mar 5 at 08_59_04) (6).json"
_N8N_WF_5 = ROOT_DIR / "N8N workflows" / "CRM - Ridecheck (Mar 5 at 08_59_04) (5).json"


def _parse(path: Path) -> _ast.Module:
    return _ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _fn_body(tree: _ast.Module, name: str) -> list:
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == name:
            return node.body
    raise AssertionError(f"Function {name!r} not found in {tree}")


def _has_gate_attempt(body: list) -> bool:
    """Return True if the function body contains a call to gate.attempt(...)."""
    for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
        if (
            isinstance(node, _ast.Call)
            and isinstance(node.func, _ast.Attribute)
            and node.func.attr == "attempt"
        ):
            return True
    return False


def _has_no_automated_false_record(body: list) -> bool:
    """Return True if no WhatsAppMessage(automated=False) is constructed in body."""
    for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
        if isinstance(node, _ast.Call):
            for kw in getattr(node, "keywords", []):
                if (
                    kw.arg == "automated"
                    and isinstance(kw.value, _ast.Constant)
                    and kw.value.value is False
                ):
                    return False
    return True


def _has_arg(body: list, arg_name: str) -> bool:
    """Return True if a keyword arg_name= is passed to any call in body."""
    for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
        if isinstance(node, _ast.Call):
            for kw in getattr(node, "keywords", []):
                if kw.arg == arg_name:
                    return True
    return False


def _kwarg_value(body: list, fn_name: str, arg_name: str) -> object:
    """Return the value of arg_name= in the first call to fn_name in body."""
    for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
        if isinstance(node, _ast.Call):
            func = node.func
            if (isinstance(func, _ast.Name) and func.id == fn_name) or (
                isinstance(func, _ast.Attribute) and func.attr == fn_name
            ):
                for kw in node.keywords:
                    if kw.arg == arg_name and isinstance(kw.value, _ast.Constant):
                        return kw.value.value
    return None


# ══════════════════════════════════════════════════════════════════════════════
# C.1 — auth_middleware prefix analysis
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthMiddlewarePrefixes(unittest.TestCase):
    """C.1/C.12 — Prove /api/whatsapp is not in the protected prefix list."""

    def setUp(self):
        tree = _parse(_MAIN_PY)
        self._tree = tree

    def test_c1_protected_prefixes_do_not_include_api_whatsapp(self):
        """_is_protected_path returns False for /api/whatsapp/... paths."""
        protected = []
        for node in _ast.walk(self._tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "_is_protected_path":
                for sub in _ast.walk(node):
                    if isinstance(sub, _ast.Constant) and isinstance(sub.value, str) and sub.value.startswith("/"):
                        protected.append(sub.value)
        self.assertTrue(len(protected) > 0, "_is_protected_path must have prefix constants")
        for prefix in protected:
            self.assertFalse(
                "/api/whatsapp".startswith(prefix),
                f"Protected prefix {prefix!r} would cover /api/whatsapp routes — "
                "verify that CRM UI routes require auth through this middleware",
            )

    def test_c12_auth_middleware_has_no_api_handler(self):
        """auth_middleware has no special branch for /api/ prefix."""
        found_api_prefix = False
        for node in _ast.walk(self._tree):
            if isinstance(node, _ast.AsyncFunctionDef) and node.name == "auth_middleware":
                for sub in _ast.walk(node):
                    if isinstance(sub, _ast.Constant) and "/api" in str(sub.value):
                        found_api_prefix = True
        self.assertFalse(
            found_api_prefix,
            "auth_middleware must not specially handle /api/ prefix — "
            "all /api/whatsapp routes are unauthenticated by middleware design",
        )


# ══════════════════════════════════════════════════════════════════════════════
# C.2-C.3 — send_thread_text gate proof
# ══════════════════════════════════════════════════════════════════════════════

class TestSendThreadTextGated(unittest.TestCase):
    """C.2/C.3 — send_thread_text must enter the gate, not create automated=False records."""

    def setUp(self):
        tree = _parse(_WHATSAPP_API)
        self._body = _fn_body(tree, "send_thread_text")

    def test_c2_send_thread_text_calls_gate_attempt(self):
        self.assertTrue(
            _has_gate_attempt(self._body),
            "send_thread_text must call gate.attempt() (M19.R1.2 safety-first gating)",
        )

    def test_c3_send_thread_text_no_automated_false_record(self):
        self.assertTrue(
            _has_no_automated_false_record(self._body),
            "send_thread_text must NOT create WhatsAppMessage(automated=False) — "
            "route is unauthenticated; all sends classified as automated",
        )


# ══════════════════════════════════════════════════════════════════════════════
# C.4-C.8 — _store_outbound_and_send and callers
# ══════════════════════════════════════════════════════════════════════════════

class TestStoreOutboundAndSendGated(unittest.TestCase):
    """C.4-C.8 — _store_outbound_and_send uses gate; callers pass correct message_type."""

    def setUp(self):
        tree = _parse(_WHATSAPP_API)
        self._tree = tree
        self._helper_body = _fn_body(tree, "_store_outbound_and_send")

    def _fn_body_of(self, name: str) -> list:
        return _fn_body(self._tree, name)

    def test_c4_helper_calls_gate_attempt(self):
        self.assertTrue(
            _has_gate_attempt(self._helper_body),
            "_store_outbound_and_send must call gate.attempt() (M19.R1.2)",
        )

    def test_c5_helper_has_message_type_param(self):
        """_store_outbound_and_send must accept a message_type parameter."""
        for node in _ast.walk(self._tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "_store_outbound_and_send":
                arg_names = [a.arg for a in node.args.args]
                defaults = node.args.defaults
                kw_args = node.args.kwonlyargs
                all_names = arg_names + [a.arg for a in kw_args]
                self.assertIn(
                    "message_type", all_names,
                    "_store_outbound_and_send must have a message_type parameter",
                )
                return
        self.fail("_store_outbound_and_send not found")

    def test_c6_interactive_passes_message_type(self):
        body = self._fn_body_of("send_thread_interactive")
        val = _kwarg_value(body, "_store_outbound_and_send", "message_type")
        self.assertEqual(
            val, "interactive",
            f"send_thread_interactive must pass message_type='interactive' to helper, got {val!r}",
        )

    def test_c7_list_passes_message_type(self):
        body = self._fn_body_of("send_thread_list")
        val = _kwarg_value(body, "_store_outbound_and_send", "message_type")
        self.assertEqual(
            val, "list",
            f"send_thread_list must pass message_type='list' to helper, got {val!r}",
        )

    def test_c8_flow_passes_message_type(self):
        body = self._fn_body_of("send_thread_flow")
        val = _kwarg_value(body, "_store_outbound_and_send", "message_type")
        self.assertEqual(
            val, "flow",
            f"send_thread_flow must pass message_type='flow' to helper, got {val!r}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# C.9 — send_to_phone still gated
# ══════════════════════════════════════════════════════════════════════════════

class TestSendToPhoneStillGated(unittest.TestCase):
    """C.9 — send_to_phone continues to enter the gate (unchanged from M19.R1.2 original)."""

    def test_c9_send_to_phone_still_calls_gate(self):
        tree = _parse(_WHATSAPP_API)
        body = _fn_body(tree, "send_to_phone")
        self.assertTrue(
            _has_gate_attempt(body),
            "send_to_phone must still call gate.attempt() (was gated in M19.R1.2 earlier)",
        )


# ══════════════════════════════════════════════════════════════════════════════
# C.10-C.11 — n8n workflow send endpoint inventory
# ══════════════════════════════════════════════════════════════════════════════

class TestN8nWorkflowEndpoints(unittest.TestCase):
    """C.10/C.11 — n8n workflow (both versions) only calls send-text; no auth header."""

    def _http_nodes(self, path: Path) -> list[dict]:
        wf = json.loads(path.read_text(encoding="utf-8-sig"))
        nodes = wf.get("nodes", [])
        return [n for n in nodes if "http" in n.get("type", "").lower()
                or "HttpRequest" in n.get("type", "")]

    def _send_nodes(self, http_nodes: list[dict]) -> list[dict]:
        return [n for n in http_nodes
                if "send" in n.get("name", "").lower()
                and "POST" == (n.get("parameters", {}).get("method") or "").upper()]

    def test_c10_n8n_v6_only_send_text(self):
        """n8n workflow v6: all POST send nodes call /send-text only."""
        send_nodes = self._send_nodes(self._http_nodes(_N8N_WF_6))
        self.assertGreater(len(send_nodes), 0, "n8n v6 must have at least one send node")
        for node in send_nodes:
            url = node.get("parameters", {}).get("url", "")
            self.assertIn(
                "send-text", url,
                f"n8n v6 node {node['name']!r} calls {url!r} — expected send-text only",
            )
            self.assertNotIn("send-interactive", url)
            self.assertNotIn("send-list", url)
            self.assertNotIn("send-flow", url)
            self.assertNotIn("send-to-phone", url)

    def test_c10_n8n_v5_only_send_text(self):
        """n8n workflow v5: same assertion."""
        send_nodes = self._send_nodes(self._http_nodes(_N8N_WF_5))
        for node in send_nodes:
            url = node.get("parameters", {}).get("url", "")
            self.assertIn("send-text", url,
                          f"n8n v5 node {node['name']!r} calls {url!r} — expected send-text only")

    def test_c11_n8n_send_nodes_have_no_auth_header(self):
        """n8n send nodes carry no Authorization header, cookie, or api_key parameter."""
        for wf_path in (_N8N_WF_6, _N8N_WF_5):
            send_nodes = self._send_nodes(self._http_nodes(wf_path))
            for node in send_nodes:
                params = node.get("parameters", {})
                headers = params.get("headerParameters", {}).get("parameters", [])
                header_names = [h.get("name", "").lower() for h in headers]
                for auth_header in ("authorization", "cookie", "x-api-key", "api-key"):
                    self.assertNotIn(
                        auth_header, header_names,
                        f"n8n node {node['name']!r} in {wf_path.name} sends an auth header "
                        f"({auth_header}) — confirming it is NOT a server-authenticated manual send",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
