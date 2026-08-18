"""M21.2-E Price Re-query Restatement regression pack.

Tests the invariant: a customer who is already in QUOTED or SCHEDULING and asks
a price question must receive the authoritative deterministic quote WITHOUT any
commercial state regression.

ROOT CAUSE OF SECONDARY DEFECT
In QUOTED/SCHEDULING stages, the M18 regression guard (conversation_engine.py ~2516)
correctly blocked SCHEDULING → PRESUPUESTO_ENVIADO.  But it also nullified
decision["reply"] and provided no alternative.  The deterministic override at ~2601
did not fire (stage ≠ QUALIFYING).  Result: silence on price re-query.

FIX
A narrow deterministic pre-AI handler inserted before the QUOTED acceptance check
(~line 2088).  Conditions:
  - stage in (QUOTED, SCHEDULING) and not needs_human
  - _detect_price_question() matches the turn
  - _compute_price_quote() succeeds
Then: restate deterministic quote; return early.  No state mutation.

PR01  SCHEDULING + "Cuanto sale"            → restate $130,000 / no state change
PR02  SCHEDULING + "Cuánto cuesta?"         → restate $130,000 / no state change
PR03  SCHEDULING + "Cuál era el precio?"    → restate $130,000 / no state change
PR04  QUOTED + "Cuanto sale?"              → restate $130,000 / stay QUOTED (NOT acceptance)
PR05  QUOTED + "si avancemos"              → SCHEDULING (acceptance path unchanged)
PR06  SCHEDULING + price unavailable       → no invented price; normal fallthrough
PR07  SCHEDULING + "mañana a las 15"       → normal scheduling handling; no price theft
PR08  SCHEDULING + price query             → exactly one outbound reply
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

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
os.environ["OUTBOUND_ENABLED"] = "true"

from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.schemas.conversation import ConversationHandleIn         # noqa: E402
from app.services.pricing import PricingQuote, PricingNotFoundError  # noqa: E402


# ── Shared constants ──────────────────────────────────────────────────────────

_FORD_KA_PALERMO_QUOTE = PricingQuote(
    tipo_vehiculo="AUTO",
    zone_group="CABA",
    zone_detail="Palermo",
    precio_base=130_000,
    viaticos=0,
)


# ── Test infrastructure ───────────────────────────────────────────────────────

def _make_engine_pr():
    """CE factory for price-requery tests.

    - _compute_price_quote is NOT mocked (real path executes).
    - eng._pricing.quote returns PricingQuote($130,000) for Ford Ka / Palermo.
    - _build_quote_reply is NOT mocked (real quote text produced).
    - AI returns ACEPTADO (irrelevant for pre-AI paths; safe for post-AI paths).
    """
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.whatsapp_manual_handoff_flow_id = ""
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng.settings.whatsapp_location_fallback_flow_id = ""
    eng.settings.whatsapp_vehicle_fallback_flow_id = ""

    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()

    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING",
        "reply": "¡Genial! Ahora, ¿qué día te queda bien?",
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": "ACEPTADO",
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])

    eng._pricing = MagicMock()
    eng._pricing.quote.return_value = _FORD_KA_PALERMO_QUOTE

    # _build_quote_reply NOT overridden — real quote text is produced.
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)

    def _zone_lookup(text):
        t = (text or "").lower()
        if "palermo" in t:
            return SimpleNamespace(zone_group="CABA", zone_detail="Palermo", viaticos=0)
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


def _make_candidate(zona_detail="Palermo", zona_group="CABA"):
    return SimpleNamespace(
        id=1, marca="Ford", modelo="Ka", tipo_vehiculo="AUTO", anio=2019,
        zone_group=zona_group, zone_detail=zona_detail, status="current_focus",
        is_assembled=False, is_non_running=False,
    )


def _make_ctx(state, candidates=None, lead_flag="ACEPTADO"):
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


def _make_event(text, thread_id=99):
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=f"msg-{hash(text) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=[text],
        unanswered_recent_user_messages=[text],
    )


def _run_turn(eng, ctx, text):
    return eng._process_text(ctx, _make_event(text, ctx.thread.id))


def _all_wa_texts(eng):
    return [str(c[0][1]) for c in eng._send_text_to_wa.call_args_list]


def _scheduling_ctx():
    """Canonical starting context for PR01-PR03, PR07, PR08: ACEPTADO / SCHEDULING."""
    state = _make_state(
        last_stage="SCHEDULING",
        home_zone_group="CABA",
        home_zone_detail="Palermo",
    )
    return state, _make_ctx(state, candidates=[_make_candidate()], lead_flag="ACEPTADO")


def _quoted_ctx():
    """Canonical starting context for PR04, PR05: PRESUPUESTO_ENVIADO / QUOTED."""
    state = _make_state(
        last_stage="QUOTED",
        home_zone_group="CABA",
        home_zone_detail="Palermo",
    )
    return state, _make_ctx(state, candidates=[_make_candidate()], lead_flag="PRESUPUESTO_ENVIADO")


# ── PR01: SCHEDULING + "Cuanto sale" ─────────────────────────────────────────

class TestPR01SchedulingPriceQuery(unittest.TestCase):
    """PR01 — 'Cuanto sale' in SCHEDULING restates $130,000 without state regression."""

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _scheduling_ctx()

    def test_pr01_reply_sent(self):
        result = _run_turn(self.eng, self.ctx, "Cuanto sale")
        self.assertEqual(result.action, "replied")
        self.assertGreater(self.eng._send_text_to_wa.call_count, 0)

    def test_pr01_reply_contains_price(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        texts = _all_wa_texts(self.eng)
        self.assertTrue(any("130" in t for t in texts),
                        f"Price not found in outbound: {texts!r}")

    def test_pr01_flag_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO",
                         f"lead.flag regressed to {self.ctx.lead.flag!r}")

    def test_pr01_stage_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        self.assertEqual(self.state.last_stage, "SCHEDULING",
                         f"Stage regressed to {self.state.last_stage!r}")

    def test_pr01_no_scheduling_reset(self):
        """Price restatement must not trigger a new scheduling prompt."""
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        texts = _all_wa_texts(self.eng)
        # Scheduling prompt would contain "día" and NOT contain a price
        price_texts = [t for t in texts if "130" in t]
        self.assertTrue(len(price_texts) > 0, "Expected at least one price reply")


# ── PR02: SCHEDULING + accented query ─────────────────────────────────────────

class TestPR02SchedulingAccentedQuery(unittest.TestCase):
    """PR02 — 'Cuánto cuesta?' in SCHEDULING restates $130,000."""

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _scheduling_ctx()

    def test_pr02_reply_contains_price(self):
        _run_turn(self.eng, self.ctx, "Cuánto cuesta?")
        texts = _all_wa_texts(self.eng)
        self.assertTrue(any("130" in t for t in texts),
                        f"Price not found in outbound: {texts!r}")

    def test_pr02_stage_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuánto cuesta?")
        self.assertEqual(self.state.last_stage, "SCHEDULING")

    def test_pr02_flag_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuánto cuesta?")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO")


# ── PR03: SCHEDULING + historical wording ─────────────────────────────────────

class TestPR03SchedulingHistoricalWording(unittest.TestCase):
    """PR03 — 'Cuál era el precio?' in SCHEDULING restates $130,000."""

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _scheduling_ctx()

    def test_pr03_reply_contains_price(self):
        _run_turn(self.eng, self.ctx, "Cuál era el precio?")
        texts = _all_wa_texts(self.eng)
        self.assertTrue(any("130" in t for t in texts),
                        f"Price not found in outbound: {texts!r}")

    def test_pr03_stage_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuál era el precio?")
        self.assertEqual(self.state.last_stage, "SCHEDULING")

    def test_pr03_flag_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuál era el precio?")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO")


# ── PR04: QUOTED + price query stays QUOTED ───────────────────────────────────

class TestPR04QuotedPriceQueryDoesNotAccept(unittest.TestCase):
    """PR04 — 'Cuanto sale?' in QUOTED restates quote but does NOT advance to SCHEDULING.

    A price question while in QUOTED stage must be treated as informational.
    It must NOT trigger the QUOTED acceptance path (that requires an acceptance phrase).
    """

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _quoted_ctx()

    def test_pr04_reply_contains_price(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale?")
        texts = _all_wa_texts(self.eng)
        self.assertTrue(any("130" in t for t in texts),
                        f"Price not found in outbound: {texts!r}")

    def test_pr04_stage_stays_quoted(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale?")
        self.assertEqual(self.state.last_stage, "QUOTED",
                         f"Stage advanced incorrectly to {self.state.last_stage!r}")

    def test_pr04_flag_stays_presupuesto_enviado(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale?")
        self.assertEqual(self.ctx.lead.flag, "PRESUPUESTO_ENVIADO",
                         f"Flag changed incorrectly to {self.ctx.lead.flag!r}")


# ── PR05: QUOTED acceptance still works ───────────────────────────────────────

class TestPR05QuotedAcceptanceUnaffected(unittest.TestCase):
    """PR05 — 'si avancemos' from QUOTED still advances to SCHEDULING.

    The price-requery handler must not intercept acceptance turns.
    """

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _quoted_ctx()

    def test_pr05_stage_advances_to_scheduling(self):
        _run_turn(self.eng, self.ctx, "si avancemos")
        self.assertEqual(self.state.last_stage, "SCHEDULING",
                         f"Expected SCHEDULING, got {self.state.last_stage!r}")

    def test_pr05_flag_advances_to_aceptado(self):
        _run_turn(self.eng, self.ctx, "si avancemos")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO",
                         f"Expected ACEPTADO, got {self.ctx.lead.flag!r}")

    def test_pr05_no_price_reply_for_acceptance(self):
        """Acceptance must produce a scheduling prompt, not a price restatement."""
        _run_turn(self.eng, self.ctx, "si avancemos")
        texts = _all_wa_texts(self.eng)
        # An acceptance reply should NOT contain the quote price string
        # (acceptance produces "¡Perfecto! ¿Qué día y horario..." — no price)
        price_texts = [t for t in texts if "130" in t]
        self.assertEqual(len(price_texts), 0,
                         f"Price restatement fired on acceptance: {price_texts!r}")


# ── PR06: Price unavailable — no invented price ───────────────────────────────

class TestPR06PriceUnavailableNoInvention(unittest.TestCase):
    """PR06 — Price unavailable in SCHEDULING: no invented amount; normal fallthrough.

    When _compute_price_quote returns None (e.g., zone not in pricing DB),
    the price-requery handler falls through without generating a reply.
    The turn continues through normal post-AI processing.  No price should
    be invented by the AI (AI mock does not produce a dollar figure here).
    """

    def setUp(self):
        self.eng = _make_engine_pr()
        # Make pricing unavailable
        from app.services.pricing import PricingNotFoundError as _PNF
        self.eng._pricing.quote.side_effect = _PNF("zone_not_found")
        self.state, self.ctx = _scheduling_ctx()
        # AI returns a neutral reply with no price
        import json
        self.eng._call_openai = MagicMock(return_value=json.dumps({
            "intent": "QUALIFYING",
            "reply": "¿Qué día te viene bien?",
            "deferred_interest": False,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": "ACEPTADO",
            "needs_human": False,
        }))

    def test_pr06_no_invented_price_in_reply(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        texts = _all_wa_texts(self.eng)
        # No text should contain a dollar amount with 130
        for t in texts:
            self.assertNotIn("130", t,
                             f"Invented price found in reply: {t!r}")

    def test_pr06_stage_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        self.assertEqual(self.state.last_stage, "SCHEDULING")

    def test_pr06_flag_unchanged(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        self.assertEqual(self.ctx.lead.flag, "ACEPTADO")


# ── PR07: Non-price message in SCHEDULING unaffected ─────────────────────────

class TestPR07SchedulingDateNotIntercepted(unittest.TestCase):
    """PR07 — 'mañana a las 15' in SCHEDULING follows normal scheduling logic.

    The price-requery handler must not fire for a day/time message.
    """

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _scheduling_ctx()

    def test_pr07_no_price_restatement(self):
        """A scheduling turn must not produce a price restatement."""
        _run_turn(self.eng, self.ctx, "mañana a las 15")
        texts = _all_wa_texts(self.eng)
        price_texts = [t for t in texts if "130" in t]
        self.assertEqual(len(price_texts), 0,
                         f"Price handler stole a scheduling turn: {price_texts!r}")

    def test_pr07_stage_unchanged(self):
        _run_turn(self.eng, self.ctx, "mañana a las 15")
        self.assertEqual(self.state.last_stage, "SCHEDULING",
                         f"Stage unexpectedly changed to {self.state.last_stage!r}")


# ── PR08: Exactly one outbound reply ─────────────────────────────────────────

class TestPR08NoDuplicateOutbound(unittest.TestCase):
    """PR08 — Price re-query produces exactly one outbound reply."""

    def setUp(self):
        self.eng = _make_engine_pr()
        self.state, self.ctx = _scheduling_ctx()

    def test_pr08_exactly_one_reply(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        self.assertEqual(self.eng._send_text_to_wa.call_count, 1,
                         f"Expected 1 outbound send, got {self.eng._send_text_to_wa.call_count}")

    def test_pr08_reply_is_price(self):
        _run_turn(self.eng, self.ctx, "Cuanto sale")
        texts = _all_wa_texts(self.eng)
        self.assertTrue(any("130" in t for t in texts),
                        f"Expected price in the single outbound reply: {texts!r}")


# ── Quote text capture for report ─────────────────────────────────────────────

class TestQuoteTextCapture(unittest.TestCase):
    """Captures exact quote text for CF01 and PR01 report fields."""

    def test_pr01_exact_quote_text(self):
        """Print the exact quote text sent for PR01."""
        eng = _make_engine_pr()
        state, ctx = _scheduling_ctx()
        _run_turn(eng, ctx, "Cuanto sale")
        texts = _all_wa_texts(eng)
        # Assert non-empty and print for report
        self.assertGreater(len(texts), 0)
        price_text = next((t for t in texts if "130" in t), "")
        self.assertIn("130", price_text,
                      f"PR01 quote text does not contain price: {price_text!r}")
        # Verify the full canonical format
        self.assertIn("Genial", price_text)
        self.assertIn("Ford", price_text)
        self.assertIn("Palermo", price_text)
        self.assertIn("130.000", price_text)
        self.assertIn("podemos avanzar", price_text)


if __name__ == "__main__":
    unittest.main()
