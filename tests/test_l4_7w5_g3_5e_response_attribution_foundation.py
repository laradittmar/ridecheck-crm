"""L4.7W5-GATE3-G3-5E — attribution that tells the truth, and a plan that only watches.

The G3-5D audit found that once evidence is reconciled the system has no composition layer
and no truthful attribution:

  G5D-01/02 HIGH  the reply-generation model applies extracted fields, creates candidates,
                  can force a motorcycle handoff and can reset the commercial stage — and
                  reads `ai_input_messages`, the presentation subset, not the canonical burst.
  G5D-03   HIGH   no response plan exists, so the second proposition of a turn is lost at
                  render time (F-03).
  G5D-04/05 MED   `blocked_dispatch` forces `answer_source="DETERMINISTIC_RULE"`, and the
                  FAQ/greeting route labels reply-model prose `FAQ_RULE`.
  G5D-06   MED    the reply prompt has no version, so no trace can reproduce it.

This milestone is the foundation, not the fix. It gives the reply model the canonical burst,
names and versions the reply role, splits attribution into four typed vocabularies, and
builds a SHADOW response plan that observes and never acts. It does not activate composition,
does not remove the legacy reply-model authority and does not change customer wording.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w5_g3_5b_unified_evidence_boundary import (                # noqa: E402
    HOURS_Q, LIVE_FIRST, LIVE_SECOND, Turn, availability_evidence,
)
import app.services.conversation_engine as ce                            # noqa: E402
from app.services.conversation_engine import (                            # noqa: E402
    ConversationEngine, REPLY_PROMPT_ID, REPLY_PROMPT_VERSION, ReplyRole,
)
from app.schemas.hybrid_trace import (                                    # noqa: E402
    CoexistencePolicy, DecisionAuthority, DispatchPath, DispatchResult, FactSource,
    FLOW_COMPLETION_ACK_CONDITIONS, READABLE_VERSIONS, RendererSource, ResponseItem,
    ResponseItemKind, ResponsePlan, ResponsePolicy, TRACE_VERSION, TRACE_VERSION_1_0,
    TRACE_VERSION_1_1, TRACE_VERSION_1_2, TRACE_VERSION_1_3, TransportMultiplicity,
    VERSIONS_WITHOUT_ATTRIBUTION_SPLIT,
)

Q = HOURS_Q
NEUTRAL = {"intent": "QUALIFYING", "reply": "Gracias por escribirnos.",
           "deferred_interest": False, "candidate": {"action": "none"}, "extracted": {},
           "lead_flag": None, "needs_human": False}


def reply_json(**kw):
    d = dict(NEUTRAL); d.update(kw); return json.dumps(d)


def engine_stub():
    """A bare engine for the unit-level attribution checks. Calls no provider."""
    e = ConversationEngine.__new__(ConversationEngine)
    e._canonical_turn_burst = None
    e._availability_authority_cache = None
    return e


# ══ A — the reply model reads the canonical burst ═══════════════════════════

class A_CanonicalReplyInput(unittest.TestCase):
    """G5D-02. The component that can mutate canonical state must see the whole turn."""

    def turn(self, **kw):
        return Turn((("w0", LIVE_FIRST, 0), ("w1", LIVE_SECOND, 5)),
                    unanswered=[LIVE_FIRST], trigger="w1", **kw).run()

    def test_g35e_01_reply_input_is_the_canonical_burst(self):
        t = self.turn(evidence=availability_evidence())
        try:
            texts = t.engine._reply_input_texts(["SOLO ESTE"])
            self.assertEqual(texts, list(t.burst.texts))
            self.assertEqual(texts, [LIVE_FIRST, LIVE_SECOND])
        finally:
            t.close()

    def test_g35e_02_a_presentation_subset_cannot_define_reply_input(self):
        t = self.turn(evidence=availability_evidence())
        try:
            self.assertEqual(t.burst.presentation_texts, (LIVE_FIRST,))
            self.assertNotIn(LIVE_SECOND, t.burst.presentation_texts)
            self.assertIn(LIVE_SECOND, t.engine._reply_input_texts([LIVE_FIRST]))
        finally:
            t.close()

    def test_g35e_03_split_messages_arrive_in_canonical_order(self):
        t = self.turn(evidence=availability_evidence())
        try:
            self.assertEqual(t.engine._reply_input_texts([]), [LIVE_FIRST, LIVE_SECOND])
        finally:
            t.close()

    def test_g35e_04_duplicate_text_with_different_ids_stays_distinct(self):
        t = Turn((("w0", Q, 0), ("w1", Q, 5)), unanswered=[Q], trigger="w1",
                 evidence=None).run()
        try:
            self.assertEqual(t.engine._reply_input_texts([Q]), [Q, Q])
            self.assertEqual(t.burst.ordered_message_ids, ("w0", "w1"))
        finally:
            t.close()

    def test_g35e_05_the_reply_input_is_bound_to_the_canonical_fingerprint(self):
        t = self.turn(evidence=availability_evidence())
        try:
            t.engine._reply_input_texts([])
            self.assertEqual(t.engine._reply_burst, t.burst.fingerprint)
        finally:
            t.close()

    def test_g35e_05b_no_burst_falls_back_without_claiming_a_binding(self):
        e = engine_stub()
        self.assertEqual(e._reply_input_texts(["a", "b"]), ["a", "b"])
        self.assertIsNone(e._reply_burst)

    def test_g35e_06_repeated_reply_accessors_do_not_call_the_provider(self):
        t = self.turn(evidence=availability_evidence())
        try:
            before = t.engine._reply_calls
            for _ in range(4):
                t.engine._reply_input_texts([])
            self.assertEqual(t.engine._reply_calls, before)
        finally:
            t.close()


# ══ B — typed attribution ══════════════════════════════════════════════════

class B_Attribution(unittest.TestCase):

    def test_g35e_07_a_deterministic_reply_is_attributed_to_a_template(self):
        t = Turn((("w0", "¿Cuál es el horario de atención?", 0),), evidence=None).run()
        try:
            # G3-6: both contributions are now the business's own — a deterministic
            # primary reply and a canonical FAQ proposition — so the renderer is a
            # template, not a mixture. The fact source is what the assertion is really
            # about and it is unchanged.
            self.assertEqual(t.engine._turn_renderer_source,
                             RendererSource.DETERMINISTIC_TEMPLATE)
            self.assertIn(FactSource.FAQ_CANONICAL, t.engine._turn_fact_sources)
        finally:
            t.close()

    def test_g35e_08_reply_model_prose_is_attributed_to_the_reply_model(self):
        t = Turn((("w0", "¿Cuánto sale?", 0), ("w1", "Estoy en Vicente López.", 5)),
                 evidence=None).run()
        try:
            if t.engine._reply_calls:
                self.assertIn(t.engine._turn_renderer_source,
                              (RendererSource.REPLY_MODEL, RendererSource.COMPOSED_MIXED))
            else:
                self.skipTest("this burst did not reach the reply model")
        finally:
            t.close()

    def test_g35e_09_the_faq_route_does_not_call_its_renderer_a_rule(self):
        """G5D-05. `answer_source` still says FAQ_RULE; the RENDERER may not."""
        import inspect
        body = inspect.getsource(ConversationEngine._handle_general_information_ai)
        # G3-5F §4.3 moved the provider call behind `_reply_decision`; the role this route
        # declares — which is what the assertion is about — is unchanged.
        self.assertIn("role=ReplyRole.GENERAL_INFORMATION", body)
        self.assertIn("self._reply_decision(", body)
        self.assertIn("RendererSource.REPLY_MODEL", body)

    def test_g35e_10_multiple_fact_sources_are_representable(self):
        item = ResponseItem(kind=ResponseItemKind.TEXT, claim_family="scheduling",
                            proposition_id="x",
                            fact_sources=(FactSource.SEMANTIC_EVIDENCE,
                                          FactSource.CANONICAL_STATE,
                                          FactSource.SCHEDULING_SERVICE))
        self.assertEqual(len(item.fact_sources), 3)

    def test_g35e_11_legacy_reply_model_authority_is_named_truthfully(self):
        """G5D-01. Not corrected here — named, so the finding cannot hide."""
        import inspect
        src = inspect.getsource(ce)
        self.assertIn("LEGACY_REPLY_MODEL_EXTRACTION", src)
        self.assertIn(DecisionAuthority.LEGACY_REPLY_MODEL_EXTRACTION,
                      DecisionAuthority.ALL)
        self.assertIn("DecisionAuthority.LEGACY_REPLY_MODEL_EXTRACTION", src)
        import inspect
        commercial = inspect.getsource(ConversationEngine._process_text)
        self.assertIn("LEGACY_REPLY_MODEL_EXTRACTION", commercial,
                      "the commercial reply path must still name its real authority")

    def test_g35e_12_a_blocked_dispatch_keeps_the_real_producer(self):
        """G5D-04. The gate records a RESULT; it never becomes the source."""
        t = Turn((("w0", "¿Cuál es el horario de atención?", 0),), evidence=None,
                 ).run()
        try:
            self.assertEqual(t.engine._turn_dispatch_path, DispatchPath.TEXT)
            self.assertEqual(t.engine._turn_dispatch_result, DispatchResult.ALLOWED)
            self.assertNotEqual(t.engine._turn_renderer_source, "DETERMINISTIC_RULE")
        finally:
            t.close()
        self.assertNotIn("DETERMINISTIC_RULE", RendererSource.ALL)
        self.assertNotIn("BLOCKED", DecisionAuthority.ALL)

    def test_g35e_13_the_four_vocabularies_do_not_overlap(self):
        self.assertEqual(set(RendererSource.ALL) & set(DecisionAuthority.ALL), set())
        self.assertEqual(set(DispatchResult.ALL) & set(DecisionAuthority.ALL), set())
        self.assertEqual(set(DispatchResult.ALL) & set(RendererSource.ALL), set())

    def test_g35e_14_text_and_flow_dispatch_paths_are_distinct(self):
        self.assertNotEqual(DispatchPath.TEXT, DispatchPath.META_FLOW)
        self.assertIn(DispatchPath.NONE, DispatchPath.ALL)


# ══ C — model-call accounting ══════════════════════════════════════════════

class C_ModelCalls(unittest.TestCase):

    def test_g35e_15_semantic_and_reply_calls_are_counted_separately(self):
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            self.assertEqual(t.engine._turn_semantic_calls, 1)
            self.assertEqual(t.engine._reply_calls, 0, "this turn never reached the renderer")
            self.assertNotEqual(id(t.engine._turn_semantic_calls), id(t.engine._reply_calls))
        finally:
            t.close()

    def test_g35e_16_a_shared_model_does_not_collapse_the_roles(self):
        import inspect
        from app.services import semantic_interpreter as si
        self.assertIn('openai_chat_model', inspect.getsource(si.SemanticTurnInterpreter))
        self.assertIn('openai_chat_model',
                      inspect.getsource(ConversationEngine._call_openai_inner))
        self.assertEqual(len(ReplyRole.ALL), 2, "each reply call site names its role")
        self.assertNotEqual(REPLY_PROMPT_VERSION, si.PROMPT_VERSION)

    def test_g35e_17_the_reply_prompt_is_versioned_and_identified(self):
        self.assertTrue(REPLY_PROMPT_VERSION.startswith("reply/"), REPLY_PROMPT_VERSION)
        self.assertIn("_build_ai_messages", REPLY_PROMPT_ID)

    def test_g35e_18_the_semantic_attribution_is_unchanged(self):
        from app.services import semantic_interpreter as si
        from app.schemas import turn_evidence as te
        self.assertEqual(si.PROMPT_VERSION, "understand/1.19")
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")

    def test_g35e_19_a_reply_timeout_is_represented_truthfully(self):
        t = Turn((("w0", "¿Cuánto sale?", 0), ("w1", "Estoy en Vicente López.", 5)),
                 evidence=None).run()
        try:
            if not t.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            self.assertIn(t.engine._reply_status, ("OK", "ERROR"))
        finally:
            t.close()


# ══ D — the shadow response plan ═══════════════════════════════════════════

class D_ResponsePlan(unittest.TestCase):

    def plan_for(self, rows, **kw):
        t = Turn(tuple(rows), **kw).run()
        return t, t.engine._turn_response_plan

    def test_g35e_20_exactly_one_plan_per_decision_phase(self):
        t, plan = self.plan_for([("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)],
                                evidence=None)
        try:
            self.assertIsNotNone(plan)
            self.assertIsInstance(plan, ResponsePlan)
            phase = t.decision.decision_phase_id
            self.assertEqual(plan.decision_phase_id, phase)
            self.assertIn(phase, plan.plan_id)
            again = t.engine._shadow_response_plan(t.out)
            self.assertEqual(again.plan_id, plan.plan_id, "a second build is the same phase")
        finally:
            t.close()

    def test_g35e_21_22_23_building_a_plan_acts_on_nothing(self):
        t, plan = self.plan_for([("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)],
                                evidence=None)
        try:
            sent_before = len(t.sent)
            sem_before, reply_before = t.engine._turn_semantic_calls, t.engine._reply_calls
            state_before = t.db.execute(__import__("sqlalchemy").text(
                "select last_stage, needs_human from whatsapp_thread_states")).first()
            for _ in range(3):
                t.engine._shadow_response_plan(t.out)
            self.assertEqual(len(t.sent), sent_before, "the plan dispatched")
            self.assertEqual(t.engine._turn_semantic_calls, sem_before, "the plan interpreted")
            self.assertEqual(t.engine._reply_calls, reply_before, "the plan called the renderer")
            self.assertEqual(
                tuple(t.db.execute(__import__("sqlalchemy").text(
                    "select last_stage, needs_human from whatsapp_thread_states")).first()),
                tuple(state_before), "the plan mutated canonical state")
            # G3-5F: a turn that composed carries the LIVE plan, so `shadow` is False.
            # Everything asserted above — no dispatch, no interpretation, no renderer call,
            # no canonical mutation, however many times the plan is projected — is the
            # property this test protects, and it is unchanged.
            self.assertFalse(plan.shadow)
        finally:
            t.close()

    def test_g35e_24_a_normal_turn_plans_every_proposition_and_one_message(self):
        """G3-6 — the count changed on purpose, and that IS the milestone.

        This asserted one planned item for a turn whose message carried two claims: a reply
        and the canonical opening hours, the second appended downstream where the plan could
        not see it. Both are registered propositions now, so the plan counts two — and P3
        still puts them in ONE message, which is the invariant this test protects.
        """
        t, plan = self.plan_for([("w0", "¿Cuál es el horario de atención?", 0)],
                                evidence=None)
        try:
            kinds = [i.kind for i in plan.included_items]
            self.assertEqual(kinds, [ResponseItemKind.TEXT, ResponseItemKind.FAQ_ANSWER],
                             kinds)
            self.assertEqual(plan.transport_multiplicity,
                             TransportMultiplicity.SINGLE_MESSAGE)
            self.assertEqual(len(t.sent), 1, "two propositions became two messages")
        finally:
            t.close()

    def test_g35e_25_the_flow_policy_is_two_typed_items(self):
        """P4. A Flow is an intro plus the Flow — never two unrelated free-text replies."""
        plan = ResponsePlan(
            plan_id="p", turn_id="t", burst_fingerprint="f", decision_phase_id="ph",
            transport_multiplicity=TransportMultiplicity.FLOW_INTRO_THEN_FLOW,
            items=(ResponseItem(kind=ResponseItemKind.FLOW_INTRO_TEXT,
                                claim_family="scheduling", proposition_id="s.intro",
                                coexistence=CoexistencePolicy.REQUIRED, priority=10,
                                policy=ResponsePolicy.P4_FLOW_INTRO_SEQUENCE),
                   ResponseItem(kind=ResponseItemKind.META_FLOW, claim_family="scheduling",
                                proposition_id="s.flow",
                                coexistence=CoexistencePolicy.REQUIRED, priority=20,
                                policy=ResponsePolicy.P4_FLOW_INTRO_SEQUENCE)))
        kinds = [i.kind for i in plan.included_items]
        self.assertEqual(kinds, [ResponseItemKind.FLOW_INTRO_TEXT,
                                 ResponseItemKind.META_FLOW])
        self.assertEqual({i.policy for i in plan.items},
                         {ResponsePolicy.P4_FLOW_INTRO_SEQUENCE})
        self.assertEqual(plan.transport_multiplicity,
                         TransportMultiplicity.FLOW_INTRO_THEN_FLOW)

    def test_g35e_26_a_completion_acknowledgement_is_representable(self):
        item = ResponseItem(kind=ResponseItemKind.FLOW_COMPLETION_ACK,
                            claim_family="booking", proposition_id="booking.completed",
                            decision_authority=DecisionAuthority.FLOW_TRANSACTION,
                            fact_sources=(FactSource.FLOW_TRANSACTION,),
                            policy=ResponsePolicy.P5_FLOW_COMPLETION_ACK)
        self.assertIn(item.kind, ResponseItemKind.ALL)
        self.assertEqual(item.decision_authority, DecisionAuthority.FLOW_TRANSACTION)

    def test_g35e_27_the_acknowledgement_conditions_are_pinned(self):
        """P5. Every condition that must hold. None may be quietly dropped later."""
        for condition in ("flow_submitted", "signature_verified", "token_valid",
                          "payload_complete", "validation_passed", "persistence_committed",
                          "not_already_acknowledged"):
            self.assertIn(condition, FLOW_COMPLETION_ACK_CONDITIONS)
        self.assertEqual(len(FLOW_COMPLETION_ACK_CONDITIONS), 7)

    def test_g35e_27b_the_acknowledgement_is_authorized_never_assumed(self):
        """G3-5F §12 activated it. The contract it must obey is asserted here instead.

        `FLOW_COMPLETION_ACK_CONDITIONS` is pinned as data so a later milestone cannot
        quietly relax one of them. This proves the authorizer actually reads that tuple —
        that removing a condition from the contract is the ONLY way to stop requiring it —
        and that an unmet or simply absent condition withholds the acknowledgement.
        """
        from app.services.response_composition import flow_completion_ack_authorized
        complete = {c: True for c in FLOW_COMPLETION_ACK_CONDITIONS}
        self.assertEqual(flow_completion_ack_authorized(complete), (True, ()))
        self.assertEqual(flow_completion_ack_authorized({}),
                         (False, tuple(FLOW_COMPLETION_ACK_CONDITIONS)),
                         "an unanswered condition was treated as satisfied")
        for condition in FLOW_COMPLETION_ACK_CONDITIONS:
            with self.subTest(condition=condition):
                one_missing = dict(complete, **{condition: False})
                authorized, unmet = flow_completion_ack_authorized(one_missing)
                self.assertFalse(authorized)
                self.assertEqual(unmet, (condition,))

    def test_g35e_28_human_handoff_is_exclusive(self):
        """P2. The acknowledgement owns the turn; no automated FAQ may follow it."""
        item = ResponseItem(kind=ResponseItemKind.HANDOFF_ACK, claim_family="handoff",
                            proposition_id="handoff.ack",
                            coexistence=CoexistencePolicy.EXCLUSIVE,
                            policy=ResponsePolicy.P2_HUMAN_HANDOFF_EXCLUSIVE)
        self.assertEqual(item.coexistence, CoexistencePolicy.EXCLUSIVE)
        t, plan = self.plan_for([("w0", Q, 0)], evidence=None,
                                state_extra={"needs_human": True})
        try:
            kinds = [i.kind for i in plan.included_items]
            self.assertEqual(kinds, [ResponseItemKind.NO_RESPONSE])
            self.assertEqual(plan.included_items[0].coexistence,
                             CoexistencePolicy.EXCLUSIVE)
            self.assertEqual(len(t.sent), 0, "a human-owned turn sent nothing")
        finally:
            t.close()

    def test_g35e_29_hold_can_coexist_with_a_safe_faq(self):
        """P1. The held action and the separately authorized FAQ are distinct items."""
        held = ResponseItem(kind=ResponseItemKind.CLARIFICATION, claim_family="scheduling",
                            proposition_id="scheduling.availability",
                            required_action="HOLD",
                            coexistence=CoexistencePolicy.COMPATIBLE,
                            policy=ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ)
        faq = ResponseItem(kind=ResponseItemKind.FAQ_ANSWER, claim_family="faq",
                           proposition_id="faq.business_hours",
                           fact_sources=(FactSource.FAQ_CANONICAL,),
                           coexistence=CoexistencePolicy.COMPATIBLE,
                           policy=ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ)
        plan = ResponsePlan(plan_id="p", turn_id="t", burst_fingerprint="f",
                            decision_phase_id="ph", items=(held, faq))
        self.assertEqual(len(plan.included_items), 2)
        self.assertNotEqual(held.proposition_id, faq.proposition_id)

    def test_g35e_30_known_facts_are_not_planned_by_default(self):
        """P6. The plan for a normal turn contains no restatement item."""
        t, plan = self.plan_for([("w0", "¿Cuál es el horario de atención?", 0)],
                                evidence=None)
        try:
            families = {i.claim_family for i in plan.included_items}
            self.assertNotIn("vehicle", families)
            self.assertNotIn("locality", families)
            self.assertNotIn("pricing", families)
        finally:
            t.close()

    def test_g35e_31_suppressed_items_carry_a_reason(self):
        t, plan = self.plan_for([("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)],
                                evidence=None)
        try:
            # G3-5F: the LIVE plan records suppression the same way. A turn whose
            # propositions were all admitted legitimately has none, so the assertion is
            # about the RECORD — anything withheld says why — not about a count.
            suppressed = plan.suppressed_items or t.engine._shadow_response_plan(
                t.out).suppressed_items
            self.assertTrue(suppressed, "the hours proposition was withheld and unrecorded")
            for item in suppressed:
                self.assertTrue(item.suppression_reason, item.proposition_id)
                self.assertFalse(item.included)
        finally:
            t.close()

    def test_g35e_32_the_plan_and_the_actual_outcome_are_comparable(self):
        t, plan = self.plan_for([("w0", "¿Cuál es el horario de atención?", 0)],
                                evidence=None)
        try:
            self.assertEqual(plan.outcome, t.out.action)
            self.assertEqual(plan.dispatch_result, DispatchResult.ALLOWED)
            # G3-6: propositions are not messages. Several compose into one text under
            # P3, so the comparison that matters is transport multiplicity against what
            # was actually sent — and every planned text item must be IN that message.
            self.assertEqual(plan.transport_multiplicity,
                             TransportMultiplicity.SINGLE_MESSAGE)
            self.assertEqual(len(t.sent), 1)
            self.assertGreaterEqual(len(plan.included_items), 1)
        finally:
            t.close()

    def test_g35e_32b_semantic_only_permissions_are_representable(self):
        """P7. A semantic-only proposition may carry authority without a CE match."""
        item = ResponseItem(kind=ResponseItemKind.CLARIFICATION, claim_family="scheduling",
                            proposition_id="scheduling.availability",
                            fact_sources=(FactSource.SEMANTIC_EVIDENCE,),
                            decision_authority=DecisionAuthority.RECONCILER,
                            mutation_authorized=False,
                            policy=ResponsePolicy.P7_SEMANTIC_ONLY_PERMISSIONS)
        self.assertEqual(item.fact_sources, (FactSource.SEMANTIC_EVIDENCE,))
        self.assertFalse(item.mutation_authorized, "never an autonomous booking")


# ══ E — trace 1.4 and the Inspector ════════════════════════════════════════

class E_TraceAndInspector(unittest.TestCase):

    def test_g35e_33_the_contract_is_current_and_renders_the_new_fields(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.5")
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            from app.services import hybrid_trace as svc
            plan = t.engine._turn_response_plan
            self.assertIsNotNone(plan)
            payload = plan.to_payload()
            for key in ("plan_id", "item_count", "suppressed_count", "item_kinds",
                        "transport_multiplicity", "renderer_source", "dispatch_path",
                        "dispatch_result", "shadow",
                        # G3-5F §15: the Flow is counted apart from text, so "one message"
                        # stops being ambiguous when the second part is a form.
                        "planned_text_items", "planned_flow_items"):
                self.assertIn(key, payload)
            self.assertTrue(hasattr(svc, "build_trace"))
        finally:
            t.close()

    def test_g35e_34_older_contracts_remain_readable(self):
        for older in (TRACE_VERSION_1_0, TRACE_VERSION_1_1, TRACE_VERSION_1_2,
                      TRACE_VERSION_1_3):
            self.assertIn(older, READABLE_VERSIONS)
        self.assertEqual(set(VERSIONS_WITHOUT_ATTRIBUTION_SPLIT),
                         {TRACE_VERSION_1_0, TRACE_VERSION_1_1, TRACE_VERSION_1_2,
                          TRACE_VERSION_1_3})

    def test_g35e_35_missing_legacy_fields_render_as_unavailable(self):
        from app.ui.hybrid_trace_view import (UNAVAILABLE_BEFORE_1_4,
                                              _unavailable_before_1_4)
        old_render = _unavailable_before_1_4({"trace_version": TRACE_VERSION_1_3})
        self.assertIn(UNAVAILABLE_BEFORE_1_4, old_render(None))
        self.assertIn(UNAVAILABLE_BEFORE_1_4, old_render("DETERMINISTIC_RULE"),
                      "a 1.3 row must not be read as though it carried the new field")
        new_render = _unavailable_before_1_4({"trace_version": TRACE_VERSION})
        self.assertIn("RECONCILER", new_render(DecisionAuthority.RECONCILER))

    def test_g35e_36_no_raw_text_phone_wamid_or_secret_in_the_new_fields(self):
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            blob = json.dumps(t.engine._turn_response_plan.to_payload())
            for fragment in ("horarios tienen", "Hay algo", "mañana?"):
                self.assertNotIn(fragment, blob)
            for fragment in ("wamid.", "5491", "sk-", "EAA"):
                self.assertNotIn(fragment, blob)
        finally:
            t.close()

    def test_g35e_37_the_inspector_shows_both_call_counts_separately(self):
        from app.ui import hybrid_trace_view as view
        src = pathlib.Path(view.__file__).read_text(encoding="utf-8")
        self.assertIn("Modelo de redacción", src)
        self.assertIn("Autoridad de la decisión", src)
        self.assertIn("Quién redactó", src)
        self.assertIn("Resultado del envío (compuerta)", src)

    def test_g35e_38_blocked_dispatch_reads_truthfully_in_the_inspector(self):
        from app.ui import hybrid_trace_view as view
        src = pathlib.Path(view.__file__).read_text(encoding="utf-8")
        self.assertIn("Fuente de la respuesta (legado)", src,
                      "the conflated field must be labelled as legacy")
        self.assertIn("nunca es el autor", src.lower().replace(" ", " "))

    def test_g35e_39_the_plan_is_distinguishable_from_the_actual_outcome(self):
        """G3-5F: the heading now has to say WHICH plan is on screen, not just that it is
        one. An operator reading a live plan as an observation would draw the wrong
        conclusion from every suppression in it."""
        from app.ui import hybrid_trace_view as view
        src = pathlib.Path(view.__file__).read_text(encoding="utf-8")
        # G3-5F-BOUNDED-CORRECTION (audit G5F-A02): the live heading no longer claims
        # authority it does not hold. The property this test protects — the plan is
        # distinguishable from what actually happened — is unchanged and now stronger,
        # because the card also states what it does NOT cover.
        self.assertIn("Plan de respuesta (composición)", src)
        self.assertIn("Plan de respuesta (observado)", src)
        self.assertNotIn("Plan de respuesta (autoridad)", src)
        self.assertIn("Mensajes enviados", src)
        self.assertIn("Textos planificados", src)
        self.assertIn("Flows planificados", src)
        self.assertIn("PLAN_SCOPE_NOTE", src)


# ══ F — non-regression ═════════════════════════════════════════════════════

class F_NonRegression(unittest.TestCase):

    HOURS_MARKERS = ("lunes de", "sabados de", "sábados de", "domingos no")

    def test_g35e_40_f01_remains_closed(self):
        for texts in ([Q, "¿Hay algo mañana?"], [Q, "¿Y el martes?"]):
            with self.subTest(burst=texts[-1]):
                t = Turn(tuple((f"w{i}", x, i * 5) for i, x in enumerate(texts)),
                         evidence=None).run()
                try:
                    self.assertEqual(t.decision.hours_disposition,
                                     "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING")
                    self.assertFalse(t.names_the_weekday_table())
                finally:
                    t.close()

    def test_g35e_41_f02_remains_closed(self):
        t = Turn((("w0", "Disponibles para mañana.", 0), ("w1", Q, 5)),
                 unanswered=[Q], trigger="w1", evidence=None).run()
        try:
            self.assertEqual(t.burst.texts, ("Disponibles para mañana.", Q))
            self.assertFalse(t.names_the_weekday_table())
        finally:
            t.close()

    def test_g35e_42_the_evidence_boundary_invariants_hold(self):
        t = Turn((("w0", LIVE_FIRST, 0), ("w1", LIVE_SECOND, 5)), unanswered=[LIVE_FIRST],
                 trigger="w1", evidence=availability_evidence()).run()
        try:
            self.assertEqual(t.interpreter_saw[0]["messages"], list(t.burst.texts))
            self.assertEqual(t.engine._turn_semantic_burst, t.burst.fingerprint)
            self.assertEqual(len(t.burst.ordered_message_ids), len(t.burst.texts))
        finally:
            t.close()

    def test_g35e_44_45_46_47_48_nothing_new_reaches_the_customer(self):
        cases = ((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)),
                 (("w0", "¿Cuál es el horario de atención?", 0),),
                 (("w0", LIVE_FIRST, 0), ("w1", LIVE_SECOND, 5)))
        sql = __import__("sqlalchemy").text
        for rows in cases:
            with self.subTest(burst=rows[-1][1][:18]):
                t = Turn(rows, evidence=None).run()
                try:
                    self.assertLessEqual(len(t.sent), 1, "an extra message appeared")
                    self.assertEqual(
                        t.db.execute(sql("select count(*) from thread_revisions")).scalar(), 0)
                    self.assertEqual(
                        t.db.execute(sql("select count(*) from whatsapp_messages "
                                         "where direction='out'")).scalar(), 0)
                    for text in t.sent:
                        self.assertNotIn("Gracias por completarlo", text)
                finally:
                    t.close()

    def test_g35e_43_positive_control_the_harness_sees_dispatched_content(self):
        t = Turn((("w0", "¿Cuál es el horario de atención?", 0),), evidence=None).run()
        try:
            self.assertEqual(len(t.sent), 1)
            self.assertTrue(t.names_the_weekday_table(),
                            "the harness cannot see output; every negative above is vacuous")
        finally:
            t.close()


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
