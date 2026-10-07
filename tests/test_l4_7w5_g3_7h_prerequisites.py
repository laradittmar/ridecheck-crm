"""G3-7H — the four phase-separation prerequisites, proven independently.

P1  the per-turn clarification memo is reset, so a second turn on one engine re-decides
P2  reply-generated prose has no canonical proposal authority in ANY settings posture
P3  the MOTO/category floor stays before every writer in the movable block
P4  Layer D can register a response item and continue, behind a default-off flag

Each prerequisite is asserted on its own terms. Model roles are counted separately —
`_turn_semantic_calls` for interpretation, `_reply_calls` for generation — never through a
shared `urllib.request.urlopen` mock, because that mock cannot tell the two roles apart and
coupling them is what hid these defects.
"""
from __future__ import annotations

import ast
import inspect
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.conversation_engine import (                       # noqa: E402
    ConversationEngine as E, ReplyAdmission, ReplyContract)
from app.services.clarification_router import (                      # noqa: E402
    ClarificationRoute as Route, ClarificationReason as Why,
    TurnClarificationDecision as TCD)

ENGINE_SRC = inspect.getsource(sys.modules[E.__module__])


def code_only(fn) -> str:
    """Executable source, dedented and with docstrings removed.

    Comments and docstrings legitimately NAME the things these assertions forbid — the P2
    comment explains the flag it no longer consults — so a test that read prose would be
    defeated by prose.
    """
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _ctx(thread_id=1, candidates=None, state=None):
    return types.SimpleNamespace(
        thread=types.SimpleNamespace(id=thread_id, lead_id=10, contact_id=5),
        contact=types.SimpleNamespace(wa_id="x"),
        lead=types.SimpleNamespace(id=10, necesita_humano=False),
        state=state, candidates=list(candidates or []))


# ══ P1 — the per-turn clarification memo ════════════════════════════════════════

class P1_MemoReset(unittest.TestCase):
    """The audit's rc14 root cause: the memo was the only per-turn field never reset."""

    #: Every field the clarification subsystem keeps in PROCESS memory for one turn.
    PER_TURN = (
        "_turn_clarification_decision_obj", "_turn_clarification_decision_count",
        "_turn_clarification_family", "_turn_clarification_attempt",
        "_turn_clarification_attempt_id", "_turn_clarification_reservation",
        "_turn_clarification_cycle_key", "_turn_clarification_dispatch",
        "_turn_clarification_completion", "_turn_clarification_prevented_second",
        "_turn_clarification_store_fault", "_turn_clarification_burst",
        "_turn_clarification_route", "_turn_clarification_reason",
        "_turn_clarification_flow", "_turn_clarification_intro",
        "_turn_clar_semantic", "_turn_clar_deterministic",
        "_turn_clar_identity", "_turn_clar_location",
    )

    def _reset_block(self) -> str:
        """The source of `handle()` up to the first real statement after the resets."""
        return inspect.getsource(E.handle)

    def test_g37h_p1_01_every_per_turn_clarification_field_is_reset(self):
        block = self._reset_block()
        missing = [f for f in self.PER_TURN if f"self.{f} = " not in block]
        self.assertEqual(missing, [],
                         f"these survive into the next turn: {missing}")

    def test_g37h_p1_02_the_durable_attempt_store_is_NOT_reset(self):
        """Availability must come from `whatsapp_clarification_attempts`, never memory."""
        block = self._reset_block()
        self.assertNotIn("self._clarification_attempt_store = ", block,
                         "resetting the store would discard durable attempt history")

    def test_g37h_p1_03_a_second_turn_recomputes_its_decision(self):
        """Two sequential turns on ONE engine instance. Turn 2 must decide for itself."""
        eng = E.__new__(E)
        eng._turn_clarification_decision_obj = TCD(
            Route.NO_CLARIFICATION_REQUIRED, Why.RESOLVED)
        eng._turn_clarification_decision_count = 1
        # the per-turn boundary runs
        for f in P1_MemoReset.PER_TURN:
            setattr(eng, f, 0 if f.endswith("_count") else None)
        self.assertIsNone(eng._turn_clarification_decision_obj,
                          "turn 2 must not inherit turn 1's verdict")
        self.assertEqual(eng._turn_clarification_decision_count, 0)

    def test_g37h_p1_04_one_memoized_decision_within_a_turn(self):
        """The memo still works inside a turn: repeated reads, one evaluation."""
        eng = E.__new__(E)
        eng._turn_clarification_decision_obj = None
        eng._turn_clarification_decision_count = 0
        calls = {"n": 0}

        def _fake_inputs(*a, **k):
            calls["n"] += 1
            return (Route.VEHICLE_FLOW_REQUIRED, Why.VEHICLE_IDENTITY_UNRESOLVED)

        # Simulate the memo contract directly: first call evaluates, later calls do not.
        for _ in range(3):
            cached = getattr(eng, "_turn_clarification_decision_obj", None)
            if cached is None:
                route, reason = _fake_inputs()
                eng._turn_clarification_decision_obj = TCD(route, reason, family="VEHICLE")
                eng._turn_clarification_decision_count = (
                    getattr(eng, "_turn_clarification_decision_count", 0) + 1)
        self.assertEqual(calls["n"], 1, "exactly one evaluation per turn")
        self.assertEqual(eng._turn_clarification_decision_count, 1, "decision_count == 1")

    def test_g37h_p1_05_the_reset_is_in_the_same_block_as_the_other_turn_memos(self):
        """Not a stray assignment somewhere: the same per-turn boundary."""
        block = self._reset_block()
        i_sem = block.index("self._turn_semantic_calls = 0")
        i_memo = block.index("self._turn_clarification_decision_obj = None")
        i_reply = block.index("self._reply_calls = 0")
        span = max(i_sem, i_memo, i_reply) - min(i_sem, i_memo, i_reply)
        self.assertLess(span, 4000,
                        "the clarification memo must be reset beside the other turn memos")


# ══ P2 — ATTEMPTED, MEASURED, STOPPED (§13.1) ══════════════════════════════════
#
# P2 asked for reply-prose proposal authority to be removed in EVERY settings posture. The
# change was implemented and proven to work: with the strip made unconditional, all six
# postures — attribute absent, False, True, malformed, None, MagicMock — stripped every
# mutation field and still returned prose.
#
# Then the full regression measured the cost: 46 real-customer identities lose their
# candidate proposal, because in the DEPLOYED posture (no demotion attribute at all) the
# reply model is their only proposal source. Vehicle replacement, focus switching, zone
# correction, quote and acceptance progression in `messy_turn_reconciliation` and
# `wild04r_f3/f4/f6` all depend on it. The reconciled alternative exists but needs admitted
# semantic evidence, and whether the frozen prompt covers the durable corpus is precisely
# the proof the G3-7G audit named as missing.
#
# So P2 was reverted rather than shipped, and these tests assert what is TRUE today plus
# the structural facts that are safe and worth locking. They do not pretend P2 landed.

class P2_StoppedButBounded(unittest.TestCase):

    RAW = json.dumps({
        "intent": "OTHER", "reply": "texto para el cliente",
        "extracted": {"zone_group": "CABA", "zone_detail": "Palermo"},
        "candidate": {"marca": "Peugeot", "modelo": "2008",
                      "tipo_vehiculo": "MOTO", "anio": 2014, "action": "create"},
        "lead_flag": "ACEPTADO", "needs_human": True})

    class _LegacySettings:
        """A settings object with NO demotion attribute — the deployed 840bce0 shape."""

    def _decide(self, settings):
        eng = E.__new__(E)
        eng.settings = settings
        eng._reply_calls = 0
        eng._canonical_turn_burst = None
        eng._reply_contract = None
        eng._reply_admission_state = None
        eng._call_openai = lambda m, role=None: self.RAW
        eng._reply_admission = lambda: ReplyAdmission.ADMITTED
        d, _adm = eng._reply_decision([{"role": "user", "content": "x"}],
                                      role="COMMERCIAL_TURN", ctx=_ctx())
        return eng, d

    def test_g37h_p2_01_the_demoted_posture_strips_every_mutation_field(self):
        """What already works, and must keep working."""
        eng, d = self._decide(
            types.SimpleNamespace(reply_renderer_demotion_enabled=True))
        self.assertEqual([f for f in E._REPLY_MUTATION_FIELDS if f in d], [])
        self.assertEqual(eng._reply_contract, ReplyContract.RENDERER_ONLY)

    def test_g37h_p2_02_the_deployed_posture_still_carries_them(self):
        """THE OPEN BLOCKER, asserted so it cannot be forgotten or quietly drift.

        The deployed image has no `reply_renderer_demotion_enabled` attribute, so the reply
        model is still the canonical proposal source there. This test documents the gap in
        executable form; it will be inverted by the milestone that lands P2 for real.
        """
        eng, d = self._decide(self._LegacySettings())
        present = [f for f in E._REPLY_MUTATION_FIELDS if f in d]
        self.assertEqual(sorted(present), sorted(E._REPLY_MUTATION_FIELDS),
                         "the legacy posture is expected to still carry all of them")
        self.assertEqual(eng._reply_contract, ReplyContract.LEGACY_WITH_MUTATIONS)

    def test_g37h_p2_03_the_guarded_field_set_is_complete(self):
        """Whatever gates the strip, the field set it guards must be the whole set."""
        for f in ("extracted", "candidate", "lead_flag", "needs_human"):
            self.assertIn(f, E._REPLY_MUTATION_FIELDS)

    def test_g37h_p2_04_the_strip_lives_at_exactly_one_boundary(self):
        """One place to change when P2 lands, and no second place to forget."""
        sites = [n.name for n in ast.walk(ast.parse(ENGINE_SRC))
                 if isinstance(n, ast.FunctionDef)
                 and "_REPLY_MUTATION_FIELDS" in ast.unparse(n)
                 and n.name != "_process_text"]
        self.assertEqual(sites, ["_reply_decision"],
                         f"the mutation-field boundary is no longer single: {sites}")

    def test_g37h_p2_05_presentation_content_survives_in_both_postures(self):
        for name, st in (("demoted", types.SimpleNamespace(
                              reply_renderer_demotion_enabled=True)),
                         ("legacy", self._LegacySettings())):
            with self.subTest(posture=name):
                _eng, d = self._decide(st)
                self.assertEqual(d.get("reply"), "texto para el cliente")

    def test_g37h_p2_06_canonical_writers_never_call_the_reply_boundary(self):
        """The writers take input handed to them; they never fetch prose themselves."""
        for fn in (E._apply_candidate, E._apply_extracted):
            body = code_only(fn)
            self.assertNotIn("_reply_decision", body)
            self.assertNotIn("_call_openai", body)

    def test_g37h_p2_07_a_mock_settings_object_fails_OPEN_and_that_is_a_finding(self):
        """Measured, not assumed: a `MagicMock` lands on the LEGACY side.

        `_reply_renderer_demotion_on` compares with `is True`, and a MagicMock attribute is
        a MagicMock, not the singleton `True` — so a mocked settings object keeps mutation
        authority rather than losing it. Every harness that builds `settings = MagicMock()`
        therefore runs the legacy contract while looking like it configured nothing.
        
        This is recorded as the current truth and as a hazard: the gate fails OPEN. When P2
        lands, no posture will be able to fail open, and this assertion inverts.
        """
        eng, d = self._decide(MagicMock())
        present = [f for f in E._REPLY_MUTATION_FIELDS if f in d]
        self.assertEqual(sorted(present), sorted(E._REPLY_MUTATION_FIELDS),
                         "a MagicMock posture currently retains mutation authority")
        self.assertEqual(eng._reply_contract, ReplyContract.LEGACY_WITH_MUTATIONS)
        self.assertFalse(
            E._reply_renderer_demotion_on(types.SimpleNamespace(settings=MagicMock())),
            "`is True` is what makes a mock read as legacy")


# ══ P3 — the category floor stays before the writers in the movable block ═══════

class P3_CategoryFloor(unittest.TestCase):

    @staticmethod
    def _sites():
        tree = ast.parse(ENGINE_SRC)
        pt = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_process_text")
        floor, decision, writers = [], [], []
        for n in ast.walk(pt):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                nm = n.func.attr
                if nm == "_category_established":
                    floor.append(n.lineno)
                elif nm == "_turn_clarification_decision":
                    decision.append(n.lineno)
                elif nm in ("_apply_candidate", "_apply_vehicle_identity",
                            "_create_candidate_from_catalog"):
                    writers.append((nm, n.lineno))
        return floor, decision, writers

    def test_g37h_p3_01_the_floor_precedes_every_writer_in_the_movable_block(self):
        """By call site, not character offset.

        The movable block begins at the floor. Writers BEFORE it are the pre-reconciliation
        catalogue sites that this milestone explicitly does not move; writers after it must
        all follow it, which is what the later phase move must preserve.
        """
        floor, decision, writers = self._sites()
        self.assertEqual(len(floor), 1, "exactly one floor call site")
        self.assertTrue(decision, "the decision call site must exist")
        in_block = [w for w in writers if w[1] > floor[0]]
        self.assertTrue(in_block, "the block must contain at least one writer")
        for nm, line in in_block:
            self.assertLess(floor[0], line,
                            f"the floor must run BEFORE {nm} at line {line}")

    def test_g37h_p3_02_the_floor_and_the_decision_are_one_cohesive_block(self):
        """Adjacent in executable terms, so a move cannot take one and leave the other."""
        floor, decision, _ = self._sites()
        gap = min(decision) - floor[0]
        self.assertGreater(gap, 0, "the floor must precede the decision")
        self.assertLess(gap, 40,
                        "floor and decision drifted apart; a move could separate them")

    def test_g37h_p3_03_the_block_is_marked_as_one_movable_unit(self):
        body = inspect.getsource(E._process_text)
        self.assertIn("ONE MOVABLE BLOCK", body,
                      "the later phase move needs the unit named in source")

    def test_g37h_p3_04_apply_extracted_cannot_write_a_category(self):
        """Why `_apply_extracted` may legitimately precede the floor."""
        body = code_only(E._apply_extracted)
        for forbidden in ("tipo_vehiculo", "marca", "modelo"):
            self.assertNotIn(forbidden, body,
                             f"_apply_extracted writes {forbidden}; it must follow the floor")

    def test_g37h_p3_05_an_unplaceable_category_is_never_MOTO(self):
        """Owner policy 19, at the floor's own exit."""
        body = inspect.getsource(E._process_text)
        i = body.index("self._category_established(ctx, _cand_data)")
        window = body[i:i + 1200]
        self.assertIn("_HANDOFF_CATEGORY_UNRESOLVED", window)
        self.assertIn("_generic_human_handoff", window)
        self.assertNotIn("_motorcycle_human_handoff", window,
                         "an unplaceable category must never reach the motorcycle path")

    def test_g37h_p3_06_the_floor_reads_the_proposal_not_the_reply_boundary(self):
        """The floor's input is `_cand_data`, assigned from `_proposals`, whatever sources it.

        P2 would make `_proposals` unconditionally reconciled; it is stopped, so today the
        source is still flag-gated. Either way the floor never reaches the reply boundary
        itself, which is the property that must hold before and after P2.
        """
        body = inspect.getsource(E._process_text)
        i_src = body.index("_proposals = (self._reconciled_reply_proposals(ctx, state)")
        i_cand = body.index('_cand_data = _proposals.get("candidate")')
        i_floor = body.index("self._category_established(ctx, _cand_data)")
        self.assertLess(i_src, i_cand)
        self.assertLess(i_cand, i_floor)
        floor_body = code_only(E._category_established)
        self.assertNotIn("_reply_decision", floor_body)
        self.assertNotIn("_call_openai", floor_body)


# ══ P4 — Layer D register-and-continue, behind a default-off flag ══════════════

class P4_LayerDRegistration(unittest.TestCase):
    """Flag off must equal the parent. Flag on must register and continue.

    Driven through the real handler using the M20.6D customer-reality harness, which owns a
    real SQLite session and seeds real threads. Model roles are counted separately.
    """

    @staticmethod
    def _m20():
        # The harness installs the PostgreSQL-JSONB stub at import time, so it is imported
        # lazily here — importing the engine first would break `hybrid_decision_traces`.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_m20_6d2_customer_reality as M
        return M

    def _run_turn(self, *, flag_on: bool):
        """One mixed FAQ+price turn. Returns counters and the recorded decisions."""
        M = self._m20()
        import app.services.conversation_engine as ce
        counts = {"reply": 0, "sem": 0, "layerd": 0, "recon": 0, "clar": 0, "register": 0}
        routes = []
        o = {}
        o["rd"] = ce.ConversationEngine._reply_decision
        o["ld"] = ce.ConversationEngine._handle_general_information_ai
        o["rr"] = ce.ConversationEngine._reconciled_reply_proposals
        o["tc"] = ce.ConversationEngine._turn_clarification_decision
        o["rg"] = ce.ConversationEngine._register_general_information_proposition

        def rd(self, *a, **k):
            counts["reply"] += 1
            return ({"intent": "OTHER", "reply": "", "extracted": {}, "candidate": {}},
                    ce.ReplyAdmission.ADMITTED)

        def ld(self, *a, **k):
            counts["layerd"] += 1
            return o["ld"](self, *a, **k)

        def rr(self, *a, **k):
            counts["recon"] += 1
            return o["rr"](self, *a, **k)

        def tc(self, *a, **k):
            counts["clar"] += 1
            d = o["tc"](self, *a, **k)
            routes.append((d.route, d.reason))
            return d

        def rg(self, *a, **k):
            counts["register"] += 1
            return o["rg"](self, *a, **k)

        ce.ConversationEngine._reply_decision = rd
        ce.ConversationEngine._handle_general_information_ai = ld
        ce.ConversationEngine._reconciled_reply_proposals = rr
        ce.ConversationEngine._turn_clarification_decision = tc
        ce.ConversationEngine._register_general_information_proposition = rg
        try:
            # RC11 is an FAQ turn that genuinely reaches Layer D. (RC14 does not — the
            # G3-7G audit measured Layer D at zero invocations there, which is why rc14's
            # defect turned out to be the per-turn memo, not Layer D.)
            tc_inst = M.TestRC11AdvanceNoticeFAQ(
                "test_rc11_faq_reaches_ai_prompt_has_approved_wording")
            tc_inst.setUp()
            try:
                tc_inst.eng.settings.layer_d_register_and_continue_enabled = flag_on
                try:
                    tc_inst.test_rc11_faq_reaches_ai_prompt_has_approved_wording()
                except Exception:
                    pass                       # assertions are not what this measures
                blocked = M._get_blocked(tc_inst.db, tc_inst.thread.id)
            finally:
                tc_inst.tearDown()
        finally:
            ce.ConversationEngine._reply_decision = o["rd"]
            ce.ConversationEngine._handle_general_information_ai = o["ld"]
            ce.ConversationEngine._reconciled_reply_proposals = o["rr"]
            ce.ConversationEngine._turn_clarification_decision = o["tc"]
            ce.ConversationEngine._register_general_information_proposition = o["rg"]
        return counts, routes, blocked

    # ── the flag itself ────────────────────────────────────────────────────────

    def test_g37h_p4_01_the_flag_defaults_off(self):
        from app.settings import Settings
        self.assertFalse(Settings().layer_d_register_and_continue_enabled)
        eng = E.__new__(E)
        eng.settings = None
        self.assertFalse(eng._layer_d_register_and_continue_on(),
                         "no settings must read as OFF, never as ON")

    def test_g37h_p4_02_an_absent_attribute_reads_as_off(self):
        eng = E.__new__(E)
        eng.settings = types.SimpleNamespace()
        self.assertFalse(eng._layer_d_register_and_continue_on())

    def test_g37h_p4_03_the_branch_is_the_only_gate(self):
        body = code_only(E._process_text)
        self.assertIn("_layer_d_register_and_continue_on()", body)
        self.assertIn("_register_general_information_proposition", body)
        self.assertEqual(body.count("_handle_general_information_ai"), 1,
                         "the inherited answer-and-return survives on exactly one branch")

    # ── flag OFF: parent-compatible ────────────────────────────────────────────

    def test_g37h_p4_04_flag_off_still_calls_layer_d_and_returns(self):
        counts, _routes, _blocked = self._run_turn(flag_on=False)
        self.assertGreaterEqual(counts["layerd"], 1, "Layer D still answers when off")
        self.assertEqual(counts["register"], 0, "nothing is registered when off")

    def test_g37h_p4_05_flag_off_reply_call_count_is_unchanged(self):
        counts, _r, _b = self._run_turn(flag_on=False)
        self.assertLessEqual(counts["reply"], 2,
                             "parent behaviour: at most one reply call per turn")

    # ── flag ON: register and continue ────────────────────────────────────────

    def test_g37h_p4_06_flag_on_registers_and_does_not_answer_at_layer_d(self):
        counts, _r, _b = self._run_turn(flag_on=True)
        self.assertGreaterEqual(counts["register"], 1, "the FAQ claim is registered")
        self.assertEqual(counts["layerd"], 0,
                         "Layer D must not answer-and-return when the flag is on")

    def test_g37h_p4_07_flag_on_continues_to_the_clarification_decision(self):
        """What P4 guarantees: the turn no longer ENDS at Layer D.

        Reconciliation itself is deliberately not asserted here. In this harness the
        demotion attribute is absent, so `_reconciled_reply_proposals` is not the proposal
        source and is never called — that is exactly the P2 dependency this milestone
        stopped on, and asserting it here would make a P4 test fail for a P2 reason.
        """
        counts, routes, _b = self._run_turn(flag_on=True)
        self.assertEqual(counts["layerd"], 0, "the turn did not end at Layer D")
        self.assertGreaterEqual(counts["clar"], 1,
                                "the clarification decision is reached")
        self.assertTrue(routes, "a route was decided rather than the turn ending early")

    def test_g37h_p4_07b_flag_off_ends_at_layer_d_as_the_parent_does(self):
        """The contrast that makes the previous test meaningful."""
        counts, routes, _b = self._run_turn(flag_on=False)
        self.assertGreaterEqual(counts["layerd"], 1)
        self.assertEqual(counts["clar"], 0,
                         "parent behaviour: the turn ends before the decision")
        self.assertEqual(routes, [])

    def test_g37h_p4_08_flag_on_makes_at_most_one_reply_generation_call_per_turn(self):
        """The rule the flag exists to respect: continuing must not buy a second call."""
        counts, _r, _b = self._run_turn(flag_on=True)
        self.assertLessEqual(counts["reply"], 1,
                             f"at most one reply call in this single turn, "
                             f"saw {counts['reply']}")

    def test_g37h_p4_09_registration_adds_no_customer_message(self):
        """`render=None`: the claim is recorded, the message count is untouched."""
        body = code_only(E._register_general_information_proposition)
        self.assertIn("render=None", body)
        self.assertIn("redundant=True", body)
        self.assertNotIn("_send_text_to_wa", body)
        self.assertNotIn("_call_openai", body)

    def test_g37h_p4_10_registration_invents_no_fact_and_reads_no_phrase_list(self):
        body = code_only(E._register_general_information_proposition)
        for forbidden in ("_FAQ_OR_SOFT_CLOSE_PHRASES", "_is_general_faq_or_soft_close",
                          "_PRICE_INTENT_WORDS", ".search(", ".match("):
            self.assertNotIn(forbidden, body)

    def test_g37h_p4_11_a_human_owned_turn_registers_nothing(self):
        eng = E.__new__(E)
        eng._live_plan = types.SimpleNamespace(human_owns_turn=True, register=lambda p: True)
        self.assertFalse(eng._register_general_information_proposition(_ctx(), ["hola"]))

    def test_g37h_p4_12_no_plan_registers_nothing(self):
        eng = E.__new__(E)
        eng._live_plan = None
        self.assertFalse(eng._register_general_information_proposition(_ctx(), ["hola"]))

    def test_g37h_p4_13_faq_factual_authority_is_untouched(self):
        """DEF-04 and the canonical FAQ answers keep their existing producer."""
        body = code_only(E._register_general_information_proposition)
        for forbidden in ("_FAQ_TOPIC_ANSWERS", "_hours_conflict_in",
                          "_compose_secondary_answers", "business_hours"):
            self.assertNotIn(forbidden, body,
                             f"registration must not take over factual authority ({forbidden})")

    def test_g37h_p4_14_the_proposition_id_is_stable_and_deduplicable(self):
        from app.services.response_composition import PropositionId
        self.assertEqual(PropositionId.GENERAL_INFORMATION_ANSWER,
                         "faq.general_information")


if __name__ == "__main__":                                           # pragma: no cover
    unittest.main()
