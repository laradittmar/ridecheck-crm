"""L4.7C.4A — the same interpretation, available to the turn it is about.

L4.7C.4 closed the scheduling interface but left the producer behind the turn: the model
read the burst *after* the customer had already been answered. These tests hold the fix and,
just as importantly, its price — exactly one model call per inbound burst.

LIVESEM-01 same-turn TurnEvidence available   LIVESEM-10 invalid schema does not progress
LIVESEM-02 one semantic call maximum          LIVESEM-11 requested != available
LIVESEM-03 reused by the shadow recorder      LIVESEM-12 available != booked
LIVESEM-04 reused by claim projection         LIVESEM-13 no second model call
LIVESEM-05 "mañana 15 o jueves" — two live    LIVESEM-14 prior-cycle context excluded
LIVESEM-06 WILD-A-04 — two branches live      LIVESEM-15 C2 vehicle/location unchanged
LIVESEM-07 clause-local time preserved        LIVESEM-16 C3B acceptance unchanged
LIVESEM-08 timeout does not guess             LIVESEM-17 C4 scheduling rules unchanged
LIVESEM-09 model failure does not guess       LIVESEM-18 feature-flag rollback
"""
from __future__ import annotations

import ast
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

from app.schemas.claims import (  # noqa: E402
    ClaimEvidence,
    ClaimType,
    EvidenceClass,
    Explicitness,
)
from app.schemas.turn_evidence import (  # noqa: E402
    SCHEMA_VERSION,
    EvidenceStatus,
    SchedulingPriority,
    SchedulingRequestEvidence,
    TurnEvidence,
)
from app.services.scheduling_reconciler import (  # noqa: E402
    RULE_ID,
    reconcile_scheduling,
    resolve_day_expression,
    semantic_covers_deterministic,
    to_record,
)
from app.services.semantic_turn_evidence import TurnSemanticEvidence  # noqa: E402

CE_PATH = ROOT / "backend" / "app" / "services" / "conversation_engine.py"
CE_SOURCE = CE_PATH.read_text(encoding="utf-8")
RECONCILER_SOURCE = (ROOT / "backend" / "app" / "services"
                     / "scheduling_reconciler.py").read_text(encoding="utf-8")
PROVIDER_SOURCE = (ROOT / "backend" / "app" / "services"
                   / "semantic_turn_evidence.py").read_text(encoding="utf-8")

MONDAY = date(2026, 8, 31)      # tomorrow is Tuesday 2026-09-01, Thursday is 2026-09-03


def code_of(source: str) -> str:
    """Executable code only — a docstring is documentation, not behaviour."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


def function_source(name: str) -> str:
    tree = ast.parse(CE_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found in conversation_engine.py")


# ── the shapes the live interpreter actually produced on 2026-09-03 ───────────
#
# Measured on the deployed image with understand/1.18, today = Monday 2026-08-31. The model
# names DAY EXPRESSIONS and leaves resolved_date empty: the calendar belongs to the resolver.

LIVE_SEMANTIC = {
    "mañana 15 o jueves": [("TOMORROW", "15:00", False), ("THURSDAY", None, True)],
    "Mñ 15hs? O nose jueves que tenes": [("TOMORROW", "15:00", False),
                                         ("THURSDAY", None, True)],
    "jueves a la tarde": [("THURSDAY", None, True)],
    "qué horarios tienen?": [],
}


def turn_evidence_for(text: str) -> TurnEvidence:
    priorities = (SchedulingPriority.PRIMARY, SchedulingPriority.FALLBACK,
                  SchedulingPriority.ADDITIONAL)
    requests = tuple(
        SchedulingRequestEvidence(priority=priorities[min(i, 2)], day_expression=day,
                                  time=time, flexible_time=flexible, rank=i + 1,
                                  status=EvidenceStatus.CONFIRMED)
        for i, (day, time, flexible) in enumerate(LIVE_SEMANTIC.get(text, [])))
    return TurnEvidence(scheduling_requests=requests)


def interpretation(evidence, ok=True, error=None):
    return SimpleNamespace(evidence=evidence, ok=ok, latency_ms=2400, model="gpt-4o-mini",
                           prompt_version="understand/1.18", schema_version=SCHEMA_VERSION,
                           error=error, prompt_tokens=None, completion_tokens=None,
                           total_tokens=None, context_keys=(), sanitized_items=0)


def make_engine(*, scheduling=True, same_turn=True, shadow=True, timeout=5.0,
                evidence_path=""):
    from app.services.conversation_engine import ConversationEngine
    engine = ConversationEngine.__new__(ConversationEngine)
    engine.db = MagicMock()
    engine.settings = SimpleNamespace(
        reconciler_scheduling_authority_enabled=scheduling,
        reconciler_vehicle_authority_enabled=False,
        reconciler_location_authority_enabled=False,
        reconciler_acceptance_authority_enabled=False,
        semantic_same_turn_enabled=same_turn,
        semantic_same_turn_timeout_seconds=timeout,
        shadow_understand_enabled=shadow,
        shadow_understand_async=False,
        shadow_evidence_path=evidence_path,
        openai_chat_model="gpt-4o-mini",
        openai_api_key="test-key")
    engine._correlation_id = "corr-c4a"
    return engine


def make_ctx(db_messages=()):
    return SimpleNamespace(
        thread=SimpleNamespace(id=7),
        lead=SimpleNamespace(flag="PRESUPUESTANDO"),
        db_messages=list(db_messages),
        state=SimpleNamespace(current_cycle_start_message_db_id=11,
                              current_revision_id=None, last_stage="SCHEDULING",
                              last_offered_slots=None,
                              pending_fuzzy_catalog_key=None,
                              vehicle_clarification_sent=None,
                              location_clarification_sent=None,
                              inspectability_clarification_sent=None))


def run_turn(engine, ctx, text, *, interpret_side_effect=None):
    """Dispatch the one interpretation for this burst, exactly as a live turn does."""
    side_effect = interpret_side_effect
    if side_effect is None:
        def side_effect(*a, **k):          # noqa: ANN001 — test double
            return interpretation(turn_evidence_for(text))
    with patch("app.services.semantic_interpreter.SemanticTurnInterpreter.interpret",
               side_effect=side_effect) as interpret:
        engine._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="wamid.C4A"),
                                      [text])
        requests = engine._reconciled_scheduling_requests(ctx, ctx.state, [text], MONDAY)
    return requests, interpret


def branches(requests):
    return [(r.day_iso, r.time_str) for r in requests]


# ── same-turn availability and the single-call invariant ─────────────────────

class TestSameTurnEvidence(unittest.TestCase):

    def test_livesem_01_same_turn_turn_evidence_available(self):
        engine, ctx = make_engine(), make_ctx()
        with patch("app.services.semantic_interpreter.SemanticTurnInterpreter.interpret",
                   side_effect=lambda *a, **k: interpretation(
                       turn_evidence_for("mañana 15 o jueves"))):
            engine._run_shadow_understand(ctx, SimpleNamespace(wa_message_id="w"),
                                          ["mañana 15 o jueves"])
            evidence = engine._semantic_turn_evidence()
        self.assertIsNotNone(evidence, "the turn can read its own interpretation")
        self.assertEqual(len(evidence.scheduling_requests), 2)

    def test_livesem_02_one_semantic_call_maximum(self):
        calls = []

        def once(*_a, **_k):
            calls.append(1)
            return interpretation(turn_evidence_for("mañana 15 o jueves"))

        engine, ctx = make_engine(), make_ctx()
        requests, interpret = run_turn(engine, ctx, "mañana 15 o jueves",
                                       interpret_side_effect=once)
        for _ in range(5):                       # every consumer asks again
            engine._semantic_turn_evidence()
            engine._semantic_scheduling_claims(ctx.state)
        self.assertEqual(len(calls), 1, "one burst, one model call")
        self.assertEqual(engine._turn_semantic.calls, 1)
        self.assertEqual(interpret.call_count, 1)
        self.assertEqual(len(requests), 2)

    def test_livesem_02b_concurrent_consumers_still_call_once(self):
        """Two threads racing for the evidence must not buy two interpretations."""
        started, calls = threading.Event(), []

        def slow():
            calls.append(1)
            started.set()
            threading.Event().wait(0.05)
            return "result"

        provider = TurnSemanticEvidence(slow)
        threads = [threading.Thread(target=provider.get) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.get(), "result")

    def test_livesem_03_result_reused_by_shadow_recorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "shadow.jsonl"
            engine = make_engine(evidence_path=str(path))
            ctx = make_ctx()
            _, interpret = run_turn(engine, ctx, "mañana 15 o jueves")
            self.assertEqual(interpret.call_count, 1,
                             "the recorder read the interpretation, it did not buy one")
            self.assertTrue(path.exists(), "the same result was still recorded")
            self.assertIn("scheduling", path.read_text(encoding="utf-8"))
        self.assertEqual(engine._turn_semantic.calls, 1)

    def test_livesem_04_result_reused_by_claim_projection(self):
        engine, ctx = make_engine(), make_ctx()
        run_turn(engine, ctx, "mañana 15 o jueves")
        claims = engine._semantic_scheduling_claims(ctx.state)
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(claim.claim_type, ClaimType.SCHEDULING_PREFERENCE)
        self.assertEqual(getattr(claim.evidence_class, "value", claim.evidence_class),
                         EvidenceClass.SEMANTIC_INFERRED.value)
        self.assertEqual([b["day"] for b in claim.value], ["TOMORROW", "THURSDAY"])
        self.assertEqual(engine._turn_semantic.calls, 1)

    def test_livesem_13_no_second_model_call_anywhere_in_the_turn(self):
        """Authority, projection, reconciliation and the record share one call."""
        engine, ctx = make_engine(), make_ctx()
        with tempfile.TemporaryDirectory() as tmp:
            engine.settings.shadow_evidence_path = str(pathlib.Path(tmp) / "s.jsonl")
            _, interpret = run_turn(engine, ctx, "Mñ 15hs? O nose jueves que tenes")
            engine._semantic_turn_evidence()
            engine._semantic_scheduling_claims(ctx.state)
            engine._reconciled_scheduling_requests(
                ctx, ctx.state, ["Mñ 15hs? O nose jueves que tenes"], MONDAY)
        self.assertEqual(interpret.call_count, 1)
        self.assertEqual(engine._turn_semantic.calls, 1)


# ── the live value: branches that used to collapse ───────────────────────────

class TestLiveBranches(unittest.TestCase):

    def test_livesem_05_manana_15_o_jueves_keeps_two_branches(self):
        """The case L4.7C.4 could not fix: the parser dropped the PRIMARY entirely."""
        from app.services.conversation_engine import _parse_scheduling_requests
        text = "mañana 15 o jueves"
        legacy = [(r.day_iso, r.time_str) for r in _parse_scheduling_requests([text], MONDAY)]
        self.assertEqual(legacy, [("2026-09-03", None)],
                         "the deterministic parser still collapses this burst")
        engine, ctx = make_engine(), make_ctx()
        requests, _ = run_turn(engine, ctx, text)
        self.assertEqual(branches(requests),
                         [("2026-09-01", "15:00"), ("2026-09-03", None)])

    def test_livesem_06_wild_a_04_two_branches_live(self):
        engine, ctx = make_engine(), make_ctx()
        requests, _ = run_turn(engine, ctx, "Mñ 15hs? O nose jueves que tenes")
        self.assertEqual(branches(requests),
                         [("2026-09-01", "15:00"), ("2026-09-03", None)])

    def test_livesem_07_clause_local_time_preserved(self):
        """The 15:00 belongs to tomorrow. It is never transplanted onto Thursday."""
        engine, ctx = make_engine(), make_ctx()
        requests, _ = run_turn(engine, ctx, "mañana 15 o jueves")
        self.assertEqual(requests[1].time_str, None)
        claims = engine._semantic_scheduling_claims(ctx.state)
        decision = reconcile_scheduling(claims, today=MONDAY)
        self.assertTrue(decision.branches[1].flexible_time)
        self.assertEqual(decision.branches[0].priority, "PRIMARY")
        self.assertEqual(decision.branches[1].priority, "FALLBACK")

    def test_a_day_without_a_stated_time_is_flexible_whatever_the_model_says(self):
        """Flexibility is a fact about the burst, not a model opinion."""
        from app.services.claim_projection import claims_from_turn_evidence
        evidence = TurnEvidence(scheduling_requests=(
            SchedulingRequestEvidence(priority=SchedulingPriority.PRIMARY,
                                      day_expression="THURSDAY", time=None,
                                      flexible_time=False, rank=1,
                                      status=EvidenceStatus.CONFIRMED),))
        claim = [c for c in claims_from_turn_evidence(evidence, texts=["jueves"])
                 if c.claim_type == ClaimType.SCHEDULING_PREFERENCE][0]
        self.assertTrue(claim.value[0]["flexible"])


# ── failure is absence, never a guess ────────────────────────────────────────

class TestFailureIsAbsence(unittest.TestCase):

    def deterministic_only(self, engine, ctx, text):
        from app.services.conversation_engine import _parse_scheduling_requests
        requests = engine._reconciled_scheduling_requests(ctx, ctx.state, [text], MONDAY)
        self.assertEqual(branches(requests),
                         [(r.day_iso, r.time_str)
                          for r in _parse_scheduling_requests([text], MONDAY)])
        return requests

    def test_livesem_08_timeout_does_not_guess(self):
        release = threading.Event()
        engine = make_engine(timeout=0.05)
        ctx = make_ctx()
        text = "Mñ 15hs? O nose jueves que tenes"

        def stalled(*_a, **_k):
            release.wait(3)
            return interpretation(turn_evidence_for(text))

        # Dispatch is declined, so nothing runs on a worker; the provider is asked directly
        # and must give up rather than hold the turn open.
        provider = TurnSemanticEvidence(lambda: stalled(), thread_id=7, burst_id="b")
        provider.start(submit=lambda run: bool(
            threading.Thread(target=run, daemon=True).start()) or True)
        engine._turn_semantic = provider
        engine._turn_semantic_texts = [text]
        try:
            self.assertIsNone(engine._semantic_turn_evidence())
            self.assertTrue(provider.timed_out)
            self.assertEqual(engine._semantic_scheduling_claims(ctx.state), [])
            self.deterministic_only(engine, ctx, text)
        finally:
            release.set()

    def test_livesem_09_model_failure_does_not_guess(self):
        engine, ctx = make_engine(), make_ctx()
        text = "Mñ 15hs? O nose jueves que tenes"

        def boom(*_a, **_k):
            raise RuntimeError("connection reset")

        requests, _ = run_turn(engine, ctx, text, interpret_side_effect=boom)
        self.assertIsNone(engine._semantic_turn_evidence())
        self.assertEqual(branches(requests), [("2026-09-01", "15:00"), ("2026-09-03", None)])
        self.deterministic_only(engine, ctx, text)

    def test_livesem_10_invalid_schema_does_not_progress_unsafely(self):
        """`ok=False` is not evidence. A malformed answer cannot create a branch."""
        engine, ctx = make_engine(), make_ctx()
        text = "mañana 15 o jueves"
        requests, _ = run_turn(engine, ctx, text,
                               interpret_side_effect=lambda *a, **k: interpretation(
                                   None, ok=False, error="ValueError: bad json"))
        self.assertIsNone(engine._semantic_turn_evidence())
        self.assertEqual(branches(requests), [("2026-09-03", None)])
        self.deterministic_only(engine, ctx, text)

    def test_a_declined_dispatch_still_produces_evidence_for_the_turn(self):
        """A full shadow queue must cost records, never correctness."""
        calls = []
        provider = TurnSemanticEvidence(lambda: (calls.append(1), "value")[1])
        provider.start(submit=lambda run: False)      # queue full
        self.assertEqual(provider.get(timeout=1), "value")
        self.assertEqual(len(calls), 1)


# ── producer precedence: enrichment yes, contradiction no ────────────────────

class TestProducerPrecedence(unittest.TestCase):

    def det_claim(self, branches_):
        return ClaimEvidence(claim_type=ClaimType.SCHEDULING_PREFERENCE,
                             value=tuple(branches_),
                             evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
                             producer="ce:_parse_scheduling_requests",
                             explicitness=Explicitness.STATED, cycle_id="c1").with_id()

    def sem_claim(self, branches_):
        return ClaimEvidence(claim_type=ClaimType.SCHEDULING_PREFERENCE,
                             value=tuple(branches_),
                             evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                             producer="semantic:understand",
                             explicitness=Explicitness.IMPLIED, cycle_id="c1").with_id()

    def test_enrichment_is_accepted(self):
        det = self.det_claim([{"resolved_date": "2026-09-03", "time": None}])
        sem = self.sem_claim([{"day": "TOMORROW", "time": "15:00", "flexible": False},
                              {"day": "THURSDAY", "time": None, "flexible": True}])
        decision = reconcile_scheduling([det, sem], today=MONDAY)
        self.assertEqual(decision.source, "semantic")
        self.assertEqual([b.resolved_date for b in decision.branches],
                         ["2026-09-01", "2026-09-03"])
        self.assertTrue(decision.superseded, "the deterministic reading is kept, not erased")

    def test_a_contradicted_time_keeps_the_certified_reading(self):
        det = self.det_claim([{"resolved_date": "2026-09-03", "time": "10:00"}])
        sem = self.sem_claim([{"day": "THURSDAY", "time": "16:00", "flexible": False}])
        decision = reconcile_scheduling([det, sem], today=MONDAY)
        self.assertEqual(decision.source, "deterministic_conflict")
        self.assertEqual(decision.branches[0].time, "10:00")

    def test_a_lost_deterministic_branch_keeps_the_certified_reading(self):
        det = self.det_claim([{"resolved_date": "2026-09-01", "time": None},
                              {"resolved_date": "2026-09-04", "time": None}])
        sem = self.sem_claim([{"day": "TOMORROW", "time": None, "flexible": True}])
        decision = reconcile_scheduling([det, sem], today=MONDAY)
        self.assertEqual(decision.source, "deterministic_conflict")
        self.assertEqual([b.resolved_date for b in decision.branches],
                         ["2026-09-01", "2026-09-04"])

    def test_reordering_is_a_contradiction(self):
        det = self.det_claim([{"resolved_date": "2026-09-01", "time": None},
                              {"resolved_date": "2026-09-03", "time": None}])
        sem = self.sem_claim([{"day": "THURSDAY", "time": None, "flexible": True},
                              {"day": "TOMORROW", "time": None, "flexible": True}])
        self.assertFalse(semantic_covers_deterministic(
            [SimpleNamespace(resolved_date="2026-09-03", time=None),
             SimpleNamespace(resolved_date="2026-09-01", time=None)],
            [SimpleNamespace(resolved_date="2026-09-01", time=None),
             SimpleNamespace(resolved_date="2026-09-03", time=None)]))
        self.assertEqual(reconcile_scheduling([det, sem], today=MONDAY).source,
                         "deterministic_conflict")

    def test_absence_of_a_time_is_never_a_contradiction(self):
        self.assertTrue(semantic_covers_deterministic(
            [SimpleNamespace(resolved_date="2026-09-03", time="10:00")],
            [SimpleNamespace(resolved_date="2026-09-03", time=None)]))


# ── layer boundaries ─────────────────────────────────────────────────────────

class TestBoundaries(unittest.TestCase):

    def test_livesem_11_requested_is_not_available(self):
        code = code_of(RECONCILER_SOURCE)
        for forbidden in ("ScheduleService", "available_slots", "TravelProvider",
                          "business_hours_for_weekday", "session", "Session"):
            self.assertNotIn(forbidden, code,
                             f"the interpretation layer must not reach {forbidden}")
        engine, ctx = make_engine(), make_ctx()
        run_turn(engine, ctx, "mañana 15 o jueves")
        decision = reconcile_scheduling(engine._semantic_scheduling_claims(ctx.state),
                                        today=MONDAY)
        record = to_record(decision)
        self.assertTrue(record["requested_only"])
        self.assertTrue(all(b.is_request_only for b in decision.branches))
        self.assertNotIn("available", record)

    def test_livesem_12_available_is_not_booked(self):
        code = code_of(RECONCILER_SOURCE) + code_of(PROVIDER_SOURCE)
        for forbidden in ("ThreadRevision", "booked", "BookingFlow", "make_booking_token"):
            self.assertNotIn(forbidden, code)
        tree = ast.parse(CE_SOURCE)
        writers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if 'status="booked"' in ast.unparse(node) or "'booked'" in ast.unparse(node):
                writers.add(node.name)
        self.assertTrue(writers <= {"_process_flow_response"},
                        f"booking is still the Flow's alone; found {sorted(writers)}")

    def test_livesem_14_prior_cycle_context_excluded(self):
        """Bounded context: this cycle's previous customer turn, never the thread."""
        ctx = make_ctx(db_messages=[
            SimpleNamespace(direction="in", text="hola, un Focus 2019"),
            SimpleNamespace(direction="out", text="te paso el precio"),
        ])
        engine = make_engine()
        context = engine._build_shadow_context(ctx, ["mañana 15 o jueves"])
        self.assertEqual(context.previous_customer_turn, "hola, un Focus 2019")
        self.assertEqual(context.stage, "SCHEDULING")
        source = function_source("_build_shadow_context")
        self.assertIn("ctx.db_messages", source)
        self.assertNotIn("all_messages", source)
        # The claim carries the cycle, so a finished cycle can never be read as this one.
        run_turn(engine, ctx, "mañana 15 o jueves")
        claims = engine._semantic_scheduling_claims(ctx.state)
        self.assertEqual(claims[0].cycle_id, "11")

    def test_livesem_15_c2_vehicle_and_location_unchanged(self):
        """The vehicle and location chokepoints do not consume semantic evidence."""
        for name in ("_apply_vehicle_identity", "_apply_inspection_zone"):
            source = function_source(name)
            self.assertNotIn("_semantic_turn_evidence", source)
            self.assertNotIn("_semantic_scheduling_claims", source)
        from app.services.field_reconciler import reconcile_vehicle_identity
        claims = [ClaimEvidence(claim_type=ClaimType.VEHICLE_MODEL, value="FOCUS",
                                evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
                                producer="ce:ce", explicitness=Explicitness.STATED,
                                cycle_id="c1").with_id()]
        decision = reconcile_vehicle_identity(claims, catalog_lookup=lambda **_k: None)
        self.assertEqual(decision.rule_id, "reconcile.vehicle_identity")

    def test_livesem_16_c3b_acceptance_unchanged(self):
        source = function_source("_authorize_acceptance")
        self.assertNotIn("_semantic_turn_evidence", source)
        self.assertIn("if _is_acceptance(texts):", source)
        from app.services.conversation_engine import _is_acceptance
        self.assertFalse(_is_acceptance(["Bueno, quería revisar una 2008"]),
                         "an acceptance-shaped word inside a longer sentence is not a stance")
        self.assertTrue(_is_acceptance(["Si avancemos"]))

    def test_livesem_17_c4_scheduling_rules_unchanged(self):
        """One producer behaves exactly as it did in L4.7C.4."""
        self.assertEqual(RULE_ID, "reconcile.scheduling_preference")
        for expression, today, expected in (("TOMORROW", MONDAY, "2026-09-01"),
                                            ("THURSDAY", MONDAY, "2026-09-03"),
                                            ("MONDAY", MONDAY, "2026-09-07"),
                                            ("TOMORROW", date(2028, 2, 28), "2028-02-29")):
            self.assertEqual(resolve_day_expression(expression, today), expected)
        sem = ClaimEvidence(claim_type=ClaimType.SCHEDULING_PREFERENCE,
                            value=({"day": "TOMORROW", "time": "15:00", "flexible": False},
                                   {"day": "THURSDAY", "time": None, "flexible": True}),
                            evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                            producer="semantic:understand",
                            explicitness=Explicitness.IMPLIED, cycle_id="c1").with_id()
        decision = reconcile_scheduling([sem], today=MONDAY)
        self.assertEqual(decision.source, "semantic")
        self.assertEqual([(b.priority, b.resolved_date, b.time) for b in decision.branches],
                         [("PRIMARY", "2026-09-01", "15:00"),
                          ("FALLBACK", "2026-09-03", None)])

    def test_livesem_18_feature_flag_rollback(self):
        """Flag off restores L4.7C.4's producer timing exactly."""
        from app.services.conversation_engine import _parse_scheduling_requests
        text = "mañana 15 o jueves"
        off = make_engine(same_turn=False)
        ctx = make_ctx()
        requests, interpret = run_turn(off, ctx, text)
        self.assertIsNone(off._semantic_turn_evidence())
        self.assertEqual(off._semantic_scheduling_claims(ctx.state), [])
        self.assertEqual(branches(requests),
                         [(r.day_iso, r.time_str)
                          for r in _parse_scheduling_requests([text], MONDAY)])
        self.assertEqual(interpret.call_count, 1, "the shadow record is still produced")
        live = make_engine(same_turn=True)
        self.assertTrue(live._same_turn_semantic_on())
        live.settings = MagicMock()
        self.assertFalse(live._same_turn_semantic_on(),
                         "a test double must never make a turn wait on a model")

    def test_the_default_is_off_everywhere(self):
        from app.settings import Settings
        defaults = Settings()
        self.assertFalse(defaults.semantic_same_turn_enabled)
        self.assertFalse(defaults.reconciler_scheduling_authority_enabled)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
