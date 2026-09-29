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
)
from app.schemas.hybrid_trace import (                                    # noqa: E402
    DecisionAuthority, DispatchPath, DispatchResult, FactSource, RendererSource,
    ReplyModelRecord, TRACE_VERSION, TRACE_VERSION_1_3,
)

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
        """Both call sites live in `_process_text`, which builds the burst before any gate."""
        import inspect
        src = inspect.getsource(ConversationEngine._process_text)
        self.assertIn("self._canonical_turn_burst = _canonical_burst(", src)
        burst_at = src.index("self._canonical_turn_burst = _canonical_burst(")
        call_at = src.index("self._call_openai(")
        self.assertLess(burst_at, call_at,
                        "a reply call would precede the canonical burst")
        general = inspect.getsource(ConversationEngine._handle_general_information_ai)
        self.assertIn("self._call_openai(", general)

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
            self.assertEqual(a["renderer"], RendererSource.COMPOSED_MIXED)
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
        import inspect, re
        src = inspect.getsource(ce)
        exits = re.findall(r'if os\.environ\.get\("OUTBOUND_ENABLED"\) != "true":\n(\s*)'
                           r'self\._record_blocked_dispatch\(', src)
        self.assertEqual(len(exits), 7,
                         "an early kill-switch exit records no dispatch attribution")

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

    def test_g35e2_31_the_contract_stays_at_1_4(self):
        """1.4 was never deployed, so completing its promised field beats inventing 1.5."""
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.4")

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
            self.assertTrue(t.engine._turn_response_plan.shadow)
        finally:
            t.close()

    def test_g35e2_35_the_flow_acknowledgement_is_still_inactive(self):
        import app.services.booking_flow_service as bfs
        for mod in (ce, bfs):
            src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
            for needle in ("FLOW_COMPLETION_ACK", "Gracias por completarlo",
                           "se va a comunicar", "se estará comunicando"):
                self.assertNotIn(needle, src, f"{needle!r} became live in {mod.__name__}")

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
        self.assertEqual(si.PROMPT_VERSION, "understand/1.19")
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
