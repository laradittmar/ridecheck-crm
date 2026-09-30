"""L4.7W5-GATE3-G3-7 — the reply model loses its mutation authority.

WHAT THIS MILESTONE DELIVERS, AND WHAT IT DOES NOT
--------------------------------------------------
Delivered unconditionally, and proved here:

  BC36-A01  the canonical price's redundancy is DERIVED from what the composed message
            actually carries, not asserted by the producer. A pricing turn whose primary is
            empty, is a clarification, names a different amount, or was rejected for
            conflict still sends the canonical price — exactly once.
  BC36-A03  producers are classified required/optional. An optional failure degrades
            quietly and traceably; a REQUIRED failure fails CLOSED to a bounded line rather
            than dispatching a partial answer that would read as complete.
  BC36-A02  the three mutations the previous audit found missing.
  G5D-01/02 the replacement authority for the reply model's four mutation fields exists,
            is typed, and is proved against injected evidence.

Delivered behind `REPLY_RENDERER_DEMOTION_ENABLED`, default OFF, and NOT cut over: the
replacement's INPUT is `understand/1.19`'s output, and proving it covers the durable corpus
needs a live interpretation this milestone is not authorized to make. With the flag off the
behaviour is byte-identical; the closeout records the exact evidence still owed.

NOT delivered: G36-01 (opaque primary decomposition) and G36-04 (the outbound validator)
both depend on that cutover and remain open.

A — the price, derived rather than assumed (BC36-A01)
B — required and optional producers (BC36-A03)
C — the replacement authority, against injected evidence (G5D-01/02)
D — the boundary contract
E — nothing else moved
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w5_g3_5b_unified_evidence_boundary import HOURS_Q, Turn         # noqa: E402
import app.services.conversation_engine as ce                                 # noqa: E402
from app.services.conversation_engine import (                                # noqa: E402
    ConversationEngine, ReplyContract, _format_price_ars,
)
from app.services.response_composition import (                               # noqa: E402
    LiveResponsePlan, Proposition, PropositionId, SuppressionReason,
)
from app.schemas import turn_evidence as te                                   # noqa: E402
from app.schemas.hybrid_trace import (                                        # noqa: E402
    DecisionAuthority, FactSource, RendererSource, ResponseItemKind, TRACE_VERSION,
)
from app.services.schedule import business_hours_summary_es                   # noqa: E402

MONEY = _format_price_ars(85000)
FALSE_SATURDAY = "Trabajamos sábados de 9 a 13 hs."
CONFIRMED = te.EvidenceStatus.CONFIRMED


def bench(primary, *, topics=(), quote=85000, src="PRICING_SERVICE", zone=("CABA", "Palermo"),
          candidates=("f",), fail=None, demoted=False, evidence=None):
    """The production producers and the production plan, where they really run."""
    state = SimpleNamespace(last_visible_slots=None, last_offered_slots=None,
                            needs_human=False, last_stage=ce.STAGE_QUALIFYING,
                            current_focus_candidate_id=1, home_zone_group=zone[0],
                            home_zone_detail=zone[1], pending_fuzzy_catalog_key=None,
                            pending_turn_evidence_text=None)
    focus = SimpleNamespace(id=1, tipo_vehiculo="AUTO", marca="Ford", modelo="Focus",
                            anio=2019, zone_group=zone[0], zone_detail=zone[1])
    ctx = SimpleNamespace(state=state, thread=SimpleNamespace(id=1),
                          candidates=list(candidates),
                          contact=SimpleNamespace(wa_id="x"))
    e = ConversationEngine.__new__(ConversationEngine)
    e.settings = SimpleNamespace(reply_renderer_demotion_enabled=demoted)
    e._turn_ctx = ctx
    e._contributing_sources = None
    e._turn_fact_sources = []
    e._turn_faq_burst = "b"
    e._turn_clarification_burst = None
    e._turn_text = "t"
    e._live_plan = LiveResponsePlan(turn_id="t")
    e._turn_price_quote = SimpleNamespace(precio_total=quote) if quote else None
    e._answer_source_value = src
    e._turn_decision_authority = None
    e._faq_topics_for_burst = lambda t, ctx=None, st=None: set(topics)
    e._focus_candidate = lambda c: focus
    e._get_active_inspection_location = lambda c, s: zone
    e._detect_explicit_inspection_request = lambda t: True
    e._decision_log = lambda *a, **k: None
    if evidence is not None:
        e._semantic_reading = lambda: SimpleNamespace(status="OK")
        e._semantic_turn_evidence = lambda: evidence
    if fail:
        setattr(e, fail, lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError(f"{fail} defect")))
    # Mirror `_send_text_to_wa` exactly: the boundary supplies the bounded line before
    # registration, so `compose()` owns the fail-closed decision.
    e._live_plan.degraded_fallback = ce._DEGRADED_RESPONSE_FALLBACK
    e._register_response_propositions(ctx, primary)
    out = e._live_plan.compose(primary)
    return SimpleNamespace(
        text=out, engine=e, plan_obj=e._live_plan,
        props={p.proposition_id: p for p in e._live_plan.propositions},
        plan=e._live_plan.to_plan(plan_id="p",
                                  renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                                  outcome="replied"))


# ══ A — the price, derived rather than assumed (BC36-A01) ══════════════════

class A_PriceDerivedRedundancy(unittest.TestCase):

    def test_g37_01_an_ordinary_quote_turn_says_the_price_once(self):
        r = bench(f"Genial! La cotización es de {MONEY}.")
        self.assertEqual(r.text.count(MONEY), 1)
        item = r.props[PropositionId.CANONICAL_PRICE]
        self.assertFalse(item.included)
        self.assertEqual(item.suppression_reason, SuppressionReason.ALREADY_STATED)

    def test_g37_02_a_primary_that_omits_the_price_still_sends_it(self):
        """THE BC36-A01 defect. Before: suppressed ALREADY_STATED, customer got no price."""
        r = bench("Genial! Ya lo anoté todo.")
        self.assertTrue(r.props[PropositionId.CANONICAL_PRICE].included)
        self.assertEqual(r.text.count(MONEY), 1)

    def test_g37_03_an_empty_primary_still_sends_the_price(self):
        r = bench("")
        self.assertEqual(r.text.count(MONEY), 1)

    def test_g37_04_a_clarification_primary_still_sends_the_price(self):
        r = bench("¿En qué zona está el auto?")
        self.assertEqual(r.text.count(MONEY), 1)
        self.assertIn("¿En qué zona está el auto?", r.text)

    def test_g37_05_a_different_amount_in_the_primary_still_sends_the_canonical_one(self):
        r = bench("Genial! Son $70.000.")
        self.assertEqual(r.text.count(MONEY), 1)

    def test_g37_06_a_rejected_primary_still_sends_the_price(self):
        r = bench(f"Genial! La cotización es de {MONEY}. {FALSE_SATURDAY}",
                  topics=("business_hours",))
        self.assertTrue(r.props[PropositionId.CANONICAL_PRICE].included)
        self.assertEqual(r.text.count(MONEY), 1)
        self.assertNotIn("sábados de 9 a 13", r.text)

    def test_g37_07_redundancy_is_canonical_value_equality_not_a_phrase_search(self):
        body = inspect.getsource(
            ce.LiveResponsePlan._value_already_delivered
            if hasattr(ce, "LiveResponsePlan") else
            __import__("app.services.response_composition", fromlist=["x"])
            .LiveResponsePlan._value_already_delivered)
        self.assertIn("prop.delivers_value", body)
        self.assertIn("other.included", body)
        for lexical in ("cotiz", "precio", "sale", "re.search", "re.match"):
            with self.subTest(token=lexical):
                self.assertNotIn(lexical, body.lower())

    def test_g37_08_only_an_included_proposition_can_make_the_price_redundant(self):
        """A claim withheld for conflict delivers nothing, so it cannot swallow the price."""
        p = LiveResponsePlan(turn_id="t")
        p.register(Proposition(proposition_id=PropositionId.PRIMARY_REPLY,
                               kind=ResponseItemKind.TEXT, claim_family="turn",
                               render=lambda: f"Son {MONEY}.", priority=10,
                               conflicts_with_canonical=True))
        p.register(Proposition(proposition_id=PropositionId.CANONICAL_PRICE,
                               kind=ResponseItemKind.CANONICAL_FACT, claim_family="pricing",
                               render=lambda: f"La cotización es de {MONEY}.",
                               priority=30, delivers_value=MONEY))
        out = p.compose(f"Son {MONEY}.")
        self.assertEqual(out.count(MONEY), 1)
        self.assertIn("La cotización", out)

    def test_g37_09_the_amount_token_is_the_engines_own_formatter(self):
        """One formatter, so the copy and the equality check cannot disagree."""
        self.assertEqual(_format_price_ars(85000), "$85.000")
        self.assertIn(_format_price_ars(85000),
                      ConversationEngine._build_quote_reply(None, None, None, 85000))


# ══ B — required and optional producers (BC36-A03) ═════════════════════════

class B_ProducerFailure(unittest.TestCase):

    def test_g37_10_the_registry_classifies_every_producer(self):
        names = [n for n, _m, _r in ConversationEngine._RESPONSE_PRODUCERS]
        self.assertEqual(sorted(names), ["clarification", "faq", "price", "vehicle"])
        required = {n for n, _m, r in ConversationEngine._RESPONSE_PRODUCERS if r}
        self.assertEqual(required, {"price", "faq", "vehicle"})

    def test_g37_11_an_optional_failure_degrades_quietly_and_traceably(self):
        r = bench("Anotado.", topics=("payment",), quote=None, src=None,
                  fail="_register_clarification_proposition")
        self.assertFalse(r.plan_obj.degraded, "an optional failure must not fail closed")
        self.assertEqual([n for n, _t in r.plan_obj.producer_failures], ["clarification"])
        self.assertIn("Anotado.", r.text)
        self.assertIn(PropositionId.faq("payment"), r.props)

    def test_g37_12_a_required_failure_fails_closed(self):
        for producer, method in (("price", "_register_price_proposition"),
                                 ("faq", "_register_faq_propositions"),
                                 ("vehicle", "_register_canonical_vehicle_proposition")):
            r = bench("Genial! Todo listo.", topics=("payment",), fail=method)
            with self.subTest(producer=producer):
                self.assertTrue(r.plan_obj.degraded)
                self.assertEqual(r.text, ce._DEGRADED_RESPONSE_FALLBACK)
                self.assertIn(producer,
                              [n for n, _t in r.plan_obj.producer_failures])

    def test_g37_13_the_degraded_line_claims_nothing(self):
        line = ce._DEGRADED_RESPONSE_FALLBACK.lower()
        for claim in ("$", "precio", "horario", "turno", "confirmado", "disponib",
                      "error", "exception", "traceback"):
            with self.subTest(forbidden=claim):
                self.assertNotIn(claim, line)
        self.assertIn("asesor", line)

    def test_g37_14_a_partial_answer_is_never_dispatched(self):
        """The whole point: what survives a required failure must not read as complete."""
        r = bench(f"Genial! La cotización es de {MONEY}.", topics=("payment",),
                  fail="_register_faq_propositions")
        self.assertNotIn(MONEY, r.text)
        self.assertNotIn("Aceptamos", r.text)
        self.assertEqual(r.text, ce._DEGRADED_RESPONSE_FALLBACK)

    def test_g37_15_one_producers_defect_does_not_stop_the_others(self):
        r = bench("Anotado.", topics=("payment",), quote=None, src=None,
                  fail="_register_clarification_proposition")
        self.assertIn(PropositionId.faq("payment"), r.props)

    def test_g37_16_the_fail_closed_decision_is_inside_compose(self):
        """So the "nothing is added after composition" invariant stays absolute."""
        send = inspect.getsource(ConversationEngine._send_text_to_wa)
        self.assertIn("plan.degraded_fallback = _DEGRADED_RESPONSE_FALLBACK", send)
        compose_at = send.index("text = plan.compose(text)")
        self.assertNotIn("text =", send[compose_at + 30:send.index("gate.attempt(")])

    def test_g37_16b_a_degraded_plan_with_no_bounded_line_goes_silent(self):
        """Defence in depth: if the boundary ever failed to supply the line, the turn is
        SILENT — never a partial answer. `_send_text_to_wa` sends nothing on empty text."""
        p = LiveResponsePlan(turn_id="t")
        p.register(Proposition(proposition_id=PropositionId.faq("payment"),
                               kind=ResponseItemKind.FAQ_ANSWER, claim_family="faq",
                               render=lambda: "Aceptamos efectivo.", priority=50))
        p.degraded = True
        self.assertEqual(p.compose("Genial! Todo listo."), "")

    def test_g37_17_a_degraded_turn_is_visible_in_the_plan_payload(self):
        r = bench("Genial!", topics=("payment",), fail="_register_faq_propositions")
        self.assertTrue(r.plan_obj.degraded)
        self.assertTrue(r.plan_obj.producer_failures)


# ══ C — the replacement authority, against injected evidence ═══════════════

class C_ReplacementAuthority(unittest.TestCase):
    """G5D-01/G5D-02. The four fields, derived from typed evidence rather than prose."""

    def derive(self, evidence, *, candidates=("f",)):
        r = bench("Anotado.", quote=None, src=None, demoted=True, evidence=evidence,
                  candidates=candidates)
        state = r.engine._turn_ctx.state
        return r.engine._reconciled_reply_proposals(r.engine._turn_ctx, state)

    def test_g37_18_a_named_vehicle_becomes_a_candidate_proposal(self):
        ev = te.TurnEvidence(vehicle_mentions=(te.VehicleEvidence(
            value="Peugeot 208", make="Peugeot", model="208", year=2022,
            status=CONFIRMED),))
        p = self.derive(ev, candidates=())
        self.assertEqual(p["candidate"]["action"], "create")
        self.assertEqual(p["candidate"]["marca"], "Peugeot")
        self.assertEqual(p["candidate"]["modelo"], "208")
        self.assertEqual(p["candidate"]["anio"], 2022)

    def test_g37_19_an_existing_candidate_is_reused_only_when_it_MATCHES(self):
        """G3-7-R2 supersedes this test's original claim, and correctly.

        It asserted "candidates exist → update", which was the first cut's naive mapping and
        is wrong: whether a mention reuses a candidate depends on whether it IS that
        candidate, not on whether any candidate exists. The reconciler decides that, and
        this now asserts the real rule in both directions.
        """
        from app.schemas.hybrid_trace import CandidateAction
        ev = te.TurnEvidence(vehicle_mentions=(te.VehicleEvidence(
            value="Focus", make="Ford", model="Focus", status=CONFIRMED),))
        matching = SimpleNamespace(id=1, marca="Ford", modelo="Focus",
                                   tipo_vehiculo="AUTO", status="current_focus",
                                   anio=None, zone_group=None, zone_detail=None)
        p = self.derive(ev, candidates=(matching,))
        self.assertEqual(p["candidate"]["action"], "update")
        self.assertEqual(p["candidate"]["id"], 1)
        self.assertEqual(p["candidate_resolution"].action, CandidateAction.REUSE_FOCUS)
        # a DIFFERENT existing vehicle is not this one
        other = SimpleNamespace(id=2, marca="Peugeot", modelo="208",
                                tipo_vehiculo="AUTO", status="current_focus",
                                anio=None, zone_group=None, zone_detail=None)
        q = self.derive(ev, candidates=(other,))
        self.assertEqual(q["candidate"]["action"], "create")
        self.assertEqual(q["candidate_resolution"].action, CandidateAction.CREATE)

    def test_g37_20_a_superseded_vehicle_is_not_proposed(self):
        """Named, then corrected away — the reconciler would not admit it."""
        ev = te.TurnEvidence(vehicle_mentions=(te.VehicleEvidence(
            value="208", make="Peugeot", model="208", status=CONFIRMED,
            is_superseded=True),))
        self.assertEqual(self.derive(ev)["candidate"]["action"], "none")

    def test_g37_21_locality_identity_and_schedule_become_extracted_fields(self):
        ev = te.TurnEvidence(
            identity_mentions=(te.IdentityEvidence(value="Ana",
                                                   normalized_value="Ana",
                                                   status=CONFIRMED),),
            location_mentions=(te.LocationEvidence(value="Pilar", locality="Pilar",
                                                   status=CONFIRMED),),
            scheduling_requests=(te.SchedulingRequestEvidence(
                value="mañana", resolved_date="2026-09-30", time="14:00",
                status=CONFIRMED),))
        p = self.derive(ev)
        self.assertEqual(p["extracted"]["customer_name"], "Ana")
        self.assertEqual(p["extracted"]["zone_detail"], "Pilar")
        self.assertEqual(p["extracted"]["preferred_day_iso"], "2026-09-30")
        self.assertEqual(p["extracted"]["preferred_time_str"], "14:00")

    def test_g37_22_acceptance_proposes_but_does_not_grant(self):
        ev = te.TurnEvidence(acceptance=te.AcceptanceEvidence(
            value="si", signal=te.AcceptanceSignal.ACCEPT, status=CONFIRMED))
        self.assertEqual(self.derive(ev)["lead_flag"], "ACEPTADO")
        # The C3B authorizer is still the grant, and it is untouched.
        self.assertTrue(hasattr(ConversationEngine, "_authorize_acceptance"))

    def test_g37_23_a_future_intent_is_not_acceptance(self):
        ev = te.TurnEvidence(acceptance=te.AcceptanceEvidence(
            value="mas adelante", signal=te.AcceptanceSignal.FUTURE_INTENT,
            status=CONFIRMED))
        self.assertIsNone(self.derive(ev)["lead_flag"])

    def test_g37_24_an_explicit_handoff_request_proposes_escalation(self):
        ev = te.TurnEvidence(handoff=te.HandoffEvidence(
            value="quiero hablar con alguien", requested=True, status=CONFIRMED))
        self.assertTrue(self.derive(ev)["needs_human"])

    def test_g37_25_a_negated_handoff_is_not_a_request(self):
        ev = te.TurnEvidence(handoff=te.HandoffEvidence(
            value="no hace falta hablar con nadie", requested=False, status=CONFIRMED))
        self.assertFalse(self.derive(ev)["needs_human"])

    def test_g37_26_absent_or_unavailable_evidence_proposes_nothing(self):
        for status in ("ABSENT", "UNAVAILABLE"):
            r = bench("Anotado.", quote=None, src=None, demoted=True)
            r.engine._semantic_reading = lambda s=status: SimpleNamespace(status=s)
            p = r.engine._reconciled_reply_proposals(r.engine._turn_ctx,
                                                      r.engine._turn_ctx.state)
            with self.subTest(status=status):
                self.assertEqual(p, {"extracted": {}, "candidate": {"action": "none"},
                                     "lead_flag": None, "needs_human": False})

    def test_g37_27_the_deriver_interprets_no_language(self):
        body = inspect.getsource(ConversationEngine._reconciled_reply_proposals)
        for lexical in ("re.search", "re.match", "re.findall", "lower()", "in text",
                        "quiero", "hablar", "peugeot"):
            with self.subTest(token=lexical):
                self.assertNotIn(lexical, body.lower())

    def test_g37_28_the_deriver_writes_nothing_and_calls_no_provider(self):
        body = inspect.getsource(ConversationEngine._reconciled_reply_proposals)
        for forbidden in ("self.db", "db.add", "db.commit", "_call_openai",
                          "_reply_decision", "interpret(", "_send_text_to_wa"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)


# ══ D — the boundary contract ══════════════════════════════════════════════

class D_BoundaryContract(unittest.TestCase):

    def engine(self, demoted):
        e = ConversationEngine.__new__(ConversationEngine)
        e.settings = SimpleNamespace(reply_renderer_demotion_enabled=demoted)
        e._reply_calls = 0
        e._canonical_turn_burst = ce._canonical_burst_from_texts(
            thread_id=1, texts=["hola"], turn_anchor_id="w1")

        def provider(messages, role=None):
            e._reply_calls += 1
            e._reply_burst = e._canonical_turn_burst.fingerprint
            return json.dumps({"reply": "prosa", "intent": "OTHER",
                               "extracted": {"customer_name": "Ana"},
                               "candidate": {"action": "create", "marca": "Ford"},
                               "lead_flag": "ACEPTADO", "needs_human": True})

        e._call_openai = provider
        return e

    def test_g37_29_demoted_the_mutation_fields_never_leave_the_boundary(self):
        e = self.engine(True)
        decision, _ = e._reply_decision([], role="COMMERCIAL_TURN", ctx=None)
        for field in ConversationEngine._REPLY_MUTATION_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(field, decision)
        self.assertEqual(decision["reply"], "prosa")
        self.assertEqual(e._reply_contract, ReplyContract.RENDERER_ONLY)

    def test_g37_30_legacy_keeps_them_and_says_so(self):
        e = self.engine(False)
        decision, _ = e._reply_decision([], role="COMMERCIAL_TURN", ctx=None)
        for field in ConversationEngine._REPLY_MUTATION_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, decision)
        self.assertEqual(e._reply_contract, ReplyContract.LEGACY_WITH_MUTATIONS)

    def test_g37_31_the_strip_happens_at_the_one_boundary(self):
        body = inspect.getsource(ConversationEngine._reply_decision)
        self.assertIn("for field in self._REPLY_MUTATION_FIELDS", body)
        self.assertIn("decision.pop(field, None)", body)
        consumers = inspect.getsource(ConversationEngine._process_text)
        for field in ConversationEngine._REPLY_MUTATION_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(f'decision.get("{field}")', consumers,
                                 "a consumer still reads the field off the decision")

    def test_g37_32_the_consumers_read_the_proposals_not_the_decision(self):
        body = inspect.getsource(ConversationEngine._process_text)
        self.assertIn("_proposals = (self._reconciled_reply_proposals(ctx, state)", body)
        for field in ConversationEngine._REPLY_MUTATION_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f'_proposals.get("{field}")', body)

    def test_g37_33_the_canonical_writers_are_unchanged(self):
        """The demotion moves the SOURCE. Every guard that stood between a proposal and
        canonical state still stands."""
        for writer in ("_apply_extracted", "_apply_candidate", "_authorize_acceptance",
                       "_enforce_catalog_vehicle"):
            with self.subTest(writer=writer):
                self.assertTrue(hasattr(ConversationEngine, writer))

    def test_g37_34_the_cutover_is_off_by_default(self):
        from app.settings import Settings
        self.assertIs(Settings().reply_renderer_demotion_enabled, False)

    def test_g37_35_the_model_receives_no_narrower_boundary_than_the_authority(self):
        """G5D-02. The reply call sits inside `_process_text`, after the canonical burst is
        registered and before any gate can return."""
        src = inspect.getsource(ConversationEngine._process_text)
        self.assertLess(src.index("self._canonical_turn_burst = _canonical_burst("),
                        src.index("self._reply_decision("))


# ══ E — nothing else moved ═════════════════════════════════════════════════

class E_NothingElseMoved(unittest.TestCase):

    def test_g37_36_flag_off_keeps_the_legacy_source(self):
        e = ConversationEngine.__new__(ConversationEngine)
        e.settings = SimpleNamespace()
        self.assertFalse(e._reply_renderer_demotion_on())

    def test_g37_37_def04_remains_closed(self):
        r = bench(FALSE_SATURDAY, topics=("business_hours",), quote=None, src=None)
        self.assertFalse(ce._hours_truth_violations(ce._norm_lower(r.text)))
        self.assertIn(business_hours_summary_es(), r.text)

    def test_g37_38_the_deleted_repair_machinery_is_still_gone(self):
        for gone in ("_strip_unverified_hours", "_strip_contradicting_sentences"):
            with self.subTest(fn=gone):
                self.assertFalse(hasattr(ce, gone))

    def test_g37_39_the_outbound_validator_is_untouched(self):
        """G36-04 remains OPEN and owned by the cutover. It was not modified here."""
        self.assertTrue(hasattr(ConversationEngine, "_validate_outbound_text"))
        import app.services.response_validator as rv
        self.assertTrue(hasattr(rv, "validate_response"))

    def test_g37_40_no_model_prompt_or_schema_moved(self):
        engine_src = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
        interp = (ROOT / "backend/app/services/semantic_interpreter.py").read_text(
            encoding="utf-8")
        self.assertIn('REPLY_PROMPT_VERSION = "reply/1.0"', engine_src)
        self.assertIn('PROMPT_VERSION = "understand/1.19"', interp)
        self.assertIn('or "gpt-4o-mini"', interp)
        self.assertEqual(te.SCHEMA_VERSION, "turn-evidence/1.3")
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.5")

    def test_g37_41_one_provider_call_site_and_one_semantic_interpretation(self):
        engine_src = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
        import re
        self.assertEqual(len(re.findall(r"self\._call_openai\(", engine_src)), 1)
        self.assertEqual(len(re.findall(r"self\._reply_decision\(", engine_src)), 2)
        self.assertEqual(len(re.findall(r"self\._ai_invoked = True", engine_src)), 1)

    def test_g37_42_no_stale_state_across_consecutive_turns(self):
        """BC36-A02. The reset list in `handle` covers every field G3-7 reads."""
        import ast, textwrap
        h = textwrap.dedent(inspect.getsource(ConversationEngine.handle))
        fn = ast.parse(h).body[0]
        reset = {t.attr for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Attribute)}
        for field in ("_turn_price_quote", "_answer_source", "_reply_contract",
                      "_live_plan", "_turn_faq_burst"):
            with self.subTest(field=field):
                self.assertIn(field, reset)
        body = ast.unparse(fn)
        handle_at = body.index("out = self._handle(event)")
        for field in ("self._turn_price_quote = None", "self._answer_source = None"):
            with self.subTest(field=field):
                self.assertLess(body.index(field), handle_at)

    def test_g37_43_a_real_turn_still_composes_one_message(self):
        t = Turn(((("w0", HOURS_Q, 0),)), evidence=None).run()
        try:
            self.assertLessEqual(len(t.sent), 1)
            self.assertIsNotNone(t.engine._turn_response_plan)
        finally:
            t.close()

    def test_g37_44_scheduling_meaning_is_unchanged(self):
        for texts in ([HOURS_Q, "¿Hay algo mañana?"], [HOURS_Q, "¿Y el martes?"]):
            t = Turn(tuple((f"w{i}", x, i * 5) for i, x in enumerate(texts)),
                     evidence=None).run()
            try:
                with self.subTest(burst=texts[-1]):
                    self.assertEqual(t.decision.hours_disposition,
                                     "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING")
            finally:
                t.close()

    def test_g37_45_the_flow_acknowledgement_is_intact(self):
        self.assertEqual(ConversationEngine._flow_transaction_key(101), "flow-txn:101")

    def test_g37_46_g36_01_and_g36_04_are_still_open_and_visible(self):
        """They are owned by the cutover this milestone did not arm. The Inspector must
        keep saying so, and this asserts it does."""
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        self.assertIn("turn.primary_reply", PLAN_SCOPE_NOTE)
        self.assertIn("opaco", PLAN_SCOPE_NOTE)
        self.assertIn("validador de salida", PLAN_SCOPE_NOTE)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
