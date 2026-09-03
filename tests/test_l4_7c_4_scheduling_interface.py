"""L4.7C.4 — one interpretation of the request, one resolver for the date.

Four stages stay apart: what was asked (semantic), what date that means (deterministic
resolver), what is possible (ScheduleService), what was confirmed (Booking Flow). These tests
hold the first two and prove the last two were not touched.

SCHEDCUT-01 relative TOMORROW preserved     SCHEDCUT-11 future intent cannot confirm a slot
SCHEDCUT-02 deterministic date resolution   SCHEDCUT-12 single interpretation path
SCHEDCUT-03 primary/fallback order          SCHEDCUT-13 legacy helper cannot write alone
SCHEDCUT-04 clause-local time               SCHEDCUT-14 flag rollback
SCHEDCUT-05 flexible day                    SCHEDCUT-15 cycle scoping
SCHEDCUT-06 time band                       SCHEDCUT-16 WILD-A-04
SCHEDCUT-07 scheduling correction           SCHEDCUT-17 business hours unchanged
SCHEDCUT-08 requested != available          SCHEDCUT-18 C2 vehicle/location unchanged
SCHEDCUT-09 available != booked             SCHEDCUT-19 C3B acceptance unchanged
SCHEDCUT-10 acceptance + scheduling coexist SCHEDCUT-20 booking authority unchanged
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

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
from app.services.scheduling_reconciler import (  # noqa: E402
    reconcile_scheduling,
    resolve_day_expression,
    to_record,
)

def code_of(path: pathlib.Path) -> str:
    """Executable code only: docstrings and comments are documentation, not behaviour."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)
    return ast.unparse(tree)


CE_SOURCE = (ROOT / "backend" / "app" / "services" / "conversation_engine.py").read_text()
MONDAY = date(2026, 8, 31)          # tomorrow is Tuesday, Thursday is three days out


def semantic_claim(branches, cycle_id="cycle-1"):
    """A claim in the shape the semantic interpreter produces (day expressions, not dates)."""
    return ClaimEvidence(claim_type=ClaimType.SCHEDULING_PREFERENCE, value=tuple(branches),
                         evidence_class=EvidenceClass.SEMANTIC_INFERRED,
                         producer="semantic:understand", explicitness=Explicitness.IMPLIED,
                         cycle_id=cycle_id).with_id()


# ── interpretation ────────────────────────────────────────────────────────────

class TestInterpretation(unittest.TestCase):

    def test_schedcut_01_relative_day_preserved_and_resolved(self):
        decision = reconcile_scheduling(
            [semantic_claim([{"priority": "PRIMARY", "day": "TOMORROW", "time": "15:00",
                              "flexible": False, "rank": 1}])], today=MONDAY)
        branch = decision.primary
        self.assertEqual(branch.day_expression, "TOMORROW")
        self.assertEqual(branch.resolved_date, "2026-09-01")
        self.assertEqual(branch.time, "15:00")
        self.assertFalse(branch.flexible_time)

    def test_schedcut_02_deterministic_resolution(self):
        """Calendar arithmetic lives in one function, and no model touches it."""
        cases = [("TODAY", MONDAY, "2026-08-31"), ("TOMORROW", MONDAY, "2026-09-01"),
                 ("DAY_AFTER_TOMORROW", MONDAY, "2026-09-02"),
                 ("THURSDAY", MONDAY, "2026-09-03"),
                 ("MONDAY", MONDAY, "2026-09-07"),              # never today
                 ("TOMORROW", date(2026, 9, 30), "2026-10-01"),  # month boundary
                 ("TOMORROW", date(2026, 12, 31), "2027-01-01"),  # year boundary
                 ("TOMORROW", date(2028, 2, 28), "2028-02-29"),   # leap year
                 ("TOMORROW", date(2026, 9, 5), "2026-09-06")]    # Saturday → Sunday
        for expression, today, expected in cases:
            self.assertEqual(resolve_day_expression(expression, today), expected,
                             f"{expression} from {today}")
        self.assertIsNone(resolve_day_expression("SOMEDAY", MONDAY))
        self.assertIsNone(resolve_day_expression(None, MONDAY))

    def test_schedcut_03_primary_and_fallback_order_preserved(self):
        decision = reconcile_scheduling(
            [semantic_claim([{"priority": "PRIMARY", "day": "TOMORROW", "time": "15:00",
                              "flexible": False, "rank": 1},
                             {"priority": "FALLBACK", "day": "THURSDAY", "time": None,
                              "flexible": True, "rank": 2}])], today=MONDAY)
        self.assertEqual([(b.priority, b.day_expression) for b in decision.branches],
                         [("PRIMARY", "TOMORROW"), ("FALLBACK", "THURSDAY")])

    def test_schedcut_04_time_stays_in_its_own_clause(self):
        decision = reconcile_scheduling(
            [semantic_claim([{"priority": "PRIMARY", "day": "TOMORROW", "time": "15:00",
                              "flexible": False, "rank": 1},
                             {"priority": "FALLBACK", "day": "THURSDAY", "time": None,
                              "flexible": True, "rank": 2}])], today=MONDAY)
        first, second = decision.branches
        self.assertEqual(first.time, "15:00")
        self.assertIsNone(second.time, "a time never migrates to the other branch")
        self.assertTrue(second.flexible_time)

    def test_schedcut_05_day_without_a_time_is_flexible(self):
        decision = reconcile_scheduling(
            [semantic_claim([{"priority": "PRIMARY", "day": "THURSDAY", "time": None,
                              "rank": 1}])], today=MONDAY)
        self.assertTrue(decision.primary.flexible_time)
        self.assertIsNone(decision.primary.time, "no time is invented")

    def test_schedcut_06_time_band_is_not_a_time(self):
        decision = reconcile_scheduling(
            [semantic_claim([{"priority": "PRIMARY", "day": "THURSDAY", "time": None,
                              "flexible": True, "time_band": "TARDE", "rank": 1}])],
            today=MONDAY)
        self.assertEqual(decision.primary.time_band, "TARDE")
        self.assertIsNone(decision.primary.time)
        self.assertTrue(decision.primary.flexible_time)

    def test_schedcut_07_correction_supersedes_without_erasing(self):
        first = semantic_claim([{"priority": "PRIMARY", "day": "WEDNESDAY", "rank": 1}])
        corrected = semantic_claim([{"priority": "PRIMARY", "day": "THURSDAY", "rank": 1}])
        decision = reconcile_scheduling([first, corrected], today=MONDAY)
        self.assertEqual(decision.primary.day_expression, "THURSDAY")
        self.assertEqual([b.day_expression for b in decision.superseded], ["WEDNESDAY"])
        self.assertTrue(all(b.superseded for b in decision.superseded))

    def test_schedcut_15_cycle_scoping(self):
        from app.services.claim_projection import in_cycle
        stale = semantic_claim([{"day": "MONDAY", "rank": 1}], cycle_id="cycle-old")
        current = semantic_claim([{"day": "THURSDAY", "rank": 1}], cycle_id="cycle-1")
        scoped = in_cycle([stale, current], "cycle-1")
        self.assertEqual(len(scoped), 1)
        self.assertEqual(reconcile_scheduling(scoped, today=MONDAY).primary.day_expression,
                         "THURSDAY")

    def test_schedcut_16_wild_a_04(self):
        """"Mñ 15hs? O nose jueves que tenes" through the live CE producer."""
        from app.services.conversation_engine import (_detect_time_period,
                                                      _parse_scheduling_requests)
        texts = ["Mñ 15hs? O nose jueves que tenes"]
        legacy = _parse_scheduling_requests(texts, MONDAY)
        branches = [{"priority": ("PRIMARY" if i == 0 else "FALLBACK"), "rank": i + 1,
                     "resolved_date": r.day_iso, "time": r.time_str,
                     "flexible": r.time_str is None}
                    for i, r in enumerate(legacy)]
        decision = reconcile_scheduling(
            [ClaimEvidence(claim_type=ClaimType.SCHEDULING_PREFERENCE, value=tuple(branches),
                           evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
                           explicitness=Explicitness.STATED).with_id()], today=MONDAY)
        self.assertEqual(len(decision.branches), 2)
        primary, fallback = decision.branches
        self.assertEqual((primary.priority, primary.resolved_date, primary.time),
                         ("PRIMARY", "2026-09-01", "15:00"))
        self.assertFalse(primary.flexible_time)
        self.assertEqual((fallback.priority, fallback.resolved_date, fallback.time),
                         ("FALLBACK", "2026-09-03", None))
        self.assertTrue(fallback.flexible_time)


# ── the three states stay distinct ────────────────────────────────────────────

class TestStateSeparation(unittest.TestCase):

    def test_schedcut_08_requested_is_not_available(self):
        """The reconciler cannot reach ScheduleService, so it cannot claim availability."""
        module = (ROOT / "backend" / "app" / "services" / "scheduling_reconciler.py").read_text()
        tree = ast.parse(module)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("schedule", "models", "db", "conversation_engine", "travel",
                          "booking_flow_service", "pricing"):
            self.assertFalse(any(forbidden in m for m in imported),
                             f"the scheduling reconciler must not reach {forbidden}")
        decision = reconcile_scheduling(
            [semantic_claim([{"day": "THURSDAY", "rank": 1}])], today=MONDAY)
        self.assertTrue(decision.primary.is_request_only)
        self.assertTrue(to_record(decision)["requested_only"])

    def test_schedcut_09_available_is_not_booked(self):
        code = code_of(ROOT / "backend" / "app" / "services" / "scheduling_reconciler.py")
        for forbidden in ("booked", "ThreadRevision", "AVAILABLE", "CONFIRMED"):
            self.assertNotIn(forbidden, code,
                             f"the reconciler must not express {forbidden}")
        self.assertIn('status="booked"', CE_SOURCE,
                      "booking still happens only on the Flow path")

    def test_schedcut_11_future_intent_cannot_confirm_a_slot(self):
        """"después te confirmo" carries no scheduling branch at all."""
        from app.services.conversation_engine import _parse_scheduling_requests
        self.assertEqual(_parse_scheduling_requests(["después te confirmo"], MONDAY), [])
        self.assertEqual(reconcile_scheduling([], today=MONDAY).branches, ())

    def test_an_availability_question_invents_no_day(self):
        from app.services.conversation_engine import _parse_scheduling_requests
        self.assertEqual(_parse_scheduling_requests(["qué horarios tienen?"], MONDAY), [])


# ── the single path, the flag, and the untouched neighbours ───────────────────

class TestCutover(unittest.TestCase):

    def engine(self, scheduling=False):
        from app.services.conversation_engine import ConversationEngine
        engine = ConversationEngine.__new__(ConversationEngine)
        engine.db = MagicMock()
        engine.settings = SimpleNamespace(reconciler_scheduling_authority_enabled=scheduling)
        engine._correlation_id = "corr-c4"
        return engine

    def ctx(self):
        return SimpleNamespace(thread=SimpleNamespace(id=5),
                               state=SimpleNamespace(current_cycle_start_message_db_id=3,
                                                     current_revision_id=None))

    def test_schedcut_12_single_interpretation_path(self):
        """Every multi-branch call site goes through the chokepoint."""
        self.assertEqual(CE_SOURCE.count(
            "sched_requests = self._reconciled_scheduling_requests("), 2)
        tree = ast.parse(CE_SOURCE)
        chokepoint = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                           and n.name in ("_reconciled_scheduling_requests",
                                          "_scheduling_claims_from_texts")), None)
        self.assertIsNotNone(chokepoint)
        allowed = {id(n) for f in ast.walk(tree)
                   if isinstance(f, ast.FunctionDef)
                   and f.name in ("_reconciled_scheduling_requests",
                                  "_scheduling_claims_from_texts")
                   for n in ast.walk(f)}
        offenders = [n.lineno for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and id(n) not in allowed
                     and isinstance(n.func, ast.Name)
                     and n.func.id == "_parse_scheduling_requests"]
        self.assertEqual(offenders, [],
                         f"multi-branch parsing outside the chokepoint at {offenders}")

    def test_schedcut_13_legacy_helper_is_an_evidence_producer(self):
        """The legacy parser still runs — as evidence, inside the single path."""
        self.assertIn("legacy = _parse_scheduling_requests(texts, today)", CE_SOURCE)
        self.assertIn("producer=\"ce:_parse_scheduling_requests\"", CE_SOURCE)
        self.assertIn("EvidenceClass.DETERMINISTIC_EXTRACTED", CE_SOURCE)

    def test_schedcut_14_flag_rollback(self):
        legacy_engine = self.engine(scheduling=False)
        self.assertFalse(legacy_engine._scheduling_authority_on())
        self.assertIn("if not self._scheduling_authority_on():\n"
                      "            return _parse_scheduling_requests(texts, today)", CE_SOURCE)
        live = self.engine(scheduling=True)
        self.assertTrue(live._scheduling_authority_on())
        live.settings = MagicMock()
        self.assertFalse(live._scheduling_authority_on(),
                         "a test double must never enable authority")

    def test_both_flag_positions_agree_on_the_live_examples(self):
        """Flag on and off produce the same requested branches for today's producer."""
        from app.services.conversation_engine import _parse_scheduling_requests
        for text in ["Mñ 15hs? O nose jueves que tenes", "mañana a las 15", "jueves",
                     "hoy 11hs", "qué horarios tienen?"]:
            off = self.engine(False)._reconciled_scheduling_requests(
                self.ctx(), self.ctx().state, [text], MONDAY)
            on = self.engine(True)._reconciled_scheduling_requests(
                self.ctx(), self.ctx().state, [text], MONDAY)
            self.assertEqual([(r.day_iso, r.time_str) for r in off],
                             [(r.day_iso, r.time_str) for r in on], text)
            self.assertEqual([(r.day_iso, r.time_str) for r in off],
                             [(r.day_iso, r.time_str)
                              for r in _parse_scheduling_requests([text], MONDAY)], text)

    def test_schedcut_10_acceptance_and_scheduling_coexist(self):
        """"Sí avancemos, mañana a las 15 puede ser?" carries both facts."""
        from app.services.acceptance_authorizer import (CommercialState,
                                                        authorize_quote_acceptance)
        from app.services.claim_projection import claims_from_turn_evidence
        from app.schemas.turn_evidence import (AcceptanceEvidence, AcceptanceSignal,
                                               EvidenceStatus, TurnEvidence)
        text = "Sí avancemos, mañana a las 15 puede ser?"
        evidence = TurnEvidence(acceptance=AcceptanceEvidence(
            signal=AcceptanceSignal.ACCEPT, value=True, status=EvidenceStatus.CONFIRMED))
        claims = claims_from_turn_evidence(evidence, texts=[text], cycle_id="c1")
        state = CommercialState(cycle_id="c1", revision_id=1, candidate_id=2,
                                quote_total=95000, quote_candidate_id=2, quote_cycle_id="c1",
                                quote_tipo_vehiculo="AUTO", current_tipo_vehiculo="AUTO",
                                quote_zone_group="Sur", current_zone_group="Sur",
                                quote_zone_detail="Berazategui",
                                current_zone_detail="Berazategui",
                                delivered_amounts=(95000,), quote_delivered=True)
        self.assertEqual(authorize_quote_acceptance(claims, state).result, "ALLOW")
        from app.services.conversation_engine import _parse_scheduling_requests
        branches = _parse_scheduling_requests([text], MONDAY)
        self.assertEqual([(r.day_iso, r.time_str) for r in branches],
                         [("2026-09-01", "15:00")], "the scheduling request survives too")

    def test_schedcut_17_business_hours_and_travel_unchanged(self):
        """Schedule policy is untouched, and lives nowhere near the interpretation layer."""
        from datetime import time as _time
        from app.services.schedule import SERVICE_MINUTES, business_hours_for_weekday
        from app.services.travel import ZoneTravelProvider

        self.assertEqual(SERVICE_MINUTES, 45, "inspection duration unchanged")
        expected = {0: (_time(13, 0), _time(18, 0), False),   # Monday    13-18
                    1: (_time(9, 30), _time(14, 0), False),   # Tuesday   9.30-14
                    2: (_time(9, 0), _time(18, 0), False),    # Wednesday 9-18
                    3: (_time(9, 0), _time(14, 0), False),    # Thursday  9-14
                    4: (_time(9, 0), _time(18, 0), False),    # Friday    9-18
                    5: (_time(9, 0), _time(15, 0), False),    # Saturday  9-15
                    6: (_time(9, 0), _time(9, 0), True)}      # Sunday    closed
        for weekday, hours in expected.items():
            self.assertEqual(business_hours_for_weekday(weekday), hours, weekday)

        travel = ZoneTravelProvider()
        self.assertEqual(travel.get_travel_minutes("Sur", "Sur"), 30)
        self.assertEqual(travel.get_travel_minutes("CABA", "Norte"), 60)
        self.assertEqual(travel.get_travel_minutes("Norte", "Sur"), 90)
        # NOTE for the record: the certified implementation returns 0 for a missing group
        # ("no constraint applied"), not 30. L4.7C.4 changed neither — schedule policy is
        # out of scope here — and the discrepancy with the milestone brief is reported.
        self.assertEqual(travel.get_travel_minutes(None, "Sur"), 0,
                         "missing group → no constraint, as certified")
        self.assertEqual(travel.get_travel_minutes("Sur", "Desconocido"), 90,
                         "an unknown pair falls back to the cross-GBA maximum")

        code = code_of(ROOT / "backend" / "app" / "services" / "scheduling_reconciler.py")
        for forbidden in ("SERVICE_MINUTES", "business_hours", "travel", "45", "90"):
            self.assertNotIn(forbidden, code,
                             "schedule policy stays in ScheduleService")

    def test_schedcut_18_c2_vehicle_location_unchanged(self):
        self.assertIn("def _apply_vehicle_identity", CE_SOURCE)
        self.assertIn("def _apply_inspection_zone", CE_SOURCE)
        self.assertIn("reconcile.vehicle_identity", (ROOT / "backend" / "app" / "services"
                                                     / "field_reconciler.py").read_text())

    def test_schedcut_19_c3b_acceptance_unchanged(self):
        self.assertIn("_authorize_acceptance", CE_SOURCE)
        self.assertIn("if _is_acceptance(texts):", CE_SOURCE)
        self.assertIn("authorize.quote_acceptance",
                      (ROOT / "backend" / "app" / "services"
                       / "acceptance_authorizer.py").read_text())

    def test_schedcut_20_booking_authority_unchanged(self):
        self.assertIn("_process_flow_response", CE_SOURCE)
        self.assertIn('status="booked"', CE_SOURCE)
        self.assertNotIn("booked",
                         code_of(ROOT / "backend" / "app" / "services"
                                 / "scheduling_reconciler.py"))


if __name__ == "__main__":
    unittest.main()
