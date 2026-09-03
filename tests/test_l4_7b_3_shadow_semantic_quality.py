"""L4.7B.3 — interpreter semantics under the repaired instrument.

L4.7B.2B straightened the ruler; this milestone moved the interpreter. These tests pin the
*semantic contracts* the prompt now states — intent scope, stance granularity, scheduling
shape, location roles, corrections, quote discipline, FAQ completeness, catalog ceiling —
using a stub transport, so they assert behaviour rather than re-measuring the corpus.

Authority is unchanged: the interpreter proposes, and nothing here lets it decide price,
availability, booking, catalog identity or lead state.

INTENT-01  channel alone does not imply service intent
INTENT-02  an explicit service question emits intent
INTENT-03  condition-check wording emits intent
STANCE-01  HESITATE
STANCE-02  FUTURE_INTENT
STANCE-03  SEARCHING_NOT_READY (process fact, not a stance)
STANCE-04  courtesy is not ACCEPT
SCHED-01   a relative day stays relative
SCHED-02   PRIMARY/FALLBACK order preserved
SCHED-03   a time stays inside its own clause
LOC-01     inspection vs origin split
LOC-02     an origin-only mention never becomes an inspection location
CORR-01    vehicle correction
CORR-02    year correction
CORR-03    prior-cycle history is excluded from context
QUOTE-01   an explicit price question is a quote request
QUOTE-02   vehicle/service interest alone is not a quote request
FAQ-01     a multi-question burst keeps every topic
CAT-01     an inferred make is capped at PROPOSED
ASYNC-01   the shadow path stays non-blocking
CORPUS-01  all 162 corpus cases remain evaluable
"""
from __future__ import annotations

import json
import pathlib
import sys
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
    AcceptanceSignal,
    EvidenceStatus,
    LocationRole,
    ServiceIntentKind,
)
from app.services.semantic_interpreter import (  # noqa: E402
    FAQ_TOPICS,
    PROMPT_VERSION,
    READINESS_VALUES,
    SemanticTurnInterpreter,
    TurnContext,
    _SYSTEM_PROMPT,
)
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


def interpret(payload, messages=("hola",), **kw):
    """Run the real mapper over a canned model payload. No network."""
    interp = SemanticTurnInterpreter(
        _settings(),
        transport=lambda msgs, model: (json.dumps({**EMPTY, **payload}, ensure_ascii=False), {}))
    return interp.interpret(list(messages), **kw).evidence


def fields(evidence):
    return {item.field: item for _ref, item in evidence.iter_items()}


def field_names(evidence):
    return {item.field for _ref, item in evidence.iter_items()}


def flat(text: str) -> str:
    """Prompt assertions must survive line wrapping: compare on collapsed whitespace."""
    return " ".join(text.split())


PROMPT = flat(_SYSTEM_PROMPT)


# ── service intent scope ──────────────────────────────────────────────────────

class TestServiceIntent(unittest.TestCase):

    def test_intent_01_channel_alone_is_not_intent(self):
        """Writing to us, being polite and promising to return are not service intent."""
        ev = interpret({"service_intents": [{"kind": "READINESS",
                                             "value": "SEARCHING_NOT_READY",
                                             "status": "CONFIRMED"}],
                        "acceptance": {"signal": "FUTURE_INTENT", "status": "CONFIRMED"}},
                       ["hola, por ahora estoy buscando un auto, cuando decida te aviso"])
        names = field_names(ev)
        self.assertNotIn("service_intent", names)
        self.assertIn("readiness", names)
        # the prompt must state the exclusion, not rely on the model's mood
        self.assertIn("El canal no es evidencia", PROMPT)

    def test_intent_02_service_question_emits_intent(self):
        ev = interpret({"service_intents": [{"kind": "INSPECTION",
                                             "value": "PREPURCHASE_INSPECTION",
                                             "status": "CONFIRMED"}]},
                       ["¿ustedes revisan autos usados?"])
        self.assertEqual(ev.service_intents[0].kind, ServiceIntentKind.INSPECTION)
        self.assertEqual(ev.service_intents[0].field, "service_intent")

    def test_intent_03_condition_check_emits_intent(self):
        ev = interpret({"service_intents": [{"kind": "INSPECTION",
                                             "value": "PREPURCHASE_INSPECTION",
                                             "status": "CONFIRMED"}],
                        "vehicles": [{"make": "Volkswagen", "model": "Fox",
                                      "status": "PROPOSED"}]},
                       ["quiero comprar un fox y ver en qué estado está"])
        self.assertIn("service_intent", field_names(ev))
        self.assertIn("CHEQUEAR EL ESTADO", PROMPT)

    def test_a_named_kind_without_a_value_is_not_lost(self):
        """The kind is the evidence; a missing constant must not silently drop the item."""
        for kind, field, expected in (
            ("QUOTE_REQUEST", "quote_request", True),
            ("INSPECTION", "service_intent", "PREPURCHASE_INSPECTION"),
            ("READINESS", "readiness", "SEARCHING_NOT_READY"),
            ("LOGISTICS_OFFER", "customer_logistics_offer", "CUSTOMER_OFFERS_TRANSPORT"),
        ):
            ev = interpret({"service_intents": [{"kind": kind, "status": "CONFIRMED"}]})
            item = fields(ev).get(field)
            self.assertIsNotNone(item, f"{kind} was dropped")
            self.assertEqual(item.value, expected)


# ── stance ────────────────────────────────────────────────────────────────────

class TestStance(unittest.TestCase):

    def _stance(self, signal, messages=("...",)):
        ev = interpret({"acceptance": {"signal": signal, "status": "CONFIRMED"}}, messages)
        return ev.acceptance

    def test_stance_01_hesitate(self):
        stance = self._stance("HESITATE", ["lo voy a pensar"])
        self.assertEqual(stance.signal, AcceptanceSignal.HESITATE)
        self.assertFalse(stance.value, "hesitation is not acceptance")

    def test_stance_02_future_intent(self):
        stance = self._stance("FUTURE_INTENT", ["si me cierra te escribo"])
        self.assertEqual(stance.signal, AcceptanceSignal.FUTURE_INTENT)
        self.assertFalse(stance.value)

    def test_stance_03_searching_not_ready_is_a_fact_not_a_stance(self):
        ev = interpret({"service_intents": [{"kind": "READINESS",
                                             "value": "SEARCHING_NOT_READY",
                                             "status": "CONFIRMED"}],
                        "acceptance": {"signal": "FUTURE_INTENT", "status": "CONFIRMED"}},
                       ["por ahora estoy buscando un auto, después te aviso"])
        self.assertEqual(fields(ev)["readiness"].value, "SEARCHING_NOT_READY")
        self.assertEqual(ev.acceptance.signal, AcceptanceSignal.FUTURE_INTENT)
        self.assertEqual(READINESS_VALUES, ("SEARCHING_NOT_READY",),
                         "stance values were retired from readiness in L4.7B.2B")

    def test_stance_04_courtesy_is_not_accept(self):
        ev = interpret({}, ["gracias!"])
        self.assertIsNone(ev.acceptance, "courtesy carries no stance")
        self.assertIn("NO expresan conformidad", PROMPT)

    def test_hesitation_and_future_intent_stay_distinct(self):
        self.assertNotEqual(self._stance("HESITATE").signal,
                            self._stance("FUTURE_INTENT").signal)


# ── scheduling ────────────────────────────────────────────────────────────────

class TestScheduling(unittest.TestCase):

    WILD_A_04 = {"scheduling_requests": [
        {"priority": "PRIMARY", "day_expression": "TOMORROW", "time": "15:00",
         "flexible_time": False, "rank": 1, "status": "CONFIRMED"},
        {"priority": "FALLBACK", "day_expression": "THURSDAY", "time": None,
         "flexible_time": True, "rank": 2, "status": "CONFIRMED"}]}

    def test_sched_01_relative_day_stays_relative(self):
        ctx = TurnContext.now(tz="America/Argentina/Buenos_Aires", today=date(2026, 9, 2))
        ev = interpret(self.WILD_A_04, ["Mñ 15hs? O nose jueves que tenes"], context=ctx)
        first = ev.scheduling_requests[0]
        self.assertEqual(first.day_expression, "TOMORROW")
        self.assertIsNone(first.resolved_date, "date arithmetic belongs to reconciliation")
        self.assertIn("NUNCA la conviertas en el nombre del día", PROMPT)

    def test_sched_02_primary_and_fallback_order_preserved(self):
        ev = interpret(self.WILD_A_04, ["Mñ 15hs? O nose jueves que tenes"])
        days = [(r.priority.value, r.day_expression, r.rank) for r in ev.scheduling_requests]
        self.assertEqual(days, [("PRIMARY", "TOMORROW", 1), ("FALLBACK", "THURSDAY", 2)])

    def test_sched_03_time_stays_in_its_own_clause(self):
        ev = interpret(self.WILD_A_04, ["Mñ 15hs? O nose jueves que tenes"])
        first, second = ev.scheduling_requests
        self.assertEqual(first.time, "15:00")
        self.assertIsNone(second.time, "a time never migrates to the other option")
        self.assertTrue(second.flexible_time)

    def test_a_day_outside_the_vocabulary_is_dropped(self):
        ev = interpret({"scheduling_requests": [
            {"priority": "PRIMARY", "day_expression": "UNKNOWN", "time": "10:00",
             "rank": 1, "status": "PROPOSED"}]}, ["cuando puedan"])
        self.assertIsNone(ev.scheduling_requests[0].day_expression)


# ── location roles ────────────────────────────────────────────────────────────

class TestLocationRoles(unittest.TestCase):

    def test_loc_01_inspection_and_origin_split(self):
        ev = interpret({"locations": [
            {"locality": "Berazategui", "role": "INSPECTION_LOCATION", "status": "CONFIRMED"},
            {"locality": "Tigre", "role": "CUSTOMER_ORIGIN", "status": "CONFIRMED"}]},
            ["Está en Berazategui, pero yo soy de Tigre."])
        roles = {l.locality: l.role for l in ev.location_mentions}
        self.assertEqual(roles["Berazategui"], LocationRole.INSPECTION_LOCATION.value)
        self.assertEqual(roles["Tigre"], LocationRole.CUSTOMER_ORIGIN.value)

    def test_loc_02_origin_only_never_becomes_inspection_location(self):
        ev = interpret({"locations": [{"locality": "Tigre", "role": "CUSTOMER_ORIGIN",
                                       "status": "CONFIRMED"}]}, ["Yo vivo en Tigre"])
        self.assertEqual([l.role for l in ev.location_mentions],
                         [LocationRole.CUSTOMER_ORIGIN.value])
        self.assertIn("NO inventes un origen del cliente", PROMPT)

    def test_a_time_expression_is_never_a_location(self):
        self.assertIn("expresiones de TIEMPO, no localidades", PROMPT)


# ── corrections ───────────────────────────────────────────────────────────────

class TestCorrections(unittest.TestCase):

    def test_corr_01_vehicle_correction_keeps_both_sides(self):
        ev = interpret({
            "corrections": [{"relation": "REPLACE_CANDIDATE", "from_value": "Ford Ka",
                             "to_value": "Ford Kuga", "status": "CONFIRMED"}],
            "vehicles": [{"make": "Ford", "model": "Kuga", "is_superseded": False,
                          "status": "CONFIRMED"},
                         {"make": "Ford", "model": "Ka", "is_superseded": True,
                          "status": "CONFIRMED"}]},
            ["Es un Ford Ka... no, perdón, es un Ford Kuga"])
        self.assertEqual(len(ev.corrections), 1)
        current = [v for v in ev.vehicle_mentions if not v.is_superseded]
        dropped = [v for v in ev.vehicle_mentions if v.is_superseded]
        self.assertEqual(current[0].value, "Ford Kuga")
        self.assertEqual(dropped[0].field, "vehicle_superseded")

    def test_corr_02_year_correction_survives_without_a_vehicle(self):
        ev = interpret({
            "corrections": [{"relation": "CORRECT_EXISTING", "from_value": 2014,
                             "to_value": 2015, "status": "CONFIRMED"}],
            "vehicles": [{"model": None, "year": 2015, "status": "CONFIRMED",
                          "year_status": "CONFIRMED"}]},
            ["Es del 2015 no del 2014"])
        self.assertEqual(len(ev.corrections), 1)
        self.assertEqual(ev.vehicle_mentions[0].year, 2015)

    def test_corr_03_prior_cycle_is_excluded_from_context(self):
        """Only the current cycle may be fed back; a finished cycle must not leak."""
        from app.services.conversation_engine import ConversationEngine, _Context
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.db = MagicMock()
        eng.settings = _settings()
        eng._correlation_id = "corr-l473"
        ctx = _Context.__new__(_Context)
        ctx.thread = SimpleNamespace(id=1)
        ctx.lead = SimpleNamespace(id=1)
        ctx.contact = SimpleNamespace(wa_id="549110000000")
        ctx.candidates = []
        ctx.state = SimpleNamespace(last_stage="QUALIFYING", last_offered_slots=None)
        # db_messages is the CURRENT cycle only, by construction upstream
        ctx.db_messages = [SimpleNamespace(direction="in", text="un 2008 del 2014")]
        ctx.inbound_wa_message_id = "wamid.X"
        context = eng._build_shadow_context(ctx, ["Berazategui"])
        self.assertEqual(context.previous_customer_turn, "un 2008 del 2014")
        self.assertIn("Nunca uses historia de ciclos anteriores", PROMPT)


# ── quote discipline, FAQ completeness, catalog ceiling ───────────────────────

class TestQuoteFaqCatalog(unittest.TestCase):

    def test_quote_01_explicit_price_question(self):
        ev = interpret({"service_intents": [{"kind": "QUOTE_REQUEST", "value": True,
                                             "status": "CONFIRMED"}]},
                       ["¿cuánto sale?"])
        self.assertEqual(fields(ev)["quote_request"].value, True)

    def test_quote_02_interest_alone_is_not_a_quote_request(self):
        ev = interpret({"service_intents": [{"kind": "INSPECTION",
                                             "value": "PREPURCHASE_INSPECTION",
                                             "status": "CONFIRMED"}],
                        "vehicles": [{"make": "Ford", "model": "Focus", "year": 2017,
                                      "status": "CONFIRMED"}]},
                       ["quiero revisar un Focus 2017"])
        self.assertNotIn("quote_request", field_names(ev))
        self.assertIn("Una pregunta por plata no es una FAQ", PROMPT)

    def test_faq_01_multi_topic_burst_keeps_every_topic(self):
        ev = interpret({"faq_topics": ["report", "presence", "payment"]},
                       ["¿entregan informe? ¿tengo que estar presente? ¿aceptan débito?"])
        self.assertEqual([f.topic for f in ev.faq_intents],
                         ["report", "presence", "payment"])
        self.assertNotIn("mixed", FAQ_TOPICS, "the sentinel was retired in L4.7B.2B")

    def test_cat_01_inferred_make_capped_at_proposed(self):
        ev = interpret({"vehicles": [{"make": "Volkswagen", "model": "Fox",
                                      "status": "CONFIRMED"}]}, ["quiero revisar un fox"])
        vehicle = ev.vehicle_mentions[0]
        self.assertEqual(vehicle.status, EvidenceStatus.PROPOSED,
                         "a make the customer did not say is a catalog suggestion")
        self.assertEqual(vehicle.catalog_candidate, "Volkswagen Fox")

    def test_literal_make_keeps_confirmed(self):
        ev = interpret({"vehicles": [{"make": "Volkswagen", "model": "Fox",
                                      "status": "CONFIRMED"}]},
                       ["es un volkswagen fox"])
        self.assertEqual(ev.vehicle_mentions[0].status, EvidenceStatus.CONFIRMED)


# ── async shadow, unchanged ───────────────────────────────────────────────────

class TestAsyncShadow(unittest.TestCase):

    def test_async_01_shadow_stays_non_blocking(self):
        from app.services.conversation_engine import ConversationEngine, _Context
        from app.services import shadow_worker as worker_module

        eng = ConversationEngine.__new__(ConversationEngine)
        eng.db = MagicMock()
        eng.settings = _settings(shadow_understand_async=True)
        eng._correlation_id = "corr-async-l473"
        ctx = _Context.__new__(_Context)
        ctx.thread = SimpleNamespace(id=5)
        ctx.lead = SimpleNamespace(id=1)
        ctx.contact = SimpleNamespace(wa_id="549110000000")
        ctx.candidates = []
        ctx.state = SimpleNamespace(last_stage="QUALIFYING", last_offered_slots=None)
        ctx.db_messages = []
        ctx.inbound_wa_message_id = "wamid.X"

        worker = ShadowWorker(max_queue=4, name="l473-async")
        started, release = threading.Event(), threading.Event()

        def slow(*a, **kw):
            started.set()
            release.wait(3)
            return SimpleNamespace(evidence=None, ok=True, latency_ms=1, model="stub",
                                   prompt_version=PROMPT_VERSION, schema_version="x",
                                   error=None, prompt_tokens=None, completion_tokens=None,
                                   total_tokens=None, context_keys=(), sanitized_items=0)
        try:
            with patch.object(worker_module, "get_worker", return_value=worker), \
                 patch("app.services.semantic_interpreter.SemanticTurnInterpreter.interpret",
                       side_effect=slow):
                eng._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="w"), ["hola"])
                self.assertTrue(started.wait(3), "the job reached the worker")
                self.assertEqual(worker.submitted, 1, "the turn returned without waiting")
                release.set()
                worker.drain(timeout=3)
                self.assertEqual(worker.failed, 0)
        finally:
            release.set()
            worker.stop(timeout=2)

    def test_async_dispatch_stays_strictly_boolean(self):
        from app.services.conversation_engine import ConversationEngine
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.settings = MagicMock()
        self.assertFalse(eng._shadow_async())


# ── corpus reachability ───────────────────────────────────────────────────────

class TestCorpus(unittest.TestCase):

    def test_corpus_01_all_162_cases_remain_evaluable(self):
        from semantic_corpus.corpus_mapping import (corpus_case_to_turn_evidence,
                                                    turn_evidence_to_harness_items)
        self.assertEqual(len(CORPUS), 162)
        for case in CORPUS.values():
            evidence = corpus_case_to_turn_evidence(case)
            self.assertTrue(turn_evidence_to_harness_items(evidence) is not None, case["id"])

    def test_prompt_version_and_model(self):
        self.assertEqual(PROMPT_VERSION, "understand/1.12")
        source = (ROOT / "backend" / "app" / "services" / "semantic_interpreter.py").read_text()
        self.assertIn('or "gpt-4o-mini"', source, "L4.7B.3 changed the prompt, not the model")

    def test_interpreter_still_has_no_business_authority(self):
        import ast
        source = (ROOT / "backend" / "app" / "services" / "semantic_interpreter.py").read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("conversation_engine", "outbound_safety_gate", "pricing",
                          "schedule", "models", "db"):
            self.assertFalse(any(forbidden in m for m in imported),
                             f"interpreter must not import {forbidden}: {imported}")


if __name__ == "__main__":
    unittest.main()
