"""WILD-03-I — Intent qualification correction: bare-infinitive "Revisar un..." coverage.

Root cause (Category A): _INSPECTION_REQUEST_PATTERNS required a modal verb prefix
(quiero/queria/necesito/quisiera) before "revisar".  Argentine WhatsApp commonly starts
messages with the bare imperative/infinitive: "Revisar un auto en Palermo".

Fix: one new pattern anchored to sentence start (^ or \n) matching "Revisar + article + word".

Test classes
────────────
TestW03IPatternUnit     – new WILD-03-I pattern in isolation (positive + negative controls)
TestW03IDetectMethod    – _detect_explicit_inspection_request method (I03, I04 + regression)
TestW03ICEIntegration   – CE-level state.last_intent after full _process_text (I01, I02)
"""

from __future__ import annotations

import json
import unicodedata
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.conversation_engine import (
    ConversationEngine,
    _AWAITING_QUALIFICATION,
    _INTENT_PREPURCHASE,
    _INSPECTION_REQUEST_PATTERNS,
    _UNCERTAIN_SERVICE_REPLY,
)
from app.schemas.conversation import ConversationHandleIn


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _norm_text(text: str) -> str:
    """Mirror CE's _norm_text for unit-level tests."""
    n = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")


def _make_state(**kw):
    ns = SimpleNamespace(
        last_stage="QUALIFYING", needs_human=False, last_intent=_AWAITING_QUALIFICATION,
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
        unanswered_alert_sent_at=None,
        quote_followup_sent_at=None,
        buscando_followup_sent_at=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _make_ctx(state, candidates=None):
    ctx = SimpleNamespace()
    ctx.thread = SimpleNamespace(id=99)
    ctx.contact = SimpleNamespace(wa_id="5491199990000")
    ctx.lead = SimpleNamespace(
        id=1, flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    ctx.state = state
    ctx.candidates = list(candidates or [])
    ctx.db_messages = []
    return ctx


def _make_event(text):
    msgs = [text]
    return ConversationHandleIn(
        thread_id=99,
        wa_message_id=f"msg-{hash(text) % 100000}",
        wa_id="5491199990000",
        text=text,
        recent_user_messages=msgs,
        unanswered_recent_user_messages=msgs,
    )


def _make_engine():
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng.settings.whatsapp_location_fallback_flow_id = ""
    eng.settings.whatsapp_vehicle_fallback_flow_id = ""
    eng.settings.whatsapp_flow_id = ""
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=json.dumps({
        "intent": "QUALIFYING", "reply": "¿Qué vehículo querés revisar?",
        "deferred_interest": False,
        "candidate": {"action": "none"}, "extracted": {}, "lead_flag": None,
        "needs_human": False,
    }))
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    eng._extract_zone_from_text = MagicMock(return_value=None)
    eng._normalize_zone_from_db = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._apply_narrative_interpretation = MagicMock()
    return eng


def _make_full_engine():
    eng = _make_engine()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._focus_candidate = MagicMock(return_value=None)
    eng._enforce_catalog_vehicle = MagicMock()
    eng._handle_period_request = MagicMock(return_value=None)
    return eng


def _sent_texts(eng):
    return [c[0][1] for c in eng._send_text_to_wa.call_args_list]


# ─────────────────────────────────────────────────────────────────────────────
# TestW03IPatternUnit — new bare-infinitive pattern in isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestW03IPatternUnit(unittest.TestCase):
    """Tests the WILD-03-I pattern (_INSPECTION_REQUEST_PATTERNS[-1]) only.

    Positive cases prove it adds coverage. Negative cases prove the new pattern
    does NOT introduce new false positives. Pre-existing pattern behavior is
    tested separately in TestW03IDetectMethod.
    """

    def setUp(self):
        # The WILD-03-I pattern is always the last one appended.
        self._pat = _INSPECTION_REQUEST_PATTERNS[-1]

    def _match(self, text: str) -> bool:
        return bool(self._pat.search(_norm_text(text)))

    # ── Positive cases (must match) ──────────────────────────────────────────

    def test_i01_exact_wild03_input_matches(self):
        """W03-I01: exact live WILD-03 input triggers new bare-infinitive pattern."""
        self.assertTrue(
            self._match("Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?"),
            "Bare 'Revisar una ...' must match new WILD-03-I pattern",
        )

    def test_i01_revisar_una_camioneta_matches(self):
        """'Revisar una camioneta' — bare infinitive with article → match."""
        self.assertTrue(self._match("Revisar una camioneta"))

    def test_i02_revisar_un_auto_balvanera_matches(self):
        """W03-I02: 'Revisar un auto en Balvanera' → match."""
        self.assertTrue(self._match("Revisar un auto en Balvanera"))

    def test_i02_revisar_un_auto_no_location_matches(self):
        """'Revisar un auto' without location → match (minimal form)."""
        self.assertTrue(self._match("Revisar un auto"))

    def test_revisar_el_precio_not_matched(self):
        """'Revisar el precio' — 'el' excluded from article set (prevent false positives) → no match."""
        self.assertFalse(self._match("Revisar el precio"))

    def test_revisar_la_camioneta_not_matched_by_new_pattern(self):
        """'Revisar la camioneta' — definite 'la' not in indefinite-only set → new pattern False.
        Customer would say 'Quiero revisar la camioneta' → existing pattern[1] handles that form."""
        self.assertFalse(self._match("Revisar la camioneta"))

    def test_revisar_lowercase_matches(self):
        """All-lowercase 'revisar un auto' → match (pattern is case-insensitive)."""
        self.assertTrue(self._match("revisar un auto"))

    # ── Negative controls (must NOT match the new pattern) ───────────────────

    def test_i05_que_revisan_not_matched(self):
        """W03-I05: '¿Qué revisan?' — verb form 'revisan', not 'revisar' → no match."""
        self.assertFalse(
            self._match("¿Qué revisan?"),
            "New pattern must not match '¿Qué revisan?' (different verb form)",
        )

    def test_i06_quiero_revisar_no_new_match(self):
        """W03-I06: 'Quiero revisar el precio' — 'revisar' not at sentence start → new pattern False.

        Note: existing pattern[1] already matches this phrase; that is a pre-existing
        behavior out of scope for this fix. This test verifies the NEW pattern adds
        no further coverage here.
        """
        self.assertFalse(
            self._match("Quiero revisar el precio"),
            "New bare-infinitive pattern must not add coverage for 'Quiero revisar...'",
        )

    def test_i07_necesito_revisar_mis_datos_not_matched(self):
        """W03-I07: 'Necesito revisar mis datos' — not at sentence start, 'mis' not in article set → False."""
        self.assertFalse(self._match("Necesito revisar mis datos"))

    def test_i07_revisar_mis_datos_at_start_not_matched(self):
        """'revisar mis datos' at sentence start — 'mis' not in allowed article set → False."""
        self.assertFalse(self._match("revisar mis datos"))

    def test_i08_quiero_revisar_informe_no_new_match(self):
        """W03-I08: 'Quiero revisar el informe' — not at sentence start → new pattern False.

        Note: existing pattern[1] already matches this; pre-existing, out of scope.
        """
        self.assertFalse(self._match("Quiero revisar el informe"))

    def test_puedo_revisar_el_informe_not_matched(self):
        """'¿Puedo revisar el informe?' — starts with '¿Puedo', not 'revisar' → False."""
        self.assertFalse(self._match("¿Puedo revisar el informe?"))

    def test_hola_revisar_not_matched(self):
        """'Buenas, revisar un auto' — 'revisar' not at ^ or after \\n → False (safely anchored)."""
        self.assertFalse(self._match("Buenas, revisar un auto en Palermo"))


# ─────────────────────────────────────────────────────────────────────────────
# TestW03IDetectMethod — _detect_explicit_inspection_request full method
# ─────────────────────────────────────────────────────────────────────────────

class TestW03IDetectMethod(unittest.TestCase):
    """Confirm full _detect_explicit_inspection_request behavior:
    - I03, I04: existing patterns still fire (regression guard)
    - I01, I02: new pattern fires after fix
    - I05, I07: genuine negative controls remain False
    """

    def setUp(self):
        self.eng = ConversationEngine.__new__(ConversationEngine)
        self.eng.db = MagicMock()

    def _detect(self, text: str) -> bool:
        return self.eng._detect_explicit_inspection_request(_norm_text(text))

    # ── I01/I02: new pattern coverage ────────────────────────────────────────

    def test_i01_bare_revisar_una_now_true(self):
        """W03-I01 detect-level: 'Revisar una 2008 o 2014…' → True after WILD-03-I fix."""
        self.assertTrue(
            self._detect("Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?"),
            "After fix, bare 'Revisar una...' must return True",
        )

    def test_i02_bare_revisar_un_auto_now_true(self):
        """W03-I02 detect-level: 'Revisar un auto en Balvanera' → True after fix."""
        self.assertTrue(self._detect("Revisar un auto en Balvanera"))

    def test_i02_revisar_un_auto_minimal_now_true(self):
        """'Revisar un auto' (minimal) → True."""
        self.assertTrue(self._detect("Revisar un auto"))

    # ── I03/I04: existing patterns preserved (regression) ────────────────────

    def test_i03_quiero_revisar_un_auto(self):
        """W03-I03: 'Quiero revisar un auto en Palermo' → True (existing pattern[1])."""
        self.assertTrue(self._detect("Quiero revisar un auto en Palermo"))

    def test_i03_necesito_revisar_un_auto(self):
        """'Necesito revisar un auto' → True (existing pattern[1])."""
        self.assertTrue(self._detect("Necesito revisar un auto"))

    def test_i04_quisiera_revisar_un_focus(self):
        """W03-I04: 'Quisiera revisar un Focus 2019' → True (existing pattern[1])."""
        self.assertTrue(self._detect("Quisiera revisar un Focus 2019"))

    def test_i04_queria_revisar_un_auto(self):
        """'Quería revisar un auto' → True (existing pattern[1])."""
        self.assertTrue(self._detect("Quería revisar un auto"))

    def test_coordinar_revision(self):
        """'coordinar una revisión' → True (existing pattern[4])."""
        self.assertTrue(self._detect("Quiero coordinar una revisión del auto"))

    def test_quiero_cotizar(self):
        """'quiero cotizar' → True (existing pattern M21.1.1-R2)."""
        self.assertTrue(self._detect("quiero cotizar"))

    # ── I05/I07: genuine False controls ──────────────────────────────────────

    def test_i05_que_revisan_false(self):
        """W03-I05: '¿Qué revisan?' → False (different verb form 'revisan')."""
        self.assertFalse(self._detect("¿Qué revisan?"))

    def test_i07_necesito_revisar_mis_datos_false(self):
        """W03-I07: 'Necesito revisar mis datos' → False ('mis' not in any pattern's article set)."""
        self.assertFalse(self._detect("Necesito revisar mis datos"))


# ─────────────────────────────────────────────────────────────────────────────
# TestW03ICEIntegration — CE-level state after _process_text
# ─────────────────────────────────────────────────────────────────────────────

class TestW03ICEIntegration(unittest.TestCase):
    """W03-I01, W03-I02: after fix, bare 'Revisar un/una…' → PREPURCHASE intent,
    no UNCERTAIN reply sent, no spurious fuzzy catalog key committed.
    """

    def setUp(self):
        self.eng = _make_full_engine()

    def _run(self, text):
        state = _make_state(last_intent=_AWAITING_QUALIFICATION)
        ctx = _make_ctx(state)
        event = _make_event(text)
        self.eng._process_text(ctx, event)
        return state

    # ── W03-I01: exact WILD-03 live input ────────────────────────────────────

    def test_i01_sets_prepurchase_intent(self):
        """W03-I01: 'Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?' → PREPURCHASE."""
        state = self._run("Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?")
        self.assertEqual(
            state.last_intent, _INTENT_PREPURCHASE,
            "Layer F Step 1 must set PREPURCHASE for bare 'Revisar una...' input",
        )

    def test_i01_no_uncertain_reply(self):
        """W03-I01: UNCERTAIN service-boundary reply must NOT be sent."""
        self._run("Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?")
        self.assertNotIn(
            _UNCERTAIN_SERVICE_REPLY,
            _sent_texts(self.eng),
            "Step 1 must prevent UNCERTAIN reply for bare 'Revisar una...'",
        )

    def test_i01_no_pending_fuzzy_key(self):
        """W03-I01: WILD-02-B must not have committed a fuzzy Peugeot 2008 key."""
        state = self._run("Revisar una 2008 o 2014, en Balvanera. ¿Cuánto me sale?")
        self.assertIsNone(state.pending_fuzzy_catalog_key)

    # ── W03-I02: simpler bare-infinitive input ────────────────────────────────

    def test_i02_sets_prepurchase_intent(self):
        """W03-I02: 'Revisar un auto en Balvanera' → PREPURCHASE."""
        state = self._run("Revisar un auto en Balvanera")
        self.assertEqual(state.last_intent, _INTENT_PREPURCHASE)

    def test_i02_no_uncertain_reply(self):
        """W03-I02: no UNCERTAIN reply sent."""
        self._run("Revisar un auto en Balvanera")
        self.assertNotIn(_UNCERTAIN_SERVICE_REPLY, _sent_texts(self.eng))

    def test_i02_no_pending_fuzzy_key(self):
        """W03-I02: no fuzzy catalog key committed."""
        state = self._run("Revisar un auto en Balvanera")
        self.assertIsNone(state.pending_fuzzy_catalog_key)


if __name__ == "__main__":
    unittest.main()
