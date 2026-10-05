"""L4.7W5-GATE3-G3-5F — the plan composes; the handlers propose.

The G3-5E2 independent review certified the binding and the attribution, and left three
things to open this milestone:

  G5E2-01 MED  three of seven kill-switch exits recorded `META_FLOW` beside a `CE_TEXT`
               gate attempt — a falsehood in the field built to make blocked dispatch
               truthful.
  G5E2-02 MED  the canonical-burst fingerprint carried no thread or turn identity, so two
               customers opening with the same words produced the SAME fingerprint — and
               that value is the reply binding and the plan id.
  G5E2-03 LOW  `NOT_DISPATCHED` was safe only because every reply decision happened to come
               from `_call_openai`. True, but unasserted.

and to promote the shadow plan into the live composition authority, with the P5 Flow
completion acknowledgement it has been holding a pinned contract for since G3-5E.

WHAT THESE TESTS DO NOT DO
--------------------------
They do not assert that a phrase appears. The plan holds no wording; every proposition
renders through the component that owns the fact, so a test that pinned words here would
be pinning the wrong layer. What is asserted is WHO decided, WHAT was authorized, how many
messages a turn may occupy, and that a claim cannot be emitted twice.

A — the §4 opening corrections
B — the live plan: authority, identity, projection
C — owner policies P2, P3, P5, P6
D — the Flow completion acknowledgement, condition by condition
E — trace 1.5 and the Inspector
F — degraded modes and non-regression
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w5_g3_5b_unified_evidence_boundary import (                # noqa: E402
    HOURS_Q, Turn, availability_evidence,
)
import app.services.conversation_engine as ce                            # noqa: E402
from app.services.conversation_engine import (                            # noqa: E402
    ConversationEngine, OutboundBlockedError, ReplyAdmission,
    _canonical_burst, _canonical_burst_from_texts, _canonical_messages,
)
from app.services.response_composition import (                           # noqa: E402
    LiveResponsePlan, Proposition, PropositionId, SuppressionReason,
    flow_completion_ack_authorized,
)
from app.schemas.hybrid_trace import (                                    # noqa: E402
    CoexistencePolicy, DecisionAuthority, DispatchPath, DispatchResult, FactSource,
    FLOW_COMPLETION_ACK_CONDITIONS, READABLE_VERSIONS, RendererSource, ResponseItemKind,
    ResponsePolicy, TRACE_VERSION, TRACE_VERSION_1_4, TransportMultiplicity,
    VERSIONS_WITH_SHADOW_ONLY_PLAN,
)

Q = HOURS_Q
ENGINE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")


def plan_with(*props, **kw) -> LiveResponsePlan:
    p = LiveResponsePlan(turn_id="t1", **kw)
    for prop in props:
        p.register(prop)
    return p


def faq_prop(topic: str, text: str, **kw) -> Proposition:
    kw.setdefault("priority", 50)
    kw.setdefault("policy", ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ)
    return Proposition(
        proposition_id=PropositionId.faq(topic), kind=ResponseItemKind.FAQ_ANSWER,
        claim_family="faq", render=lambda: text,
        decision_authority=DecisionAuthority.DETERMINISTIC_RULE,
        fact_sources=(FactSource.FAQ_CANONICAL,), **kw)


# ══ A — the §4 opening corrections ══════════════════════════════════════════

class A_OpeningCorrections(unittest.TestCase):

    # ── §4.1 / G5E2-01 ───────────────────────────────────────────────────────

    def test_g35f_01_every_blocked_exit_derives_its_transport_from_its_path(self):
        """The defect was a literal at the call site. There is no longer a literal."""
        import re
        sites = re.findall(
            r'if not self\._outbound_armed\(\):\n\s*'
            r'return self\._blocked_outbound\([^,]+,\s*[^,]+,\s*'
            r'OutboundPathId\.(\w+)\.value\)', ENGINE_SRC)
        self.assertEqual(len(sites), 7)
        self.assertNotIn("_record_blocked_dispatch(DispatchPath", ENGINE_SRC)
        # §14: one read of the kill switch inside the engine, not seven spellings.
        self.assertEqual(ENGINE_SRC.count('os.environ.get("OUTBOUND_ENABLED")'), 1)
        self.assertIn("def _outbound_armed", ENGINE_SRC)
        for path in sites:
            with self.subTest(path=path):
                expected = (DispatchPath.META_FLOW if path == "CE_FLOW"
                            else DispatchPath.TEXT)
                self.assertEqual(
                    ConversationEngine._transport_for_path_id(
                        getattr(ce.OutboundPathId, path).value), expected)

    def test_g35f_02_the_three_text_exits_no_longer_claim_a_flow(self):
        """Named, because the review named them: the coverage response and the two
        `_check_fallback_flow_triggers` chat clarifications all send text."""
        for fn_name, pid in (("_send_coverage_response", "CE_TEXT"),
                             ("_check_fallback_flow_triggers", "CE_TEXT")):
            body = inspect.getsource(getattr(ConversationEngine, fn_name))
            with self.subTest(fn=fn_name):
                self.assertIn(f"OutboundPathId.{pid}.value", body)
        triggers = inspect.getsource(ConversationEngine._check_fallback_flow_triggers)
        self.assertEqual(triggers.count("OutboundPathId.CE_TEXT.value"), 2)
        self.assertEqual(triggers.count("OutboundPathId.CE_FLOW.value"), 2)

    def test_g35f_03_the_transport_table_covers_every_authorized_path(self):
        """A path the table forgets records NONE, never a guess."""
        for pid in ce.OutboundPathId:
            with self.subTest(path=pid.value):
                got = ConversationEngine._transport_for_path_id(pid.value)
                self.assertIn(got, (DispatchPath.TEXT, DispatchPath.META_FLOW,
                                    DispatchPath.NONE))
        # The one path the outbound invariants BLOCK must not be given a transport.
        self.assertEqual(
            ConversationEngine._transport_for_path_id(
                ce.OutboundPathId.LEGACY_N8N_AI_PIPELINE.value), DispatchPath.NONE)

    # ── §4.2 / G5E2-02 ───────────────────────────────────────────────────────

    def burst(self, thread_id, texts, anchor=None, ids=None):
        msgs = tuple(
            ce.CanonicalMessage(message_id=(ids[i] if ids else f"n8n:{i}"), text=t,
                                index=i, timestamp=None, source="s")
            for i, t in enumerate(texts))
        return _canonical_burst(thread_id=thread_id, turn_id="corr", messages=msgs,
                                provenance=("s",), presentation_texts=texts,
                                turn_anchor_id=anchor)

    def test_g35f_04_identical_first_turns_in_two_threads_are_distinct(self):
        """THE finding. Synthetic identities plus identical words used to collide."""
        a = self.burst(11, ["hola, quiero cotizar"], anchor="wamid.A")
        b = self.burst(22, ["hola, quiero cotizar"], anchor="wamid.B")
        self.assertNotEqual(a.fingerprint, b.fingerprint)
        # Thread identity alone is sufficient — an anchorless pair must still differ.
        c = self.burst(11, ["hola, quiero cotizar"])
        d = self.burst(22, ["hola, quiero cotizar"])
        self.assertNotEqual(c.fingerprint, d.fingerprint)

    def test_g35f_05_the_same_words_later_in_one_thread_are_distinct(self):
        first = self.burst(11, ["¿y ahora?"], anchor="wamid.1", ids=["wamid.1"])
        later = self.burst(11, ["¿y ahora?"], anchor="wamid.9", ids=["wamid.9"])
        self.assertNotEqual(first.fingerprint, later.fingerprint)

    def test_g35f_06_reordering_a_burst_changes_the_fingerprint(self):
        fwd = self.burst(11, ["uno", "dos"], anchor="wamid.2")
        rev = self.burst(11, ["dos", "uno"], anchor="wamid.2")
        self.assertNotEqual(fwd.fingerprint, rev.fingerprint)

    def test_g35f_07_caller_constructed_bursts_carry_the_thread(self):
        a = _canonical_burst_from_texts(thread_id=11, texts=["hola"], turn_anchor_id="w1")
        b = _canonical_burst_from_texts(thread_id=22, texts=["hola"], turn_anchor_id="w1")
        self.assertNotEqual(a.fingerprint, b.fingerprint)

    def test_g35f_08_the_fingerprint_is_deterministic_and_survives_redelivery(self):
        """Idempotency is the reason `turn_id` is NOT in the digest: it is a fresh uuid
        per request, so folding it in would give the same turn two identities."""
        mk = lambda: self.burst(11, ["hola", "che"], anchor="wamid.7")
        self.assertEqual(mk().fingerprint, mk().fingerprint)
        self.assertNotIn("turn_id", inspect.getsource(
            ce.CanonicalTurnBurst.fingerprint.fget))

    def test_g35f_09_the_fingerprint_carries_no_customer_text(self):
        b = self.burst(11, ["Tengo un Focus 2019 en Vicente López"], anchor="wamid.X")
        fp = b.fingerprint
        for fragment in ("Focus", "2019", "Vicente", "wamid.X", "Tengo"):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, fp)
        self.assertEqual(len(fp), 64, "the digest shape changed")

    def test_g35f_10_the_production_burst_is_anchored(self):
        t = Turn(((("w0", Q, 0),)), evidence=None).run()
        try:
            self.assertEqual(t.burst.turn_anchor_id, "w0")
            self.assertEqual(t.burst.thread_id, t.thread_id)
        finally:
            t.close()

    # ── §4.3 / G5E2-03 ───────────────────────────────────────────────────────

    def test_g35f_11_one_boundary_owns_the_provider_call(self):
        callers = [n for n, fn in vars(ConversationEngine).items()
                   if inspect.isfunction(fn) and "self._call_openai(" in inspect.getsource(fn)]
        self.assertEqual(sorted(callers), ["_reply_decision"])

    def test_g35f_12_the_boundary_owns_binding_role_count_and_admission(self):
        body = inspect.getsource(ConversationEngine._reply_decision)
        for owned in ("self._call_openai(", "json.loads(", "self._reply_admission()",
                      "self._reply_admission_state = admission", "self._refuse_reply("):
            with self.subTest(owned=owned):
                self.assertIn(owned, body)

    def test_g35f_13_a_rejected_binding_never_reaches_a_consumer(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e._reply_calls = 0
        e._canonical_turn_burst = _canonical_burst_from_texts(
            thread_id=1, texts=["hola"], turn_anchor_id="w1")

        def provider(messages, role=None):
            e._reply_calls += 1
            e._reply_burst = "a-different-burst"
            return '{"reply": "prosa del modelo", "needs_human": true, "lead_flag": "ACEPTADO"}'

        e._call_openai = provider
        e._refuse_reply = lambda ctx, adm, role: {"reply": "bounded", "needs_human": False}
        decision, admission = e._reply_decision([], role="COMMERCIAL_TURN", ctx=None)
        self.assertEqual(admission, ReplyAdmission.REJECTED_FOREIGN_BURST)
        self.assertEqual(decision["reply"], "bounded")
        self.assertNotIn("lead_flag", decision)


# ══ B — the live plan ═══════════════════════════════════════════════════════

class B_LivePlan(unittest.TestCase):

    def test_g35f_14_a_proposition_cannot_be_registered_twice(self):
        p = plan_with(faq_prop("business_hours", "A"))
        self.assertFalse(p.register(faq_prop("business_hours", "B")))
        self.assertEqual(len(p.propositions), 1)
        self.assertEqual(p.compose("primaria"), "primaria\n\nA")

    def test_g35f_15_two_different_claims_both_survive(self):
        """F-03's shape: no proposition disappears because another produced the action."""
        p = plan_with(faq_prop("business_hours", "Horario canónico."),
                      faq_prop("payment", "Métodos de pago."))
        out = p.compose("Necesito saber el modelo.")
        for fragment in ("Necesito saber el modelo.", "Horario canónico.",
                         "Métodos de pago."):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, out)

    def test_g35f_16_wording_that_coincides_stays_two_propositions(self):
        """Deduplication is by claim identity, never by comparing rendered text."""
        p = plan_with(faq_prop("business_hours", "Lo mismo."),
                      faq_prop("coverage", "Lo mismo."))
        self.assertEqual(len(p.propositions), 2)
        self.assertEqual(p.compose("").count("Lo mismo."), 2)

    def test_g35f_17_composition_follows_plan_order_not_registration_order(self):
        p = plan_with(faq_prop("late", "SEGUNDA", priority=90),
                      faq_prop("early", "PRIMERA", priority=20))
        out = p.compose("cabeza")
        self.assertLess(out.index("PRIMERA"), out.index("SEGUNDA"))

    def test_g35f_18_the_primary_prose_is_policed_like_any_other_item(self):
        """It enters as PRIMARY_REPLY, so a turn P2 silenced cannot smuggle it out."""
        p = plan_with(faq_prop("business_hours", "Horario."), human_owns_turn=True)
        self.assertEqual(p.compose("prosa del handler"), "")

    def test_g35f_19_a_renderer_that_raises_costs_only_its_own_proposition(self):
        def boom():
            raise RuntimeError("renderer defect")
        p = plan_with(Proposition(proposition_id="faq.broken",
                                  kind=ResponseItemKind.FAQ_ANSWER, claim_family="faq",
                                  render=boom, priority=50),
                      faq_prop("payment", "Métodos de pago.", priority=60))
        out = p.compose("primaria")
        self.assertIn("primaria", out)
        self.assertIn("Métodos de pago.", out)

    def test_g35f_20_a_turn_that_composed_carries_a_live_plan(self):
        t = Turn(((("w0", Q, 0),)), evidence=None).run()
        try:
            plan = t.engine._turn_response_plan
            self.assertIsNotNone(plan)
            self.assertFalse(plan.shadow, "a composed turn reported a shadow plan")
            self.assertTrue(plan.plan_id.startswith("response-plan:"))
            self.assertEqual(plan.outcome, t.out.action)
        finally:
            t.close()

    def test_g35f_21_the_plan_id_is_stable_and_carries_no_text(self):
        t = Turn(((("w0", Q, 0),)), evidence=None).run()
        try:
            plan_id = t.engine._turn_response_plan.plan_id
            self.assertEqual(plan_id, t.engine._turn_plan(t.out).plan_id)
            for fragment in ("horario", "Qué", "w0"):
                with self.subTest(fragment=fragment):
                    self.assertNotIn(fragment, plan_id)
        finally:
            t.close()

    def test_g35f_22_projecting_the_plan_dispatches_interprets_and_mutates_nothing(self):
        t = Turn(((("w0", Q, 0),)), evidence=None).run()
        try:
            before = (len(t.sent), t.engine._turn_semantic_calls, t.engine._reply_calls)
            for _ in range(3):
                t.engine._turn_plan(t.out)
            self.assertEqual(
                (len(t.sent), t.engine._turn_semantic_calls, t.engine._reply_calls),
                before)
        finally:
            t.close()

    def test_g35f_23_the_plan_and_the_recorded_attribution_agree(self):
        for rows in (((("w0", Q, 0),)),
                     ((("w0", "¿Hay algo mañana?", 0),)),
                     ((("w0", "gracias, muy amables", 0),))):
            t = Turn(rows, evidence=None).run()
            try:
                plan = t.engine._turn_response_plan
                if plan is None or not plan.items:
                    continue
                with self.subTest(burst=rows[0][1][:20]):
                    primary = next(i for i in plan.items
                                   if i.proposition_id == PropositionId.PRIMARY_REPLY)
                    self.assertEqual(primary.decision_authority,
                                     t.engine._turn_decision_authority
                                     or DecisionAuthority.NONE)
                    self.assertEqual(plan.dispatch_path, t.engine._turn_dispatch_path
                                     or DispatchPath.NONE)
                    self.assertEqual(plan.dispatch_result,
                                     t.engine._turn_dispatch_result
                                     or DispatchResult.NOT_ATTEMPTED)
            finally:
                t.close()


# ══ C — the owner policies ══════════════════════════════════════════════════

class C_OwnerPolicies(unittest.TestCase):

    # ── P2 ───────────────────────────────────────────────────────────────────

    def test_g35f_24_p2_a_human_owned_turn_suppresses_every_automated_answer(self):
        p = plan_with(faq_prop("business_hours", "Horario."),
                      faq_prop("payment", "Pagos."), human_owns_turn=True)
        self.assertEqual(p.compose("prosa"), "")
        plan = p.to_plan(plan_id="x", renderer_source=RendererSource.NO_PROSE,
                         outcome="skipped_human")
        self.assertEqual(plan.included_items, ())
        for item in plan.suppressed_items:
            with self.subTest(item=item.proposition_id):
                self.assertEqual(item.suppression_reason,
                                 SuppressionReason.HUMAN_OWNS_TURN)
                self.assertEqual(item.policy, ResponsePolicy.P2_HUMAN_HANDOFF_EXCLUSIVE)

    def test_g35f_25_p2_is_declared_by_the_production_handoff_gate(self):
        body = inspect.getsource(ConversationEngine._handle)
        self.assertIn("_plan.human_owns_turn = True", body)
        t = Turn(((("w0", Q, 0),)), evidence=None, state_extra={"needs_human": True}).run()
        try:
            self.assertEqual(t.out.action, "skipped_human")
            self.assertTrue(t.engine._live_plan.human_owns_turn)
            self.assertEqual(t.sent, [], "an automated answer followed a human handoff")
        finally:
            t.close()

    def test_g35f_26_p2_survives_a_proposition_registered_earlier_in_the_turn(self):
        """A producer that ran BEFORE the handoff was declared is suppressed too."""
        p = plan_with(faq_prop("business_hours", "Horario."))
        p.human_owns_turn = True
        self.assertEqual(p.compose("prosa"), "")

    # ── P3 ───────────────────────────────────────────────────────────────────

    def test_g35f_27_p3_several_propositions_become_one_message(self):
        p = plan_with(faq_prop("business_hours", "Horario."), faq_prop("payment", "Pagos."))
        out = p.compose("primaria")
        self.assertEqual(p.to_plan(plan_id="x", renderer_source=RendererSource.NO_PROSE,
                                   outcome=None).transport_multiplicity,
                         TransportMultiplicity.SINGLE_MESSAGE)
        self.assertNotIn("\n\n\n", out)

    def test_g35f_28_p3_a_second_text_in_one_turn_is_refused(self):
        """Not warned about — refused, through the same blocked-outbound shape a kill
        switch uses, so the turn cannot silently occupy two messages."""
        e = ConversationEngine.__new__(ConversationEngine)
        e._live_plan = LiveResponsePlan(turn_id="t")
        e._live_plan.compose("ya dije lo que había que decir")
        ctx = SimpleNamespace(contact=SimpleNamespace(wa_id="5491100"),
                              thread=SimpleNamespace(id=1))
        with self.assertRaises(OutboundBlockedError) as caught:
            ConversationEngine._send_text_to_wa(e, ctx, "y además esto")
        self.assertEqual(caught.exception.gate_outcome, "BLOCKED_SINGLE_MESSAGE_POLICY")
        self.assertEqual(e._live_plan.refused_second_text, 1)

    def test_g35f_29_p3_a_real_turn_sends_at_most_one_text(self):
        for rows in (((("w0", Q, 0),)),
                     ((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5))),
                     ((("w0", "Tengo un Focus 2019.", 0), ("w1", "Estoy en Pilar.", 5)))):
            t = Turn(rows, evidence=None).run()
            try:
                with self.subTest(burst=rows[-1][1][:24]):
                    self.assertLessEqual(len(t.sent), 1,
                                         f"the turn occupied {len(t.sent)} messages")
            finally:
                t.close()

    # ── P4 ───────────────────────────────────────────────────────────────────

    def test_g35f_30_p4_the_flow_sequence_is_typed_as_one_plan(self):
        p = LiveResponsePlan(turn_id="t", flow_sequence=True)
        plan = p.to_plan(plan_id="x", renderer_source=RendererSource.NO_PROSE,
                         outcome="flow_button_sent")
        self.assertEqual(plan.transport_multiplicity,
                         TransportMultiplicity.FLOW_INTRO_THEN_FLOW)

    def test_g35f_31_p4_the_flow_is_counted_apart_from_text(self):
        p = plan_with(
            Proposition(proposition_id=PropositionId.FLOW_INTRO,
                        kind=ResponseItemKind.FLOW_INTRO_TEXT, claim_family="scheduling",
                        render=lambda: "Te dejo el formulario.", priority=10),
            Proposition(proposition_id=PropositionId.BOOKING_FLOW,
                        kind=ResponseItemKind.META_FLOW, claim_family="scheduling",
                        dispatch_path=DispatchPath.META_FLOW, priority=20),
            flow_sequence=True)
        plan = p.to_plan(plan_id="x", renderer_source=RendererSource.NO_PROSE,
                         outcome="flow_button_sent")
        payload = plan.to_payload()
        self.assertEqual(payload["planned_text_items"], 1)
        self.assertEqual(payload["planned_flow_items"], 1)

    # ── P6 ───────────────────────────────────────────────────────────────────

    def test_g35f_32_p6_a_fact_the_producer_marked_redundant_is_withheld(self):
        p = plan_with(faq_prop("business_hours", "Horario.", redundant=True),
                      faq_prop("payment", "Pagos."))
        out = p.compose("primaria")
        self.assertNotIn("Horario.", out)
        self.assertIn("Pagos.", out)
        plan = p.to_plan(plan_id="x", renderer_source=RendererSource.NO_PROSE, outcome=None)
        withheld = next(i for i in plan.suppressed_items
                        if i.proposition_id == PropositionId.faq("business_hours"))
        self.assertEqual(withheld.suppression_reason, SuppressionReason.ALREADY_STATED)
        self.assertEqual(withheld.policy, ResponsePolicy.P6_NO_ROUTINE_FACT_REPETITION)

    def test_g35f_33_every_suppressed_item_says_why(self):
        for plan_obj in (plan_with(faq_prop("a", "A"), human_owns_turn=True),
                         plan_with(faq_prop("b", "B", redundant=True))):
            plan_obj.compose("x")
            plan = plan_obj.to_plan(plan_id="x",
                                    renderer_source=RendererSource.NO_PROSE, outcome=None)
            for item in plan.suppressed_items:
                with self.subTest(item=item.proposition_id):
                    self.assertTrue(item.suppression_reason)

    def test_g35f_34_the_plan_holds_no_wording_of_its_own(self):
        """A phrase catalogue here would put business copy outside its owner."""
        src = pathlib.Path(
            ROOT / "backend/app/services/response_composition.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines()
                         if not l.lstrip().startswith("#"))
        for spanish in ("Horario", "revisión", "Gracias por", "Hola", "cotiza"):
            with self.subTest(word=spanish):
                self.assertNotIn(spanish, code)


# ══ D — the Flow completion acknowledgement ═════════════════════════════════

class D_FlowCompletionAck(unittest.TestCase):

    COMPLETE = {c: True for c in FLOW_COMPLETION_ACK_CONDITIONS}

    def test_g35f_35_all_seven_conditions_authorize(self):
        self.assertEqual(flow_completion_ack_authorized(self.COMPLETE), (True, ()))

    def test_g35f_36_any_single_unmet_condition_withholds_it(self):
        for condition in FLOW_COMPLETION_ACK_CONDITIONS:
            with self.subTest(condition=condition):
                authorized, unmet = flow_completion_ack_authorized(
                    dict(self.COMPLETE, **{condition: False}))
                self.assertFalse(authorized)
                self.assertEqual(unmet, (condition,))

    def test_g35f_37_an_absent_condition_is_unmet_never_assumed(self):
        self.assertEqual(flow_completion_ack_authorized({}),
                         (False, tuple(FLOW_COMPLETION_ACK_CONDITIONS)))
        for condition in FLOW_COMPLETION_ACK_CONDITIONS:
            partial = {c: True for c in FLOW_COMPLETION_ACK_CONDITIONS if c != condition}
            with self.subTest(missing=condition):
                self.assertEqual(flow_completion_ack_authorized(partial)[1], (condition,))

    def test_g35f_38_the_conditions_come_from_the_pinned_contract(self):
        """Relaxing one must mean editing the contract, not editing a call site."""
        body = inspect.getsource(flow_completion_ack_authorized)
        self.assertIn("FLOW_COMPLETION_ACK_CONDITIONS", body)
        self.assertEqual(len(FLOW_COMPLETION_ACK_CONDITIONS), 7)
        for expected in ("flow_submitted", "signature_verified", "token_valid",
                         "payload_complete", "validation_passed",
                         "persistence_committed", "not_already_acknowledged"):
            with self.subTest(condition=expected):
                self.assertIn(expected, FLOW_COMPLETION_ACK_CONDITIONS)

    # ── the engine's own evidence ────────────────────────────────────────────

    CTX = SimpleNamespace(thread=SimpleNamespace(id=1),
                          contact=SimpleNamespace(wa_id="5491100000000"))

    def engine_for_ack(self, *, secret="s" * 32, already=False, expected_token=None):
        e = ConversationEngine.__new__(ConversationEngine)
        e.settings = SimpleNamespace(whatsapp_app_secret=secret)
        e._live_plan = LiveResponsePlan(turn_id="t")
        e._turn_fact_sources = []
        e._flow_token_expected = expected_token
        e._flow_ack_already_sent = lambda ctx, txn_key: already
        return e

    FLOW_DATA = {"nombre": "Ana", "direccion": "Av. Siempreviva 742",
                 "tipo_vendedor": "particular"}

    def test_g35f_39_a_committed_booking_plans_exactly_one_acknowledgement(self):
        e = self.engine_for_ack()
        self.assertTrue(e._plan_flow_completion_ack(
            self.CTX, thread_rev_id=7, flow_token=None, flow_data=self.FLOW_DATA))
        acks = [p for p in e._live_plan.propositions
                if p.proposition_id == PropositionId.FLOW_COMPLETION_ACK]
        self.assertEqual(len(acks), 1)
        self.assertTrue(acks[0].included)
        self.assertEqual(acks[0].decision_authority, DecisionAuthority.FLOW_TRANSACTION)

    def test_g35f_40_it_is_never_planned_before_persistence(self):
        e = self.engine_for_ack()
        self.assertFalse(e._plan_flow_completion_ack(
            self.CTX, thread_rev_id=None, flow_token=None, flow_data=self.FLOW_DATA))
        ack = next(p for p in e._live_plan.propositions
                   if p.proposition_id == PropositionId.FLOW_COMPLETION_ACK)
        self.assertFalse(ack.included)
        self.assertIn("persistence_committed", ack.suppression_reason)

    def test_g35f_41_an_empty_or_incomplete_payload_plans_none(self):
        for payload in ({}, {"nombre": ""}, {"otra_cosa": 1}):
            e = self.engine_for_ack()
            with self.subTest(payload=payload):
                self.assertFalse(e._plan_flow_completion_ack(
                    self.CTX, thread_rev_id=7, flow_token=None, flow_data=payload))

    def test_g35f_42_a_mismatched_token_plans_none(self):
        e = self.engine_for_ack(expected_token="expected")
        self.assertFalse(e._plan_flow_completion_ack(
            self.CTX, thread_rev_id=7, flow_token="otro", flow_data=self.FLOW_DATA))
        ack = next(p for p in e._live_plan.propositions
                   if p.proposition_id == PropositionId.FLOW_COMPLETION_ACK)
        self.assertIn("token_valid", ack.suppression_reason)

    def test_g35f_43_an_unauthenticated_webhook_plans_none(self):
        """A business fact told to a customer may not rest on an unverified request."""
        for secret in ("", "   ", None):
            e = self.engine_for_ack(secret=secret)
            with self.subTest(secret=repr(secret)):
                self.assertFalse(e._plan_flow_completion_ack(
                    self.CTX, thread_rev_id=7, flow_token=None, flow_data=self.FLOW_DATA))

    def test_g35f_44_a_duplicate_completion_plans_no_second_acknowledgement(self):
        e = self.engine_for_ack(already=True)
        self.assertFalse(e._plan_flow_completion_ack(
            self.CTX, thread_rev_id=7, flow_token=None, flow_data=self.FLOW_DATA))
        ack = next(p for p in e._live_plan.propositions
                   if p.proposition_id == PropositionId.FLOW_COMPLETION_ACK)
        self.assertIn("not_already_acknowledged", ack.suppression_reason)

    def test_g35f_45_idempotency_reads_the_durable_outbound_ledger(self):
        """No new column and no migration: the ledger already records every send.

        G3-5F-BOUNDED-CORRECTION (audit G5F-A01): the lookup keys on the transaction, not
        on the acknowledgement's words. The phrase-matching form is asserted gone.
        """
        body = inspect.getsource(ConversationEngine._flow_ack_already_sent)
        self.assertIn("WhatsAppMessage", body)
        self.assertIn('direction == "out"', body)
        self.assertIn("correlation_id == txn_key", body)
        self.assertNotIn("_FLOW_COMPLETION_ACK", body)
        self.assertNotIn(".contains(", body)

    def test_g35f_46_a_ledger_read_that_fails_withholds_the_acknowledgement(self):
        """Fail closed: a second acknowledgement is customer-visible, a missing one is
        recoverable."""
        e = ConversationEngine.__new__(ConversationEngine)
        e.db = SimpleNamespace(query=lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        self.assertTrue(e._flow_ack_already_sent(
            SimpleNamespace(thread=SimpleNamespace(id=1)), "flow-txn:1"))

    def test_g35f_47_it_rides_in_the_same_message_as_the_receipt(self):
        """P3 governs this turn too: one completed form, one reply."""
        e = self.engine_for_ack()
        e._plan_flow_completion_ack(self.CTX, thread_rev_id=7, flow_token=None,
                                    flow_data=self.FLOW_DATA)
        composed = e._live_plan.compose("¡Listo! Recibimos tu solicitud 🎉")
        self.assertIn("Recibimos tu solicitud", composed)
        self.assertIn(ConversationEngine._FLOW_COMPLETION_ACK, composed)
        self.assertEqual(composed.count(ConversationEngine._FLOW_COMPLETION_ACK), 1)

    def test_g35f_48_it_is_planned_only_after_the_commit(self):
        body = inspect.getsource(ConversationEngine._process_flow_response)
        self.assertLess(body.index("self.db.commit()"),
                        body.index("self._plan_flow_completion_ack("),
                        "the acknowledgement was planned before the booking was committed")

    def test_g35f_49_the_wording_promises_contact_not_a_confirmed_appointment(self):
        """`appointment_approval_status` is PENDING at this moment. Saying the appointment
        is confirmed would be a false business fact — the class of defect this programme
        keeps closing."""
        ack = ConversationEngine._FLOW_COMPLETION_ACK.lower()
        for forbidden in ("turno confirmado", "confirmado", "agendado", "reservado"):
            with self.subTest(word=forbidden):
                self.assertNotIn(forbidden, ack)
        self.assertIn("se va a comunicar", ack)


# ══ E — trace 1.5 and the Inspector ═════════════════════════════════════════

class E_TraceAndInspector(unittest.TestCase):

    def test_g35f_50_the_contract_moved_and_every_older_one_stays_readable(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.8")
        for older in READABLE_VERSIONS[1:]:
            with self.subTest(version=older):
                self.assertIn(older, READABLE_VERSIONS)
        self.assertIn(TRACE_VERSION_1_4, READABLE_VERSIONS)

    def test_g35f_51_a_pre_1_5_plan_is_never_read_as_a_decision(self):
        self.assertIn(TRACE_VERSION_1_4, VERSIONS_WITH_SHADOW_ONLY_PLAN)
        self.assertNotIn(TRACE_VERSION, VERSIONS_WITH_SHADOW_ONLY_PLAN)
        from app.ui.hybrid_trace_view import _response_plan_card, UNAVAILABLE_BEFORE_1_5
        old = _response_plan_card({"trace_version": TRACE_VERSION_1_4,
                                   "response_plan": {"plan_id": "p", "shadow": False}})
        self.assertIn(UNAVAILABLE_BEFORE_1_5, old)
        self.assertIn("observado", old)

    def test_g35f_52_a_live_plan_is_labelled_by_the_scope_it_actually_governs(self):
        from app.ui.hybrid_trace_view import _response_plan_card
        card = _response_plan_card({
            "trace_version": TRACE_VERSION, "final_message_count": 1,
            "response_plan": {"plan_id": "response-plan:abc", "shadow": False,
                              "item_count": 1, "planned_text_items": 1,
                              "planned_flow_items": 0, "suppressed_count": 0,
                              "items": [{"kind": "TEXT", "proposition_id": "turn.primary_reply",
                                         "included": True,
                                         "decision_authority": "DETERMINISTIC_RULE",
                                         "renderer_source": "REPLY_MODEL"}]}})
        # G3-5F-BOUNDED-CORRECTION (audit G5F-A02). The card used to read "(autoridad)",
        # which claimed more than the plan holds: it governs the propositions REGISTERED
        # with it, not the principal action, the canonical mutations, the transport or the
        # transaction — and content added downstream reaches the customer uncounted.
        self.assertIn("composición", card)
        self.assertNotIn("autoridad", card)
        # G3-6 replaced the temporary "legacy content is uncounted" warning with a standing
        # scope statement: every proposition is counted now, and the plan still refuses to
        # claim the action, mutation, transport or transaction authorities.
        self.assertIn("Alcance", card)
        self.assertIn("turn.primary_reply", card)
        self.assertIn("DETERMINISTIC_RULE", card)

    def test_g35f_53_the_trace_carries_no_customer_text_or_identifier(self):
        t = Turn(((("wamid.HHH", "Tengo un Focus 2019 en Vicente López", 0),)),
                 evidence=None).run()
        try:
            plan = t.engine._turn_response_plan
            blob = json.dumps(plan.to_payload())
            for fragment in ("Focus", "Vicente", "wamid.HHH", "5491"):
                with self.subTest(fragment=fragment):
                    self.assertNotIn(fragment, blob)
        finally:
            t.close()


# ══ F — degraded modes and non-regression ═══════════════════════════════════

class F_DegradedAndNonRegression(unittest.TestCase):

    def test_g35f_54_a_turn_with_no_semantic_interpretation_still_plans(self):
        for kw in ({"evidence": None}, {"interpret_ok": False},
                   {"arm_interpreter": False}):
            t = Turn(((("w0", Q, 0),)), **kw).run()
            try:
                with self.subTest(mode=list(kw)[0]):
                    self.assertIsNotNone(t.engine._turn_response_plan)
                    self.assertLessEqual(len(t.sent), 1)
            finally:
                t.close()

    def test_g35f_55_a_plan_projection_failure_never_costs_the_turn(self):
        t = Turn(((("w0", Q, 0),)), evidence=None).run()
        try:
            sent_before = list(t.sent)
            t.engine._live_plan.to_plan = lambda **kw: (_ for _ in ()).throw(RuntimeError())
            self.assertIsNotNone(t.engine._turn_plan(t.out))
            self.assertEqual(t.sent, sent_before)
        finally:
            t.close()

    def test_g35f_56_f01_and_f02_remain_closed(self):
        """A dated burst is still not answered with opening hours."""
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

    def test_g35f_57_no_provider_call_and_no_model_or_prompt_change(self):
        import re
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertEqual(len(re.findall(r"self\._call_openai\(", ENGINE_SRC)), 1)
        self.assertEqual(len(re.findall(r"self\._reply_decision\(", ENGINE_SRC)), 2)
        self.assertEqual(len(re.findall(r"self\._ai_invoked = True", ENGINE_SRC)), 1)
        self.assertIn('PROMPT_VERSION = "understand/1.20"', interp)
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertIn('REPLY_PROMPT_VERSION = "reply/1.0"', ENGINE_SRC)

    def test_g35f_58_the_outbound_gate_is_still_the_only_sender(self):
        # Three constructions: the text send, the Flow send, and the single blocked exit
        # `_blocked_outbound`, which audits the intent without sending. Nothing else may
        # construct a gate, and no send may bypass one.
        self.assertEqual(ENGINE_SRC.count("OutboundSafetyGate("), 3,
                         "a send appeared outside the gate")
        self.assertIn("gate.attempt(", ENGINE_SRC)
        senders = [n for n, fn in vars(ConversationEngine).items()
                   if inspect.isfunction(fn)
                   and "OutboundSafetyGate(" in inspect.getsource(fn)]
        self.assertEqual(sorted(senders),
                         ["_blocked_outbound", "_send_flow_button", "_send_text_to_wa"])

    def test_g35f_59_the_schedule_remains_the_sole_hours_authority(self):
        table = "_WEEKDAY" + "_HOURS"
        self.assertNotIn(table, ENGINE_SRC)
        self.assertNotIn(table, (ROOT / "backend/app/services/response_composition.py")
                         .read_text(encoding="utf-8"))
        self.assertIn("business_hours_for_weekday", ENGINE_SRC)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
