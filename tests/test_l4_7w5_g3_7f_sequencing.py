"""G3-7F HYBRID-EVIDENCE SEQUENCING — the corrected order, and what it protects.

The sequencing audit established that the inherited clarification decision ran at
`_routing_gate`, 207 lines before `_apply_candidate`, where this turn's RECONCILED
PROPOSAL does not exist yet. It therefore re-derived vehicle identity itself and answered a
customer who said "Focus 2008" with a confirmation for a Peugeot 2008.

This suite proves the corrected sequence structurally (not from comments), proves the
degraded-mode policy row by row, and proves the real-customer protections that the
placement defect broke.

Nothing here asserts on a comment, a docstring or a sentence of outbound prose: the
clarification decision is a typed function of typed facts, and that is what is measured.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import types
import unittest
from unittest.mock import MagicMock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import clarification_attempt as ca            # noqa: E402
from app.services import clarification_router as cr             # noqa: E402
from app.services.conversation_engine import ConversationEngine as E  # noqa: E402

Route = cr.ClarificationRoute
Why = cr.ClarificationReason
Fam = ca.ClarificationFamily
St = ca.AttemptStatus

ENGINE_SRC = inspect.getsource(sys.modules[E.__module__])


def code_only(fn) -> str:
    """Source with docstrings stripped, so a docstring can never satisfy an assertion."""
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Module)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def code_only_module(mod) -> str:
    """A module's executable source, docstrings removed."""
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ══ A — the sequence itself ══════════════════════════════════════════════════════

class A_Sequencing(unittest.TestCase):
    """Order proven from executable control flow, never from a comment."""

    def setUp(self):
        self.src = ENGINE_SRC

    def _pos(self, needle):
        self.assertIn(needle, self.src, f"anchor missing: {needle}")
        return self.src.index(needle)

    def test_seq_01_semantic_is_dispatched_before_reconciliation(self):
        semantic = self._pos("self._run_shadow_understand(")
        reconcile = self._pos("_proposals = (self._reconciled_reply_proposals(")
        self.assertLess(semantic, reconcile)

    def test_seq_02_typed_deterministic_evidence_exists_before_reconciliation(self):
        detect = self._pos("pre_detected_vehicle = lookup_vehicle(")
        reconcile = self._pos("_proposals = (self._reconciled_reply_proposals(")
        self.assertLess(detect, reconcile)

    def test_seq_03_the_reconciled_proposal_exists_before_the_decision(self):
        reconcile = self._pos("_proposals = (self._reconciled_reply_proposals(")
        cand = self._pos('_cand_data = _proposals.get("candidate")')
        decision = self._pos("_clar = self._turn_clarification_decision(")
        self.assertLess(reconcile, cand)
        self.assertLess(cand, decision)

    def test_seq_04_the_decision_precedes_canonical_mutation(self):
        decision = self._pos("_clar = self._turn_clarification_decision(")
        write = self.src.index("_outcome = self._apply_candidate(", decision)
        self.assertLess(decision, write)

    def test_seq_05_exactly_one_decision_call_site_and_one_executor(self):
        self.assertEqual(
            self.src.count("_clar = self._turn_clarification_decision("), 1)
        self.assertEqual(self.src.count("self._execute_clarification("), 1)

    def test_seq_06_only_the_executor_can_reserve(self):
        """No other method may call `reserve`. One reservation authority, by construction."""
        offenders = []
        tree = ast.parse(self.src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = ast.unparse(node)
            if ".reserve(" in body and node.name != "_execute_clarification":
                offenders.append(node.name)
        self.assertEqual(offenders, [],
                         f"these also reserve an attempt: {offenders}")

    def test_seq_07_the_decision_is_memoized_so_a_turn_cannot_decide_twice(self):
        body = code_only(E._turn_clarification_decision)
        self.assertIn("_turn_clarification_decision_obj", body)
        self.assertIn("_turn_clarification_decision_count", body)

    def test_seq_08_the_decision_is_not_behind_either_correction_flag(self):
        i = self._pos("_clar = self._turn_clarification_decision(")
        guard_start = self.src.rindex("\n        ", 0, i - 400)
        window = self.src[guard_start:i]
        self.assertNotIn("_provisional_correction_on()", window)
        self.assertNotIn("_correction_grounding_on()", window)


# ══ B — the pure router: typed inputs only ═══════════════════════════════════════

class B_RouterPurity(unittest.TestCase):

    def test_seq_09_the_router_takes_no_text_argument(self):
        params = inspect.signature(cr.clarification_decision).parameters
        for name in params:
            self.assertNotIn("text", name.lower(), f"{name} smells like prose")
            self.assertNotIn("message", name.lower())
            self.assertNotIn("phrase", name.lower())

    def test_seq_10_every_router_input_is_a_bool(self):
        params = inspect.signature(cr.clarification_decision).parameters
        for name, prm in params.items():
            self.assertIsInstance(prm.default, bool,
                                  f"{name} default {prm.default!r} is not a boolean fact")

    def test_seq_11_the_router_module_imports_nothing_from_the_engine(self):
        # Docstrings stripped: the module docstring legitimately NAMES the phrase sets it
        # replaced, and a test that reads prose would be satisfied (or defeated) by prose.
        src = code_only_module(cr)
        for forbidden in ("lookup_vehicle", "conversation_engine", "fuzzy_lookup",
                          "_PRICE_INTENT_WORDS", "_FAQ_OR_SOFT_CLOSE_PHRASES",
                          "resolve_field_evidence", "re.compile", "import re"):
            self.assertNotIn(forbidden, src)

    def test_seq_12_the_decision_collector_never_queries_the_catalogue(self):
        """§9. The reconciler distinguishes a Ford Focus from a Peugeot 2008; the router
        must not form a second opinion by re-resolving the catalogue itself.
        """
        body = code_only(E._turn_clarification_decision)
        for forbidden in ("lookup_vehicle", "fuzzy_lookup_vehicle", "_catalog_tipo_for",
                          "_contextual_numeric_model_lookup", "extract_model_del_year"):
            self.assertNotIn(forbidden, body,
                             f"the router re-interprets the turn via {forbidden}")

    def test_seq_13_the_decision_collector_reads_no_raw_customer_text(self):
        body = code_only(E._turn_clarification_decision)
        for forbidden in ("_norm_text(", "_is_general_faq_or_soft_close",
                          "_wants_price_quote", "_has_vehicle_concern",
                          "_is_generic_vehicle_text", ".search(", ".match(", "in combined"):
            self.assertNotIn(forbidden, body)

    def test_seq_14_the_decision_collector_writes_no_canonical_state(self):
        body = code_only(E._turn_clarification_decision)
        for forbidden in ("_apply_candidate", "_apply_extracted", "_apply_vehicle_identity",
                          "_apply_inspection_zone", "_create_candidate_from_catalog",
                          "self.db.commit()", "_send_text_to_wa", "_send_flow_button"):
            self.assertNotIn(forbidden, body)


# ══ C — degraded mode, row by row (§12) ══════════════════════════════════════════

class C_DegradedMode(unittest.TestCase):
    """Neither engine's silence is a veto. Nine rows, nine assertions."""

    def test_seq_15_row1_semantic_resolves_the_family(self):
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=True),
            (Route.NO_CLARIFICATION_REQUIRED, Why.RESOLVED))

    def test_seq_16_row2_semantic_absent_deterministic_resolves(self):
        """Semantic absence is not a denial."""
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=True, location_domain_relevant=True,
            location_resolved=True),
            (Route.NO_CLARIFICATION_REQUIRED, Why.RESOLVED))

    def test_seq_17_row3_relevant_but_unresolved_gets_the_family_flow(self):
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False),
            (Route.VEHICLE_FLOW_REQUIRED, Why.VEHICLE_IDENTITY_UNRESOLVED))

    def test_seq_18_row4_required_next_information_makes_it_relevant(self):
        """Deterministic silence is not a veto either: the workflow itself needs the value."""
        self.assertEqual(cr.clarification_decision(
            required_next_information_authorized=True, location_resolved=False,
            vehicle_identity_resolved=True, vehicle_category_resolved=True),
            (Route.LOCATION_FLOW_REQUIRED, Why.LOCATION_UNRESOLVED))

    def test_seq_19_row5_a_spent_attempt_escalates_instead_of_repeating(self):
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False,
            vehicle_attempt_spent=True),
            (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_ATTEMPT_SPENT))

    def test_seq_20_row6_an_outstanding_flow_waits_and_asks_nothing(self):
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False,
            vehicle_flow_outstanding=True),
            (Route.NO_CLARIFICATION_REQUIRED, Why.VEHICLE_FLOW_OUTSTANDING))

    def test_seq_21_row7_a_competing_confirmable_proposal_goes_to_a_person(self):
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False,
            other_confirmable_proposal=True),
            (Route.GENERIC_HUMAN_REQUIRED, Why.PROPOSAL_WOULD_COEXIST))

    def test_seq_22_row8_a_human_owned_turn_asks_nothing(self):
        self.assertEqual(cr.clarification_decision(
            human_owns_turn=True, vehicle_domain_relevant=True,
            vehicle_identity_resolved=False),
            (Route.NO_CLARIFICATION_REQUIRED, Why.HUMAN_OWNS_TURN))

    def test_seq_23_row9_stored_incompleteness_is_not_current_turn_relevance(self):
        """Owner policy 2.8: an old half-filled candidate must not turn a payment question
        into a vehicle form."""
        self.assertEqual(cr.clarification_decision(
            vehicle_identity_resolved=False, location_resolved=False),
            (Route.NO_CLARIFICATION_REQUIRED, Why.DOMAIN_NOT_RELEVANT))

    def test_seq_24_vehicle_outranks_location_when_both_are_unresolved(self):
        route, _ = cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=False,
            location_domain_relevant=True, location_resolved=False)
        self.assertEqual(route, Route.VEHICLE_FLOW_REQUIRED)

    def test_seq_25_a_known_identity_with_no_category_never_gets_a_flow(self):
        """Owner policy 18: do not re-ask an identity we already hold — and never as a moto."""
        self.assertEqual(cr.clarification_decision(
            vehicle_domain_relevant=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=False),
            (Route.GENERIC_HUMAN_REQUIRED, Why.VEHICLE_CATEGORY_UNRESOLVED))

    def test_seq_26_a_grounded_correction_asks_the_typed_confirmation_not_a_flow(self):
        """§13A. A new value that merely DIFFERS from stored state is not a clarification."""
        self.assertEqual(cr.clarification_decision(
            correction_target_known=True, correction_values_known=True,
            vehicle_domain_relevant=True, vehicle_identity_resolved=False),
            (Route.CORRECTION_CONFIRMATION_REQUIRED, Why.CORRECTION_VALUES_KNOWN))

    def test_seq_27_a_correction_beside_another_live_proposal_goes_to_a_person(self):
        """Owner policy 8 outranks the confirmation: one "sí" may not have two referents."""
        self.assertEqual(cr.clarification_decision(
            correction_target_known=True, correction_values_known=True,
            other_confirmable_proposal=True),
            (Route.GENERIC_HUMAN_REQUIRED, Why.PROPOSAL_WOULD_COEXIST))

    def test_seq_28_an_ungrounded_target_does_not_reach_confirmation(self):
        """§13A ambiguous target: values known but no grounded target must not confirm."""
        route, _ = cr.clarification_decision(
            correction_target_known=False, correction_values_known=True,
            vehicle_domain_relevant=True, vehicle_identity_resolved=True,
            vehicle_category_resolved=True)
        self.assertNotEqual(route, Route.CORRECTION_CONFIRMATION_REQUIRED)


# ══ D — the attempt store: three outcomes, never two (SEQ-02) ════════════════════

class D_StoreFailureSafety(unittest.TestCase):

    def test_seq_29_the_store_reports_three_distinct_outcomes(self):
        self.assertEqual(len(set(ca.AttemptStoreStatus.ALL)), 3)
        self.assertIn(ca.AttemptStoreStatus.STORE_UNAVAILABLE, ca.AttemptStoreStatus.ALL)

    def test_seq_30_unavailable_is_not_already_attempted(self):
        res = ca.ReservationResult(ca.AttemptStoreStatus.STORE_UNAVAILABLE,
                                   fault=ca.StoreFault.FLUSH)
        self.assertFalse(res.won)
        self.assertFalse(res.already_attempted)
        self.assertFalse(res.available)

    def test_seq_31_an_empty_spent_set_is_not_permission_when_unreadable(self):
        unreadable = ca.SpentFamiliesResult(frozenset(), available=False,
                                            fault=ca.StoreFault.OPERATIONAL)
        readable = ca.SpentFamiliesResult(frozenset())
        self.assertEqual(len(unreadable), len(readable))
        self.assertNotEqual(unreadable.available, readable.available,
                            "emptiness and unavailability must be distinguishable")

    def test_seq_32_reserve_never_raises_a_database_error(self):
        """A mocked session is exactly the shape that produced the measured FlushError."""
        store = ca.ClarificationAttemptStore(MagicMock())
        res = store.reserve(thread_id=1, cycle=0, family=Fam.VEHICLE)
        self.assertEqual(res.status, ca.AttemptStoreStatus.STORE_UNAVAILABLE)
        self.assertFalse(res.already_attempted)
        self.assertIn(res.fault, ca.StoreFault.ALL)

    def test_seq_33_spent_families_never_raises_and_reports_unavailable(self):
        """Explicitly injected failure, not an incidental mock.

        A `MagicMock` bind happens to answer a SELECT with nothing, which is exactly the
        trap the audit named: an empty answer from a broken store is indistinguishable from
        an empty answer from an un-asked thread. So the failure is injected on purpose.
        """
        from sqlalchemy.exc import OperationalError
        store = ca.ClarificationAttemptStore(MagicMock())

        class _Boom:
            def __call__(self):
                raise OperationalError("SELECT 1", {}, Exception("connection lost"))

        store._Session = _Boom()
        res = store.spent_families(thread_id=1, cycle=0)
        self.assertFalse(res.available, "a failed read must not look like permission")
        self.assertEqual(res.families, frozenset())
        self.assertEqual(res.fault, ca.StoreFault.OPERATIONAL)

    def test_seq_33b_an_unreadable_store_fails_closed_to_a_person(self):
        """The decision must not dispatch on an answer it could not obtain."""
        body = code_only(E._turn_clarification_decision)
        i = body.index("spent.available")
        window = body[i:i + 600]
        self.assertIn("GENERIC_HUMAN_REQUIRED", window)
        self.assertIn("_HANDOFF_CLARIFICATION_STORE_UNAVAILABLE", window)
        self.assertIn("store_available=False", window)

    def test_seq_34_a_fault_is_a_type_never_a_driver_message(self):
        """A driver message can carry a DSN, a host or a user name. Only types escape."""
        for exc, expect in ((Exception("x"), ca.StoreFault.UNEXPECTED),):
            self.assertEqual(ca._fault_kind(exc), expect)
        src = inspect.getsource(ca)
        self.assertNotIn("str(exc)", src)
        self.assertNotIn("%s\", exc)", src)
        self.assertIn("type(exc).__name__", src)

    def test_seq_35_an_unavailable_store_produces_no_flow_and_a_person(self):
        body = code_only(E._execute_clarification)
        i = body.index("reservation.available")
        window = body[i:i + 700]
        self.assertIn("_HANDOFF_CLARIFICATION_STORE_UNAVAILABLE", window)
        for forbidden in ("_dispatch_vehicle_clarification_flow",
                          "_dispatch_location_clarification_flow"):
            self.assertNotIn(forbidden, window,
                             "a Flow must not be dispatched on an unreadable store")

    def test_seq_36_no_automatic_retry_inside_the_turn(self):
        body = code_only(E._execute_clarification)
        self.assertEqual(body.count(".reserve("), 1, "one reservation attempt per turn")
        for forbidden in ("for _ in range", "while True", "retry", "sleep"):
            self.assertNotIn(forbidden, body)

    def test_seq_37_an_illegal_transition_still_raises(self):
        """Infrastructure failure is absorbed; a programming error is not."""
        body = code_only(E.__init__) if False else inspect.getsource(
            ca.ClarificationAttemptStore.transition)
        self.assertIn("except ValueError:", body)
        self.assertIn("raise", body)


# ══ E — transport and the outstanding flag (SEQ-04) ══════════════════════════════

class E_Transport(unittest.TestCase):

    def test_seq_38_reservation_precedes_dispatch(self):
        body = code_only(E._execute_clarification)
        res = body.index(".reserve(")
        disp = min(body.index("_dispatch_vehicle_clarification_flow"),
                   body.index("_dispatch_location_clarification_flow"))
        self.assertLess(res, disp, "the attempt must be reserved before transport")

    def test_seq_39_the_flag_is_set_only_after_a_confirmed_wa_message_id(self):
        for fn in (E._dispatch_vehicle_flow_direct, E._dispatch_location_flow_direct):
            body = code_only(fn)
            flag = body.index("fallback_flow_sent = True")
            guard = body.rindex("if not sent_id:", 0, flag)
            self.assertLess(guard, flag,
                            f"{fn.__name__} sets the flag without a proven dispatch")

    def test_seq_40_blocked_and_failed_are_distinct_statuses(self):
        body = code_only(E._execute_clarification)
        self.assertIn("DISPATCH_BLOCKED", body)
        self.assertIn("DISPATCH_FAILED", body)
        self.assertIn("DISPATCHED", body)

    def test_seq_41_a_blocked_dispatch_returns_the_blocked_result_unchanged(self):
        body = code_only(E._execute_clarification)
        i = body.index("DISPATCH_BLOCKED")
        self.assertIn("return result", body[i:i + 400],
                      "the certified blocked-audit result must be preserved")

    def test_seq_42_exactly_one_outbound_message_per_clarification(self):
        """The explanatory sentence IS the Flow body: no separate duplicate text."""
        for fn in (E._dispatch_vehicle_clarification_flow,
                   E._dispatch_location_clarification_flow):
            body = code_only(fn)
            self.assertNotIn("_send_text_to_wa", body)

    def test_seq_43_the_executor_clears_nothing_that_would_reopen_an_attempt(self):
        body = code_only(E._execute_clarification)
        for forbidden in ("DELETE", "delete(", "fallback_flow_sent = False"):
            self.assertNotIn(forbidden, body)


# ══ F — one cycle identity (SEQ-05) ══════════════════════════════════════════════

class F_CycleIdentity(unittest.TestCase):

    def test_seq_44_the_reconciler_uses_the_stores_cycle_key(self):
        body = code_only(E._reconciler_cycle_id)
        self.assertIn("cycle_key", body)
        self.assertNotIn("current_cycle_started_at", body,
                         "the timestamp fallback mixed a message id with a clock")

    def test_seq_45_the_cycle_key_is_an_integer_with_a_zero_default(self):
        self.assertEqual(ca.cycle_key(types.SimpleNamespace(
            current_cycle_start_message_db_id=None)), 0)
        self.assertEqual(ca.cycle_key(types.SimpleNamespace(
            current_cycle_start_message_db_id=4217)), 4217)
        self.assertEqual(ca.cycle_key(types.SimpleNamespace()), 0)

    def test_seq_46_the_two_cycle_identities_agree(self):
        state = types.SimpleNamespace(current_cycle_start_message_db_id=99,
                                      current_cycle_started_at="2026-10-06T00:00:00Z")
        eng = E.__new__(E)
        self.assertEqual(eng._reconciler_cycle_id(state), str(ca.cycle_key(state)))

    def test_seq_47_no_automatic_cycle_creation_was_invented(self):
        body = code_only(ca.cycle_key)
        for forbidden in ("datetime", "now(", "uuid", "+ 1", "time()"):
            self.assertNotIn(forbidden, body)

    def test_seq_48_the_idempotency_key_fits_the_correlation_columns(self):
        from app.models import WhatsAppMessage, SecurityEvent
        key = ca.idempotency_key(42, 0, Fam.VEHICLE)
        self.assertEqual(len(key), 36)
        for model in (WhatsAppMessage, SecurityEvent):
            col = model.__table__.columns.get("correlation_id")
            if col is not None and getattr(col.type, "length", None):
                self.assertGreaterEqual(col.type.length, len(key))

    def test_seq_49_the_idempotency_key_is_derived_from_identity_alone(self):
        body = code_only(ca.idempotency_key)
        for forbidden in ("now(", "datetime", "random", "token", "text", "wa_id"):
            self.assertNotIn(forbidden, body)
        self.assertEqual(ca.idempotency_key(1, 0, Fam.VEHICLE),
                         ca.idempotency_key(1, 0, Fam.VEHICLE))
        self.assertNotEqual(ca.idempotency_key(1, 0, Fam.VEHICLE),
                            ca.idempotency_key(1, 0, Fam.LOCATION))
        self.assertNotEqual(ca.idempotency_key(1, 0, Fam.VEHICLE),
                            ca.idempotency_key(1, 1, Fam.VEHICLE))


# ══ G — legacy authority removal (§14) ══════════════════════════════════════════

class G_AuthorityRemoval(unittest.TestCase):

    def test_seq_50_neither_legacy_authority_can_dispatch_a_clarification(self):
        for fn in (E._routing_gate, E._check_fallback_flow_triggers):
            body = code_only(fn)
            for forbidden in ("_dispatch_vehicle_flow_direct",
                              "_dispatch_location_flow_direct",
                              "_dispatch_vehicle_clarification_flow",
                              "_dispatch_location_clarification_flow",
                              "_send_flow_button"):
                self.assertNotIn(forbidden, body, f"{fn.__name__} -> {forbidden}")

    def test_seq_51_neither_legacy_authority_writes_an_attempt_flag(self):
        for fn in (E._routing_gate, E._check_fallback_flow_triggers):
            body = code_only(fn)
            for forbidden in ("vehicle_fallback_flow_sent = True",
                              "location_fallback_flow_sent = True",
                              "vehicle_clarification_sent = True",
                              "location_clarification_sent = True"):
                self.assertNotIn(forbidden, body)

    def test_seq_52_the_retired_trigger_returns_none_and_nothing_else(self):
        body = code_only(E._check_fallback_flow_triggers)
        self.assertEqual([l.strip() for l in body.strip().splitlines()][-1], "return None")

    def test_seq_53_the_surviving_phrase_sets_cannot_reach_a_clarification_flow(self):
        """SEQ-09 debt is bounded: Priority 1 and 3 remain phrase-driven but ask nothing."""
        gate = code_only(E._routing_gate)
        self.assertIn("_is_outside_coverage", gate)
        self.assertIn("_is_general_faq_or_soft_close", gate)
        self.assertNotIn("_dispatch_vehicle_flow_direct", gate)
        self.assertNotIn("_dispatch_location_flow_direct", gate)

    def test_seq_54_the_engine_no_longer_calls_the_superseded_router(self):
        self.assertNotIn("pc.route_clarification", ENGINE_SRC)
        self.assertNotIn("pending_correction.route_clarification", ENGINE_SRC)

    def test_seq_55_one_canonical_candidate_writer_on_this_path(self):
        i = ENGINE_SRC.index("_clar = self._turn_clarification_decision(")
        j = ENGINE_SRC.index("self._record_candidate_mutation(_outcome)", i)
        window = ENGINE_SRC[i:j]
        # Every canonical write between the decision and the mutation record goes through
        # the one existing writer. The safe-partial branch calls it; nothing else writes.
        self.assertGreaterEqual(window.count("self._apply_candidate("), 1)
        for forbidden in ("_create_candidate_from_catalog", "_apply_vehicle_identity",
                          "_apply_extracted("):
            self.assertNotIn(forbidden, window,
                             f"a second candidate writer appeared: {forbidden}")

    def test_seq_56_the_decision_never_announces_a_motorcycle(self):
        body = code_only(E._turn_clarification_decision)
        self.assertNotIn("MOTO", body)
        self.assertNotIn("_motorcycle_human_handoff", body)
        exe = code_only(E._execute_clarification)
        self.assertNotIn("_motorcycle_human_handoff", exe)
        self.assertNotIn("MOTO", exe)


# ══ H — trace 1.9 truth ═════════════════════════════════════════════════════════

class H_Trace(unittest.TestCase):

    def test_seq_57_the_contract_is_1_9_and_1_8_stays_readable(self):
        from app.schemas.hybrid_trace import TRACE_VERSION, READABLE_VERSIONS
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.9")
        self.assertIn("hybrid-decision-trace/1.8", READABLE_VERSIONS)

    def test_seq_58_the_lifecycle_fields_let_turns_be_joined(self):
        eng = E.__new__(E)
        eng._turn_clarification_attempt_id = 7
        eng._turn_clarification_cycle_key = 0
        eng._turn_clarification_family = Fam.VEHICLE
        eng._turn_clarification_reservation = "WON"
        eng._turn_clarification_dispatch = "DISPATCHED"
        block = eng._clarification_for_trace()
        for field in ("attempt_id", "cycle_key", "family", "reservation",
                      "already_attempted", "store_available", "store_fault",
                      "dispatch_result", "completion_result", "resolved_family",
                      "prevented_second", "decision_count", "g3_8_enforced"):
            self.assertIn(field, block, f"1.9 lifecycle field missing: {field}")
        self.assertEqual(block["attempt_id"], 7)
        self.assertIs(block["g3_8_enforced"], False)

    def test_seq_59_producer_contributions_are_separate_fields(self):
        eng = E.__new__(E)
        block = eng._clarification_for_trace()
        self.assertIn("semantic_contributed", block)
        self.assertIn("deterministic_contributed", block)
        self.assertIn("reconciled_identity_resolved", block)
        self.assertIn("reconciled_location_resolved", block)

    def test_seq_60_already_attempted_has_three_states_not_two(self):
        eng = E.__new__(E)
        eng._turn_clarification_reservation = "WON"
        self.assertIs(eng._clarification_for_trace()["already_attempted"], False)
        eng._turn_clarification_reservation = "ALREADY_ATTEMPTED"
        self.assertIs(eng._clarification_for_trace()["already_attempted"], True)
        eng._turn_clarification_reservation = "STORE_UNAVAILABLE"
        self.assertIsNone(eng._clarification_for_trace()["already_attempted"],
                          "an unreadable store is not a claim about history")

    def test_seq_61_the_trace_block_carries_no_prose_phone_or_wamid(self):
        eng = E.__new__(E)
        eng._turn_clarification_reservation = "WON"
        block = eng._clarification_for_trace()
        for key in block:
            self.assertNotIn("wa_id", key)
            self.assertNotIn("phone", key)
            self.assertNotIn("text", key)
        self.assertNotIn("wa_message_id", block,
                         "a full WAMID must not reach the trace block")

    def test_seq_62_the_inspector_separates_the_six_readings(self):
        from app.ui.hybrid_trace_view import _clarification_card
        html = _clarification_card({"clarification": {
            "route": "LOCATION_FLOW_REQUIRED", "reason": "LOCATION_UNRESOLVED",
            "attempt_id": 3, "cycle_key": 0, "family": "LOCATION",
            "reservation": "WON", "dispatch_result": "DISPATCHED",
            "store_available": True, "attempt_status": "DISPATCHED",
            "completion_result": "RESOLVED", "decision_count": 1,
            "semantic_contributed": True, "deterministic_contributed": False,
            "reconciled_identity_resolved": True,
            "reconciled_location_resolved": False, "prevented_second": False}})
        for label in ("Evidencia semántica", "Evidencia determinística",
                      "Reconciliación", "Aclaración requerida",
                      "Resultado del envío", "Resultado de la respuesta del cliente"):
            self.assertIn(label, html, f"Inspector hides: {label}")

    def test_seq_63_a_1_8_payload_renders_without_empty_lifecycle_rows(self):
        from app.ui.hybrid_trace_view import _clarification_card
        html = _clarification_card({"clarification": {
            "route": "NO_CLARIFICATION_REQUIRED", "reason": "RESOLVED",
            "g3_8_enforced": False}})
        self.assertIn("Aclaración requerida", html)
        self.assertNotIn("Estado del intento", html,
                         "a 1.8 trace must not show lifecycle rows it never had")


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
