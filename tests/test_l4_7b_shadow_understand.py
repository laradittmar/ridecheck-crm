"""L4.7B — shadow UNDERSTAND pass: isolation, position and evidence quality.

The shadow interpreter proposes TurnEvidence for every burst; it must be incapable of
changing anything. These tests use a stub transport (no network, no cost) so they assert
the *contract*: one call per burst, before every deterministic gate, isolated on failure,
no canonical mutation, no outbound effect, append-only recording.

SHADOW-01  one semantic call maximum per burst
SHADOW-02  runs before the existing deterministic early returns
SHADOW-03  failure does not affect CE runtime behaviour
SHADOW-04  produced TurnEvidence validates as turn-evidence/1.0
SHADOW-05  no canonical state mutation
SHADOW-06  no DB business mutation
SHADOW-07  no outbound effect
SHADOW-08  location roles preserved
SHADOW-09  ordered scheduling preserved
SHADOW-10  ambiguity preserved
SHADOW-11  conflict preserved
SHADOW-12  FAQ + vehicle evidence coexist
SHADOW-13  owner REAL-001…004 evaluable
SHADOW-14  failed Wild cases evaluable
SHADOW-15  shadow record append-only / auditable
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import tempfile
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

from app.schemas.turn_evidence import (  # noqa: E402
    SCHEMA_VERSION,
    AcceptanceSignal,
    BurstReconstruction,
    EvidenceStatus,
    LocationRole,
    SchedulingPriority,
    TurnEvidence,
)
from app.services.semantic_interpreter import (  # noqa: E402
    PROMPT_VERSION,
    SemanticTurnInterpreter,
)
from app.services.shadow_recorder import build_record, read_records, record_shadow  # noqa: E402
from semantic_corpus.evaluation import evaluate_case, load_corpus  # noqa: E402
from semantic_corpus.corpus_mapping import turn_evidence_to_harness_items  # noqa: E402

CORPUS = {c["id"]: c for c in load_corpus()}


# ── stub transport ────────────────────────────────────────────────────────────

class StubTransport:
    """Returns a canned model payload and counts calls. No network."""

    def __init__(self, payload: dict | str, usage: dict | None = None):
        self.payload = payload
        self.usage = usage or {"prompt_tokens": 900, "completion_tokens": 200,
                               "total_tokens": 1100}
        self.calls: list[list[dict]] = []

    def __call__(self, messages, model):
        self.calls.append(messages)
        content = (self.payload if isinstance(self.payload, str)
                   else json.dumps(self.payload, ensure_ascii=False))
        return content, self.usage


def _settings(**kw):
    base = dict(openai_api_key="sk-stub", openai_chat_model="gpt-4o-mini",
                shadow_understand_enabled=True, shadow_evidence_path=None)
    base.update(kw)
    return SimpleNamespace(**base)


WILD_B_LOCATION_PAYLOAD = {
    "service_intents": [],
    "vehicles": [],
    "locations": [
        {"locality": "Berazategui", "role": "INSPECTION_LOCATION", "status": "CONFIRMED"},
        {"locality": "Tigre", "role": "CUSTOMER_ORIGIN", "status": "CONFIRMED"},
    ],
    "faq_topics": [], "acceptance": None, "scheduling_requests": [],
    "corrections": [], "identity": [], "handoff": None,
    "ambiguities": [], "conflicts": [], "notes": [],
}

WILD_A_SCHEDULING_PAYLOAD = {
    "service_intents": [], "vehicles": [], "locations": [], "faq_topics": [],
    "acceptance": None,
    "scheduling_requests": [
        {"priority": "PRIMARY", "day_expression": "TOMORROW", "time": "15:00",
         "flexible_time": False, "rank": 1, "status": "CONFIRMED"},
        {"priority": "FALLBACK", "day_expression": "THURSDAY", "time": None,
         "flexible_time": True, "rank": 2, "status": "CONFIRMED"},
    ],
    "corrections": [], "identity": [], "handoff": None,
    "ambiguities": [], "conflicts": [], "notes": [],
}

WILD_B_VEHICLE_PAYLOAD = {
    "service_intents": [{"kind": "INSPECTION", "value": "PREPURCHASE_INSPECTION",
                         "status": "CONFIRMED", "reason": None}],
    "vehicles": [{"make": "Peugeot", "model": "2008", "year": 2014,
                  "category_suggestion": "SUV_4X4_DEPORTIVO", "is_superseded": False,
                  "status": "CONFIRMED", "alternatives": [], "reason": None}],
    "locations": [], "faq_topics": ["report", "presence", "payment"],
    "acceptance": None, "scheduling_requests": [], "corrections": [], "identity": [],
    "handoff": None, "ambiguities": [], "conflicts": [], "notes": [],
}


# ── interpreter contract ──────────────────────────────────────────────────────

class TestInterpreterContract(unittest.TestCase):

    def test_shadow_01_one_call_per_burst(self):
        transport = StubTransport(WILD_B_VEHICLE_PAYLOAD)
        interp = SemanticTurnInterpreter(_settings(), transport=transport)
        interp.interpret(["Hola", "para revisar un 2008 del 2014", "¿aceptan débito?"])
        self.assertEqual(len(transport.calls), 1, "exactly one model call per burst")

    def test_shadow_04_output_validates_as_schema_v1(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport(WILD_B_VEHICLE_PAYLOAD))
        result = interp.interpret(["para revisar un 2008 del 2014"])
        self.assertTrue(result.ok)
        self.assertIsInstance(result.evidence, TurnEvidence)
        self.assertEqual(result.evidence.schema_version, SCHEMA_VERSION)
        restored = TurnEvidence.from_json(result.evidence.to_canonical_json())
        self.assertEqual(restored.to_canonical_json(), result.evidence.to_canonical_json())

    def test_telemetry_recorded(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport(WILD_B_VEHICLE_PAYLOAD))
        result = interp.interpret(["hola"])
        self.assertEqual(result.model, "gpt-4o-mini")
        self.assertEqual(result.prompt_version, PROMPT_VERSION)
        self.assertEqual(result.total_tokens, 1100)
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_shadow_03_failure_is_isolated(self):
        def boom(messages, model):
            raise RuntimeError("openai http 500")
        interp = SemanticTurnInterpreter(_settings(), transport=boom)
        result = interp.interpret(["hola"])
        self.assertFalse(result.ok)
        self.assertIsNone(result.evidence)
        self.assertIn("openai http 500", result.error)

    def test_malformed_json_is_isolated(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport("not json at all"))
        result = interp.interpret(["hola"])
        self.assertFalse(result.ok)
        self.assertIsNone(result.evidence)

    def test_empty_burst_makes_no_call(self):
        transport = StubTransport(WILD_B_VEHICLE_PAYLOAD)
        interp = SemanticTurnInterpreter(_settings(), transport=transport)
        result = interp.interpret(["", "   "])
        self.assertFalse(result.ok)
        self.assertEqual(len(transport.calls), 0, "no model call for an empty burst")

    def test_spans_are_not_fabricated(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport(WILD_B_VEHICLE_PAYLOAD))
        evidence = interp.interpret(["para revisar un 2008 del 2014"]).evidence
        for _ref, item in evidence.iter_items():
            self.assertEqual(item.provenance.spans, (),
                             "spans must be empty when the model cannot supply them")

    def test_provenance_is_populated(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport(WILD_B_VEHICLE_PAYLOAD))
        evidence = interp.interpret(
            ["para revisar un 2008 del 2014"], thread_id=2037, burst_id="corr-1",
            message_ids=("wamid.X",),
            reconstruction=BurstReconstruction.LIVE_DEBOUNCE).evidence
        self.assertEqual(evidence.turn.thread_id, 2037)
        self.assertEqual(evidence.turn.burst_id, "corr-1")
        self.assertEqual(evidence.turn.ordered_message_ids, ("wamid.X",))
        self.assertEqual(evidence.turn.reconstruction, BurstReconstruction.LIVE_DEBOUNCE)
        for _ref, item in evidence.iter_items():
            self.assertEqual(item.provenance.model_version, "gpt-4o-mini")
            self.assertIn(PROMPT_VERSION, item.provenance.interpreter)

    def test_prompt_forbids_business_decisions(self):
        transport = StubTransport(WILD_B_VEHICLE_PAYLOAD)
        SemanticTurnInterpreter(_settings(), transport=transport).interpret(["hola"])
        system = transport.calls[0][0]["content"]
        for required in ("RAW EVIDENCE", "TURN EVIDENCE", "CANONICAL STATE",
                         "INSPECTION_LOCATION", "CUSTOMER_ORIGIN", "AMBIGUOUS", "CONFLICT",
                         "PRIMARY", "FALLBACK", "ACCEPT", "HESITATE"):
            self.assertIn(required, system)
        self.assertIn("NUNCA decidas ni menciones: precio", system)


# ── semantic representation ───────────────────────────────────────────────────

class TestRepresentation(unittest.TestCase):

    def _evidence(self, payload):
        return SemanticTurnInterpreter(
            _settings(), transport=StubTransport(payload)).interpret(["x"]).evidence

    def test_shadow_08_location_roles_preserved(self):
        evidence = self._evidence(WILD_B_LOCATION_PAYLOAD)
        by_role = {loc.role: loc.locality for loc in evidence.location_mentions}
        self.assertEqual(by_role[LocationRole.INSPECTION_LOCATION.value], "Berazategui")
        self.assertEqual(by_role[LocationRole.CUSTOMER_ORIGIN.value], "Tigre")

    def test_shadow_09_ordered_scheduling_preserved(self):
        evidence = self._evidence(WILD_A_SCHEDULING_PAYLOAD)
        self.assertEqual(len(evidence.scheduling_requests), 2)
        primary, fallback = evidence.scheduling_requests
        self.assertEqual(primary.priority, SchedulingPriority.PRIMARY)
        self.assertEqual(primary.time, "15:00")
        self.assertEqual(fallback.priority, SchedulingPriority.FALLBACK)
        self.assertIsNone(fallback.time)

    def test_shadow_10_ambiguity_preserved(self):
        payload = copy.deepcopy(WILD_B_VEHICLE_PAYLOAD)
        payload["vehicles"] = [{"make": None, "model": None, "year": None,
                                "status": "AMBIGUOUS", "is_superseded": False,
                                "alternatives": [{"value": "Peugeot 2008"}, {"value": 2008}],
                                "reason": "model vs year"}]
        payload["ambiguities"] = [{"field": "vehicle", "alternatives": [
            {"value": "Peugeot 2008"}, {"value": 2008}], "reason": "model vs year"}]
        evidence = self._evidence(payload)
        self.assertEqual(evidence.vehicle_mentions[0].status, EvidenceStatus.AMBIGUOUS)
        self.assertIsNone(evidence.vehicle_mentions[0].value)
        self.assertEqual(len(evidence.ambiguities[0].alternatives), 2)

    def test_shadow_11_conflict_preserved(self):
        payload = copy.deepcopy(WILD_B_LOCATION_PAYLOAD)
        payload["conflicts"] = [{"field": "inspection_location",
                                 "sides": [{"value": "Berazategui"}, {"value": "Quilmes"}],
                                 "reason": "two locations, no resolving context"}]
        evidence = self._evidence(payload)
        self.assertEqual(len(evidence.conflicts[0].sides), 2)

    def test_shadow_12_faq_and_vehicle_coexist(self):
        evidence = self._evidence(WILD_B_VEHICLE_PAYLOAD)
        self.assertTrue(evidence.faq_intents)
        self.assertTrue(evidence.vehicle_mentions)
        self.assertTrue(evidence.service_intents)

    def test_acceptance_signals_mapped(self):
        for signal, expected_value in (("ACCEPT", True), ("HESITATE", False),
                                       ("QUESTION_ONLY", None)):
            payload = copy.deepcopy(WILD_B_VEHICLE_PAYLOAD)
            payload["acceptance"] = {"signal": signal, "status": "CONFIRMED"}
            evidence = self._evidence(payload)
            self.assertEqual(evidence.acceptance.signal, AcceptanceSignal(signal))
            self.assertEqual(evidence.acceptance.value, expected_value)


# ── corpus evaluability ───────────────────────────────────────────────────────

class TestCorpusEvaluability(unittest.TestCase):
    """The interpreter's output must be scorable by the L4.7E harness for every case."""

    def _score(self, case_id, payload):
        evidence = SemanticTurnInterpreter(
            _settings(), transport=StubTransport(payload)).interpret(
                CORPUS[case_id]["raw"]["messages"]).evidence
        produced = {"turn_evidence": turn_evidence_to_harness_items(evidence)}
        return evaluate_case(CORPUS[case_id], produced)

    def test_shadow_13_owner_examples_evaluable(self):
        empty = {"service_intents": [], "vehicles": [], "locations": [], "faq_topics": [],
                 "acceptance": None, "scheduling_requests": [], "corrections": [],
                 "identity": [], "handoff": None, "ambiguities": [], "conflicts": [],
                 "notes": []}
        for cid in ("REAL-001", "REAL-002", "REAL-003", "REAL-004"):
            with self.subTest(case=cid):
                result = self._score(cid, empty)
                self.assertEqual(result.case_id, cid)
                self.assertEqual(result.kind, "REAL")
                self.assertEqual(result.unsupported_inferences, [],
                                 "an empty interpretation can never hallucinate")

    def test_shadow_14_failed_wild_cases_evaluable(self):
        for cid, payload in (("WILD-B-01", WILD_B_VEHICLE_PAYLOAD),
                             ("WILD-B-02", WILD_B_LOCATION_PAYLOAD),
                             ("WILD-A-04", WILD_A_SCHEDULING_PAYLOAD)):
            with self.subTest(case=cid):
                result = self._score(cid, payload)
                self.assertEqual(result.unsupported_inferences, [], result.notes)
                self.assertGreater(result.true_positives, 0, result.notes)

    def test_shadow_14b_wild_b_location_scores_perfectly(self):
        """The exact Wild B failure, interpreted correctly, must score clean."""
        result = self._score("WILD-B-02", WILD_B_LOCATION_PAYLOAD)
        self.assertTrue(result.clean, result.notes)
        self.assertEqual(result.role_correct, result.role_expected)

    def test_shadow_14c_wild_a_scheduling_scores_perfectly(self):
        result = self._score("WILD-A-04", WILD_A_SCHEDULING_PAYLOAD)
        self.assertTrue(result.clean, result.notes)


# ── recording ─────────────────────────────────────────────────────────────────

class TestShadowRecording(unittest.TestCase):

    def test_shadow_15_records_are_append_only_and_auditable(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport(WILD_B_VEHICLE_PAYLOAD))
        result = interp.interpret(["para revisar un 2008 del 2014"], thread_id=2037,
                                  burst_id="corr-1", message_ids=("wamid.X",))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "shadow.jsonl")
            for _ in range(3):
                self.assertTrue(record_shadow(build_record(
                    thread_id=2037, burst_id="corr-1", message_ids=("wamid.X",),
                    result=result, deployment_id="test-deploy",
                    correlation_id="corr-1"), path=path))
            records = read_records(path)
            self.assertEqual(len(records), 3, "each write appends; nothing is overwritten")
            first = records[0]
            for key in ("record_version", "shadow", "recorded_at", "thread_id", "burst_id",
                        "message_ids", "schema_version", "model", "latency_ms",
                        "total_tokens", "turn_evidence", "ok"):
                self.assertIn(key, first)
            self.assertTrue(first["shadow"])
            self.assertEqual(first["turn_evidence"]["schema_version"], SCHEMA_VERSION)

    def test_record_stores_no_raw_message_text(self):
        interp = SemanticTurnInterpreter(_settings(), transport=StubTransport(WILD_B_VEHICLE_PAYLOAD))
        result = interp.interpret(["Hola, para revisar un 2008 del 2014"])
        blob = json.dumps(build_record(thread_id=1, burst_id="b", result=result),
                          ensure_ascii=False)
        self.assertNotIn("Hola, para revisar", blob,
                         "raw text stays in whatsapp_messages; the record holds evidence")

    def test_record_failure_degrades_to_log_only(self):
        """An unwritable path must degrade to log-only, never raise."""
        record = build_record(thread_id=1, burst_id="b", result=None)
        with tempfile.NamedTemporaryFile() as blocker:
            # A file where a directory is required — mkdir/open must fail.
            self.assertFalse(record_shadow(record, path=f"{blocker.name}/nested/shadow.jsonl"))

    def test_recorder_never_raises(self):
        self.assertFalse(record_shadow({"broken": object()},  # type: ignore[dict-item]
                                       path="/nonexistent-root/x.jsonl"))


# ── CE integration: position, isolation, no authority ─────────────────────────

def _ce_engine(shadow_enabled=True, transport_payload=None, fail=False):
    from app.services.conversation_engine import ConversationEngine
    import app.models as models
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    sqlite = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(sqlite)
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock(bind=sqlite)
    eng.settings = _settings(shadow_understand_enabled=shadow_enabled)
    eng._correlation_id = "corr-shadow"
    return eng


def _ce_ctx():
    from app.services.conversation_engine import _Context
    ctx = _Context.__new__(_Context)
    ctx.thread = SimpleNamespace(id=2037, lead_id=1, contact_id=1, last_message_at=None)
    ctx.lead = SimpleNamespace(id=1, flag=None, estado="CONSULTA_NUEVA", nombre=None,
                               telefono="549110000000", necesita_humano=False)
    ctx.contact = SimpleNamespace(wa_id="549110000000")
    ctx.candidates = []
    ctx.state = SimpleNamespace(last_stage="QUALIFYING", needs_human=False,
                                home_zone_group=None, home_zone_detail=None,
                                current_focus_candidate_id=None, preferred_day=None,
                                preferred_time=None, pending_fuzzy_catalog_key=None)
    ctx.db_messages = []
    ctx.inbound_wa_message_id = "wamid.X"
    return ctx


class TestCeIntegration(unittest.TestCase):

    def test_shadow_02_hook_runs_before_every_deterministic_gate(self):
        """Source position: the hook must sit above Layer A and every early return."""
        import inspect
        from app.services.conversation_engine import ConversationEngine
        src = inspect.getsource(ConversationEngine._process_text)
        hook = src.index("_run_shadow_understand")
        for later_gate in ("Layer A: Motorcycle", "Layer D: FAQ bypass",
                           "WILD-02-B", "WILD-04-F1",
                           "Deterministic SCHEDULING day/time parse"):
            self.assertLess(hook, src.index(later_gate),
                            f"shadow must run before {later_gate}")

    def test_shadow_03b_hook_swallows_every_failure(self):
        eng = _ce_engine()
        ctx = _ce_ctx()
        event = SimpleNamespace(wa_message_id="wamid.X")
        with patch("app.services.semantic_interpreter.SemanticTurnInterpreter.interpret",
                   side_effect=RuntimeError("boom")):
            eng._run_shadow_understand(ctx, event, ["hola"])   # must not raise

    def test_shadow_disabled_makes_no_call(self):
        eng = _ce_engine(shadow_enabled=False)
        ctx = _ce_ctx()
        with patch("app.services.semantic_interpreter.SemanticTurnInterpreter.interpret") as spy:
            eng._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="w"), ["hola"])
        spy.assert_not_called()

    def test_shadow_05_06_no_canonical_or_db_mutation(self):
        eng = _ce_engine()
        ctx = _ce_ctx()
        before_state = dict(vars(ctx.state))
        before_lead = dict(vars(ctx.lead))
        with patch("app.services.semantic_interpreter.SemanticTurnInterpreter._call_openai",
                   return_value=(json.dumps(WILD_B_VEHICLE_PAYLOAD), {})), \
             tempfile.TemporaryDirectory() as tmp:
            eng.settings.shadow_evidence_path = str(pathlib.Path(tmp) / "s.jsonl")
            eng._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="w"), ["hola"])
        self.assertEqual(dict(vars(ctx.state)), before_state, "thread state untouched")
        self.assertEqual(dict(vars(ctx.lead)), before_lead, "lead untouched")
        self.assertEqual(ctx.candidates, [], "no candidate created")
        eng.db.add.assert_not_called()
        eng.db.commit.assert_not_called()

    def test_shadow_07_no_outbound_effect(self):
        eng = _ce_engine()
        ctx = _ce_ctx()
        eng._send_text_to_wa = MagicMock()
        eng._send_flow_button = MagicMock()
        with patch("app.services.semantic_interpreter.SemanticTurnInterpreter._call_openai",
                   return_value=(json.dumps(WILD_B_VEHICLE_PAYLOAD), {})), \
             patch("app.services.conversation_engine.OutboundSafetyGate") as gate, \
             tempfile.TemporaryDirectory() as tmp:
            eng.settings.shadow_evidence_path = str(pathlib.Path(tmp) / "s.jsonl")
            eng._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="w"), ["hola"])
        eng._send_text_to_wa.assert_not_called()
        eng._send_flow_button.assert_not_called()
        gate.assert_not_called()

    def test_interpreter_has_no_business_imports(self):
        import ast
        source = (ROOT / "backend" / "app" / "services" / "semantic_interpreter.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        for forbidden in ("sqlalchemy", "app.db", "app.models",
                          "app.services.pricing", "app.services.schedule",
                          "app.services.outbound_safety_gate",
                          "app.services.conversation_engine"):
            self.assertNotIn(forbidden, imported)
        # Check executable code only — the module docstring legitimately *names* the
        # services it must never call.
        body = ast.parse(source.read_text(encoding="utf-8"))
        body.body = [n for n in body.body
                     if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                             and isinstance(n.value.value, str))]
        code = ast.unparse(body)
        for forbidden in ("db.add(", "db.commit(", "PricingService(", "ScheduleService(",
                          "OutboundSafetyGate(", "_send_whatsapp"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
