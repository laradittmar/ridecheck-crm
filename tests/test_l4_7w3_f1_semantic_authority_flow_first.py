"""L4.7W3-F1 — the semantic layer finally reaches acceptance, and a day opens the Flow.

Wild W3 was correct in outcome and wrong in reasoning three times over:

  * "Bueno dale avancemos" arrived beside an FAQ, so `_is_acceptance` — which demands
    acceptance THROUGHOUT — returned False and C3B logged stance=None for a turn where the
    customer plainly said yes. The interpreter had ACCEPT all along.
  * "¿Qué tenés mañana?" in stage=SCHEDULING was labelled `business_hours`, and the whole
    weekday table went out again one turn after it had already been sent.
  * A day request answered with five times in prose — a slot picker rendered as a sentence.

SEM-AUTH-01..08  acceptance    FAQ-CTX-01..06  context      FLOWFIRST-01..09  scheduling
W3-REPRO-01..04  the session that produced all three
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import types
import unittest
from datetime import date, timedelta
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

from app.schemas.turn_evidence import (  # noqa: E402
    AcceptanceEvidence,
    AcceptanceSignal,
    EvidenceStatus,
    FaqIntentEvidence,
    TurnEvidence,
)
from app.services.acceptance_authorizer import (  # noqa: E402
    CommercialState,
    authorize_quote_acceptance,
)
from app.services.claim_projection import claims_from_turn_evidence  # noqa: E402
from app.services.conversation_engine import (  # noqa: E402
    _FAQ_TOPIC_ANSWERS,
    ConversationEngine,
    _is_acceptance,
)

CE_SOURCE = (ROOT / "backend" / "app" / "services"
             / "conversation_engine.py").read_text(encoding="utf-8-sig")

W3_ACCEPT = "Bueno dale avancemos"
W3_FAQ = "Que horarios tienen?"
W3_TOMORROW = "Nose que tenes mañana ?"


def quoted_state(**kw):
    base = dict(cycle_id="c1", revision_id=1, candidate_id=7, quote_total=240000,
                quote_tipo_vehiculo="SUV_4X4_DEPORTIVO", quote_zone_group="Sur",
                quote_zone_detail="Berazategui", quote_candidate_id=7, quote_cycle_id="c1",
                current_tipo_vehiculo="SUV_4X4_DEPORTIVO", current_zone_group="Sur",
                current_zone_detail="Berazategui", delivered_amounts=(240000,),
                quote_delivered=False, lead_flag="PRESUPUESTO_ENVIADO", stage="QUOTED")
    base.update(kw)
    return CommercialState(**base)


def evidence(signal=None, faq_topics=()):
    return TurnEvidence(
        acceptance=(AcceptanceEvidence(signal=signal, value=(signal is AcceptanceSignal.ACCEPT),
                                       status=EvidenceStatus.CONFIRMED)
                    if signal is not None else None),
        faq_intents=tuple(FaqIntentEvidence(topic=t, value=t,
                                            status=EvidenceStatus.CONFIRMED)
                          for t in faq_topics))


def engine(sem=None, stage="QUOTED", db_messages=(), scheduling=False):
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = SimpleNamespace(
        reconciler_acceptance_authority_enabled=True,
        reconciler_vehicle_authority_enabled=True,
        reconciler_location_authority_enabled=True,
        reconciler_scheduling_authority_enabled=True,
        semantic_same_turn_enabled=True, booking_flow_id="28104222025943520")
    eng._semantic_turn_evidence = lambda: sem
    eng._turn_scheduling_requested = scheduling
    ctx = SimpleNamespace(
        thread=SimpleNamespace(id=1), lead=SimpleNamespace(flag="PRESUPUESTO_ENVIADO"),
        db_messages=list(db_messages),
        state=SimpleNamespace(last_stage=stage, current_cycle_start_message_db_id="c1",
                              current_revision_id=None, current_cycle_started_at=None))
    eng._turn_ctx = ctx
    return eng, ctx


def out_msg(mid, text):
    return SimpleNamespace(id=mid, direction="out", text=text)


# ── acceptance ────────────────────────────────────────────────────────────────

class TestSemanticAcceptance(unittest.TestCase):

    def authorize(self, sem, texts, state=None):
        eng, ctx = engine(sem=sem)
        claims = eng._semantic_acceptance_claims(ctx.state, texts)
        if _is_acceptance(texts):
            from app.schemas.claims import (ClaimEvidence, ClaimType, EvidenceClass,
                                            Explicitness, Polarity)
            claims.append(ClaimEvidence(
                claim_type=ClaimType.QUOTE_ACCEPTED, value=True, polarity=Polarity.ASSERTED,
                evidence_class=EvidenceClass.DETERMINISTIC_EXTRACTED,
                producer="ce:_is_acceptance", explicitness=Explicitness.IMPLIED,
                cycle_id="c1").with_id())
        return authorize_quote_acceptance(claims, state or quoted_state())

    def test_sem_auth_01_mixed_accept_plus_faq(self):
        """SEM-AUTH-01 — the exact W3 burst. The deterministic predicate still says no."""
        texts = [W3_ACCEPT, W3_FAQ]
        self.assertFalse(_is_acceptance(texts), "the predicate that missed it in W3")
        decision = self.authorize(evidence(AcceptanceSignal.ACCEPT, ("business_hours",)), texts)
        self.assertEqual(decision.result, "ALLOW")
        self.assertEqual(decision.stance, "ACCEPT")
        self.assertIn("stance_is_accept", decision.satisfied)

    def test_sem_auth_02_mixed_accept_plus_scheduling(self):
        decision = self.authorize(evidence(AcceptanceSignal.ACCEPT),
                                  ["Bueno avancemos, ¿mañana puede ser?"])
        self.assertEqual(decision.result, "ALLOW")

    def test_sem_auth_03_accept_only_still_works(self):
        """SEM-AUTH-03 — the deterministic path is not weakened, only joined."""
        self.assertTrue(_is_acceptance([W3_ACCEPT]))
        self.assertEqual(self.authorize(None, [W3_ACCEPT]).result, "ALLOW")

    def test_sem_auth_04_05_06_non_acceptance_stances_never_accept(self):
        """SEM-AUTH-04/05/06 — hesitation, future intent and rejection authorise nothing."""
        for signal in (AcceptanceSignal.HESITATE, AcceptanceSignal.FUTURE_INTENT,
                       AcceptanceSignal.QUESTION_ONLY):
            decision = self.authorize(evidence(signal), ["lo pienso y te digo"])
            self.assertNotEqual(decision.result, "ALLOW", signal)
        for text in ("si me cierra te aviso", "capaz avancemos", "lo pienso y te digo",
                     "si puedo mañana te confirmo"):
            self.assertNotEqual(self.authorize(None, [text]).result, "ALLOW", text)

    def test_a_mixed_greeting_is_not_acceptance(self):
        """The C3B regression guarded since F2: "Bueno, quería revisar…" is not a yes."""
        self.assertFalse(_is_acceptance(["Bueno, quería revisar una 2008 del 2014"]))
        self.assertNotEqual(
            self.authorize(None, ["Bueno, quería revisar una 2008 del 2014"]).result, "ALLOW")

    def test_sem_auth_07_stale_quote_blocks_semantic_acceptance(self):
        """SEM-AUTH-07 — a semantic yes cannot accept a quote whose inputs moved."""
        stale = quoted_state(current_zone_detail="Quilmes")
        decision = self.authorize(evidence(AcceptanceSignal.ACCEPT), [W3_ACCEPT, W3_FAQ], stale)
        self.assertNotEqual(decision.result, "ALLOW")

    def test_sem_auth_08_undelivered_quote_blocks_semantic_acceptance(self):
        """SEM-AUTH-08 — computing a price is not delivering it."""
        undelivered = quoted_state(delivered_amounts=())
        decision = self.authorize(evidence(AcceptanceSignal.ACCEPT), [W3_ACCEPT], undelivered)
        self.assertNotEqual(decision.result, "ALLOW")
        self.assertIn("quote_delivered", decision.failed)

    def test_the_projection_is_reused_not_reimplemented(self):
        eng, ctx = engine(sem=evidence(AcceptanceSignal.ACCEPT))
        claims = eng._semantic_acceptance_claims(ctx.state, [W3_ACCEPT])
        self.assertTrue(claims)
        direct = claims_from_turn_evidence(evidence(AcceptanceSignal.ACCEPT), texts=[W3_ACCEPT])
        self.assertTrue(any(c.claim_type == claims[0].claim_type for c in direct))
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef) and n.name == "_semantic_acceptance_claims")
        self.assertIn("claims_from_turn_evidence", fn)
        self.assertNotIn("SemanticTurnInterpreter", fn)


# ── FAQ context ───────────────────────────────────────────────────────────────

class TestFaqContext(unittest.TestCase):

    def test_faq_ctx_01_availability_question_is_not_opening_hours(self):
        """FAQ-CTX-01 — the W3 defect, stated directly."""
        eng, ctx = engine(sem=evidence(faq_topics=("business_hours",)),
                          stage="SCHEDULING", scheduling=True)
        self.assertEqual(eng._faq_topics_for_burst(W3_TOMORROW, ctx=ctx), set())

    def test_faq_ctx_02_an_explicit_hours_question_is_still_answered(self):
        """FAQ-CTX-02 — literal asks are never suppressed, at any stage."""
        eng, ctx = engine(sem=evidence(faq_topics=("business_hours",)),
                          stage="SCHEDULING", scheduling=True)
        self.assertIn("business_hours",
                      eng._faq_topics_for_burst("¿Qué horarios tienen?", ctx=ctx))

    def test_faq_ctx_03_already_answered_topic_is_not_repeated(self):
        """FAQ-CTX-03 — the weekday table twice, one turn apart."""
        prior = [out_msg(5, _FAQ_TOPIC_ANSWERS["payment"]())]
        eng, ctx = engine(sem=evidence(faq_topics=("payment",)), db_messages=prior)
        self.assertEqual(eng._faq_topics_for_burst("dale", ctx=ctx), set())

    def test_faq_ctx_04_an_explicit_repeat_is_answered_again(self):
        """FAQ-CTX-04 — asking again is a legitimate question, not a duplicate."""
        prior = [out_msg(5, _FAQ_TOPIC_ANSWERS["payment"]())]
        eng, ctx = engine(sem=evidence(faq_topics=("payment",)), db_messages=prior)
        self.assertIn("payment",
                      eng._faq_topics_for_burst("¿aceptan debito?", ctx=ctx))

    def test_faq_ctx_05_semantic_overlap_dedup(self):
        prior = [out_msg(5, _FAQ_TOPIC_ANSWERS["service_scope"]())]
        eng, ctx = engine(sem=evidence(faq_topics=("service_scope",)), db_messages=prior)
        self.assertEqual(eng._faq_topics_for_burst("ok", ctx=ctx), set())

    def test_faq_ctx_06_history_is_bounded_to_the_current_cycle(self):
        """FAQ-CTX-06 — an answer from a finished cycle does not suppress this one."""
        prior = [out_msg(2, _FAQ_TOPIC_ANSWERS["payment"]())]   # id < cycle start
        eng, ctx = engine(sem=evidence(faq_topics=("payment",)), db_messages=prior)
        ctx.state.current_cycle_start_message_db_id = 9
        self.assertIn("payment", eng._faq_topics_for_burst("dale", ctx=ctx))

    def test_inbound_messages_never_count_as_answers(self):
        prior = [SimpleNamespace(id=5, direction="in", text=_FAQ_TOPIC_ANSWERS["payment"]())]
        eng, ctx = engine(sem=evidence(faq_topics=("payment",)), db_messages=prior)
        self.assertIn("payment", eng._faq_topics_for_burst("dale", ctx=ctx))


# ── flow-first ────────────────────────────────────────────────────────────────

class TestFlowFirst(unittest.TestCase):

    def dispatcher(self, slots, allows=True, flow_id="28104222025943520"):
        eng, ctx = engine(stage="SCHEDULING")
        eng.settings.booking_flow_id = flow_id
        eng._authorize_scheduling_progression = lambda c, s: SimpleNamespace(
            allows=allows, reason="test")
        sent = {}
        def _send(c, s, fid, body_prefix=""):
            sent["flow_id"] = fid
            sent["prefix"] = body_prefix
            return SimpleNamespace(action="flow_button_sent")
        eng._send_booking_flow = _send
        eng._decision_log = lambda *a, **k: None
        return eng, ctx, sent

    def test_flowfirst_01_02_a_day_with_availability_opens_the_flow(self):
        """FLOWFIRST-01/02 — tomorrow and Thursday both open the picker."""
        for day in ("2026-09-08", "2026-09-10"):
            eng, ctx, sent = self.dispatcher(["11:00", "11:30", "12:00"])
            out = eng._dispatch_booking_flow_for_day(
                ctx, ctx.state, day_iso=day, date_human="mañana",
                slots=["11:00", "11:30", "12:00"], period_label=None)
            self.assertIsNotNone(out, day)
            self.assertEqual(sent["flow_id"], "28104222025943520")
            self.assertEqual(json.loads(ctx.state.last_visible_slots),
                             ["11:00", "11:30", "12:00"])

    def test_flowfirst_03_a_period_narrows_what_the_flow_offers(self):
        eng, ctx, sent = self.dispatcher(["15:00", "16:00"])
        eng._dispatch_booking_flow_for_day(
            ctx, ctx.state, day_iso="2026-09-08", date_human="mañana",
            slots=["15:00", "16:00"], period_label="tarde")
        self.assertEqual(json.loads(ctx.state.last_visible_slots), ["15:00", "16:00"])
        self.assertIn("tarde", sent["prefix"])

    def test_flowfirst_05_no_slots_means_no_flow(self):
        """FLOWFIRST-05 — an empty picker is a dead end; fall back to conversation."""
        eng, ctx, sent = self.dispatcher([])
        self.assertIsNone(eng._dispatch_booking_flow_for_day(
            ctx, ctx.state, day_iso="2026-09-08", date_human="mañana",
            slots=[], period_label=None))
        self.assertEqual(sent, {})

    def test_flowfirst_06_opening_the_flow_is_not_booking(self):
        """FLOWFIRST-06 — and the one booking writer is unchanged."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_dispatch_booking_flow_for_day")
        for forbidden in ("ThreadRevision", "'booked'", '"booked"', "lead.flag ="):
            self.assertNotIn(forbidden, fn, forbidden)
        writers = {n.name for n in ast.walk(ast.parse(CE_SOURCE))
                   if isinstance(n, ast.FunctionDef)
                   and 'status="booked"' in ast.unparse(n).replace("'", '"')}
        self.assertTrue(writers <= {"_process_flow_response"}, writers)

    def test_flowfirst_08_a_withheld_progression_withholds_the_flow(self):
        """FLOWFIRST-08 — no delivered quote, no booking picker."""
        eng, ctx, sent = self.dispatcher(["11:00"], allows=False)
        self.assertIsNone(eng._dispatch_booking_flow_for_day(
            ctx, ctx.state, day_iso="2026-09-08", date_human="mañana",
            slots=["11:00"], period_label=None))
        self.assertEqual(sent, {})

    def test_flowfirst_09_schedule_service_remains_the_availability_authority(self):
        """FLOWFIRST-09 — the dispatcher receives slots, it never computes them."""
        fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_dispatch_booking_flow_for_day")
        for forbidden in ("list_slots", "ScheduleCheckIn", "self._schedule"):
            self.assertNotIn(forbidden, fn, forbidden)

    def test_an_unconfigured_flow_id_falls_back_to_text(self):
        eng, ctx, sent = self.dispatcher(["11:00"], flow_id="")
        self.assertIsNone(eng._dispatch_booking_flow_for_day(
            ctx, ctx.state, day_iso="2026-09-08", date_human="mañana",
            slots=["11:00"], period_label=None))


class TestW3Reproduction(unittest.TestCase):

    def test_w3_repro_01_acceptance_is_authorized_by_acceptance(self):
        """W3-REPRO-01 — ACEPTADO for the right reason, not incidental progression."""
        eng, ctx = engine(sem=evidence(AcceptanceSignal.ACCEPT, ("business_hours",)))
        claims = eng._semantic_acceptance_claims(ctx.state, [W3_ACCEPT, W3_FAQ])
        decision = authorize_quote_acceptance(claims, quoted_state())
        self.assertEqual(decision.result, "ALLOW")
        self.assertEqual(decision.rule_id, "authorize.quote_acceptance")

    def test_w3_repro_03_the_weekday_table_is_not_repeated(self):
        """W3-REPRO-03 — hours given, then "qué tenés mañana" — silence on hours."""
        prior = [out_msg(9, _FAQ_TOPIC_ANSWERS["business_hours"]())]
        eng, ctx = engine(sem=evidence(faq_topics=("business_hours",)),
                          stage="SCHEDULING", db_messages=prior, scheduling=True)
        self.assertEqual(eng._faq_topics_for_burst(W3_TOMORROW, ctx=ctx), set())

    def test_w3_repro_04_the_day_path_reaches_the_flow_dispatcher(self):
        """W3-REPRO-04 — wired into both the day and the period handler."""
        for name in ("_handle_day_only_request", "_handle_period_request"):
            fn = next(ast.unparse(n) for n in ast.walk(ast.parse(CE_SOURCE))
                      if isinstance(n, ast.FunctionDef) and n.name == name)
            self.assertIn("_dispatch_booking_flow_for_day", fn, name)

    def test_faq_regression_the_four_business_answers_are_unchanged(self):
        """W2/F4 live-proven answers stay deterministic."""
        self.assertEqual(set(_FAQ_TOPIC_ANSWERS),
                         {"business_hours", "report", "presence", "payment", "service_scope"})
        self.assertIn("débito", _FAQ_TOPIC_ANSWERS["payment"]().lower())
        self.assertIn("informe", _FAQ_TOPIC_ANSWERS["report"]().lower())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
