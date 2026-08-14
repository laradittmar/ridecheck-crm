"""M21.2-D Commercial-Flow Integrity regression pack.

Invariant enforced: QUALIFYING/None → ACEPTADO/SCHEDULING is NEVER a valid direct
transition, regardless of whether deterministic pricing is available.

Canonical sequence (CE file header):
    A. flag=PRESUPUESTANDO           (collecting data)
    B. flag=PRESUPUESTO_ENVIADO      (quote sent — committed AFTER WhatsApp send)
       stage=QUOTED
    C. flag=ACEPTADO, stage=SCHEDULING (customer explicitly accepts known quote)

PRIMARY DEFECT CLOSED
Prior BUG-3 guard (conversation_engine.py ~2533) had a third condition:
    `and real_price_quote is None`
That made the guard a no-op when pricing succeeded.  The AI could return
lead_flag="ACEPTADO" from QUALIFYING whenever vehicle+zone inputs were complete,
jumping directly to SCHEDULING without the customer ever seeing the price.

TEST GAP DOCUMENTED
All prior CT suites masked this via two mocks:
  1. _compute_price_quote = MagicMock(return_value=None)
       → BUG-3 third condition (is None) was always True in tests → guard always fired
  2. AI mock returned lead_flag=None
       → ACEPTADO was never proposed from QUALIFYING
Neither mock path exercised the real defect path (real pricing + AI returning ACEPTADO).
These CF tests use the real _compute_price_quote path and deliberately mock the AI to
return "ACEPTADO" to prove the guard works regardless of pricing availability.

CF01  LIVE03 exact: two-turn fuzzy → "sí" with AI returning ACEPTADO + real pricing
      Required: guard fires, quote sent ($130,000), flag=PRESUPUESTO_ENVIADO, stage=QUOTED
CF02  Next-turn acceptance from legitimate QUOTED state ("si avancemos")
      Required: flag=ACEPTADO, stage=SCHEDULING
CF03  Price available does not imply acceptance (direct candidate, single-turn)
      Required: quote sent, QUOTED — never direct SCHEDULING
CF04  Price unavailable: prior BUG-3 fallback behavior preserved
      Required: not SCHEDULING, fallback reply is sent
CF05  Guard does NOT block legitimate QUOTED → ACEPTADO transition
      Required: ACEPTADO/SCHEDULING only from QUOTED, never from QUALIFYING
CF06  Send failure: OutboundBlockedError propagates; PRESUPUESTO_ENVIADO not silently committed
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

for _mod_name in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

import os
os.environ["OUTBOUND_ENABLED"] = "true"   # force live code path; outbound is mocked

from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.schemas.conversation import ConversationHandleIn         # noqa: E402
from app.services.pricing import PricingQuote                    # noqa: E402
from app.services.outbound_guard import OutboundBlockedError     # noqa: E402


# ── Shared constants ──────────────────────────────────────────────────────────

_FORD_KA_PALERMO_QUOTE = PricingQuote(
    tipo_vehiculo="AUTO",
    zone_group="CABA",
    zone_detail="Palermo",
    precio_base=130_000,
    viaticos=0,
)


# ── Test infrastructure ───────────────────────────────────────────────────────

def _make_engine_cf(ai_lead_flag: str = "ACEPTADO"):
    """CE factory for commercial-flow integrity tests.

    Key differences from the CT cross-turn factory (_make_engine_real_routing):
    - _compute_price_quote is NOT mocked — real pricing path executes.
    - eng._pricing.quote returns a real PricingQuote ($130,000) for Ford Ka / Palermo.
    - _build_quote_reply is NOT mocked — real quote text is produced.
    - AI is mocked to return ai_lead_flag (default: "ACEPTADO") to exercise BUG-3.

    Everything else (vehicle catalog, candidate creation, routing gate, location
    triggers, outbound send) is identical to the CT factory.
    """
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng.settings.whatsapp_location_fallback_flow_id = ""
    eng.settings.whatsapp_vehicle_fallback_flow_id = ""

    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()

    # AI deliberately returns the dangerous flag to prove the guard intercepts it.
    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING",
        "reply": "¡Genial! Ahora, ¿qué día te queda bien?",
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": ai_lead_flag,
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])

    # _pricing.quote returns the real Ford Ka / Palermo quote — real _compute_price_quote runs.
    eng._pricing = MagicMock()
    eng._pricing.quote.return_value = _FORD_KA_PALERMO_QUOTE

    # _build_quote_reply is intentionally NOT overridden — real implementation produces quote text.
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)

    def _zone_lookup(text):
        t = (text or "").lower()
        if "palermo" in t:
            return SimpleNamespace(zone_group="CABA", zone_detail="Palermo", viaticos=0)
        if "tigre" in t:
            return SimpleNamespace(zone_group="Norte", zone_detail="Tigre", viaticos=40000)
        return None

    eng._extract_zone_from_text = MagicMock(side_effect=_zone_lookup)
    eng._normalize_zone_from_db = MagicMock()

    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._apply_narrative_interpretation = MagicMock()

    return eng


def _make_state(**kw):
    ns = SimpleNamespace(
        last_stage=None, needs_human=False, last_intent=None,
        home_zone_group=None, home_zone_detail=None, home_address=None,
        distance_km=None, current_focus_candidate_id=None, preferred_day=None,
        preferred_time=None, active_requested_date=None, last_requested_time=None,
        last_offered_slots=None, last_visible_slots=None, is_website_lead=False,
        flow_booking_token=None, current_revision_id=None, customer_name=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None,
        pending_turn_evidence_text=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state, candidates=None, lead_flag="PRESUPUESTANDO"):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=99)
    ctx.contact = SimpleNamespace(wa_id="5491199990000")
    ctx.lead = SimpleNamespace(
        id=1, flag=lead_flag, estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    ctx.state = state
    ctx.candidates = list(candidates or [])
    ctx.db_messages = []
    return ctx


def _make_candidate(marca="Ford", modelo="Ka", tipo_vehiculo="AUTO", anio=2019,
                    zone_group="CABA", zone_detail="Palermo", status="current_focus"):
    return SimpleNamespace(
        id=1, marca=marca, modelo=modelo, tipo_vehiculo=tipo_vehiculo, anio=anio,
        zone_group=zone_group, zone_detail=zone_detail, status=status,
        is_assembled=False, is_non_running=False,
    )


def _make_event(text, thread_id=99, recent=None, unanswered=None):
    msgs = unanswered or [text]
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=f"msg-{hash(text) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=recent or msgs,
        unanswered_recent_user_messages=msgs,
    )


def _run_turn(eng, ctx, text, recent=None, unanswered=None):
    event = _make_event(text, ctx.thread.id, recent=recent, unanswered=unanswered)
    return eng._process_text(ctx, event)


def _last_wa_text(eng):
    """Return the text of the most recent _send_text_to_wa call."""
    if not eng._send_text_to_wa.call_args_list:
        return ""
    return str(eng._send_text_to_wa.call_args_list[-1][0][1])


def _all_wa_texts(eng):
    """Return all texts passed to _send_text_to_wa across all calls."""
    return [str(c[0][1]) for c in eng._send_text_to_wa.call_args_list]


# ── CF01: LIVE03 exact failure reproduction ───────────────────────────────────

class TestCF01Live03ExactGuard(unittest.TestCase):
    """CF01 — 'ford ksl 2019 en Palermo' → 'sí' with AI returning ACEPTADO + real pricing.

    Prior to the fix, the BUG-3 guard was bypassed because real_price_quote was not None.
    Required: guard intercepts ACEPTADO from QUALIFYING, sends deterministic quote,
    commits PRESUPUESTO_ENVIADO/QUOTED — NEVER SCHEDULING.
    """

    def setUp(self):
        self.eng = _make_engine_cf(ai_lead_flag="ACEPTADO")
        self.state = _make_state()
        self.ctx = _make_ctx(self.state)
        # TURN 1: sets pending_fuzzy_catalog_key="Ford||Ka" + pending evidence
        _run_turn(self.eng, self.ctx, "ford ksl 2019 en Palermo")

    def test_cf01_guard_fires_price_is_not_none(self):
        """BUG-3 guard must fire even when real_price_quote is not None."""
        _run_turn(self.eng, self.ctx, "sí")
        # If guard had not fired, lead.flag would be "ACEPTADO"
        self.assertNotEqual(self.ctx.lead.flag, "ACEPTADO",
                            "Guard failed: lead.flag must not be ACEPTADO from QUALIFYING")

    def test_cf01_lead_flag_is_presupuesto_enviado(self):
        """After 'sí' in QUALIFYING with real pricing, flag must be PRESUPUESTO_ENVIADO."""
        _run_turn(self.eng, self.ctx, "sí")
        self.assertEqual(self.ctx.lead.flag, "PRESUPUESTO_ENVIADO",
                         f"Expected PRESUPUESTO_ENVIADO, got {self.ctx.lead.flag!r}")

    def test_cf01_stage_is_quoted_not_scheduling(self):
        """After 'sí' in QUALIFYING with real pricing, stage must be QUOTED, not SCHEDULING."""
        _run_turn(self.eng, self.ctx, "sí")
        self.assertEqual(self.state.last_stage, "QUOTED",
                         f"Expected QUOTED, got {self.state.last_stage!r}")

    def test_cf01_quote_text_contains_price(self):
        """The outbound message on TURN 2 must contain the deterministic price $130.000."""
        _run_turn(self.eng, self.ctx, "sí")
        texts = _all_wa_texts(self.eng)
        # TURN 1 sends confirmation question; TURN 2 should send quote
        self.assertGreaterEqual(len(texts), 2, "Expected at least 2 outbound messages")
        turn2_text = texts[1]
        self.assertIn("130", turn2_text,
                      f"Quote price not found in TURN 2 reply: {turn2_text!r}")

    def test_cf01_quote_text_contains_vehicle(self):
        """Quote reply must name the vehicle."""
        _run_turn(self.eng, self.ctx, "sí")
        texts = _all_wa_texts(self.eng)
        turn2_text = texts[1] if len(texts) >= 2 else ""
        self.assertTrue(
            "Ford" in turn2_text or "Ka" in turn2_text,
            f"Vehicle not found in TURN 2 reply: {turn2_text!r}",
        )

    def test_cf01_quote_text_contains_location(self):
        """Quote reply must name the zone."""
        _run_turn(self.eng, self.ctx, "sí")
        texts = _all_wa_texts(self.eng)
        turn2_text = texts[1] if len(texts) >= 2 else ""
        self.assertIn("Palermo", turn2_text,
                      f"Zone not found in TURN 2 reply: {turn2_text!r}")

    def test_cf01_no_scheduling_prompt_sent(self):
        """TURN 2 must not produce a scheduling prompt."""
        _run_turn(self.eng, self.ctx, "sí")
        texts = _all_wa_texts(self.eng)
        for t in texts[1:]:
            self.assertNotIn("día", t.lower(),
                             f"Scheduling prompt leaked into TURN 2 replies: {t!r}")


# ── CF02: Next-turn acceptance from legitimate QUOTED state ───────────────────

class TestCF02AcceptanceFromQuoted(unittest.TestCase):
    """CF02 — 'si avancemos' from legitimate QUOTED state advances to SCHEDULING.

    Verifies that the new guard does NOT interfere with the normal QUOTED → SCHEDULING
    transition.  State is set directly to QUOTED (bypassing the quote-send path).
    """

    def setUp(self):
        self.eng = _make_engine_cf(ai_lead_flag="ACEPTADO")
        # Start directly in QUOTED state (as if CF01 already completed)
        self.state = _make_state(last_stage="QUOTED")
        candidate = _make_candidate()
        self.ctx = _make_ctx(self.state, candidates=[candidate], lead_flag="PRESUPUESTO_ENVIADO")

    def test_cf02_lead_flag_advances_to_aceptado(self):
        """'si avancemos' from QUOTED must set lead.flag=ACEPTADO."""
        _run_turn(self.eng, self.ctx, "si avancemos")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO",
                         f"Expected ACEPTADO, got {self.ctx.lead.flag!r}")

    def test_cf02_stage_advances_to_scheduling(self):
        """'si avancemos' from QUOTED must advance stage to SCHEDULING."""
        _run_turn(self.eng, self.ctx, "si avancemos")
        self.assertEqual(self.state.last_stage, "SCHEDULING",
                         f"Expected SCHEDULING, got {self.state.last_stage!r}")

    def test_cf02_scheduling_prompt_sent(self):
        """A scheduling question must be sent after quote acceptance."""
        _run_turn(self.eng, self.ctx, "si avancemos")
        texts = _all_wa_texts(self.eng)
        self.assertGreater(len(texts), 0, "Expected at least one outbound message")
        combined = " ".join(texts).lower()
        self.assertTrue(
            "día" in combined or "horario" in combined or "fecha" in combined,
            f"No scheduling prompt found in outbound: {texts!r}",
        )


# ── CF03: Price available does NOT imply acceptance ───────────────────────────

class TestCF03PriceDoesNotImplyAcceptance(unittest.TestCase):
    """CF03 — When pricing succeeds in QUALIFYING, the quote must be sent, not SCHEDULING.

    Uses a pre-built candidate (no fuzzy-confirm flow) to isolate the state transition.
    AI returns ACEPTADO.  Required: deterministic override sends quote, QUOTED state.
    """

    def setUp(self):
        self.eng = _make_engine_cf(ai_lead_flag="ACEPTADO")
        self.state = _make_state(last_stage="QUALIFYING",
                                 home_zone_group="CABA", home_zone_detail="Palermo")
        candidate = _make_candidate()
        self.ctx = _make_ctx(self.state, candidates=[candidate])

    def test_cf03_stage_is_quoted_not_scheduling(self):
        """Complete inputs + QUALIFYING + AI saying ACEPTADO must produce QUOTED, not SCHEDULING."""
        _run_turn(self.eng, self.ctx, "quiero hacer la revisión")
        self.assertNotEqual(self.state.last_stage, "SCHEDULING",
                            "Premature SCHEDULING: price presence does not imply acceptance")
        self.assertEqual(self.state.last_stage, "QUOTED",
                         f"Expected QUOTED, got {self.state.last_stage!r}")

    def test_cf03_quote_is_sent(self):
        """Quote message must be sent when deterministic override fires."""
        _run_turn(self.eng, self.ctx, "quiero hacer la revisión")
        texts = _all_wa_texts(self.eng)
        self.assertGreater(len(texts), 0, "Expected quote to be sent")
        self.assertTrue(
            any("130" in t for t in texts),
            f"Quote price not found in outbound: {texts!r}",
        )

    def test_cf03_lead_flag_is_presupuesto_enviado(self):
        """Flag must be PRESUPUESTO_ENVIADO, not ACEPTADO."""
        _run_turn(self.eng, self.ctx, "quiero hacer la revisión")
        self.assertEqual(self.ctx.lead.flag, "PRESUPUESTO_ENVIADO",
                         f"Expected PRESUPUESTO_ENVIADO, got {self.ctx.lead.flag!r}")


# ── CF04: Price unavailable — prior fallback behavior preserved ───────────────

class TestCF04PriceUnavailableFallback(unittest.TestCase):
    """CF04 — No candidate (no pricing possible): BUG-3 fallback prompts for data.

    Verifies the guard's original behavior (real_price_quote is None branch)
    is not regressed by the fix.
    """

    def setUp(self):
        # Engine with no pricing available (no candidate → _compute_price_quote returns None)
        self.eng = _make_engine_cf(ai_lead_flag="ACEPTADO")
        # Override pricing to raise — no candidate means _compute_price_quote returns None anyway
        self.eng._pricing.quote.side_effect = Exception("no pricing")
        self.state = _make_state(last_stage=None)
        self.ctx = _make_ctx(self.state)  # no candidates

    def test_cf04_not_scheduling(self):
        """Without a price, ACEPTADO from QUALIFYING must not advance to SCHEDULING."""
        _run_turn(self.eng, self.ctx, "dale avancemos")
        self.assertNotEqual(self.state.last_stage, "SCHEDULING",
                            "Must not enter SCHEDULING without a deterministic price")

    def test_cf04_fallback_reply_sent(self):
        """A fallback prompt must be sent when pricing inputs are incomplete."""
        _run_turn(self.eng, self.ctx, "dale avancemos")
        texts = _all_wa_texts(self.eng)
        self.assertGreater(len(texts), 0,
                           "Expected a fallback reply asking for vehicle/zone info")

    def test_cf04_not_aceptado(self):
        """lead.flag must not be ACEPTADO when price is unavailable."""
        _run_turn(self.eng, self.ctx, "dale avancemos")
        self.assertNotEqual(self.ctx.lead.flag, "ACEPTADO",
                            "lead.flag must not be ACEPTADO when no price is available")


# ── CF05: Guard does not block legitimate QUOTED → ACEPTADO ───────────────────

class TestCF05LegitimateQuotedAcceptanceNotBlocked(unittest.TestCase):
    """CF05 — Guard must NOT block ACEPTADO when stage is already QUOTED.

    The guard condition checks `state.last_stage in (STAGE_QUALIFYING, None)`.
    From QUOTED, the AI returning ACEPTADO is a legitimate transition — the pre-AI
    path _handle_quoted_acceptance handles it deterministically.
    """

    def setUp(self):
        # Engine with AI returning ACEPTADO — from QUOTED this should be allowed
        self.eng = _make_engine_cf(ai_lead_flag="ACEPTADO")
        self.state = _make_state(last_stage="QUOTED")
        candidate = _make_candidate()
        self.ctx = _make_ctx(self.state, candidates=[candidate], lead_flag="PRESUPUESTO_ENVIADO")

    def test_cf05_acceptance_advances_to_scheduling(self):
        """From QUOTED state, 'sí' must advance to SCHEDULING."""
        _run_turn(self.eng, self.ctx, "sí")
        self.assertEqual(self.state.last_stage, "SCHEDULING",
                         f"Expected SCHEDULING from QUOTED, got {self.state.last_stage!r}")

    def test_cf05_flag_is_aceptado(self):
        """From QUOTED state, acceptance must set flag=ACEPTADO."""
        _run_turn(self.eng, self.ctx, "sí")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO",
                         f"Expected ACEPTADO from QUOTED, got {self.ctx.lead.flag!r}")


# ── CF06: Send failure does not silently commit PRESUPUESTO_ENVIADO ───────────

class TestCF06SendFailurePreservesSendBeforeCommit(unittest.TestCase):
    """CF06 — If quote outbound fails, the exception propagates (send-before-commit contract).

    CE sets lead.flag in memory before the send, but the DB session commit only happens
    inside _send_text_to_wa.  If the send raises, the commit never fires and the caller
    can roll back the session.  This class verifies the fix does not break that ordering
    by confirming the OutboundBlockedError propagates rather than being silently swallowed.
    """

    def setUp(self):
        self.eng = _make_engine_cf(ai_lead_flag="ACEPTADO")
        self.state = _make_state()
        self.ctx = _make_ctx(self.state)
        # TURN 1 must succeed so pending evidence is stored
        _run_turn(self.eng, self.ctx, "ford ksl 2019 en Palermo")
        # Replace the send mock so TURN 2 always raises — simulates blocked outbound
        self.eng._send_text_to_wa = MagicMock(
            side_effect=OutboundBlockedError(
                sender_path="test_cf06", kind="text",
                to_wa_id=self.ctx.contact.wa_id, thread_id=self.ctx.thread.id,
                text="quote text", gate_outcome="BLOCKED_KILL_SWITCH",
            )
        )

    def test_cf06_outbound_error_propagates(self):
        """OutboundBlockedError must propagate when quote send fails on TURN 2."""
        with self.assertRaises(OutboundBlockedError):
            _run_turn(self.eng, self.ctx, "sí")

    def test_cf06_not_silently_quoted(self):
        """When send fails, lead.flag must NOT be committed as PRESUPUESTO_ENVIADO.

        In memory lead.flag may be mutated before the send (that is the ordering
        contract), but the important guarantee is that the exception propagates so
        the caller's session can roll back. This test verifies the exception is not
        swallowed and replaced with a silent success result.
        """
        result = None
        try:
            result = _run_turn(self.eng, self.ctx, "sí")
        except OutboundBlockedError:
            pass  # correct path: exception propagates
        else:
            self.fail(
                f"Expected OutboundBlockedError to propagate, "
                f"but got result={result!r} (silent failure — commit may have occurred)"
            )


if __name__ == "__main__":
    unittest.main()
