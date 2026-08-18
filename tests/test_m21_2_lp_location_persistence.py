"""M21.2 LP — Location Persistence Contract Tests.

Tests the pre-candidate vehicle-location buffering introduced in M21.2 to fix
the LIVE04 failure: state.home_zone_* now holds vehicle-location evidence when
no candidate exists yet, and candidate creation inherits it.

LP01 — exact LIVE04 first turn (no candidate, Palermo buffered)
LP02 — LIVE04 continuity (candidate inherits Palermo, Ford Focus 2019)
LP03 — customer origin only — does NOT populate home_zone_*
LP04 — explicit vehicle location only (no candidate)
LP05 — candidate already exists, written directly
LP06 — candidate explicit correction wins over prior home_zone_*
LP07 — origin + vehicle location in reversed word order
LP08 — no contamination from vague origin statement
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Stub heavy deps before any app import ─────────────────────────────────────
for _mod in ["resend", "openai", "anthropic", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# ── Stub PostgreSQL JSONB (SQLite compat) ─────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg.JSONB = sqlalchemy.JSON          # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON     # type: ignore[attr-defined]

from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.schemas.conversation import ConversationHandleIn        # noqa: E402

# ── Zone helpers ───────────────────────────────────────────────────────────────

def _z(group: str, detail: str) -> SimpleNamespace:
    return SimpleNamespace(zone_group=group, zone_detail=detail, viaticos=0)

_Z_CABA_PALERMO = _z("CABA", "Palermo")
_Z_NORTE_TIGRE  = _z("Norte", "Tigre")
_Z_NORTE_OLIVOS = _z("Norte", "Olivos")

_DEFAULT_AI = json.dumps({
    "reply": "Respuesta de prueba.",
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})

# ── Fixture factories ──────────────────────────────────────────────────────────

def _state(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        last_stage="QUALIFYING", needs_human=False, last_intent=None,
        home_zone_group=None, home_zone_detail=None,
        home_address=None, distance_km=None,
        current_focus_candidate_id=None,
        preferred_day=None, preferred_time=None,
        active_requested_date=None, last_requested_time=None,
        last_offered_slots=None, last_visible_slots=None,
        is_website_lead=False, flow_booking_token=None,
        current_revision_id=None, customer_name=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        inspectability_clarification_sent=False,
        last_processed_inbound_wa_message_id=None,
        pending_fuzzy_catalog_key=None, pending_turn_evidence_text=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _lead(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        id=1, flag=None, estado="CONSULTA_NUEVA",
        nombre="Test", telefono=None, necesita_humano=False,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _cand(**kw) -> SimpleNamespace:
    ns = SimpleNamespace(
        id=10, thread_id=42, status="current_focus",
        tipo_vehiculo="Auto", marca="Toyota", modelo="Corolla",
        anio=2020, zone_group=None, zone_detail=None,
        direccion_texto=None, source_text=None,
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _ctx(st=None, lead=None, cands=None) -> SimpleNamespace:
    c = SimpleNamespace()
    c.thread = SimpleNamespace(id=42)
    c.contact = SimpleNamespace(wa_id="5491199999999")
    c.lead = lead or _lead()
    c.state = st or _state()
    c.candidates = cands or []
    return c


def _event(text: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=42,
        wa_message_id="lp-test-waid",
        wa_id="5491199999999",
        text=text,
        unanswered_recent_user_messages=[],
        recent_user_messages=[text],
    )


def _engine(zone_fn=None) -> ConversationEngine:
    """Minimal engine with all external calls mocked."""
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = MagicMock()
    eng.settings = MagicMock()
    eng.settings.openai_api_key = "sk-fake"
    eng.settings.openai_chat_model = "gpt-4o-mini"
    eng.settings.backend_url = "http://localhost:8000"
    eng._send_text_to_wa = MagicMock(return_value="mock-wa-id")
    eng._send_fallback_human_review_notification = MagicMock()
    eng._call_openai = MagicMock(return_value=_DEFAULT_AI)
    eng._build_ai_messages = MagicMock(return_value=[])
    eng._compute_price_quote = MagicMock(return_value=None)
    eng._extract_zone_from_text = MagicMock(side_effect=zone_fn or (lambda t: None))
    eng._normalize_zone_from_db = MagicMock()
    eng._routing_gate = MagicMock(return_value=(None, True))
    eng._check_fallback_flow_triggers = MagicMock(return_value=None)
    eng._apply_extracted = MagicMock()
    eng._apply_candidate = MagicMock()
    eng._enforce_catalog_vehicle = MagicMock()
    eng._create_candidate_from_catalog = MagicMock()
    eng._try_schedule_and_flow = MagicMock(return_value=None)
    eng._handle_day_only_request = MagicMock(return_value=None)
    eng._handle_period_request = MagicMock(return_value=None)
    eng._build_quote_reply = MagicMock(return_value="Cotización: $999.")
    eng._pricing = MagicMock()
    eng._scrub_invented_price = MagicMock(side_effect=lambda r, q: r)
    return eng


def _run(text: str, st=None, candidate=None, zone_fn=None):
    """Run _process_text. Returns (eng, result, state, candidate)."""
    eng = _engine(zone_fn=zone_fn)
    if st is None:
        st = _state()
    cands = [candidate] if candidate is not None else []
    eng._focus_candidate = MagicMock(return_value=candidate)
    c = _ctx(st=st, cands=cands)
    ev = _event(text)
    with patch("app.services.conversation_engine.lookup_vehicle", return_value=None):
        result = eng._process_text(c, ev)
    return eng, result, st, candidate


# ── LP01: exact LIVE04 first turn ─────────────────────────────────────────────

class TestLP01_LIVE04FirstTurn(unittest.TestCase):
    """No candidate; 'el auto está en Palermo' must buffer Palermo in home_zone_*."""

    TEXT = "Yo vivo en Tigre pero el auto está en Palermo."

    def _zone_fn(self, text: str):
        t = text.lower().strip()
        if "palermo" in t:
            return _Z_CABA_PALERMO
        if "tigre" in t:
            return _Z_NORTE_TIGRE
        return None

    def test_home_zone_detail_set_to_palermo(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertEqual(st.home_zone_detail, "Palermo")

    def test_home_zone_group_set_to_caba(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertEqual(st.home_zone_group, "CABA")

    def test_tigre_does_not_become_inspection_detail(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertNotEqual(st.home_zone_detail, "Tigre")

    def test_tigre_does_not_become_inspection_group(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertNotEqual(st.home_zone_group, "Norte")

    def test_no_candidate_created(self):
        st = _state()
        eng, _, _, _ = _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        eng._create_candidate_from_catalog.assert_not_called()

    def test_no_needs_human(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertFalse(st.needs_human)

    def test_no_crash(self):
        st = _state()
        _, result, _, _ = _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertIsNotNone(result)


# ── LP02: candidate inherits Palermo on creation ───────────────────────────────

class TestLP02_CandidateInheritsZone(unittest.TestCase):
    """After Palermo is buffered in home_zone_*, a new candidate must inherit it."""

    def test_create_from_catalog_inherits_home_zone(self):
        """_create_candidate_from_catalog passes home_zone_* to the candidate constructor."""
        eng = _engine()
        del eng._create_candidate_from_catalog  # use real implementation

        # Restore the real method on this instance
        from app.services.conversation_engine import ConversationEngine as _CE
        eng._create_candidate_from_catalog = _CE._create_candidate_from_catalog.__get__(eng)

        st = _state(home_zone_group="CABA", home_zone_detail="Palermo")
        c = _ctx(st=st)
        match = SimpleNamespace(marca="Ford", modelo="Focus", tipo_vehiculo="Auto")

        with patch("app.services.conversation_engine.WhatsAppThreadCandidate") as MockCand:
            mock_cand_inst = MagicMock()
            mock_cand_inst.id = 99
            MockCand.return_value = mock_cand_inst

            eng._create_candidate_from_catalog(c, st, match, source_text="Ford Focus 2019")

        kwargs = MockCand.call_args.kwargs if MockCand.call_args else MockCand.call_args[1]
        self.assertEqual(kwargs.get("zone_group"), "CABA")
        self.assertEqual(kwargs.get("zone_detail"), "Palermo")

    def test_create_from_catalog_no_zone_when_state_empty(self):
        """Candidate starts with None zones when home_zone_* is empty."""
        eng = _engine()
        del eng._create_candidate_from_catalog
        from app.services.conversation_engine import ConversationEngine as _CE
        eng._create_candidate_from_catalog = _CE._create_candidate_from_catalog.__get__(eng)

        st = _state()  # home_zone_* empty
        c = _ctx(st=st)
        match = SimpleNamespace(marca="Toyota", modelo="Corolla", tipo_vehiculo="Auto")

        with patch("app.services.conversation_engine.WhatsAppThreadCandidate") as MockCand:
            mock_cand_inst = MagicMock()
            mock_cand_inst.id = 88
            MockCand.return_value = mock_cand_inst
            eng._create_candidate_from_catalog(c, st, match, source_text="Toyota Corolla 2020")

        kwargs = MockCand.call_args.kwargs if MockCand.call_args else MockCand.call_args[1]
        self.assertIsNone(kwargs.get("zone_group"))
        self.assertIsNone(kwargs.get("zone_detail"))

    def test_apply_candidate_create_inherits_home_zone(self):
        """_apply_candidate action=create inherits home_zone_* when AI omits zone."""
        eng = _engine()
        del eng._apply_candidate  # use real implementation
        from app.services.conversation_engine import ConversationEngine as _CE
        eng._apply_candidate = _CE._apply_candidate.__get__(eng)

        st = _state(home_zone_group="CABA", home_zone_detail="Palermo")
        c = _ctx(st=st)

        with patch("app.services.conversation_engine.WhatsAppThreadCandidate") as MockCand:
            mock_cand_inst = MagicMock()
            mock_cand_inst.id = 77
            mock_cand_inst.zone_group = None   # AI didn't supply zone
            mock_cand_inst.zone_detail = None
            mock_cand_inst.status = "current_focus"
            MockCand.return_value = mock_cand_inst

            eng._apply_candidate(c, {
                "action": "create",
                "marca": "Ford", "modelo": "Focus",
                "tipo_vehiculo": "Auto", "anio": 2019,
                "status": "current_focus",
            })

        # Candidate should have received zone from state fallback
        self.assertEqual(mock_cand_inst.zone_group, "CABA")
        self.assertEqual(mock_cand_inst.zone_detail, "Palermo")

    def test_apply_candidate_create_does_not_overwrite_explicit_zone(self):
        """If AI supplies zone, home_zone_* must not overwrite it."""
        eng = _engine()
        del eng._apply_candidate
        from app.services.conversation_engine import ConversationEngine as _CE
        eng._apply_candidate = _CE._apply_candidate.__get__(eng)

        st = _state(home_zone_group="CABA", home_zone_detail="Palermo")
        c = _ctx(st=st)

        with patch("app.services.conversation_engine.WhatsAppThreadCandidate") as MockCand:
            mock_cand_inst = MagicMock()
            mock_cand_inst.id = 66
            mock_cand_inst.zone_group = "Norte"  # AI supplied a different zone
            mock_cand_inst.zone_detail = "Olivos"
            mock_cand_inst.status = "current_focus"
            MockCand.return_value = mock_cand_inst

            eng._apply_candidate(c, {
                "action": "create",
                "marca": "Honda", "modelo": "Civic",
                "tipo_vehiculo": "Auto", "anio": 2021,
                "zone_group": "Norte", "zone_detail": "Olivos",
                "status": "current_focus",
            })

        # Explicit AI zone must not be overwritten by home_zone_*
        self.assertEqual(mock_cand_inst.zone_group, "Norte")
        self.assertEqual(mock_cand_inst.zone_detail, "Olivos")


# ── LP03: customer origin only — does NOT populate home_zone_* ────────────────

class TestLP03_CustomerOriginOnly(unittest.TestCase):
    """'Yo vivo en Tigre.' — residential declaration must not set home_zone_*."""

    TEXT = "Yo vivo en Tigre."

    def _zone_fn(self, text: str):
        if "tigre" in text.lower():
            return _Z_NORTE_TIGRE
        return None

    def test_home_zone_group_remains_none(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertIsNone(st.home_zone_group)

    def test_home_zone_detail_remains_none(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertIsNone(st.home_zone_detail)

    def test_no_candidate_created(self):
        st = _state()
        eng, _, _, _ = _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        eng._create_candidate_from_catalog.assert_not_called()


# ── LP04: explicit vehicle location only, no candidate ────────────────────────

class TestLP04_VehicleLocationNoCandidateVariant(unittest.TestCase):
    """'El auto está en Palermo.' alone must buffer Palermo."""

    TEXT = "El auto está en Palermo."

    def _zone_fn(self, text: str):
        if "palermo" in text.lower().strip():
            return _Z_CABA_PALERMO
        return None

    def test_home_zone_group_caba(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertEqual(st.home_zone_group, "CABA")

    def test_home_zone_detail_palermo(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertEqual(st.home_zone_detail, "Palermo")

    def test_no_candidate_created(self):
        st = _state()
        eng, _, _, _ = _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        eng._create_candidate_from_catalog.assert_not_called()


# ── LP05: candidate already exists, written directly via LR-3 ────────────────

class TestLP05_CandidateExistsWrittenDirectly(unittest.TestCase):
    """With an existing candidate, LR-3 vehicle-location clause writes to candidate,
    not to home_zone_*. Uses 'el auto está en Palermo' which matches LR-3 patterns."""

    TEXT = "El auto está en Palermo."

    def _zone_fn(self, text: str):
        if "palermo" in text.lower().strip():
            return _Z_CABA_PALERMO
        return None

    def test_candidate_zone_updated(self):
        candidate = _cand(zone_group=None, zone_detail=None)
        _run(self.TEXT, candidate=candidate, zone_fn=self._zone_fn)
        self.assertEqual(candidate.zone_group, "CABA")
        self.assertEqual(candidate.zone_detail, "Palermo")

    def test_home_zone_not_set_when_candidate_exists_lr3(self):
        """LR-3 with existing candidate must write to candidate only, not home_zone_*."""
        candidate = _cand()
        st = _state()
        _run(self.TEXT, st=st, candidate=candidate, zone_fn=self._zone_fn)
        self.assertIsNone(st.home_zone_group)
        self.assertIsNone(st.home_zone_detail)


# ── LP06: candidate explicit correction wins ─────────────────────────────────

class TestLP06_ExplicitCorrectionWins(unittest.TestCase):
    """Candidate's explicit Olivos must not be overwritten by prior Palermo in home_zone_*.
    Uses 'el auto está en Olivos' (LR-3 pattern) to trigger the direct candidate write."""

    TEXT_TURN3 = "El auto está en Olivos."

    def _zone_fn(self, text: str):
        t = text.lower().strip()
        if "olivos" in t:
            return _Z_NORTE_OLIVOS
        if "palermo" in t:
            return _Z_CABA_PALERMO
        return None

    def test_candidate_updated_to_olivos(self):
        # Candidate already has Palermo from prior turn; now corrected to Olivos via LR-3
        candidate = _cand(zone_group="CABA", zone_detail="Palermo")
        _run(self.TEXT_TURN3, candidate=candidate, zone_fn=self._zone_fn)
        self.assertEqual(candidate.zone_group, "Norte")
        self.assertEqual(candidate.zone_detail, "Olivos")

    def test_stale_home_zone_does_not_overwrite_candidate(self):
        """After LR-3 writes Olivos to candidate, post-AI sync must not restore Palermo."""
        candidate = _cand(zone_group="CABA", zone_detail="Palermo")
        st = _state(home_zone_group="CABA", home_zone_detail="Palermo")
        _run(self.TEXT_TURN3, st=st, candidate=candidate, zone_fn=self._zone_fn)
        # _vehicle_location_written=True suppresses post-AI sync; Olivos survives
        self.assertEqual(candidate.zone_group, "Norte")
        self.assertEqual(candidate.zone_detail, "Olivos")


# ── LP07: origin + vehicle location reversed word order ──────────────────────

class TestLP07_ReversedWordOrder(unittest.TestCase):
    """'El auto está en Palermo, yo soy de Tigre.' — Palermo wins."""

    TEXT = "El auto está en Palermo, yo soy de Tigre."

    def _zone_fn(self, text: str):
        t = text.lower().strip()
        if "palermo" in t:
            return _Z_CABA_PALERMO
        if "tigre" in t:
            return _Z_NORTE_TIGRE
        return None

    def test_home_zone_detail_palermo(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertEqual(st.home_zone_detail, "Palermo")

    def test_home_zone_group_caba(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertEqual(st.home_zone_group, "CABA")

    def test_tigre_not_stored_as_inspection(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertNotEqual(st.home_zone_detail, "Tigre")


# ── LP08: vague origin statement — no contamination ──────────────────────────

class TestLP08_VagueOriginNoContamination(unittest.TestCase):
    """'Soy de Tigre pero todavía no sé dónde está el auto.' → home_zone_* empty."""

    TEXT = "Soy de Tigre pero todavía no sé dónde está el auto."

    def _zone_fn(self, text: str):
        if "tigre" in text.lower().strip():
            return _Z_NORTE_TIGRE
        return None

    def test_home_zone_group_remains_none(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertIsNone(st.home_zone_group)

    def test_home_zone_detail_remains_none(self):
        st = _state()
        _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        self.assertIsNone(st.home_zone_detail)

    def test_no_candidate_created(self):
        st = _state()
        eng, _, _, _ = _run(self.TEXT, st=st, candidate=None, zone_fn=self._zone_fn)
        eng._create_candidate_from_catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
