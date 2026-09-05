"""L4.7W1-F4 — the three blockers that stood between F3 and another Wild.

PREWILD-01 CE always owns the event      PREWILD-06 machine auth accepts n8n
PREWILD-02 n8n legacy branch unreachable PREWILD-07 human session accepted
PREWILD-03 no automated MANUAL_CRM       PREWILD-08 FAQ topics from semantics
PREWILD-04 unauthenticated CE rejected   PREWILD-09 deterministic answers only
PREWILD-05 unauthenticated send rejected PREWILD-10 payment answer is accurate
"""
from __future__ import annotations

import ast
import json
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

from app.schemas.conversation import HANDLED_ACTIONS  # noqa: E402
from app.services.conversation_engine import (  # noqa: E402
    _FAQ_PAYMENT_ANSWER,
    _FAQ_PRESENCE_ANSWER,
    _FAQ_REPORT_ANSWER,
    _FAQ_SCOPE_ANSWER,
    _FAQ_TOPIC_ANSWERS,
    ConversationEngine,
    _out,
)

LIVE_EXPORT = ROOT / "N8N workflows" / "RUNTIME_LIVE_EXPORT_2026-09-04.json"
WILD_2 = "¿Cómo trabajan ustedes? ¿Mandan un informe? ¿Deben estar presentes?"
WILD_3 = "¿Aceptan? ¿Debito?"


def engine(semantic_topics=()):
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = SimpleNamespace(semantic_same_turn_enabled=True)
    evidence = SimpleNamespace(faq_intents=tuple(
        SimpleNamespace(topic=t, value=t) for t in semantic_topics))
    eng._semantic_turn_evidence = lambda: (evidence if semantic_topics else None)
    return eng


class TestCEOwnership(unittest.TestCase):

    def test_prewild_01_blocked_dispatch_is_owned_by_ce(self):
        """PREWILD-01 — the kill switch is a CE decision, not an abdication."""
        self.assertIn("blocked_dispatch", HANDLED_ACTIONS)
        result = _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        self.assertTrue(result.handled, "n8n must not fall back to a second engine")
        self.assertFalse(result.ok, "ok=False still reports that nothing was sent")

    def test_no_lead_ownership_is_deliberately_unchanged(self):
        """The certified M21.2.8 rationale stands; only blocked_dispatch moved."""
        self.assertNotIn("no_lead", HANDLED_ACTIONS)
        self.assertFalse(_out("no_lead").handled)

    def test_prewild_02_the_live_legacy_branch_is_unreachable_from_ce(self):
        """PREWILD-02 — no CE outcome can reach n8n's legacy chain any more."""
        ce = (ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text(
            encoding="utf-8-sig")
        emitted = set()
        for node in ast.walk(ast.parse(ce)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_out" and node.args
                    and isinstance(node.args[0], ast.Constant)):
                emitted.add(node.args[0].value)
        unhandled = emitted - set(HANDLED_ACTIONS)
        self.assertEqual(unhandled, {"no_lead"},
                         f"CE outcomes that still hand the turn to n8n: {sorted(unhandled)}")

    def test_prewild_02b_live_export_shows_the_legacy_chain_behind_handled_false(self):
        """The export records WHY this mattered: the false branch is a booking writer."""
        self.assertTrue(LIVE_EXPORT.exists())
        doc = json.loads(LIVE_EXPORT.read_text(encoding="utf-8"))
        conns = doc["connections"]
        flow_gate = conns.get("IF - Engine Handled? (Flow M18)", {}).get("main", [])
        self.assertEqual(flow_gate[0], [], "handled=true branch must lead nowhere")
        self.assertTrue(flow_gate[1], "handled=false branch is the legacy chain")


class TestOutboundAttribution(unittest.TestCase):

    def test_prewild_03_sends_are_attributed_by_caller_not_by_label(self):
        """PREWILD-03 — automated traffic must not wear a human's name."""
        src = (ROOT / "backend" / "app" / "api" / "whatsapp.py").read_text(encoding="utf-8-sig")
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_caller_path_id")
        self.assertIn("machine_call", fn)
        self.assertIn("CE_FLOW", fn)
        self.assertIn("MANUAL_CRM", fn)
        from app.api.whatsapp import _caller_path_id
        machine = SimpleNamespace(state=SimpleNamespace(machine_call=True))
        human = SimpleNamespace(state=SimpleNamespace(machine_call=False))
        self.assertEqual(_caller_path_id(machine), "CE_FLOW")
        self.assertEqual(_caller_path_id(human), "MANUAL_CRM")
        self.assertEqual(_caller_path_id(None), "MANUAL_CRM")


class TestInternalApiBoundary(unittest.TestCase):

    def setUp(self):
        from app import main
        self.main = main

    def test_prewild_04_and_05_machine_paths_are_classified(self):
        """PREWILD-04/05 — CE invocation and sends are machine-class, not public."""
        self.assertTrue(self.main._is_machine_api_path("/api/conversation/handle"))
        self.assertTrue(self.main._is_machine_api_path("/api/whatsapp/thread/1/send-text"))
        self.assertTrue(self.main._is_machine_api_path("/api/revisions"))
        self.assertTrue(self.main._is_machine_api_path("/leads/1"))
        self.assertTrue(self.main._is_machine_api_path("/api/settings/ai-enabled"))
        self.assertTrue(self.main._is_machine_api_path("/api/excluded-phones/check/1"))
        # genuinely public integration surface stays public
        self.assertTrue(self.main._is_public_path("/integrations/whatsapp/webhook"))
        self.assertTrue(self.main._is_public_path(
            "/integrations/whatsapp/flows/booking/data-exchange"))
        self.assertFalse(self.main._is_machine_api_path("/integrations/whatsapp/webhook"))

    def test_prewild_06_machine_credential_is_compared_safely_and_needs_config(self):
        """PREWILD-06 — no secret configured means no machine caller is admitted."""
        request = SimpleNamespace(headers={"x-internal-auth": "anything"})
        with patch.object(self.main, "get_settings",
                          return_value=SimpleNamespace(internal_api_secret="",
                                                       internal_api_auth_enabled=True)):
            self.assertFalse(self.main._machine_auth_ok(request))
            self.assertFalse(self.main._internal_auth_enforced())
        with patch.object(self.main, "get_settings",
                          return_value=SimpleNamespace(internal_api_secret="s3cret",
                                                       internal_api_auth_enabled=True)):
            self.assertTrue(self.main._internal_auth_enforced())
            self.assertTrue(self.main._machine_auth_ok(
                SimpleNamespace(headers={"x-internal-auth": "s3cret"})))
            self.assertFalse(self.main._machine_auth_ok(
                SimpleNamespace(headers={"x-internal-auth": "wrong"})))
            self.assertFalse(self.main._machine_auth_ok(SimpleNamespace(headers={})))

    def test_prewild_07_enforcement_is_off_until_deliberately_enabled(self):
        """A configured secret alone does not switch enforcement on."""
        with patch.object(self.main, "get_settings",
                          return_value=SimpleNamespace(internal_api_secret="s3cret",
                                                       internal_api_auth_enabled=False)):
            self.assertFalse(self.main._internal_auth_enforced())
        from app.settings import Settings
        self.assertFalse(Settings().internal_api_auth_enabled)
        self.assertEqual(Settings().internal_api_secret, "")


class TestFaqCutover(unittest.TestCase):

    def test_prewild_08_semantic_topics_are_detected_where_phrases_miss(self):
        """PREWILD-08 — the four real Wild questions, none of which match a phrase set."""
        from app.services import conversation_engine as ce
        burst = f"{WILD_2} {WILD_3}"
        norm = ce._norm_lower(burst)
        for phrases in (ce._REPORT_FAQ_DETECTION, ce._PRESENCE_FAQ_DETECTION,
                        ce._PAYMENT_FAQ_DETECTION):
            self.assertFalse(any(p in norm for p in phrases),
                             "phrase detectors still miss the real wording")
        eng = engine(semantic_topics=("service_scope", "report", "presence", "payment"))
        topics = eng._faq_topics_for_burst(burst)
        self.assertEqual(topics, {"service_scope", "report", "presence", "payment"})

    def test_prewild_09_the_semantic_layer_supplies_no_answers(self):
        """PREWILD-09 — every answer is a deterministic business constant."""
        eng = engine(semantic_topics=("report", "payment"))
        supplement = eng._build_faq_supplement("cualquier cosa")
        self.assertIn(_FAQ_REPORT_ANSWER, supplement)
        self.assertIn(_FAQ_PAYMENT_ANSWER, supplement)
        # topics the interpreter can emit but for which we have no business truth are
        # deliberately absent — inventing one is what this layer exists to prevent
        self.assertNotIn("coverage", _FAQ_TOPIC_ANSWERS)
        self.assertNotIn("duration", _FAQ_TOPIC_ANSWERS)
        unknown = engine(semantic_topics=("coverage", "duration"))
        self.assertEqual(unknown._faq_topics_for_burst("x"), set())

    def test_prewild_10_the_payment_answer_answers_the_question_asked(self):
        """PREWILD-10 — "¿Aceptan? ¿Debito?" needs the "no", not just the list."""
        for accepted in ("efectivo", "transferencia", "Mercado Pago"):
            self.assertIn(accepted.lower(), _FAQ_PAYMENT_ANSWER.lower())
        self.assertIn("débito", _FAQ_PAYMENT_ANSWER.lower())
        self.assertIn("no estamos trabajando", _FAQ_PAYMENT_ANSWER.lower())

    def test_faq_answers_coexist_and_do_not_duplicate(self):
        """A topic already covered by the primary reply is not appended twice."""
        eng = engine(semantic_topics=("report", "presence"))
        primary = "Al finalizar la revisión te enviamos un informe detallado."
        out = eng._compose_secondary_answers(primary, WILD_2)
        self.assertEqual(out.count("informe detallado"), 1, "no duplicate report answer")
        self.assertIn(_FAQ_PRESENCE_ANSWER, out, "the unanswered topic is still added")

    def test_scope_answer_exists_and_is_business_truth(self):
        self.assertIn("pre-compra", _FAQ_SCOPE_ANSWER)
        self.assertIn("informe", _FAQ_SCOPE_ANSWER)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
