"""L4.7W5-GATE3-G3-6 — the plan counts what the message carries.

Three contributors used to run AFTER the message was composed, inside `_send_text_to_wa`:
the same-burst FAQ answer, the required-next-information question and the canonical vehicle
reconciliation. Each inspected finished prose and appended to it, stripped from it, or
replaced part of it. Two consequences the G3-5F review recorded:

  G5F-A02  the live plan could not see them, so it reported ONE item for a message carrying
           three claims, under a heading that called itself the response authority;
  DEF-04   the hours pass was substring surgery on words the system did not author —
           "generated prose → inspect → remove fragment → append canonical fact".

They are producers now. Each registers a typed proposition with a stable identity and its
canonical owner as renderer; the plan orders, deduplicates and suppresses; and nothing
appends a fact after composition. `_compose_secondary_answers`, `_strip_unverified_hours`
and `_strip_contradicting_sentences` are gone.

A — the legacy machinery is gone
B — FAQ propositions
C — clarification propositions
D — canonical vehicle propositions
E — DEF-04, adversarially
F — P1 and P6 through real production paths
G — the multi-proposition matrix (§12)
H — plan count versus the final message
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

from test_l4_7w5_g3_5b_unified_evidence_boundary import HOURS_Q, Turn      # noqa: E402
import app.services.conversation_engine as ce                              # noqa: E402
from app.services.conversation_engine import ConversationEngine            # noqa: E402
from app.services.response_composition import (                            # noqa: E402
    LiveResponsePlan, Proposition, PropositionId, SuppressionReason,
)
from app.schemas.hybrid_trace import (                                     # noqa: E402
    DecisionAuthority, FactSource, RendererSource, ResponseItemKind, ResponsePolicy,
    TRACE_VERSION, TransportMultiplicity,
)
from app.services.schedule import business_hours_summary_es                # noqa: E402

ENGINE_SRC = pathlib.Path(ce.__file__).read_text(encoding="utf-8")
VIEW_SRC = pathlib.Path(
    ROOT / "backend/app/ui/hybrid_trace_view.py").read_text(encoding="utf-8")

#: A burst that clears the service-intent gate and reaches the FAQ arming point.
COMMERCIAL = "Quiero revisar un Focus 2019."


def producer_engine(*, topics=None, slots=None, state_extra=None):
    """An engine positioned exactly where the producers run, with real canonical state."""
    raw = json.dumps(list(slots)) if slots else None
    state = SimpleNamespace(last_visible_slots=raw, last_offered_slots=raw,
                            needs_human=False, last_stage=ce.STAGE_QUALIFYING,
                            current_focus_candidate_id=None,
                            pending_fuzzy_catalog_key=None,
                            pending_turn_evidence_text=None,
                            **(state_extra or {}))
    ctx = SimpleNamespace(state=state, thread=SimpleNamespace(id=1), candidates=[],
                          contact=SimpleNamespace(wa_id="549110000000"))
    e = ConversationEngine.__new__(ConversationEngine)
    e._turn_ctx = ctx
    e._contributing_sources = None
    e._turn_fact_sources = []
    e._turn_faq_burst = "irrelevant"
    e._turn_clarification_burst = None
    e._live_plan = LiveResponsePlan(turn_id="t")
    if topics is not None:
        e._faq_topics_for_burst = lambda t, ctx=None, st=None: set(topics)
    return e, ctx


def compose_with(engine, ctx, primary):
    engine._register_response_propositions(ctx, primary)
    return engine._live_plan.compose(primary)


def props(engine):
    return {p.proposition_id: p for p in engine._live_plan.propositions}


# ══ A — the legacy machinery is gone ═══════════════════════════════════════

class A_LegacyRetired(unittest.TestCase):

    def test_g36_01_the_repair_functions_no_longer_exist(self):
        for gone in ("_strip_unverified_hours", "_strip_contradicting_sentences"):
            with self.subTest(fn=gone):
                self.assertFalse(hasattr(ce, gone))
        for gone in ("_compose_secondary_answers", "_apply_required_next_question",
                     "_enforce_canonical_vehicle_claim"):
            with self.subTest(method=gone):
                self.assertFalse(hasattr(ConversationEngine, gone))

    def test_g36_02_the_send_path_names_none_of_them(self):
        send = inspect.getsource(ConversationEngine._send_text_to_wa)
        for gone in ("_compose_secondary_answers", "_strip_unverified_hours",
                     "_apply_required_next_question", "_enforce_canonical_vehicle_claim",
                     "_faq_reconciliation_burst"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, send)

    def test_g36_03_nothing_is_appended_after_composition(self):
        """The ordering §14 requires: register, compose, then transport only."""
        send = inspect.getsource(ConversationEngine._send_text_to_wa)
        register = send.index("self._register_response_propositions(")
        compose = send.index("plan.compose(text)")
        gate = send.index("gate.attempt(")
        self.assertLess(register, compose)
        self.assertLess(compose, gate)
        after = send[compose:gate]
        for mutation in ("text +=", "text = text +", ".rstrip() +", "text.rstrip()"):
            with self.subTest(mutation=mutation):
                self.assertNotIn(mutation, after,
                                 "a fact is still appended after composition")

    def test_g36_04_the_side_channel_is_retired(self):
        self.assertNotIn("_faq_reconciliation_burst", ENGINE_SRC)
        self.assertIn("def _turn_owes_faq_answer", ENGINE_SRC)

    def test_g36_05_detection_survived_repair(self):
        """The per-unit judgement is what DETECTS a conflict; only the surgery is gone."""
        for kept in ("_hours_unit_survives", "_hours_prose_satisfies",
                     "_protected_slot_minutes", "_regenerated_offer_es",
                     "_hours_conflict_in", "_FAQ_TOPIC_CONTRADICTS"):
            with self.subTest(kept=kept):
                self.assertTrue(hasattr(ce, kept))


# ══ B — FAQ propositions ═══════════════════════════════════════════════════

class B_FaqPropositions(unittest.TestCase):

    def test_g36_06_one_topic_is_one_proposition_rendered_once(self):
        e, ctx = producer_engine(topics={"business_hours"})
        out = compose_with(e, ctx, "Anotado.")
        self.assertEqual(out.count(business_hours_summary_es()), 1)
        self.assertIn(PropositionId.faq("business_hours"), props(e))

    def test_g36_07_two_independent_safe_faqs_are_two_propositions(self):
        e, ctx = producer_engine(topics={"business_hours", "payment"})
        out = compose_with(e, ctx, "Anotado.")
        registered = props(e)
        self.assertIn(PropositionId.faq("business_hours"), registered)
        self.assertIn(PropositionId.faq("payment"), registered)
        self.assertEqual(out.count(business_hours_summary_es()), 1)

    def test_g36_08_a_duplicate_producer_for_one_claim_yields_one_item(self):
        e, ctx = producer_engine(topics={"business_hours"})
        compose_with(e, ctx, "Anotado.")
        before = len(e._live_plan.propositions)
        self.assertFalse(e._live_plan.register(Proposition(
            proposition_id=PropositionId.faq("business_hours"),
            kind=ResponseItemKind.FAQ_ANSWER, claim_family="faq",
            render=lambda: "otra redacción del mismo horario")))
        self.assertEqual(len(e._live_plan.propositions), before)

    def test_g36_09_the_canonical_value_comes_from_the_schedule_owner(self):
        e, ctx = producer_engine(topics={"business_hours"})
        out = compose_with(e, ctx, "Anotado.")
        self.assertIn(business_hours_summary_es(), out)
        self.assertIn("sábados de 9 a 15 hs", out, "the canonical Saturday is wrong")
        self.assertNotIn("sábados de 9 a 13", out, "the previously observed falsehood")

    def test_g36_10_the_faq_item_records_its_authority_and_owner(self):
        e, ctx = producer_engine(topics={"business_hours"})
        compose_with(e, ctx, "Anotado.")
        item = props(e)[PropositionId.faq("business_hours")]
        self.assertEqual(item.decision_authority, DecisionAuthority.DETERMINISTIC_RULE)
        self.assertEqual(item.fact_sources, (FactSource.FAQ_CANONICAL,))
        self.assertEqual(item.renderer_source, RendererSource.DETERMINISTIC_TEMPLATE)
        self.assertEqual(item.policy, ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ)
        self.assertEqual(item.locked_values, (("faq.business_hours", "canonical"),))

    def test_g36_11_identical_wording_under_two_identities_stays_two(self):
        p = LiveResponsePlan(turn_id="t")
        for topic in ("business_hours", "payment"):
            p.register(Proposition(proposition_id=PropositionId.faq(topic),
                                   kind=ResponseItemKind.FAQ_ANSWER, claim_family="faq",
                                   render=lambda: "La misma frase.", priority=50))
        self.assertEqual(len(p.propositions), 2)
        self.assertEqual(p.compose("").count("La misma frase."), 2)


# ══ C — clarification ══════════════════════════════════════════════════════

class C_Clarification(unittest.TestCase):

    def engine(self, **state_extra):
        e, ctx = producer_engine(topics=set(), state_extra=state_extra)
        ctx.candidates = []
        e._focus_candidate = lambda c: SimpleNamespace(id=1, tipo_vehiculo="AUTO")
        e._get_active_inspection_location = lambda c, s: (
            getattr(s, "home_zone_group", None), getattr(s, "home_zone_detail", None))
        e._detect_explicit_inspection_request = lambda t: True
        return e, ctx

    def test_g36_12_a_missing_location_becomes_one_typed_clarification(self):
        e, ctx = self.engine(home_zone_group=None, home_zone_detail=None)
        out = compose_with(e, ctx, "Sí, hacemos revisiones.")
        item = props(e)[PropositionId.clarification("inspection_location")]
        self.assertEqual(item.kind, ResponseItemKind.CLARIFICATION)
        self.assertTrue(item.included)
        self.assertIn(ce._CANONICAL_LOCATION_ASK.strip(), out)
        self.assertEqual(item.locked_values,
                         (("clarification.field", "inspection_location"),))

    def test_g36_13_a_known_location_is_never_asked_for(self):
        """P6 at its strongest: the producer has nothing to say, so it says nothing —
        it does not register a proposition and then hide it."""
        e, ctx = self.engine(home_zone_group="CABA", home_zone_detail="Palermo")
        out = compose_with(e, ctx, "Sí, hacemos revisiones.")
        self.assertNotIn(PropositionId.clarification("inspection_location"), props(e))
        self.assertNotIn("¿En qué localidad", out)

    def test_g36_14_a_reply_that_already_asks_suppresses_it_as_already_stated(self):
        """P6 / §10 — the suppression reason appears, and the item stays auditable."""
        e, ctx = self.engine(home_zone_group=None, home_zone_detail=None)
        out = compose_with(e, ctx, ce._CANONICAL_LOCATION_ASK.strip())
        item = props(e)[PropositionId.clarification("inspection_location")]
        self.assertFalse(item.included)
        self.assertEqual(item.suppression_reason, SuppressionReason.ALREADY_STATED)
        self.assertEqual(item.policy, ResponsePolicy.P6_NO_ROUTINE_FACT_REPETITION)
        self.assertEqual(out.count(ce._CANONICAL_LOCATION_ASK.strip()), 1,
                         "the question was asked twice")

    def test_g36_15_it_is_asked_once_however_many_producers_run(self):
        e, ctx = self.engine(home_zone_group=None, home_zone_detail=None)
        e._register_clarification_proposition(ctx, "Sí.")
        e._register_clarification_proposition(ctx, "Sí.")
        ids = [p.proposition_id for p in e._live_plan.propositions]
        self.assertEqual(ids.count(PropositionId.clarification("inspection_location")), 1)

    def test_g36_16_the_clarification_is_deterministic_and_canonical(self):
        e, ctx = self.engine(home_zone_group=None, home_zone_detail=None)
        compose_with(e, ctx, "Sí.")
        item = props(e)[PropositionId.clarification("inspection_location")]
        self.assertEqual(item.decision_authority, DecisionAuthority.DETERMINISTIC_RULE)
        self.assertEqual(item.fact_sources, (FactSource.CANONICAL_STATE,))


# ══ D — canonical vehicle ══════════════════════════════════════════════════

class D_CanonicalVehicle(unittest.TestCase):

    REPLY = "¡Hola! Sí, hacemos el servicio de revisión para un 2008 del 2014."

    def engine(self, *, candidates=()):
        e, ctx = producer_engine(topics=set())
        ctx.candidates = list(candidates)
        e._turn_text = "un 2008 del 2014"
        e._decision_log = lambda *a, **k: None
        return e, ctx

    def test_g36_17_a_vehicle_state_lacks_becomes_a_canonical_fact_item(self):
        e, ctx = self.engine()
        out = compose_with(e, ctx, self.REPLY)
        item = props(e)[PropositionId.CANONICAL_VEHICLE]
        self.assertEqual(item.kind, ResponseItemKind.CANONICAL_FACT)
        self.assertIn("¿Es un Peugeot 2008?", out)
        self.assertIn("Todavía no tengo confirmado el vehículo", out)
        self.assertEqual(dict(item.locked_values)["vehicle.marca"], "Peugeot")

    def test_g36_18_the_producer_still_arms_the_pending_confirmation(self):
        """The canonical decision belongs to the PRODUCER, never to a renderer."""
        e, ctx = self.engine()
        compose_with(e, ctx, self.REPLY)
        self.assertEqual(ctx.state.pending_fuzzy_catalog_key, "Peugeot||2008")

    def test_g36_19_a_known_vehicle_is_not_re_requested(self):
        e, ctx = self.engine(candidates=[SimpleNamespace(id=1, tipo_vehiculo="AUTO")])
        out = compose_with(e, ctx, self.REPLY)
        self.assertNotIn(PropositionId.CANONICAL_VEHICLE, props(e))
        self.assertNotIn("Todavía no tengo confirmado", out)

    def test_g36_20_a_reply_naming_no_vehicle_registers_nothing(self):
        e, ctx = self.engine()
        compose_with(e, ctx, "Perfecto, seguimos.")
        self.assertNotIn(PropositionId.CANONICAL_VEHICLE, props(e))

    def test_g36_21_a_reply_that_already_confirms_is_not_repeated(self):
        e, ctx = self.engine()
        out = compose_with(e, ctx, self.REPLY + " ¿Es un Peugeot 2008?")
        item = props(e)[PropositionId.CANONICAL_VEHICLE]
        self.assertFalse(item.included)
        self.assertEqual(item.suppression_reason, SuppressionReason.ALREADY_STATED)
        self.assertEqual(out.count("¿Es un Peugeot 2008?"), 1)

    def test_g36_22_no_phrase_specific_vehicle_matcher_was_added(self):
        body = inspect.getsource(
            ConversationEngine._register_canonical_vehicle_proposition)
        for lexical in ("peugeot", "ford", "chevrolet", "toyota", "208", "fiesta"):
            with self.subTest(word=lexical):
                self.assertNotIn(lexical, body.lower())


# ══ E — DEF-04, adversarially ══════════════════════════════════════════════

class E_Def04(unittest.TestCase):

    def compose(self, primary, slots=None, topics=("business_hours",)):
        e, ctx = producer_engine(topics=set(topics), slots=slots)
        return compose_with(e, ctx, primary), e

    def test_g36_23_false_hours_never_reach_the_customer(self):
        for wrong in ("Trabajamos sábados de 9 a 13 hs.",
                      "Trabajamos lunes de 9 a 18 hs.",
                      "Trabajamos jueves de 9 a 20 hs.",
                      "Los domingos también trabajamos."):
            out, _ = self.compose(wrong)
            with self.subTest(prose=wrong[:34]):
                self.assertFalse(ce._hours_truth_violations(ce._norm_lower(out)))
                self.assertIn(business_hours_summary_es(), out)

    def test_g36_24_the_conflicting_proposition_is_rejected_whole(self):
        out, e = self.compose(
            "Trabajamos sábados de 9 a 13 hs. Y te mandamos 250 puntos de informe.")
        primary = props(e)[PropositionId.PRIMARY_REPLY]
        self.assertFalse(primary.included)
        self.assertEqual(primary.suppression_reason,
                         SuppressionReason.CONFLICTS_WITH_CANONICAL)
        self.assertNotIn("250 puntos", out, "the prose was edited instead of rejected")

    def test_g36_25_no_substring_surgery_remains(self):
        producers = "\n".join(inspect.getsource(getattr(ConversationEngine, n)) for n in (
            "_register_response_propositions", "_register_faq_propositions",
            "_register_faq_topic", "_mark_primary_conflict",
            "_register_clarification_proposition",
            "_register_canonical_vehicle_proposition"))
        for surgery in ("re.sub(", ".replace(", "kept_units", "kept_lines"):
            with self.subTest(op=surgery):
                self.assertNotIn(surgery, producers)

    def test_g36_26_a_protected_slot_survives_a_rejected_reply(self):
        """§6 — the fallback preserves every AUTHORIZED proposition, and canonical state
        proves the appointment times even when the prose that named them is rejected."""
        out, e = self.compose(
            "Te ofrezco el sábado 13:00. Trabajamos sábados de 9 a 13 hs.",
            slots=["13:00"])
        self.assertIn("13:00", out)
        self.assertIn(PropositionId.SLOT_OFFER, props(e))
        self.assertFalse(ce._hours_truth_violations(ce._norm_lower(out)))

    def test_g36_27_a_slot_absent_from_state_is_not_rescued(self):
        out, e = self.compose(
            "Te ofrezco el sábado 13:00. Trabajamos sábados de 9 a 13 hs.", slots=[])
        self.assertNotIn(PropositionId.SLOT_OFFER, props(e))

    def test_g36_28_a_rejection_restores_facts_the_prose_had_made_redundant(self):
        """A claim marked redundant BECAUSE the prose stated it must come back when that
        prose is withheld — otherwise the rejection loses the fact entirely."""
        out, e = self.compose(
            "Te mandamos el informe. Trabajamos sábados de 9 a 13 hs.",
            topics=("business_hours", "report"))
        report = props(e)[PropositionId.faq("report")]
        self.assertTrue(report.included, "the report answer vanished with the rejection")
        self.assertIn("informe", out.lower())

    def test_g36_29_correct_prose_is_not_rejected(self):
        out, e = self.compose("Trabajamos sábados de 9 a 15 hs.")
        self.assertTrue(props(e)[PropositionId.PRIMARY_REPLY].included)
        self.assertIn("Trabajamos sábados de 9 a 15 hs.", out)

    def test_g36_30_prose_with_no_hours_claim_is_untouched(self):
        out, e = self.compose("Perfecto, seguimos entonces.")
        self.assertIn("Perfecto, seguimos entonces.", out)
        self.assertTrue(props(e)[PropositionId.PRIMARY_REPLY].included)

    def test_g36_31_the_canonical_answer_is_never_stated_twice(self):
        out, _ = self.compose("Trabajamos sábados de 9 a 13 hs.")
        self.assertEqual(out.count(business_hours_summary_es()), 1)


# ══ F — P1 and P6 through real production paths ════════════════════════════

class F_PoliciesLive(unittest.TestCase):

    P1_BURST = (("w0", f"{COMMERCIAL} ¿Qué horarios tienen? ¿Aceptan débito?", 0),)

    def test_g36_32_p1_hold_plus_safe_faq_plus_clarification_in_one_text(self):
        t = Turn(self.P1_BURST, evidence=None).run()
        try:
            registered = {p.proposition_id: p for p in t.engine._live_plan.propositions}
            self.assertEqual(getattr(t.decision, "authority", None), "HOLD",
                             "the principal action was not held")
            self.assertIn(PropositionId.faq("business_hours"), registered)
            self.assertIn(PropositionId.clarification("inspection_location"), registered)
            for p in registered.values():
                with self.subTest(item=p.proposition_id):
                    self.assertTrue(p.included)
            self.assertEqual(len(t.sent), 1, "P1 produced more than one message")
            self.assertIn(business_hours_summary_es(), t.sent[0])
            self.assertIn("¿En qué localidad", t.sent[0])
        finally:
            t.close()

    def test_g36_33_p1_does_not_authorize_the_held_action(self):
        t = Turn(self.P1_BURST, evidence=None).run()
        try:
            rows = t.db.execute(__import__("sqlalchemy").text(
                "select count(*) from thread_revisions")).scalar()
            self.assertEqual(rows, 0, "answering an FAQ created a booking")
            # The held action is the SCHEDULING one. A vehicle candidate is legitimate —
            # the customer named a vehicle — but answering an unrelated FAQ must not move
            # the conversation forward on the held axis.
            stage, needs_human = t.db.execute(__import__("sqlalchemy").text(
                "select last_stage, needs_human from whatsapp_thread_states")).first()
            self.assertNotIn(stage, ("SCHEDULING", "FLOW_SENT", "BOOKED"),
                             "the FAQ answer advanced the held scheduling action")
            self.assertFalse(needs_human, "answering an FAQ escalated the turn")
        finally:
            t.close()

    def test_g36_34_p1_items_carry_the_policy(self):
        t = Turn(self.P1_BURST, evidence=None).run()
        try:
            plan = t.engine._turn_response_plan
            faq = next(i for i in plan.items
                       if i.proposition_id == PropositionId.faq("business_hours"))
            self.assertEqual(faq.policy, ResponsePolicy.P1_HOLD_PLUS_SAFE_FAQ)
        finally:
            t.close()

    def test_g36_35_p6_canonical_state_silences_the_clarification(self):
        t = Turn(((("w0", f"{COMMERCIAL} ¿Mandan informe?", 0),)), evidence=None,
                 state_extra={"home_zone_group": "CABA",
                              "home_zone_detail": "Palermo"}).run()
        try:
            ids = [p.proposition_id for p in t.engine._live_plan.propositions]
            self.assertNotIn(PropositionId.clarification("inspection_location"), ids)
            for sent in t.sent:
                self.assertNotIn("¿En qué localidad", sent)
        finally:
            t.close()

    def test_g36_36_p6_a_suppressed_item_stays_visible_in_the_trace(self):
        e, ctx = producer_engine(topics=set())
        e._focus_candidate = lambda c: SimpleNamespace(id=1, tipo_vehiculo="AUTO")
        e._get_active_inspection_location = lambda c, s: (None, None)
        e._detect_explicit_inspection_request = lambda t: True
        compose_with(e, ctx, ce._CANONICAL_LOCATION_ASK.strip())
        plan = e._live_plan.to_plan(plan_id="p",
                                    renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                                    outcome="replied")
        suppressed = {i.proposition_id: i for i in plan.suppressed_items}
        self.assertIn(PropositionId.clarification("inspection_location"), suppressed)
        self.assertEqual(
            suppressed[PropositionId.clarification("inspection_location")].suppression_reason,
            SuppressionReason.ALREADY_STATED)
        self.assertIn("suppressed_count", plan.to_payload())

    def test_g36_37_p6_is_not_a_phrase_list(self):
        for name in ("_register_faq_topic", "_register_clarification_proposition"):
            body = inspect.getsource(getattr(ConversationEngine, name))
            with self.subTest(fn=name):
                self.assertNotIn("PHRASES", body)
                self.assertNotIn("in (\"", body)


# ══ G — the multi-proposition matrix (§12) ═════════════════════════════════

class G_MultiProposition(unittest.TestCase):
    """Nine required cases. Each reports registered, included, suppressed and the text."""

    def run_case(self, primary, *, topics, slots=None, candidates=(), zone=(None, None),
                 vehicle_known=True):
        e, ctx = producer_engine(topics=set(topics), slots=slots)
        ctx.candidates = list(candidates)
        e._turn_text = "burst"
        e._decision_log = lambda *a, **k: None
        e._focus_candidate = lambda c: (SimpleNamespace(id=1, tipo_vehiculo="AUTO")
                                        if vehicle_known else None)
        e._get_active_inspection_location = lambda c, s: zone
        e._detect_explicit_inspection_request = lambda t: True
        out = compose_with(e, ctx, primary)
        p = e._live_plan
        return SimpleNamespace(
            text=out, registered=[x.proposition_id for x in p.propositions],
            included=[x.proposition_id for x in p.propositions if x.included],
            suppressed=[(x.proposition_id, x.suppression_reason)
                        for x in p.propositions if not x.included],
            plan=p.to_plan(plan_id="p",
                           renderer_source=RendererSource.DETERMINISTIC_TEMPLATE,
                           outcome="replied"))

    def test_g36_38_case1_hours_plus_payment(self):
        r = self.run_case("Anotado.", topics=("business_hours", "payment"),
                          zone=("CABA", "Palermo"))
        self.assertIn(PropositionId.faq("business_hours"), r.included)
        self.assertIn(PropositionId.faq("payment"), r.included)
        self.assertEqual(r.text.count(business_hours_summary_es()), 1)

    def test_g36_39_case2_hold_plus_faq_plus_clarification(self):
        r = self.run_case("Anotado.", topics=("business_hours",))
        self.assertIn(PropositionId.clarification("inspection_location"), r.included)
        self.assertIn(PropositionId.faq("business_hours"), r.included)
        self.assertEqual(len(r.plan.to_payload()["item_kinds"]), 3)

    def test_g36_40_case3_known_vehicle_missing_locality(self):
        r = self.run_case("Perfecto.", topics=(),
                          candidates=[SimpleNamespace(id=1, tipo_vehiculo="AUTO")])
        self.assertIn(PropositionId.clarification("inspection_location"), r.included)
        self.assertNotIn(PropositionId.CANONICAL_VEHICLE, r.registered)

    def test_g36_41_case4_vehicle_correction_plus_safe_faq(self):
        r = self.run_case("Hacemos la revisión para un 2008 del 2014.",
                          topics=("report",), zone=("CABA", "Palermo"))
        self.assertIn(PropositionId.CANONICAL_VEHICLE, r.included)
        self.assertIn(PropositionId.faq("report"), r.included)
        self.assertIn("¿Es un Peugeot 2008?", r.text)
        self.assertIn("informe", r.text.lower())

    def test_g36_42_case5_wrong_hours_plus_a_legitimate_price(self):
        r = self.run_case("La revisión sale $85.000. Trabajamos sábados de 9 a 13 hs.",
                          topics=("business_hours",), zone=("CABA", "Palermo"))
        self.assertIn((PropositionId.PRIMARY_REPLY,
                       SuppressionReason.CONFLICTS_WITH_CANONICAL), r.suppressed)
        self.assertNotIn("85.000", r.text, "a rejected proposition was partly resurrected")
        self.assertIn(business_hours_summary_es(), r.text)

    def test_g36_43_case6_correct_prose_plus_canonical_faq(self):
        r = self.run_case("Con gusto te ayudo.", topics=("payment",),
                          zone=("CABA", "Palermo"))
        self.assertIn(PropositionId.PRIMARY_REPLY, r.included)
        self.assertIn("Con gusto te ayudo.", r.text)
        self.assertIn(PropositionId.faq("payment"), r.included)

    def test_g36_44_case7_two_producers_one_claim(self):
        e, ctx = producer_engine(topics={"business_hours"})
        e._register_faq_propositions(ctx, "Anotado.")
        e._turn_faq_burst = "again"
        e._register_faq_propositions(ctx, "Anotado.")
        ids = [p.proposition_id for p in e._live_plan.propositions]
        self.assertEqual(ids.count(PropositionId.faq("business_hours")), 1)

    def test_g36_45_case8_already_stated_plus_a_new_clarification(self):
        r = self.run_case(ce._CANONICAL_LOCATION_ASK.strip(), topics=("payment",))
        self.assertIn((PropositionId.clarification("inspection_location"),
                       SuppressionReason.ALREADY_STATED), r.suppressed)
        self.assertIn(PropositionId.faq("payment"), r.included)
        self.assertEqual(r.text.count(ce._CANONICAL_LOCATION_ASK.strip()), 1)

    def test_g36_46_case9_a_failing_renderer_costs_only_itself(self):
        e, ctx = producer_engine(topics={"business_hours"})
        e._register_faq_propositions(ctx, "Anotado.")
        e._live_plan.register(Proposition(
            proposition_id="faq.broken", kind=ResponseItemKind.FAQ_ANSWER,
            claim_family="faq", priority=55,
            render=lambda: (_ for _ in ()).throw(RuntimeError("renderer defect"))))
        out = e._live_plan.compose("Anotado.")
        self.assertIn("Anotado.", out)
        self.assertIn(business_hours_summary_es(), out)


# ══ H — plan count versus the final message ════════════════════════════════

class H_PlanCountIsTruthful(unittest.TestCase):

    CASES = (
        (("w0", "¿Cuál es el horario de atención?", 0),),
        (("w0", f"{COMMERCIAL} ¿Qué horarios tienen? ¿Aceptan débito?", 0),),
        (("w0", f"{COMMERCIAL} ¿Mandan informe?", 0),),
    )

    def test_g36_47_every_planned_text_item_is_in_the_message(self):
        for rows in self.CASES:
            t = Turn(rows, evidence=None).run()
            try:
                plan = t.engine._turn_response_plan
                if plan is None or not t.sent:
                    continue
                with self.subTest(burst=rows[0][1][:40]):
                    payload = plan.to_payload()
                    self.assertEqual(payload["planned_text_items"],
                                     len([p for p in t.engine._live_plan.propositions
                                          if p.included]))
                    self.assertEqual(len(t.sent), 1)
                    self.assertEqual(plan.transport_multiplicity,
                                     TransportMultiplicity.SINGLE_MESSAGE)
            finally:
                t.close()

    def test_g36_48_the_faq_answer_is_counted_where_it_used_to_be_invisible(self):
        """The G5F-A02 case: one message, two claims, and the plan now says two."""
        t = Turn(((("w0", "¿Cuál es el horario de atención?", 0),)), evidence=None).run()
        try:
            payload = t.engine._turn_response_plan.to_payload()
            self.assertEqual(payload["item_count"], 2)
            self.assertEqual(payload["item_kinds"],
                             [ResponseItemKind.TEXT, ResponseItemKind.FAQ_ANSWER])
            self.assertEqual(len(t.sent), 1)
            self.assertIn(business_hours_summary_es(), t.sent[0])
        finally:
            t.close()

    def test_g36_49_the_inspector_no_longer_warns_about_uncounted_content(self):
        self.assertNotIn("PARTIAL_PRODUCER_COVERAGE_NOTE", VIEW_SRC)
        self.assertNotIn("contenido heredado", VIEW_SRC)
        self.assertIn("Plan de respuesta (composición)", VIEW_SRC)
        self.assertNotIn("Plan de respuesta (autoridad)", VIEW_SRC)
        # The scope statement that replaces it still refuses the three authorities the
        # plan does NOT hold, so the card cannot drift into over-claiming.
        from app.ui.hybrid_trace_view import PLAN_SCOPE_NOTE
        for refused in ("acción principal", "mutaciones canónicas", "transporte",
                        "resultado transaccional"):
            with self.subTest(refused=refused):
                self.assertIn(refused, PLAN_SCOPE_NOTE)

    def test_g36_50_the_trace_contract_is_unchanged(self):
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.8")

    def test_g36_51_scheduling_meaning_is_preserved(self):
        """§11 — an availability enquiry is still not a business-hours FAQ."""
        for texts in ([HOURS_Q, "¿Hay algo mañana?"], [HOURS_Q, "¿Y el martes?"]):
            t = Turn(tuple((f"w{i}", x, i * 5) for i, x in enumerate(texts)),
                     evidence=None).run()
            try:
                with self.subTest(burst=texts[-1]):
                    self.assertEqual(t.decision.hours_disposition,
                                     "SUPPRESSED_BY_DETERMINISTIC_SCHEDULING")
                    self.assertFalse(t.names_the_weekday_table())
            finally:
                t.close()


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
