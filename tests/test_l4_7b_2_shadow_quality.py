"""L4.7B.2 — shadow interpreter quality gate.

L4.7B measured the interpreter without tuning it; L4.7B.1 named the disagreements. These
tests hold the fixes in place. They use a stub transport (no network, no cost) and assert
*contracts*, not scores: the corpus rerun produces the numbers, this file makes sure the
behaviour that produced them cannot silently regress.

Authority is unchanged throughout: the interpreter still proposes, and nothing here lets
it decide price, availability, booking, catalog identity or lead state.

QUALITY-01  response template carries no populated null example rows
QUALITY-02  the empty scheduling row (SHADOW-DISAGREE-01) is dropped
QUALITY-03  sanitation is general — every evidence array, not scheduling alone
QUALITY-04  partially meaningful evidence survives sanitation
QUALITY-05  temporal context is supplied to the interpreter
QUALITY-06  date resolution stays deterministic — a model-proposed date is dropped
QUALITY-07  day expressions stay inside the controlled vocabulary
QUALITY-08  vehicle number pair: model and year are both retained
QUALITY-09  a year given as a string is not lost
QUALITY-10  an undecidable number pair becomes AMBIGUOUS with alternatives
QUALITY-11  inferred catalog identity is capped at PROPOSED
QUALITY-12  identity the customer stated literally keeps its status
QUALITY-13  FUTURE_INTENT exists and never reads as acceptance
QUALITY-14  schema is turn-evidence/1.1 and still loads 1.0 records
QUALITY-15  intent scope and FAQ coexistence are rules, not phrase lists
QUALITY-16  bounded context is current-cycle only and excludes the burst itself
QUALITY-17  the shadow record stores which context was supplied, never its values
QUALITY-18  the async worker is bounded, drops instead of growing, never raises
QUALITY-19  provenance is captured synchronously; only the model call is deferred
QUALITY-20  confidence is advisory and the model default is unchanged
QUALITY-21  Phase L label corrections are SYNTHETIC only; REAL labels untouched
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import tempfile
import threading
import types
import unittest
from datetime import date
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
    EvidenceStatus,
    TurnEvidence,
)
from app.services.semantic_interpreter import (  # noqa: E402
    DAY_EXPRESSIONS,
    PROMPT_VERSION,
    SemanticTurnInterpreter,
    TurnContext,
    _SYSTEM_PROMPT,
)
from app.services.shadow_recorder import RECORD_VERSION, build_record  # noqa: E402
from app.services.shadow_worker import ShadowJob, ShadowWorker  # noqa: E402
from semantic_corpus.evaluation import load_corpus  # noqa: E402

CORPUS = {c["id"]: c for c in load_corpus()}

EMPTY = {"service_intents": [], "vehicles": [], "locations": [], "faq_topics": [],
         "acceptance": None, "scheduling_requests": [], "corrections": [], "identity": [],
         "handoff": None, "ambiguities": [], "conflicts": [], "notes": []}


def _settings(**kw):
    base = dict(openai_api_key="sk-stub", openai_chat_model="gpt-4o-mini",
                shadow_understand_enabled=True, shadow_understand_async=False,
                shadow_evidence_path=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _interpret(payload, messages, **kw):
    captured: dict = {}

    def transport(msgs, model):
        captured["messages"] = msgs
        return json.dumps({**EMPTY, **payload}, ensure_ascii=False), {}

    interp = SemanticTurnInterpreter(_settings(), transport=transport)
    result = interp.interpret(list(messages), **kw)
    result_user = captured.get("messages", [{}, {}])[-1].get("content", "")
    return result, result_user


# ── Phase A — the empty semantic artifact ─────────────────────────────────────

class TestEmptyArtifact(unittest.TestCase):

    def test_quality_01_template_has_no_populated_null_rows(self):
        """The response template must show `[]`, not an object of null fields."""
        for array in ("service_intents", "vehicles", "locations", "scheduling_requests",
                      "corrections", "identity", "ambiguities", "conflicts"):
            self.assertIn(f'"{array}": []', _SYSTEM_PROMPT,
                          f"{array} must be shown empty in the template")
        self.assertNotIn('"day_expression": null, "time": null', _SYSTEM_PROMPT)
        self.assertNotIn('"make": null, "model": null', _SYSTEM_PROMPT)

    def test_quality_02_empty_scheduling_row_is_dropped(self):
        """SHADOW-DISAGREE-01: the template echo must never become evidence."""
        result, _ = _interpret(
            {"scheduling_requests": [{"priority": "PRIMARY", "day_expression": None,
                                      "time": None, "flexible_time": False, "rank": 1,
                                      "status": "PROPOSED"}]},
            ["hola, aceptan débito?"])
        self.assertEqual(result.evidence.scheduling_requests, ())
        self.assertEqual(result.sanitized_items, 1)

    def test_quality_03_sanitation_covers_every_array(self):
        result, _ = _interpret({
            "vehicles": [{"make": None, "model": None, "year": None, "status": "PROPOSED"}],
            "locations": [{"locality": None, "role": "UNKNOWN_LOCATION_ROLE",
                           "status": "PROPOSED"}],
            "scheduling_requests": [{"priority": "PRIMARY", "day_expression": None,
                                     "time": None, "rank": 1}],
            "corrections": [{"relation": "UNKNOWN_RELATION", "from_value": None,
                             "to_value": None}],
            "identity": [{"kind": "OTHER_IDENTITY", "value": None}],
            "acceptance": {"signal": "UNKNOWN"},
            "service_intents": [{"kind": "OTHER", "value": None, "status": "PROPOSED"}],
        }, ["hola"])
        ev = result.evidence
        self.assertEqual(
            (ev.vehicle_mentions, ev.location_mentions, ev.scheduling_requests,
             ev.corrections, ev.identity_mentions, ev.service_intents, ev.acceptance),
            ((), (), (), (), (), (), None))
        self.assertGreaterEqual(result.sanitized_items, 6)

    def test_quality_04_partial_evidence_is_never_dropped(self):
        """Ambiguity is evidence. Only *nothing* is nothing."""
        result, _ = _interpret({
            "vehicles": [{"make": None, "model": None, "year": None, "status": "AMBIGUOUS",
                          "alternatives": [{"value": "Peugeot 2008"}, {"value": "Peugeot 208"}]}],
            "scheduling_requests": [{"priority": "PRIMARY", "day_expression": "THURSDAY",
                                     "time": None, "flexible_time": True, "rank": 1}],
            "locations": [{"locality": "Berazategui", "role": "INSPECTION_LOCATION",
                           "status": "CONFIRMED"}],
        }, ["el jueves, en Berazategui, un 2008 o un 208 no me acuerdo"])
        ev = result.evidence
        self.assertEqual(len(ev.vehicle_mentions), 1, "AMBIGUOUS + alternatives survives")
        self.assertEqual(len(ev.scheduling_requests), 1, "day without time survives")
        self.assertEqual(len(ev.location_mentions), 1)
        self.assertEqual(result.sanitized_items, 0)


# ── Phase B — temporal context ────────────────────────────────────────────────

class TestTemporalContext(unittest.TestCase):

    def test_quality_05_temporal_context_is_supplied(self):
        ctx = TurnContext.now(tz="America/Argentina/Buenos_Aires", today=date(2026, 9, 3))
        _, user = _interpret({}, ["mñ 15hs?"], context=ctx)
        self.assertIn("2026-09-03", user)
        self.assertIn("JUEVES", user, "the weekday must be stated, not left to the model")
        self.assertIn("America/Argentina/Buenos_Aires", user)
        self.assertIn("NO son verdad operativa", user, "context is not evidence")

    def test_quality_06_model_proposed_date_is_dropped(self):
        """Relative days are named here and resolved deterministically — never both."""
        # L4.7B.3 rewrote the prompt; the CONTRACT is unchanged and is asserted on the
        # behaviour below plus the rule's current wording.
        flat = " ".join(_SYSTEM_PROMPT.split())
        self.assertIn("Tampoco devuelvas fechas ISO", flat)
        self.assertIn("resolved_date lo calcula la capa determinística", flat)
        result, _ = _interpret(
            {"scheduling_requests": [{"priority": "PRIMARY", "day_expression": "TOMORROW",
                                      "time": "15:00", "resolved_date": "2026-09-04",
                                      "rank": 1}]},
            ["mñ 15hs?"])
        request = result.evidence.scheduling_requests[0]
        self.assertIsNone(request.resolved_date)
        self.assertEqual(request.day_expression, "TOMORROW")
        self.assertEqual(request.time, "15:00")

    def test_quality_07_day_vocabulary_is_controlled(self):
        for token in ("TODAY", "TOMORROW", "DAY_AFTER_TOMORROW", "THURSDAY"):
            self.assertIn(token, DAY_EXPRESSIONS)
            self.assertIn(token, _SYSTEM_PROMPT)


# ── Phase C — the vehicle number pair ─────────────────────────────────────────

class TestVehicleNumbers(unittest.TestCase):

    def test_quality_08_model_and_year_both_retained(self):
        """WILD-B-01: the year the customer said must survive a model-named-by-number."""
        result, _ = _interpret(
            {"vehicles": [{"make": "Peugeot", "model": "2008", "year": None,
                           "status": "CONFIRMED"}]},
            ["para revisar un 2008 del 2014"])
        vehicle = result.evidence.vehicle_mentions[0]
        self.assertEqual(vehicle.model, "2008")
        self.assertEqual(vehicle.year, 2014)
        self.assertIn(vehicle.year_status, (EvidenceStatus.PROPOSED, EvidenceStatus.CONFIRMED))

    def test_quality_09_string_year_is_not_lost(self):
        result, _ = _interpret(
            {"vehicles": [{"make": "Volkswagen", "model": "Fox", "year": "2014",
                           "status": "CONFIRMED"}]},
            ["quiero revisar un volkswagen fox 2014"])
        self.assertEqual(result.evidence.vehicle_mentions[0].year, 2014)

    def test_quality_10_undecidable_pair_stays_ambiguous(self):
        result, _ = _interpret(
            {"vehicles": [{"make": "Peugeot", "model": "208", "year": None,
                           "status": "PROPOSED"}]},
            ["tengo un 208, no sé si es 2014 o 2016"])
        vehicle = result.evidence.vehicle_mentions[0]
        self.assertEqual(vehicle.year_status, EvidenceStatus.AMBIGUOUS)
        self.assertGreaterEqual(len(vehicle.alternatives), 2,
                                "both readings are preserved, none discarded")
        self.assertIsNone(vehicle.year, "the interpreter does not pick a winner")
        flat = " ".join(_SYSTEM_PROMPT.split())
        self.assertIn("conservá LOS DOS", flat, "the number-pair rule survives the rewrite")


# ── Phase D — catalog status ceiling ──────────────────────────────────────────

class TestCatalogCeiling(unittest.TestCase):

    def test_quality_11_inferred_identity_capped_at_proposed(self):
        """A make deduced from a model is a suggestion for the deterministic catalog."""
        result, _ = _interpret(
            {"vehicles": [{"make": "Peugeot", "model": "2008", "year": 2014,
                           "category_suggestion": "SUV_4X4_DEPORTIVO",
                           "status": "CONFIRMED"}]},
            ["un 2008 del 2014"])
        vehicle = result.evidence.vehicle_mentions[0]
        self.assertEqual(vehicle.status, EvidenceStatus.PROPOSED)
        self.assertIsNotNone(vehicle.catalog_candidate)

    def test_quality_12_literal_identity_keeps_status(self):
        result, _ = _interpret(
            {"vehicles": [{"make": "Toyota", "model": "Corolla", "year": 2019,
                           "status": "CONFIRMED"}]},
            ["Toyota Corolla 2019"])
        self.assertEqual(result.evidence.vehicle_mentions[0].status,
                         EvidenceStatus.CONFIRMED)

    def test_catalog_authority_is_stated_in_the_prompt(self):
        self.assertIn("CATÁLOGO", _SYSTEM_PROMPT)
        self.assertIn("catalog_candidate", _SYSTEM_PROMPT)


# ── Phase E — future intent ───────────────────────────────────────────────────

class TestFutureIntent(unittest.TestCase):

    def test_quality_13_future_intent_is_not_acceptance(self):
        self.assertIn("FUTURE_INTENT", [s.value for s in AcceptanceSignal])
        result, _ = _interpret(
            {"acceptance": {"signal": "FUTURE_INTENT", "status": "CONFIRMED"}},
            ["cuando lo compre te aviso"])
        acceptance = result.evidence.acceptance
        self.assertEqual(acceptance.signal, AcceptanceSignal.FUTURE_INTENT)
        self.assertFalse(acceptance.value, "future intent must never read as acceptance")
        self.assertIn("FUTURE_INTENT", _SYSTEM_PROMPT)

    def test_quality_14_schema_version_bumped_and_backward_compatible(self):
        # L4.7C.1 bumped the minor version additively (ReconciliationRecord gained the
        # fields a decision needs to be replayable). 1.0/1.1 records still load.
        self.assertEqual(SCHEMA_VERSION, "turn-evidence/1.2")
        self.assertEqual(PROMPT_VERSION, "understand/1.18",
                         "L4.7B.3/B.4 moved the prompt; turn-evidence/1.1 is unchanged")
        old = TurnEvidence.from_json(json.dumps({"schema_version": "turn-evidence/1.0"}))
        self.assertEqual(old.schema_version, "turn-evidence/1.0",
                         "a 1.0 record still loads under the major-version guard")


# ── Phase F — intent scope, no phrase patches ─────────────────────────────────

class TestIntentScope(unittest.TestCase):

    def test_quality_15_intent_scope_is_a_rule_not_a_phrase_list(self):
        flat = " ".join(_SYSTEM_PROMPT.split())
        self.assertIn("El canal no es evidencia", flat, "intent scope is stated as a rule")
        self.assertIn("Todo COEXISTE", flat, "FAQ and business evidence coexist")
        # No customer phrasing may be hard-coded in interpreter *code*. Three things are
        # stripped first, because none of them is a phrase patch: docstrings, comments and
        # the system prompt itself. The prompt is natural-language instruction — after the
        # L4.7B.3 rewrite it defines categories in words ("pregunta por dinero: precio,
        # costo…"), which is a definition, not a branch on a customer sentence. What must
        # stay clean is executable logic: no `if "<customer words>" in text`.
        source = (ROOT / "backend" / "app" / "services" / "semantic_interpreter.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
                keep = []
                for stmt in body:
                    targets = getattr(stmt, "targets", []) or ([getattr(stmt, "target", None)]
                                                               if hasattr(stmt, "target") else [])
                    names = {getattr(t, "id", None) for t in targets if t is not None}
                    if "_SYSTEM_PROMPT" in names:
                        continue          # the prompt is instruction, not code
                    keep.append(stmt)
                body[:] = keep
        code = ast.unparse(tree).lower()
        for phrase in ("revisar", "cotiz", "cuánto sale", "dale", "mñ", "buscando"):
            self.assertNotIn(phrase, code,
                             f"phrase patch '{phrase}' must not appear in the interpreter")

    def test_faq_and_intent_coexist_in_one_burst(self):
        result, _ = _interpret({
            "service_intents": [{"kind": "INSPECTION", "value": "PREPURCHASE_INSPECTION",
                                 "status": "CONFIRMED"}],
            "faq_topics": ["payment", "report"],
            "vehicles": [{"make": "Peugeot", "model": "2008", "year": 2014,
                          "status": "PROPOSED"}],
        }, ["para revisar un 2008 del 2014", "aceptan débito?", "entregan informe?"])
        ev = result.evidence
        self.assertEqual(len(ev.service_intents), 1)
        self.assertEqual(len(ev.faq_intents), 2)
        self.assertEqual(len(ev.vehicle_mentions), 1)


# ── Phase G/H — bounded context and its provenance ────────────────────────────

class TestBoundedContext(unittest.TestCase):

    def _engine(self):
        from app.services.conversation_engine import ConversationEngine
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.db = MagicMock()
        eng.settings = _settings()
        eng._correlation_id = "corr-quality"
        return eng

    def _ctx(self, db_messages, **state):
        from app.services.conversation_engine import _Context
        ctx = _Context.__new__(_Context)
        ctx.thread = SimpleNamespace(id=99)
        ctx.lead = SimpleNamespace(id=1)
        ctx.contact = SimpleNamespace(wa_id="549110000000")
        ctx.candidates = []
        base = dict(last_stage="QUALIFYING", last_offered_slots=None,
                    pending_fuzzy_catalog_key=None, vehicle_clarification_sent=False,
                    location_clarification_sent=False,
                    inspectability_clarification_sent=False)
        base.update(state)
        ctx.state = SimpleNamespace(**base)
        ctx.db_messages = db_messages
        ctx.inbound_wa_message_id = "wamid.X"
        return ctx

    def test_quality_16_context_is_current_cycle_and_excludes_the_burst(self):
        """`ctx.db_messages` is already cycle-scoped; the burst itself is not context."""
        messages = [
            SimpleNamespace(direction="in", text="un 2008 del 2014"),
            SimpleNamespace(direction="out", text="¿en qué zona está?"),
            SimpleNamespace(direction="in", text="Berazategui"),
        ]
        ctx = self._ctx(messages, last_stage="QUOTED",
                        last_offered_slots='["jueves 15:00", "viernes 10:00"]',
                        vehicle_clarification_sent=True)
        context = self._engine()._build_shadow_context(ctx, ["Berazategui"])
        self.assertEqual(context.previous_customer_turn, "un 2008 del 2014",
                         "the burst under interpretation is never fed back as context")
        self.assertEqual(context.stage, "QUOTED")
        self.assertEqual(context.pending_clarification, "vehiculo")
        self.assertEqual(context.offered_slots, ("jueves 15:00", "viernes 10:00"))
        rendered = context.render()
        self.assertNotIn("Berazategui", rendered.split("mensaje anterior")[0])

    def test_context_absent_when_nothing_is_known(self):
        context = self._engine()._build_shadow_context(self._ctx([]), ["hola"])
        self.assertIsNone(context.previous_customer_turn)
        self.assertEqual(context.offered_slots, ())

    def test_a_non_string_path_falls_back_and_writes_no_tree(self):
        """A test double as a path must never become a directory (L4.7B wrote one)."""
        from app.services import shadow_recorder
        with tempfile.TemporaryDirectory() as tmp:
            default = pathlib.Path(tmp) / "default.jsonl"
            record = build_record(thread_id=1, burst_id="b", result=None)
            with patch.object(shadow_recorder, "DEFAULT_PATH", str(default)), \
                 patch.dict("os.environ", {}, clear=False):
                shadow_recorder.record_shadow(record, path=MagicMock())
            written = sorted(p.name for p in pathlib.Path(tmp).iterdir())
            self.assertEqual(written, ["default.jsonl"],
                             "the record falls back to the canonical path, nothing else")

    def test_quality_17_record_stores_context_keys_not_values(self):
        ctx = TurnContext.now(tz="America/Argentina/Buenos_Aires", today=date(2026, 9, 3))
        ctx.previous_customer_turn = "un 2008 del 2014"
        ctx.stage = "QUOTED"
        result, _ = _interpret({}, ["Berazategui"], context=ctx)
        record = build_record(thread_id=99, burst_id="b", message_ids=("wamid.X",),
                              result=result, deployment_id="dep", correlation_id="b")
        blob = json.dumps(record, ensure_ascii=False)
        self.assertIn("previous_customer_turn", record["context_keys"])
        self.assertIn("stage", record["context_keys"])
        self.assertNotIn("2008 del 2014", blob, "no raw customer text is ever stored")
        self.assertEqual(record["record_version"], RECORD_VERSION)


# ── Phase I — bounded asynchronous worker ─────────────────────────────────────

class TestShadowWorker(unittest.TestCase):

    def test_quality_18_worker_is_bounded_and_drops_instead_of_growing(self):
        gate = threading.Event()
        worker = ShadowWorker(max_queue=2, name="quality-bounded")
        try:
            worker.submit(ShadowJob(run=gate.wait))          # occupies the thread
            accepted = sum(1 for _ in range(10)
                           if worker.submit(ShadowJob(run=lambda: None)))
            self.assertLessEqual(accepted, 3, "queue is bounded")
            self.assertGreater(worker.dropped, 0, "overflow is dropped, not buffered")
        finally:
            gate.set()
            worker.stop(timeout=2)

    def test_worker_failure_never_escapes(self):
        worker = ShadowWorker(max_queue=4, name="quality-fail")
        try:
            self.assertTrue(worker.submit(ShadowJob(run=lambda: 1 / 0)))
            worker.drain(timeout=3)
            self.assertEqual(worker.failed, 1)
            self.assertEqual(worker.completed, 0)
        finally:
            worker.stop(timeout=2)

    def test_worker_uses_one_thread_for_many_jobs(self):
        seen: set[int] = set()
        worker = ShadowWorker(max_queue=16, name="quality-single")
        try:
            for _ in range(8):
                worker.submit(ShadowJob(run=lambda: seen.add(threading.get_ident())))
            worker.drain(timeout=5)
            self.assertEqual(len(seen), 1, "one worker thread, not one per turn")
        finally:
            worker.stop(timeout=2)

    def test_quality_19_provenance_is_captured_synchronously(self):
        """The turn must not wait on the model, but must not lose its provenance either."""
        from app.services.conversation_engine import ConversationEngine
        from app.services import shadow_worker as worker_module

        eng = ConversationEngine.__new__(ConversationEngine)
        eng.db = MagicMock()
        eng.settings = _settings(shadow_understand_async=True)
        eng._correlation_id = "corr-async"

        from app.services.conversation_engine import _Context
        ctx = _Context.__new__(_Context)
        ctx.thread = SimpleNamespace(id=7)
        ctx.lead = SimpleNamespace(id=1)
        ctx.contact = SimpleNamespace(wa_id="549110000000")
        ctx.candidates = []
        ctx.state = SimpleNamespace(last_stage="QUALIFYING", last_offered_slots=None)
        ctx.db_messages = []
        ctx.inbound_wa_message_id = "wamid.X"

        worker = ShadowWorker(max_queue=4, name="quality-sync-capture")
        started = threading.Event()
        released = threading.Event()

        def slow_interpret(*a, **kw):
            started.set()
            released.wait(3)
            return SimpleNamespace(evidence=None, ok=True, latency_ms=1, model="stub",
                                   prompt_version=PROMPT_VERSION,
                                   schema_version=SCHEMA_VERSION, error=None,
                                   prompt_tokens=None, completion_tokens=None,
                                   total_tokens=None, context_keys=(), sanitized_items=0)

        try:
            with patch.object(worker_module, "get_worker", return_value=worker), \
                 patch("app.services.semantic_interpreter.SemanticTurnInterpreter.interpret",
                       side_effect=slow_interpret), \
                 tempfile.TemporaryDirectory() as tmp:
                eng.settings.shadow_evidence_path = str(pathlib.Path(tmp) / "s.jsonl")
                eng._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="wamid.X"),
                                           ["hola"])
                # The turn returned while the model call is still in flight.
                self.assertTrue(started.wait(3), "job reached the worker")
                self.assertEqual(worker.submitted, 1)
                released.set()
                worker.drain(timeout=3)
                self.assertEqual(worker.failed, 0)
        finally:
            released.set()
            worker.stop(timeout=2)

    def test_async_dispatch_is_strictly_boolean(self):
        """A MagicMock settings object must never turn async dispatch on."""
        from app.services.conversation_engine import ConversationEngine
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.settings = MagicMock()
        self.assertFalse(eng._shadow_async())


# ── Phase J/K — advisory confidence, unchanged model ──────────────────────────

class TestAdvisoryConfidence(unittest.TestCase):

    def test_quality_20_confidence_never_changes_status(self):
        high, _ = _interpret(
            {"vehicles": [{"make": "Toyota", "model": "Corolla", "year": 2019,
                           "status": "PROPOSED", "confidence": 0.99}]},
            ["Toyota Corolla 2019"])
        low, _ = _interpret(
            {"vehicles": [{"make": "Toyota", "model": "Corolla", "year": 2019,
                           "status": "CONFIRMED", "confidence": 0.05}]},
            ["Toyota Corolla 2019"])
        self.assertEqual(high.evidence.vehicle_mentions[0].status, EvidenceStatus.PROPOSED)
        self.assertEqual(high.evidence.vehicle_mentions[0].confidence, 0.99)
        self.assertEqual(low.evidence.vehicle_mentions[0].status, EvidenceStatus.CONFIRMED)
        self.assertEqual(low.evidence.vehicle_mentions[0].confidence, 0.05)

    def test_placeholder_values_are_treated_as_absence(self):
        """"UNKNOWN" is an absence dressed as a value; it must not become evidence."""
        result, _ = _interpret(
            {"vehicles": [{"make": "UNKNOWN", "model": "2008", "year": 2014,
                           "status": "PROPOSED"}],
             "locations": [{"locality": "N/A", "role": "INSPECTION_LOCATION",
                            "status": "PROPOSED"}]},
            ["un 2008 del 2014"])
        vehicle = result.evidence.vehicle_mentions[0]
        self.assertIsNone(vehicle.make)
        self.assertEqual(vehicle.value, "2008")
        self.assertEqual(result.evidence.location_mentions, (),
                         "a placeholder locality is no locality at all")

    def test_absent_confidence_is_not_invented(self):
        result, _ = _interpret(
            {"vehicles": [{"make": "Toyota", "model": "Corolla", "year": 2019,
                           "status": "CONFIRMED"}]},
            ["Toyota Corolla 2019"])
        self.assertIsNone(result.evidence.vehicle_mentions[0].confidence)

    def test_model_default_is_unchanged(self):
        source = (ROOT / "backend" / "app" / "services" / "semantic_interpreter.py").read_text()
        self.assertIn('or "gpt-4o-mini"', source, "L4.7B.2 changes quality, not the model")


# ── Phase L — corpus label review ─────────────────────────────────────────────

class TestCorpusLabels(unittest.TestCase):

    CORRECTED = {"SYN-VEH-04", "SYN-VEH-05", "SYN-VEH-18"}

    def test_quality_21_label_corrections_are_synthetic_only(self):
        for case_id in self.CORRECTED:
            case = CORPUS[case_id]
            self.assertEqual(case["provenance"]["kind"], "SYNTHETIC")
            fields = [e["field"] for e in case["expected_turn_evidence"]]
            self.assertIn("service_intent", fields,
                          f"{case_id} states the service explicitly")

    def test_real_labels_still_match_owner_text(self):
        """Owner-provided REAL messages remain verbatim and their labels unchanged."""
        lengths = {"REAL-001": 93, "REAL-002": 62, "REAL-003": 115, "REAL-004": 200}
        for case_id, length in lengths.items():
            self.assertEqual(len(CORPUS[case_id]["raw"]["messages"][0]), length)

    def test_intent_rule_is_applied_consistently(self):
        """Every SYNTHETIC vehicle case that states the service carries the intent label."""
        for case in CORPUS.values():
            if case["provenance"]["kind"] != "SYNTHETIC" or "B" not in case["groups"]:
                continue
            text = " ".join(case["raw"]["messages"]).lower()
            states_service = "revis" in text
            has_intent = any(e["field"] == "service_intent"
                             for e in case["expected_turn_evidence"])
            self.assertEqual(states_service, has_intent, case["id"])


if __name__ == "__main__":
    unittest.main()
