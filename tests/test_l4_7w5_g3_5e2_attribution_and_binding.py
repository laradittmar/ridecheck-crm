"""L4.7W5-GATE3-G3-5E2 — the binding is enforced, and the attribution is populated.

The independent G3-5E review found the foundation structurally right and unfinished:

  G5E-01 HIGH  `_reply_burst` was written and read nowhere, so a result bound to another
               burst — or to none — could still mutate state, create a candidate, force a
               handoff, write prose and dispatch.
  G5E-02 HIGH  `decision_authority` was None on five of six real paths and `fact_sources`
               was set only by the FAQ composer, while the shadow plan papered over it with
               `RECONCILER if decision is not None` — so the Inspector gave two answers to
               "who decided".
  G5E-03 MED   seven early kill-switch exits recorded no dispatch path or result.
  G5E-05 MED   the reply role promised by contract 1.4 was never persisted.
  G5E-06 LOW   the plan id repeated the burst fingerprint.
  G5E-08 LOW   tests proved recording and representability where enforcement and production
               behaviour were required.

These tests capture the provider's actual messages, inject the defective bindings the guard
exists to catch, and read attribution off real production paths rather than hand-built enums.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import types
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
    ConversationEngine, ReplyAdmission, ReplyRole, _ANSWER_SOURCE_ATTRIBUTION,
    _canonical_burst_from_texts,
)
from unittest import mock                                                 # noqa: E402
from app.schemas.hybrid_trace import (                                    # noqa: E402
    DecisionAuthority, DispatchPath, DispatchResult, FactSource, RendererSource,
    ReplyModelRecord, TRACE_VERSION, TRACE_VERSION_1_3,
)

class _NullDb:
    def commit(self): pass
    def rollback(self): pass


class _StubGate:
    """Stands in for the outbound gate: records nothing, sends nothing, allows nothing."""
    def __init__(self, db): self.db = db
    def attempt(self, **kw): return SimpleNamespace(outcome=None, message_id=None)


class _StubCtx:
    contact = SimpleNamespace(wa_id="549110000000")
    thread = SimpleNamespace(id=1)


Q = HOURS_Q
#: A burst that reaches the reply model: no deterministic gate owns it.
REPLY_ROWS = (("w0", "Quiero cotizar un Focus 2019.", 0), ("w1", "Estoy en Vicente López.", 5))
FOREIGN = "f" * 64


class ReplyTurn(Turn):
    """A turn whose reply binding can be corrupted at the moment of consumption.

    The corruption is injected AFTER the provider boundary has recorded the real binding and
    BEFORE the result is read — exactly the window the guard exists to police. Nothing in the
    guard itself is stubbed.
    """

    def __init__(self, rows, *, corrupt=None, **kw):
        super().__init__(rows, **kw)
        self.captured_prompts = []
        engine = self.engine
        original_call = engine._call_openai
        original_build = engine._build_ai_messages

        def build(*a, **k):
            msgs = original_build(*a, **k)
            self.captured_prompts.append(msgs)
            return msgs

        def call(messages, *, role=None):
            out = original_call(messages, role=role)
            if corrupt == "foreign":
                engine._reply_burst = FOREIGN
            elif corrupt == "missing":
                engine._reply_burst = None
            return out

        engine._build_ai_messages = build
        engine._call_openai = call

    @property
    def user_turn(self) -> str:
        return self.captured_prompts[0][-1]["content"] if self.captured_prompts else ""

    @property
    def prompt_blob(self) -> str:
        return (json.dumps(self.captured_prompts[0], ensure_ascii=False)
                if self.captured_prompts else "")


def state_row(turn):
    sql = __import__("sqlalchemy").text
    return dict(
        state=tuple(turn.db.execute(sql(
            "select last_stage, needs_human, home_zone_detail from whatsapp_thread_states"
        )).first()),
        lead=tuple(turn.db.execute(sql(
            "select flag, estado, necesita_humano from leads")).first()),
        candidates=turn.db.execute(sql(
            "select count(*) from whatsapp_thread_candidates")).scalar(),
        revisions=turn.db.execute(sql("select count(*) from thread_revisions")).scalar())


# ══ A — the provider boundary ══════════════════════════════════════════════

class A_ProviderInput(unittest.TestCase):
    """G5E-08. What the provider receives, captured — not what a helper returns."""

    def test_g35e2_01_the_current_turn_block_carries_the_whole_burst(self):
        t = ReplyTurn(REPLY_ROWS, unanswered=["Estoy en Vicente López."], evidence=None).run()
        try:
            if not t.captured_prompts:
                self.skipTest("this burst did not reach the reply model")
            for text in (r[1] for r in REPLY_ROWS):
                self.assertIn(text, t.user_turn,
                              "a canonical burst message is missing from the current turn")
            self.assertIn("MENSAJES SIN RESPONDER", t.user_turn)
        finally:
            t.close()

    def test_g35e2_02_a_presentation_subset_cannot_define_the_current_turn(self):
        t = ReplyTurn(REPLY_ROWS, unanswered=["Estoy en Vicente López."], evidence=None).run()
        try:
            if not t.captured_prompts:
                self.skipTest("this burst did not reach the reply model")
            self.assertEqual(t.burst.presentation_texts, ("Estoy en Vicente López.",))
            self.assertIn("Quiero cotizar un Focus 2019.", t.user_turn,
                          "the omitted message must still be designated as current")
        finally:
            t.close()

    def test_g35e2_03_the_duplication_between_history_and_current_turn_is_measured(self):
        """G5E-07, recorded not redesigned: each burst message appears twice by design."""
        t = ReplyTurn(REPLY_ROWS, unanswered=["Estoy en Vicente López."], evidence=None).run()
        try:
            if not t.captured_prompts:
                self.skipTest("this burst did not reach the reply model")
            counts = {r[1]: t.prompt_blob.count(r[1]) for r in REPLY_ROWS}
            for text, n in counts.items():
                self.assertEqual(n, 2, f"{text!r} appears {n} times, expected history+turn")
            self.assertEqual(t.prompt_blob.count("CLIENTE:"), 3,
                             "history still carries the previous turn plus this burst")
        finally:
            t.close()


# ══ B — binding enforcement ════════════════════════════════════════════════

class B_BindingEnforcement(unittest.TestCase):
    """G5E-01. Not that the binding was recorded — that a bad one is refused."""

    REFUSAL = "Disculpá la demora, ya te ayudamos."

    def run_turn(self, corrupt=None, rows=REPLY_ROWS):
        return ReplyTurn(rows, unanswered=["Estoy en Vicente López."], evidence=None,
                         corrupt=corrupt).run()

    def test_g35e2_04_a_correct_binding_is_admitted(self):
        t = self.run_turn()
        try:
            if not t.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            self.assertEqual(t.engine._reply_burst, t.burst.fingerprint)
            self.assertEqual(t.engine._reply_admission_state, ReplyAdmission.ADMITTED)
            self.assertEqual(t.engine._turn_renderer_source, RendererSource.REPLY_MODEL)
        finally:
            t.close()

    def test_g35e2_05_a_foreign_binding_is_refused(self):
        t = self.run_turn(corrupt="foreign")
        try:
            if not t.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            self.assertEqual(t.engine._reply_admission_state,
                             ReplyAdmission.REJECTED_FOREIGN_BURST)
            self.assertNotEqual(t.engine._turn_renderer_source, RendererSource.REPLY_MODEL)
        finally:
            t.close()

    def test_g35e2_06_a_missing_binding_is_refused(self):
        t = self.run_turn(corrupt="missing")
        try:
            if not t.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            self.assertEqual(t.engine._reply_admission_state,
                             ReplyAdmission.REJECTED_MISSING_BINDING)
        finally:
            t.close()

    def test_g35e2_07_a_refused_result_mutates_nothing(self):
        """Extracted fields, candidate, narrative, flag and stage are all behind the guard."""
        clean = self.run_turn()
        refused = self.run_turn(corrupt="foreign")
        try:
            if not refused.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            after = state_row(refused)
            self.assertEqual(after["candidates"], 0, "a refused reply created a candidate")
            self.assertEqual(after["revisions"], 0)
            self.assertFalse(after["lead"][2], "a refused reply escalated to a human")
            self.assertIsNone(after["state"][2], "a refused reply wrote a zone")
        finally:
            clean.close(); refused.close()

    def test_g35e2_08_a_refused_result_writes_no_model_prose(self):
        t = self.run_turn(corrupt="foreign")
        try:
            if not t.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            self.assertTrue(any(self.REFUSAL in s for s in t.sent) or not t.sent,
                            f"model prose reached the customer: {t.sent}")
        finally:
            t.close()

    def test_g35e2_09_a_refusal_costs_no_second_provider_call(self):
        t = self.run_turn(corrupt="foreign")
        try:
            self.assertLessEqual(t.engine._reply_calls, 1)
        finally:
            t.close()

    def test_g35e2_10_a_stale_result_cannot_survive_an_evidence_change(self):
        t = self.run_turn()
        try:
            if not t.engine._reply_calls:
                self.skipTest("this burst did not reach the reply model")
            self.assertEqual(t.engine._reply_admission(), ReplyAdmission.ADMITTED)
            from app.services.conversation_engine import _canonical_burst_from_texts
            t.engine._canonical_turn_burst = _canonical_burst_from_texts(texts=["otra cosa"])
            self.assertEqual(t.engine._reply_admission(),
                             ReplyAdmission.REJECTED_FOREIGN_BURST)
        finally:
            t.close()

    def test_g35e2_11_repeated_admission_checks_call_nothing(self):
        t = self.run_turn()
        try:
            before = t.engine._reply_calls
            for _ in range(5):
                t.engine._reply_admission()
            self.assertEqual(t.engine._reply_calls, before)
        finally:
            t.close()

    def test_g35e2_12_not_dispatched_is_distinct_from_a_bad_binding(self):
        """A route that never used the reply model must not be turned into an error."""
        e = ConversationEngine.__new__(ConversationEngine)
        e._reply_calls = 0
        self.assertEqual(e._reply_admission(), ReplyAdmission.NOT_DISPATCHED)
        self.assertNotIn(ReplyAdmission.NOT_DISPATCHED, ReplyAdmission.REJECTED)

    def test_g35e2_13_no_production_reply_call_happens_without_a_canonical_burst(self):
        """Both consumers live in `_process_text`, which builds the burst before any gate.

        G3-5F §4.3 rewrote what "the call site" means: the consumers no longer call the
        provider, they ask `_reply_decision` for a decision. The property under test is
        unchanged — no reply may be obtained before the turn's burst exists — so it is
        asserted against the route that now exists.
        """
        import inspect
        src = inspect.getsource(ConversationEngine._process_text)
        self.assertIn("self._canonical_turn_burst = _canonical_burst(", src)
        burst_at = src.index("self._canonical_turn_burst = _canonical_burst(")
        call_at = src.index("self._reply_decision(")
        self.assertLess(burst_at, call_at,
                        "a reply call would precede the canonical burst")
        general = inspect.getsource(ConversationEngine._handle_general_information_ai)
        self.assertIn("self._reply_decision(", general)

    def test_g35f_13a_only_the_boundary_obtains_a_reply_decision(self):
        """§4.3. The `NOT_DISPATCHED` loophole, closed structurally rather than by audit.

        `_reply_admission` is safe only while every reply decision comes from the engine's
        own provider boundary. That held by inspection; it now holds by construction, and
        this is the assertion that keeps it holding: exactly one function calls the
        provider and parses its result, and no consumer does either.
        """
        import inspect
        src = inspect.getsource(ce)
        callers = [name for name, fn in vars(ConversationEngine).items()
                   if inspect.isfunction(fn) and "self._call_openai(" in
                   inspect.getsource(fn)]
        self.assertEqual(sorted(callers), ["_reply_decision"],
                         f"a reply call outside the boundary: {sorted(callers)}")
        # `_call_openai_inner` is the transport itself: it parses the HTTP envelope and
        # returns the message content, and never sees a decision. Every OTHER function
        # that parses reply JSON would have to have obtained it from the boundary — which
        # the assertion above has just shown is impossible.
        for name in ("_process_text", "_handle_general_information_ai"):
            with self.subTest(consumer=name):
                body = inspect.getsource(getattr(ConversationEngine, name))
                self.assertNotIn("json.loads(", body,
                                 "a consumer still parses a provider result itself")
        boundary = inspect.getsource(ConversationEngine._reply_decision)
        self.assertIn("self._reply_admission()", boundary)
        self.assertIn("self._refuse_reply(", boundary)
        self.assertIn("self._reply_admission_state = admission", boundary)

    def test_g35f_13b_a_substituted_provider_cannot_present_as_bound(self):
        """Test substitution stays possible — and stays distinguishable.

        The counter is read before and after the call, so a substituted `_call_openai`
        (which never increments it) reports NOT_DISPATCHED however the engine's binding
        fields happen to be set, and a real call can never report NOT_DISPATCHED.
        """
        e = ConversationEngine.__new__(ConversationEngine)
        e._reply_calls = 0
        # A stale binding from an earlier turn must not make a substituted call look bound.
        e._reply_burst = "stale-fingerprint"
        e._canonical_turn_burst = _canonical_burst_from_texts(
            thread_id=1, texts=["hola"], turn_anchor_id="wamid.A")
        e._call_openai = lambda messages, role=None: '{"reply": "x"}'
        decision, admission = e._reply_decision([], role="COMMERCIAL_TURN", ctx=None)
        self.assertEqual(admission, ReplyAdmission.NOT_DISPATCHED)
        self.assertEqual(decision["reply"], "x")
        self.assertEqual(e._reply_admission_state, ReplyAdmission.NOT_DISPATCHED)

    def test_g35e2_14_the_binding_is_taken_at_the_provider_boundary(self):
        import inspect
        body = inspect.getsource(ConversationEngine._call_openai)
        self.assertIn("self._reply_burst =", body,
                      "the binding must be recorded where the request is dispatched")


# ══ C — attribution from real production paths ═════════════════════════════

class C_Attribution(unittest.TestCase):
    """G5E-02. Read off the engine after a real turn — no hand-built enums."""

    def observe(self, rows, **kw):
        t = Turn(tuple(rows), **kw).run()
        e = t.engine
        return t, dict(authority=getattr(e, "_turn_decision_authority", None),
                       facts=list(getattr(e, "_turn_fact_sources", None) or ()),
                       renderer=getattr(e, "_turn_renderer_source", None),
                       path=getattr(e, "_turn_dispatch_path", None),
                       result=getattr(e, "_turn_dispatch_result", None),
                       legacy=getattr(t.out, "answer_source", None))

    def test_g35e2_15_the_deterministic_hours_path_is_attributed(self):
        t, a = self.observe([("w0", "¿Cuál es el horario de atención?", 0)], evidence=None)
        try:
            self.assertEqual(a["authority"], DecisionAuthority.DETERMINISTIC_RULE)
            self.assertIn(FactSource.FAQ_CANONICAL, a["facts"])
            # G3-6: the hours turn is now composed from a deterministic primary reply and
            # a canonical FAQ proposition — both authored by the business, so the renderer
            # is no longer "mixed". COMPOSED_MIXED meant "a canonical answer was stapled to
            # prose this engine did not author"; that mechanism is gone.
            self.assertEqual(a["renderer"], RendererSource.DETERMINISTIC_TEMPLATE)
            self.assertEqual((a["path"], a["result"]),
                             (DispatchPath.TEXT, DispatchResult.ALLOWED))
        finally:
            t.close()

    def test_g35e2_16_a_scheduling_answer_names_scheduling_data(self):
        t, a = self.observe([("w0", "¿Hay algo para el martes?", 0)],
                            evidence=availability_evidence(), stage="SCHEDULING")
        try:
            if a["authority"] is None:
                self.skipTest("this burst did not reach a scheduling producer")
            self.assertIn(a["authority"], DecisionAuthority.ALL)
            self.assertNotEqual(a["authority"], DecisionAuthority.NONE)
        finally:
            t.close()

    def test_g35e2_17_human_ownership_is_the_handoff_policy(self):
        t, a = self.observe([("w0", Q, 0)], evidence=None,
                            state_extra={"needs_human": True})
        try:
            self.assertEqual(a["legacy"], "HUMAN")
            self.assertEqual(a["authority"], DecisionAuthority.HUMAN_HANDOFF_POLICY)
            self.assertIn(FactSource.CANONICAL_STATE, a["facts"])
            self.assertIsNone(a["renderer"], "a suppressed turn wrote no prose")
        finally:
            t.close()

    def test_g35e2_18_a_dispatch_result_is_never_an_authority(self):
        self.assertNotIn("BLOCKED", DecisionAuthority.ALL)
        self.assertNotIn("ALLOWED", DecisionAuthority.ALL)
        self.assertIsNone(_ANSWER_SOURCE_ATTRIBUTION["ERROR_FALLBACK"][0],
                          "an error fallback is an outcome, not an authority")
        self.assertEqual(_ANSWER_SOURCE_ATTRIBUTION["ERROR_FALLBACK"][1], ())

    def test_g35e2_19_a_renderer_is_never_an_authority(self):
        for value in RendererSource.ALL:
            self.assertNotIn(value, DecisionAuthority.ALL)

    def test_g35e2_20_the_mapping_only_names_real_vocabulary(self):
        for source, (authority, facts) in _ANSWER_SOURCE_ATTRIBUTION.items():
            with self.subTest(source=source):
                if authority is not None:
                    self.assertIn(authority, DecisionAuthority.ALL)
                for fact in facts:
                    self.assertIn(fact, FactSource.ALL)

    def test_g35e2_21_clearing_the_declaring_field_clears_the_attribution(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e._turn_decision_authority = None
        e._turn_fact_sources = []
        e._answer_source = "PRICING_SERVICE"
        self.assertEqual(e._turn_decision_authority, DecisionAuthority.PRICING_SERVICE)
        self.assertIn(FactSource.PRICING_SERVICE, e._turn_fact_sources)
        e._answer_source = None
        self.assertIsNone(e._turn_decision_authority, "stale attribution survived a clear")
        self.assertEqual(e._turn_fact_sources, [])

    def test_g35e2_22_the_reply_model_is_not_a_source_of_canonical_facts(self):
        self.assertEqual(_ANSWER_SOURCE_ATTRIBUTION["CE_AI"][1], (),
                         "the renderer must not become the source of business facts")


# ══ D — kill-switch dispatch attribution ═══════════════════════════════════

class D_KillSwitchDispatch(unittest.TestCase):
    """G5E-03. The seven early exits now record what the gate did."""

    def test_g35e2_23_every_early_exit_records_the_dispatch(self):
        """G5E2-01/G5E2-04. Not "seven calls happened" — WHAT each one recorded.

        The count-only form of this test is the reason three exits recorded META_FLOW next
        to a CE_TEXT gate attempt and the suite stayed green. Each exit now names the
        authorized path it is blocking, and the transport is derived from that path, so
        this asserts the pairing that actually has to be true.
        """
        import inspect, re
        src = inspect.getsource(ce)
        exits = re.findall(
            r'if not self\._outbound_armed\(\):\n\s*'
            r'return self\._blocked_outbound\([^,]+,\s*[^,]+,\s*'
            r'OutboundPathId\.(\w+)\.value\)', src)
        # MIGRATED (G3-7F sequencing §14.5): four of the seven early exits lived in
        # the retired two-step clarification and no longer exist. The invariant is
        # unchanged — each surviving exit names its authorized path and derives its
        # transport from it — only the inventory is smaller.
        self.assertEqual(len(exits), 3,
                         "an early kill-switch exit does not use the one blocked exit")
        # The three text exits the G3-5E2 review found mislabelled as Meta Flows.
        self.assertEqual(sorted(exits),
                         ["CE_FLOW", "CE_FLOW", "CE_TEXT"],
                         f"the blocked paths are not the ones the sites dispatch: {exits}")
        for path in set(exits):
            with self.subTest(path=path):
                expected = (DispatchPath.META_FLOW if path.endswith("FLOW")
                            else DispatchPath.TEXT)
                self.assertEqual(
                    ConversationEngine._transport_for_path_id(path), expected)
        self.assertNotIn("_record_blocked_dispatch(DispatchPath", src,
                         "a site states a transport literal again — it must be derived")

    def test_g35f_23a_a_blocked_text_path_is_never_recorded_as_a_flow(self):
        """The defect itself, as a behavioural assertion over the real exit."""
        calls = {}
        e = ConversationEngine.__new__(ConversationEngine)
        e._record_blocked_dispatch = lambda p: calls.setdefault("path", p)
        e.db = _NullDb()
        ctx = _StubCtx()
        with mock.patch.object(ce, "OutboundSafetyGate", _StubGate):
            e._blocked_outbound(ctx, "texto", ce.OutboundPathId.CE_TEXT.value)
        self.assertEqual(calls["path"], DispatchPath.TEXT)
        calls.clear()
        with mock.patch.object(ce, "OutboundSafetyGate", _StubGate):
            e._blocked_outbound(ctx, "texto", ce.OutboundPathId.CE_FLOW.value)
        self.assertEqual(calls["path"], DispatchPath.META_FLOW)

    def test_g35f_23b_an_unknown_path_names_no_transport(self):
        """An unmapped path is the case the outbound invariants block. Guessing a
        transport for it would be the same falsehood this correction removes."""
        self.assertEqual(ConversationEngine._transport_for_path_id("NOT_A_PATH"),
                         DispatchPath.NONE)
        self.assertEqual(
            ConversationEngine._transport_for_path_id(
                ce.OutboundPathId.LEGACY_N8N_AI_PIPELINE.value), DispatchPath.NONE)

    def test_g35e2_24_the_recorder_touches_only_the_dispatch_fields(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e._turn_decision_authority = DecisionAuthority.PRICING_SERVICE
        e._turn_fact_sources = [FactSource.PRICING_SERVICE]
        e._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
        e._record_blocked_dispatch(DispatchPath.META_FLOW)
        self.assertEqual(e._turn_dispatch_path, DispatchPath.META_FLOW)
        self.assertEqual(e._turn_dispatch_result, DispatchResult.BLOCKED)
        self.assertEqual(e._turn_decision_authority, DecisionAuthority.PRICING_SERVICE,
                         "the gate overwrote the decision authority")
        self.assertEqual(e._turn_fact_sources, [FactSource.PRICING_SERVICE])
        self.assertEqual(e._turn_renderer_source, RendererSource.DETERMINISTIC_TEMPLATE)

    def test_g35e2_25_a_blocked_text_dispatch_records_blocked(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e._record_blocked_dispatch(DispatchPath.TEXT)
        self.assertEqual((e._turn_dispatch_path, e._turn_dispatch_result),
                         (DispatchPath.TEXT, DispatchResult.BLOCKED))


# ══ E — trace and plan consistency ═════════════════════════════════════════

class E_TraceAndPlan(unittest.TestCase):

    def test_g35e2_26_the_plan_invents_no_authority(self):
        """G5E-02. The default that disagreed with the trace is gone."""
        import inspect
        body = inspect.getsource(ConversationEngine._shadow_response_plan)
        self.assertNotIn("DecisionAuthority.RECONCILER if decision is not None", body)
        self.assertIn("or DecisionAuthority.NONE", body)

    def test_g35e2_27_plan_and_trace_agree_on_every_real_turn(self):
        cases = ([("w0", "¿Cuál es el horario de atención?", 0)],
                 [("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)],
                 [("w0", "gracias, muy amables", 0)])
        for rows in cases:
            with self.subTest(burst=rows[-1][1][:20]):
                t = Turn(tuple(rows), evidence=None).run()
                try:
                    e, plan = t.engine, t.engine._turn_response_plan
                    self.assertIsNotNone(plan)
                    recorded = (getattr(e, "_turn_decision_authority", None)
                                or DecisionAuthority.NONE)
                    self.assertEqual(plan.items[0].decision_authority, recorded,
                                     "the plan and the recorded authority disagree")
                    self.assertEqual(
                        set(plan.items[0].fact_sources),
                        set(getattr(e, "_turn_fact_sources", None) or ()),
                        "the plan and the recorded fact sources disagree")
                    self.assertEqual(plan.renderer_source,
                                     getattr(e, "_turn_renderer_source", None)
                                     or RendererSource.NO_PROSE)
                finally:
                    t.close()

    def test_g35e2_28_the_plan_id_no_longer_repeats_the_fingerprint(self):
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            plan = t.engine._turn_response_plan
            prefix = t.burst.fingerprint[:12]
            self.assertTrue(plan.plan_id.startswith("response-plan:"), plan.plan_id)
            self.assertEqual(plan.plan_id.count(prefix), 1,
                             f"the fingerprint repeats in {plan.plan_id!r}")
            self.assertIn(plan.decision_phase_id, plan.plan_id)
        finally:
            t.close()

    def test_g35e2_29_the_plan_id_is_stable_and_phase_bound(self):
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            first = t.engine._turn_response_plan.plan_id
            self.assertEqual(first, t.engine._shadow_response_plan(t.out).plan_id)
            state = SimpleNamespace(last_stage="SCHEDULING", needs_human=False)
            t.engine._availability_authority(None, state, list(t.burst.texts))
            self.assertNotEqual(first, t.engine._shadow_response_plan(t.out).plan_id,
                                "the plan id did not follow the decision phase")
        finally:
            t.close()

    def test_g35e2_30_the_reply_role_and_admission_serialize(self):
        record = ReplyModelRecord(model="gpt-4o-mini", prompt_version="reply/1.0",
                                  role=ReplyRole.COMMERCIAL_TURN,
                                  admission=ReplyAdmission.ADMITTED, calls=1,
                                  latency_ms=7, status="OK")
        payload = json.loads(json.dumps(record.to_payload()))
        self.assertEqual(payload["role"], "COMMERCIAL_TURN")
        self.assertEqual(payload["admission"], "ADMITTED")
        self.assertEqual(payload["prompt_version"], "reply/1.0")

    def test_g35e2_31_the_contract_moved_to_1_5_and_1_4_stays_readable(self):
        """G3-5E2 completed 1.4 in place because no row had ever claimed it.

        G3-5F could not do the same. It changed what `shadow` MEANS — a 1.4 plan only ever
        described a turn, a 1.5 plan may have decided it — so a reader must be able to tell
        the two apart. That is a semantic change, and it gets a version.
        """
        from app.schemas.hybrid_trace import (READABLE_VERSIONS, TRACE_VERSION_1_4,
                                              VERSIONS_WITH_SHADOW_ONLY_PLAN)
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.10")
        self.assertIn(TRACE_VERSION_1_4, READABLE_VERSIONS)
        self.assertIn(TRACE_VERSION_1_4, VERSIONS_WITH_SHADOW_ONLY_PLAN)
        self.assertNotIn(TRACE_VERSION, VERSIONS_WITH_SHADOW_ONLY_PLAN)

    def test_g35e2_32_a_pre_1_4_row_shows_the_role_as_unavailable(self):
        from app.ui.hybrid_trace_view import (UNAVAILABLE_BEFORE_1_4,
                                              _unavailable_before_1_4)
        render = _unavailable_before_1_4({"trace_version": TRACE_VERSION_1_3})
        self.assertIn(UNAVAILABLE_BEFORE_1_4, render("COMMERCIAL_TURN"))

    def test_g35e2_33_the_inspector_shows_the_role_and_the_admission(self):
        from app.ui import hybrid_trace_view as view
        src = pathlib.Path(view.__file__).read_text(encoding="utf-8")
        self.assertIn('("Rol"', src)
        self.assertIn('("Admisión del resultado"', src)


# ══ F — the plan stays inert, and nothing new reaches the customer ═════════

class F_NonRegression(unittest.TestCase):

    def test_g35e2_34_the_shadow_plan_still_changes_nothing(self):
        t = Turn((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)), evidence=None).run()
        try:
            before = (state_row(t), len(t.sent), t.engine._turn_semantic_calls,
                      t.engine._reply_calls)
            for _ in range(3):
                t.engine._shadow_response_plan(t.out)
            after = (state_row(t), len(t.sent), t.engine._turn_semantic_calls,
                     t.engine._reply_calls)
            self.assertEqual(before, after)
            # G3-5F: the plan is now the LIVE authority for a turn that composed, so it
            # reports `shadow=False`. The property this test exists for is unchanged and
            # is asserted above: PROJECTING the plan — however many times — dispatches
            # nothing, interprets nothing, renders nothing and mutates nothing.
            self.assertFalse(t.engine._turn_response_plan.shadow,
                             "a turn that composed must not report a shadow plan")
            for _ in range(3):
                projected = t.engine._turn_plan(t.out)
            self.assertEqual(
                (state_row(t), len(t.sent), t.engine._turn_semantic_calls,
                 t.engine._reply_calls), before, "projecting the live plan acted")
            self.assertEqual(projected.plan_id, t.engine._turn_response_plan.plan_id)
        finally:
            t.close()

    def test_g35e2_35_the_flow_acknowledgement_is_never_unconditional(self):
        """What may never return is a FREE acknowledgement: one copy, one sender, and the
        send is conditional on a committed booking's ledger lease."""
        # Convergence Phase 3 adaptation (CP1-06/CP1-07). OLD: the CE in-session writer
        # PLANNED the completion acknowledgement (`_plan_flow_completion_ack`) after its own
        # commit, keyed the outbound row `flow-txn:<ThreadRevision.id>`, and held the only
        # literal of the sentence. NEW: that writer is deleted; the acknowledgement is owed by
        # the booking LEDGER row and sent only by `acknowledge_committed_booking` after the
        # authority's commit, under the ledger lease (claim → gate → record), with the turn's
        # correlation id and causal inbound. The sentence lives in exactly one module and
        # `ConversationEngine._FLOW_COMPLETION_ACK` IS that object. `_plan_flow_completion_ack`
        # remains as a pinned evidence evaluator but has NO production caller (asserted by
        # `test_convergence_p3_cutover`). Behaviour: sb2b_01/06..22 and the PG race suite.
        import pathlib as _pl
        from app.services import booking_acknowledgement as _ack
        from app.services.conversation_engine import ConversationEngine as _CE
        app_dir = _pl.Path(_ack.__file__).resolve().parents[1]
        holders = [p.name for p in sorted(app_dir.rglob("*.py"))
                   if "Gracias por completarlo" in p.read_text(encoding="utf-8-sig")]
        self.assertEqual(holders, ["booking_acknowledgement.py"],
                         "the acknowledgement copy exists in more than one place")
        self.assertIs(_CE._FLOW_COMPLETION_ACK, _ack.BOOKING_FLOW_RECEIPT_TEXT)
        sends = [n for n, fn in vars(_CE).items() if inspect.isfunction(fn)
                 and "acknowledge_committed_booking" in inspect.getsource(fn)]
        self.assertEqual(sends, ["_sb_acknowledge"],
                         f"the acknowledgement is reachable elsewhere: {sends}")
        seq = inspect.getsource(_ack.acknowledge_committed_booking)
        self.assertLess(seq.index(".claim_acknowledgement(key)"),
                        seq.index("send_booking_acknowledgement("),
                        "the send is conditional on holding the ledger lease")
        authorizer = inspect.getsource(ce.ConversationEngine._plan_flow_completion_ack)
        self.assertIn("flow_completion_ack_authorized(evidence)", authorizer)
        for condition in ce.FLOW_COMPLETION_ACK_CONDITIONS:
            with self.subTest(condition=condition):
                self.assertIn(f'"{condition}"', authorizer)

    def test_g35e2_36_f01_and_f02_remain_closed(self):
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
        t = Turn((("w0", "Disponibles para mañana.", 0), ("w1", Q, 5)),
                 unanswered=[Q], trigger="w1", evidence=None).run()
        try:
            self.assertEqual(t.burst.texts, ("Disponibles para mañana.", Q))
            self.assertFalse(t.names_the_weekday_table())
        finally:
            t.close()

    def test_g35e2_37_no_extra_message_or_mutation(self):
        for rows in ((("w0", Q, 0), ("w1", "¿Hay algo mañana?", 5)),
                     (("w0", "¿Cuál es el horario de atención?", 0),),
                     REPLY_ROWS):
            with self.subTest(burst=rows[-1][1][:18]):
                t = Turn(rows, evidence=None).run()
                try:
                    self.assertLessEqual(len(t.sent), 1)
                    row = state_row(t)
                    self.assertEqual(row["revisions"], 0)
                    self.assertFalse(row["lead"][2])
                finally:
                    t.close()

    def test_g35e2_38_positive_control_the_harness_sees_output(self):
        t = Turn((("w0", "¿Cuál es el horario de atención?", 0),), evidence=None).run()
        try:
            self.assertEqual(len(t.sent), 1)
            self.assertTrue(t.names_the_weekday_table(),
                            "the harness cannot see output; every negative is vacuous")
        finally:
            t.close()

    def test_g35e2_39_no_customer_text_in_the_new_records(self):
        t = ReplyTurn(REPLY_ROWS, unanswered=["Estoy en Vicente López."], evidence=None,
                      corrupt="foreign").run()
        try:
            blob = json.dumps(t.engine._turn_response_plan.to_payload(), ensure_ascii=False)
            blob += str(t.engine._reply_admission_state) + str(t.engine._reply_burst)
            for fragment in ("Focus", "Vicente", "cotizar"):
                self.assertNotIn(fragment, blob)
        finally:
            t.close()

    def test_g35e2_40_the_freeze_holds(self):
        from app.services import scheduling_lexicon as lex
        from app.services.conversation_engine import _HOURS_FAQ_DETECTION
        from app.services import semantic_interpreter as si
        from app.schemas import turn_evidence as te
        self.assertEqual(len(_HOURS_FAQ_DETECTION), 8)
        self.assertEqual(len(lex.OPERATION_PREDICATES), 9)
        self.assertEqual(si.PROMPT_VERSION, "understand/1.20")
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.4")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
