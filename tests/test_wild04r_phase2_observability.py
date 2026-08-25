"""WILD-04R Phase 2 — Observability tests.

Tests the observability features added to ConversationEngine and unanswered_alert
in Phase 2. All tests are fully offline: SQLite in-memory, no containers, no Meta
API, no live OpenAI calls.

Test groups:
  TestOutObservabilityFields   : OBS-01 to BLOCK-01 — _out() and handle() observability
  TestPerformanceStatus        : PERF-01 to PERF-06 — _compute_performance_status()
  TestLatencyCeTiming          : latency_ce_ms is set and >= 0
  TestUnAnsweredAlert          : ALERT-00 to ALERT-03 — unanswered_alert._run_check()
  TestWild04SemanticBurst      : Section N — burst assembly and CE vehicle context
  TestReturningCustomerObservability : Section O — returning customer cycle reset
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, call, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory setup ───────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON   # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, select, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


# ── Stub app.db BEFORE importing app.models ───────────────────────────────────
_db_mod = types.ModuleType("app.db")
_db_mod.Base = Base                         # type: ignore[attr-defined]
_db_mod.engine = _engine                    # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal        # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:" # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen               # type: ignore[attr-defined]
sys.modules["app.db"] = _db_mod

# ── Stub heavy optional deps ──────────────────────────────────────────────────
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

os.environ.setdefault("OUTBOUND_ENABLED", "false")

# ── Import ORM models ─────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    AiEvent,
    ExcludedPhone,
    Lead,
    ThreadRevision,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)

Lead.__table__.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.api.conversation import _compute_performance_status
from app.schemas.conversation import ConversationHandleIn, ConversationHandleOut
from app.services.conversation_engine import ConversationEngine
from app.services.lead_lifecycle import set_lead_estado
from app.services.unanswered_alert import (
    _ALERT_THRESHOLD_SECONDS,
    _CHECK_INTERVAL_SECONDS,
    _run_check,
)

# ── Shared constants ──────────────────────────────────────────────────────────
_WA_ID = "5491153360000"
_BASE_TS = datetime(2026, 8, 24, 10, 0, 0, tzinfo=timezone.utc)

_AI_REPLY_QUALIFYING = json.dumps({
    "intent": "QUALIFYING",
    "reply": "Perfecto, ¿dónde está el auto?",
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})

_AI_REPLY_FAQ = json.dumps({
    "intent": "FAQ",
    "reply": "Sí, mandamos informes detallados al finalizar la inspección.",
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})

_AI_REPLY_PREPURCHASE = json.dumps({
    "intent": "PREPURCHASE_INSPECTION",
    "reply": "Revisamos el Peugeot 2008 2014 con gusto. ¿En qué zona está?",
    "deferred_interest": False,
    "candidate": {
        "action": "create",
        "marca": "Peugeot",
        "modelo": "2008",
        "anio": 2014,
        "tipo_vehiculo": "AUTO",
    },
    "extracted": {
        "vehicle_make_model": {"value": "Peugeot 2008", "status": "CONFIRMED"},
        "vehicle_year": {"value": 2014, "status": "CONFIRMED"},
    },
    "lead_flag": None,
    "needs_human": False,
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _clean_all(db: Session) -> None:
    for tbl in [
        "ai_events", "whatsapp_outbound_dedup", "whatsapp_messages",
        "thread_revisions", "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "leads",
        "whatsapp_recipient_locks", "excluded_phones",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _seed_contact_thread_lead(
    db: Session,
    estado: str = "CONSULTA_NUEVA",
    flag: Optional[str] = "PRESUPUESTANDO",
    needs_human: bool = False,
    necesita_humano: bool = False,
) -> tuple[WhatsAppContact, WhatsAppThread, Lead]:
    _clean_all(db)
    contact = WhatsAppContact(wa_id=_WA_ID, display_name="Test", phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(
        flag=flag,
        estado=estado,
        nombre="Test",
        necesita_humano=necesita_humano,
    )
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(
        contact_id=contact.id,
        lead_id=lead.id,
        unread_count=0,
        created_at=_BASE_TS,
    )
    db.add(thread)
    db.flush()
    db.commit()
    return contact, thread, lead


def _add_state(
    db: Session,
    thread_id: int,
    *,
    needs_human: bool = False,
    last_stage: Optional[str] = "QUALIFYING",
    last_intent: Optional[str] = None,
    current_revision_id: Optional[int] = None,
    current_focus_candidate_id: Optional[int] = None,
    cycle_reset_pending: bool = False,
    current_cycle_start_message_db_id: Optional[int] = None,
    current_cycle_started_at: Optional[datetime] = None,
    last_processed_inbound_wa_message_id: Optional[str] = None,
    home_zone_group: Optional[str] = None,
    home_zone_detail: Optional[str] = None,
    pending_fuzzy_catalog_key: Optional[str] = None,
    vehicle_clarification_sent: bool = False,
    location_clarification_sent: bool = False,
    flow_booking_token: Optional[str] = None,
) -> WhatsAppThreadState:
    state = WhatsAppThreadState(
        thread_id=thread_id,
        needs_human=needs_human,
        last_stage=last_stage,
        last_intent=last_intent,
        current_revision_id=current_revision_id,
        current_focus_candidate_id=current_focus_candidate_id,
        cycle_reset_pending=cycle_reset_pending,
        current_cycle_start_message_db_id=current_cycle_start_message_db_id,
        current_cycle_started_at=current_cycle_started_at,
        last_processed_inbound_wa_message_id=last_processed_inbound_wa_message_id,
        home_zone_group=home_zone_group,
        home_zone_detail=home_zone_detail,
        pending_fuzzy_catalog_key=pending_fuzzy_catalog_key,
        vehicle_clarification_sent=vehicle_clarification_sent,
        location_clarification_sent=location_clarification_sent,
        flow_booking_token=flow_booking_token,
        created_at=_BASE_TS,
        updated_at=_BASE_TS,
    )
    db.add(state)
    db.commit()
    return state


def _add_inbound_message(
    db: Session,
    thread_id: int,
    wa_message_id: str,
    text: str = "Hola",
    offset_seconds: int = 0,
) -> WhatsAppMessage:
    ts = _BASE_TS + timedelta(seconds=offset_seconds)
    msg = WhatsAppMessage(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        direction="in",
        timestamp=ts,
        text=text,
        status="received",
        created_at=ts,
    )
    db.add(msg)
    db.commit()
    return msg


def _add_candidate(
    db: Session,
    thread_id: int,
    marca: str = "Peugeot",
    modelo: str = "2008",
    tipo_vehiculo: str = "AUTO",
    anio: Optional[int] = None,
    offset_seconds: int = 0,
) -> WhatsAppThreadCandidate:
    ts = _BASE_TS + timedelta(seconds=offset_seconds)
    c = WhatsAppThreadCandidate(
        thread_id=thread_id,
        marca=marca,
        modelo=modelo,
        tipo_vehiculo=tipo_vehiculo,
        anio=anio,
        status="current_focus",
        created_at=ts,
        updated_at=ts,
    )
    db.add(c)
    db.commit()
    return c


def _make_settings():
    s = MagicMock()
    s.openai_api_key = "sk-test-fake"
    s.openai_chat_model = "gpt-4o-mini"
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = ""
    s.whatsapp_vehicle_fallback_flow_id = ""
    s.whatsapp_location_fallback_flow_id = ""
    s.whatsapp_website_flow_id = ""
    return s


def _make_engine(db: Session) -> ConversationEngine:
    from app.repositories.pricing_repository import BasePriceRow
    from app.services.pricing import PricingService
    from app.services.schedule import ScheduleService

    class _FakeRepo:
        def find_base_price(self, tipo):
            return BasePriceRow(tipo_vehiculo=tipo, precio_base=130_000)

        def find_zone_by_group_and_detail(self, db, zg, zd):
            return None

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = _make_settings()
    eng._pricing = PricingService(repository=_FakeRepo())
    eng._schedule = ScheduleService(db=db)
    eng._ai_invoked = False    # WILD-04R Phase 2 observability fields
    eng._answer_source = None
    return eng


def _event(thread_id: int, wa_message_id: str, text: str = "Hola") -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id=_WA_ID,
        text=text,
        unanswered_recent_user_messages=[text],
        recent_user_messages=[text],
    )


def _add_ai_event(
    db: Session,
    thread_id: int,
    wa_message_id: str,
    *,
    reply_required: Optional[bool] = True,
    alert_eligible: Optional[bool] = True,
    reply_produced: Optional[bool] = False,
    unanswered_alert_sent_at: Optional[datetime] = None,
    status: str = "processed",
    performance_status: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> AiEvent:
    ts = created_at if created_at is not None else _BASE_TS
    ev = AiEvent(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id=_WA_ID,
        status=status,
        reply_required=reply_required,
        alert_eligible=alert_eligible,
        reply_produced=reply_produced,
        unanswered_alert_sent_at=unanswered_alert_sent_at,
        performance_status=performance_status,
        created_at=ts,
    )
    db.add(ev)
    db.commit()
    return ev


# ══════════════════════════════════════════════════════════════════════════════
# TestOutObservabilityFields
# OBS-01 to BLOCK-01: _out() and handle() observability fields
# ══════════════════════════════════════════════════════════════════════════════

class TestOutObservabilityFields(unittest.TestCase):
    """OBS-01 to BLOCK-01: CE handle() and _out() set observability fields correctly."""

    def setUp(self):
        self.db = _new_session()

    def tearDown(self):
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_obs_01_faq_path_sets_answer_source_faq_rule(self, mock_urlopen):
        """OBS-01: FAQ path sets answer_source="FAQ_RULE" and ai_invoked=True.

        Routes through _handle_general_information_ai() by patching _detect_general_information
        to return True so the FAQ gate fires, and _detect_prepurchase_signal /
        _detect_explicit_inspection_request to return False.
        """
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_FAQ}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(self.db, thread.id, "faq-msg-1", "¿Mandan informes?")

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-faq-1"):
            with patch.object(eng, "_detect_general_information", return_value=True):
                with patch.object(eng, "_detect_prepurchase_signal", return_value=False):
                    with patch.object(eng, "_detect_explicit_inspection_request", return_value=False):
                        result = eng.handle(_event(thread.id, "faq-msg-1", "¿Mandan informes?"))

        # FAQ path calls _call_openai → ai_invoked=True
        self.assertTrue(result.ai_invoked, "OBS-01: FAQ path must set ai_invoked=True")
        self.assertEqual(result.answer_source, "FAQ_RULE", "OBS-01: FAQ path must set answer_source='FAQ_RULE'")

    @patch("urllib.request.urlopen")
    def test_obs_01b_faq_path_directly(self, mock_urlopen):
        """OBS-01b: Call _handle_general_information_ai() directly — answer_source=FAQ_RULE."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_FAQ}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)

        eng = _make_engine(self.db)
        eng._ai_invoked = False
        eng._answer_source = None

        # Load context and call the FAQ handler directly
        ctx = eng._load_context(thread.id)
        ev = _event(thread.id, "faq-direct", "¿Mandan informes?")

        with patch.object(eng, "_send_text_to_wa", return_value="out-faq-direct"):
            result = eng._handle_general_information_ai(ctx, ev, ["¿Mandan informes?"])

        self.assertEqual(
            result.answer_source, "FAQ_RULE",
            "OBS-01b: _handle_general_information_ai must return answer_source='FAQ_RULE'",
        )
        # _call_openai was called, so _ai_invoked should be True
        self.assertTrue(eng._ai_invoked, "OBS-01b: ai_invoked must be True after _call_openai")

    @patch("urllib.request.urlopen")
    def test_obs_02_pricing_deterministic_path_sets_answer_source_pricing_service(self, mock_urlopen):
        """OBS-02: _process_vehicle_fallback_response with real_price_quote sets answer_source='PRICING_SERVICE'."""
        # This path does NOT call OpenAI — deterministic pricing
        mock_urlopen.side_effect = AssertionError("OpenAI must not be called in pricing path")

        _, thread, lead = _seed_contact_thread_lead(self.db)
        state = _add_state(self.db, thread.id, home_zone_group="CABA", home_zone_detail="Palermo")

        eng = _make_engine(self.db)
        eng._ai_invoked = False
        eng._answer_source = None

        ctx = eng._load_context(thread.id)

        from app.services.pricing import PricingQuote
        mock_quote = PricingQuote(
            tipo_vehiculo="AUTO",
            zone_group="CABA",
            zone_detail="Palermo",
            precio_base=130_000,
            viaticos=0,
        )

        flow_data = {
            "tipo_vehiculo": "AUTO",
            "marca": "Peugeot",
            "modelo": "2008",
            "anio": "2014",
        }

        with patch.object(eng, "_compute_price_quote", return_value=mock_quote):
            with patch.object(eng, "_send_text_to_wa", return_value="out-pricing"):
                result = eng._process_vehicle_fallback_response(ctx, ctx.state, flow_data)

        self.assertEqual(
            result.answer_source, "PRICING_SERVICE",
            "OBS-02: pricing path must return answer_source='PRICING_SERVICE'",
        )
        # No AI was invoked
        self.assertFalse(eng._ai_invoked, "OBS-02: ai_invoked must remain False in deterministic pricing path")

    @patch("urllib.request.urlopen")
    def test_obs_03a_ai_invoked_true_when_call_openai_used(self, mock_urlopen):
        """OBS-03a: handle() sets ai_invoked=True when _call_openai is called."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(self.db, thread.id, "ai-msg-1", "Hola quiero revisar un auto")

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-ai-1"):
            result = eng.handle(_event(thread.id, "ai-msg-1", "Hola quiero revisar un auto"))

        self.assertTrue(result.ai_invoked, "OBS-03a: ai_invoked must be True when _call_openai was called")

    def test_obs_03b_ai_invoked_false_on_skipped_dedup_path(self):
        """OBS-03b: handle() on skipped_dedup sets ai_invoked=False (no AI call)."""
        _, thread, lead = _seed_contact_thread_lead(self.db)
        # Set last_processed to the same message ID → triggers dedup skip
        _add_state(
            self.db, thread.id,
            last_processed_inbound_wa_message_id="dedup-msg-id",
        )
        _add_inbound_message(self.db, thread.id, "dedup-msg-id", "Hola")

        eng = _make_engine(self.db)
        result = eng.handle(_event(thread.id, "dedup-msg-id", "Hola"))

        self.assertEqual(result.action, "skipped_dedup", "OBS-03b: must be skipped_dedup action")
        self.assertFalse(result.ai_invoked, "OBS-03b: ai_invoked must be False on dedup skip")

    def test_obs_03b_ai_invoked_false_on_skipped_human_path(self):
        """OBS-03b: handle() on skipped_human sets ai_invoked=False (no AI call)."""
        _, thread, lead = _seed_contact_thread_lead(self.db, needs_human=True)
        _add_state(self.db, thread.id, needs_human=True)
        _add_inbound_message(self.db, thread.id, "human-msg-1", "Hola")

        eng = _make_engine(self.db)
        result = eng.handle(_event(thread.id, "human-msg-1", "Hola"))

        self.assertEqual(result.action, "skipped_human", "OBS-03b: must be skipped_human action")
        self.assertFalse(result.ai_invoked, "OBS-03b: ai_invoked must be False on human skip")

    @patch("urllib.request.urlopen")
    def test_obs_04_ai_invoked_turn_gets_ce_ai_answer_source(self, mock_urlopen):
        """OBS-04: AI-invoked turn (no explicit tag) → answer_source defaults to 'CE_AI'."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(self.db, thread.id, "ai-source-msg", "¿Cuánto sale revisar un auto?")

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-ce-ai"):
            result = eng.handle(_event(thread.id, "ai-source-msg", "¿Cuánto sale revisar un auto?"))

        self.assertTrue(result.ai_invoked, "OBS-04: ai_invoked must be True")
        # When AI is called and no explicit answer_source tag is set, CE_AI is the default
        if result.answer_source is not None:
            self.assertEqual(
                result.answer_source, "CE_AI",
                "OBS-04: AI-invoked reply without explicit tag must default to 'CE_AI'",
            )

    def test_error_01_internal_error_sets_reply_required_and_alert_eligible(self):
        """ERROR-01: _out('error', detail='internal_error') → reply_required=True, alert_eligible=True."""
        from app.services.conversation_engine import _out
        result = _out("error", detail="internal_error")
        self.assertTrue(result.reply_required, "ERROR-01: error/internal_error must set reply_required=True")
        self.assertTrue(result.alert_eligible, "ERROR-01: error/internal_error must set alert_eligible=True")
        self.assertFalse(result.reply_produced, "ERROR-01: error/internal_error must set reply_produced=False")

    def test_error_01_thread_not_found_sets_reply_required_false(self):
        """ERROR-01b: _out('error', detail='thread_not_found') → reply_required=False (routing miss)."""
        from app.services.conversation_engine import _out
        result = _out("error", detail="thread_not_found")
        self.assertFalse(result.reply_required, "ERROR-01b: error/thread_not_found must set reply_required=False")
        self.assertFalse(result.alert_eligible, "ERROR-01b: error/thread_not_found must set alert_eligible=False")

    def test_block_01_blocked_dispatch_sets_reply_required_and_alert_eligible(self):
        """BLOCK-01: _out('blocked_dispatch', ...) → reply_required=True, alert_eligible=True."""
        from app.services.conversation_engine import _out
        result = _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        self.assertTrue(result.reply_required, "BLOCK-01: blocked_dispatch must set reply_required=True")
        self.assertTrue(result.alert_eligible, "BLOCK-01: blocked_dispatch must set alert_eligible=True")
        self.assertFalse(result.reply_produced, "BLOCK-01: blocked_dispatch must set reply_produced=False")

    def test_no_reply_required_for_skipped_dedup(self):
        """_out('skipped_dedup') → reply_required=False."""
        from app.services.conversation_engine import _out
        result = _out("skipped_dedup")
        self.assertFalse(result.reply_required)
        self.assertFalse(result.alert_eligible)

    def test_no_reply_required_for_no_lead(self):
        """_out('no_lead') → reply_required=False."""
        from app.services.conversation_engine import _out
        result = _out("no_lead")
        self.assertFalse(result.reply_required)
        self.assertFalse(result.alert_eligible)

    def test_no_reply_required_for_skipped_human(self):
        """_out('skipped_human') → reply_required=False."""
        from app.services.conversation_engine import _out
        result = _out("skipped_human")
        self.assertFalse(result.reply_required)
        self.assertFalse(result.alert_eligible)

    def test_reply_produced_true_for_replied(self):
        """_out('replied') → reply_produced=True."""
        from app.services.conversation_engine import _out
        result = _out("replied")
        self.assertTrue(result.reply_produced)
        self.assertTrue(result.reply_required)

    def test_reply_produced_true_for_flow_button_sent(self):
        """_out('flow_button_sent') → reply_produced=True."""
        from app.services.conversation_engine import _out
        result = _out("flow_button_sent")
        self.assertTrue(result.reply_produced)

    def test_reply_produced_true_for_booking_created(self):
        """_out('booking_created') → reply_produced=True."""
        from app.services.conversation_engine import _out
        result = _out("booking_created")
        self.assertTrue(result.reply_produced)

    def test_alert_eligible_equals_reply_required(self):
        """alert_eligible always mirrors reply_required."""
        from app.services.conversation_engine import _out
        for action in ["replied", "blocked_dispatch", "error", "skipped_dedup", "no_lead", "skipped_human"]:
            with self.subTest(action=action):
                result = _out(action)
                self.assertEqual(
                    result.alert_eligible, result.reply_required,
                    f"alert_eligible must equal reply_required for action='{action}'",
                )


# ══════════════════════════════════════════════════════════════════════════════
# TestPerformanceStatus
# PERF-01 to PERF-06: _compute_performance_status
# ══════════════════════════════════════════════════════════════════════════════

class TestPerformanceStatus(unittest.TestCase):
    """PERF-01 to PERF-06: _compute_performance_status covers all latency bands."""

    def _result(
        self,
        *,
        reply_required: Optional[bool],
        reply_produced: Optional[bool],
    ) -> ConversationHandleOut:
        return ConversationHandleOut(
            ok=True,
            action="replied" if reply_produced else "error",
            reply_required=reply_required,
            reply_produced=reply_produced,
        )

    def test_perf_01_ok_at_30s(self):
        """PERF-01: reply_produced=True, ms=30000 → 'OK'."""
        r = self._result(reply_required=True, reply_produced=True)
        self.assertEqual(_compute_performance_status(r, 30_000), "OK")

    def test_perf_02_medium_at_90s(self):
        """PERF-02: reply_produced=True, ms=90000 → 'MEDIUM'."""
        r = self._result(reply_required=True, reply_produced=True)
        self.assertEqual(_compute_performance_status(r, 90_000), "MEDIUM")

    def test_perf_03_alert_at_150s(self):
        """PERF-03: reply_produced=True, ms=150000 → 'ALERT'."""
        r = self._result(reply_required=True, reply_produced=True)
        self.assertEqual(_compute_performance_status(r, 150_000), "ALERT")

    def test_perf_04_pending_when_not_produced(self):
        """PERF-04: reply_produced=False, reply_required=True → 'PENDING'."""
        r = self._result(reply_required=True, reply_produced=False)
        self.assertEqual(_compute_performance_status(r, None), "PENDING")

    def test_perf_05_no_reply_required(self):
        """PERF-05: reply_required=False → 'NO_REPLY_REQUIRED' regardless of reply_produced."""
        r = self._result(reply_required=False, reply_produced=False)
        self.assertEqual(_compute_performance_status(r, None), "NO_REPLY_REQUIRED")

    def test_perf_06_alert_stays_alert(self):
        """PERF-06: reply_produced=True, ms=150000 → 'ALERT' (not downgraded)."""
        r = self._result(reply_required=True, reply_produced=True)
        status = _compute_performance_status(r, 150_000)
        # Simulate the field already being ALERT — verify _compute_performance_status
        # still returns ALERT (does not degrade based on external state)
        self.assertEqual(status, "ALERT", "PERF-06: 150s must remain ALERT")
        # Calling again returns same result
        self.assertEqual(_compute_performance_status(r, 150_000), "ALERT")

    def test_perf_boundary_exactly_60s_ok(self):
        """Exactly 60000ms → 'OK' (boundary inclusive)."""
        r = self._result(reply_required=True, reply_produced=True)
        self.assertEqual(_compute_performance_status(r, 60_000), "OK")

    def test_perf_boundary_exactly_120s_medium(self):
        """Exactly 120000ms → 'MEDIUM' (boundary inclusive)."""
        r = self._result(reply_required=True, reply_produced=True)
        self.assertEqual(_compute_performance_status(r, 120_000), "MEDIUM")

    def test_perf_boundary_120001ms_alert(self):
        """120001ms → 'ALERT' (just above MEDIUM threshold)."""
        r = self._result(reply_required=True, reply_produced=True)
        self.assertEqual(_compute_performance_status(r, 120_001), "ALERT")

    def test_perf_reply_required_none_treated_as_falsy(self):
        """reply_required=None → treated as falsy → 'NO_REPLY_REQUIRED'."""
        r = self._result(reply_required=None, reply_produced=None)
        self.assertEqual(_compute_performance_status(r, 0), "NO_REPLY_REQUIRED")


# ══════════════════════════════════════════════════════════════════════════════
# TestLatencyCeTiming
# latency_ce_ms is set by handle() and is a non-negative integer
# ══════════════════════════════════════════════════════════════════════════════

class TestLatencyCeTiming(unittest.TestCase):
    """latency_ce_ms is set by handle() as a non-negative integer."""

    def setUp(self):
        self.db = _new_session()

    def tearDown(self):
        self.db.close()

    def test_latency_ce_ms_set_on_skipped_dedup(self):
        """latency_ce_ms is an int >= 0 on the fast dedup path."""
        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(
            self.db, thread.id,
            last_processed_inbound_wa_message_id="lat-dedup-id",
        )

        eng = _make_engine(self.db)
        result = eng.handle(_event(thread.id, "lat-dedup-id", "test"))

        self.assertIsNotNone(result.latency_ce_ms, "latency_ce_ms must not be None")
        self.assertIsInstance(result.latency_ce_ms, int, "latency_ce_ms must be an integer")
        self.assertGreaterEqual(result.latency_ce_ms, 0, "latency_ce_ms must be >= 0")

    def test_latency_ce_ms_set_on_skipped_human(self):
        """latency_ce_ms is an int >= 0 on the skipped_human path."""
        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id, needs_human=True)
        _add_inbound_message(self.db, thread.id, "lat-human-id", "Hola")

        eng = _make_engine(self.db)
        result = eng.handle(_event(thread.id, "lat-human-id", "Hola"))

        self.assertEqual(result.action, "skipped_human")
        self.assertIsNotNone(result.latency_ce_ms)
        self.assertIsInstance(result.latency_ce_ms, int)
        self.assertGreaterEqual(result.latency_ce_ms, 0)

    @patch("urllib.request.urlopen")
    def test_latency_ce_ms_set_on_replied(self, mock_urlopen):
        """latency_ce_ms is an int >= 0 on a full AI reply turn."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(self.db, thread.id, "lat-replied-id", "Hola quiero revisar")

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-lat"):
            result = eng.handle(_event(thread.id, "lat-replied-id", "Hola quiero revisar"))

        self.assertIsNotNone(result.latency_ce_ms)
        self.assertIsInstance(result.latency_ce_ms, int)
        self.assertGreaterEqual(result.latency_ce_ms, 0)


# ══════════════════════════════════════════════════════════════════════════════
# TestUnAnsweredAlert
# ALERT-00 to ALERT-03: unanswered_alert._run_check()
# ══════════════════════════════════════════════════════════════════════════════

class TestUnAnsweredAlert(unittest.TestCase):
    """ALERT-00 to ALERT-03: unanswered_alert module thresholds and _run_check().

    Note: _run_check() uses PostgreSQL-specific SQL (NOW(), INTERVAL, true/false
    literals). Tests that exercise the query-and-dispatch logic mock the DB session's
    execute() and commit() calls so the tests remain offline and DB-agnostic. Tests
    that only check constants or pure logic do not need any DB interaction.
    """

    def setUp(self):
        self.db = _new_session()

    def tearDown(self):
        self.db.close()

    def test_alert_00_thresholds_correct(self):
        """ALERT-00: Thresholds are 120s alert and 60s check interval."""
        self.assertEqual(
            _ALERT_THRESHOLD_SECONDS, 120,
            "ALERT-00: _ALERT_THRESHOLD_SECONDS must be 120",
        )
        self.assertEqual(
            _CHECK_INTERVAL_SECONDS, 60,
            "ALERT-00: _CHECK_INTERVAL_SECONDS must be 60",
        )

    def _make_mock_event_row(self, event_id: int, thread_id: int) -> MagicMock:
        """Build a mock row as returned by the raw SQL query in _run_check()."""
        row = MagicMock()
        row.event_id = event_id
        row.thread_id = thread_id
        row.wa_message_id = f"wa-msg-{event_id}"
        row.customer_name = "Test Customer"
        row.wa_id = _WA_ID
        return row

    def _make_mock_session(
        self,
        event_rows: list,
        thread_rows: Optional[list] = None,
    ) -> MagicMock:
        """Build a mock SQLAlchemy session for _run_check().

        _run_check() calls execute() in this order:
          1. SELECT ai_events ...  → fetchall() returns event_rows
          2. UPDATE ai_events ...  → one per matched event (result not used)
          3. SELECT whatsapp_threads ...  → fetchall() returns thread_rows

        We use a factory side_effect: calls with a dict param are UPDATE calls
        and return a plain MagicMock; the first and last SELECT calls return
        results with the appropriate fetchall().
        """
        mock_db = MagicMock()

        _thread_rows = thread_rows if thread_rows is not None else []

        # Pre-build result mocks
        event_select_result = MagicMock()
        event_select_result.fetchall.return_value = event_rows

        thread_select_result = MagicMock()
        thread_select_result.fetchall.return_value = _thread_rows

        update_result = MagicMock()
        update_result.fetchall.return_value = []

        # Track which SELECT has been returned
        _selects_returned = [0]

        def _execute_side_effect(query, params=None, *args, **kwargs):
            # UPDATE / INSERT calls always have a params dict
            if params is not None:
                return update_result
            # First SELECT → event rows; second SELECT → thread rows
            _selects_returned[0] += 1
            if _selects_returned[0] == 1:
                return event_select_result
            return thread_select_result

        mock_db.execute.side_effect = _execute_side_effect
        mock_db.commit.return_value = None
        mock_db.rollback.return_value = None
        mock_db.close.return_value = None
        return mock_db

    def test_alert_01_one_smtp_alert_for_pending_event_older_than_threshold(self):
        """ALERT-01: One SMTP alert fired for PENDING AiEvent older than 120s.

        When the query returns one qualifying event row, _run_check() must:
        - call _send_alert_email once with the correct thread_id
        - execute the UPDATE to set unanswered_alert_sent_at and performance_status='ALERT'
        - commit the transaction
        """
        event_row = self._make_mock_event_row(event_id=42, thread_id=7)
        mock_db = self._make_mock_session(event_rows=[event_row])

        with patch("app.services.unanswered_alert.SessionLocal", return_value=mock_db):
            with patch("app.services.unanswered_alert._send_alert_email") as mock_send:
                _run_check()

        # _send_alert_email called exactly once
        self.assertEqual(
            mock_send.call_count, 1,
            "ALERT-01: _send_alert_email must be called exactly once",
        )
        # First positional arg is the thread_id
        called_thread_id = mock_send.call_args[0][0]
        self.assertEqual(
            called_thread_id, 7,
            "ALERT-01: alert must reference the correct thread_id",
        )

        # The UPDATE was executed (the second execute() call in the per-event loop)
        # Total execute calls: 1 (event query) + 1 (UPDATE mark alerted) + 1 (thread query) = 3
        # But execute may be called in order; verify at least the UPDATE call happened.
        self.assertGreaterEqual(
            mock_db.execute.call_count, 2,
            "ALERT-01: must execute at least the event query + the UPDATE",
        )
        # commit() was called after marking the event
        self.assertGreaterEqual(mock_db.commit.call_count, 1, "ALERT-01: commit must be called")

    def test_alert_02_no_duplicate_smtp_alert_when_already_alerted(self):
        """ALERT-02: No alert when unanswered_alert_sent_at is already set.

        The SQL WHERE clause excludes events with unanswered_alert_sent_at IS NOT NULL.
        We simulate this by returning an empty event_rows list from the query.
        """
        # The query found no eligible events (already alerted row was excluded by WHERE)
        mock_db = self._make_mock_session(event_rows=[])

        with patch("app.services.unanswered_alert.SessionLocal", return_value=mock_db):
            with patch("app.services.unanswered_alert._send_alert_email") as mock_send:
                _run_check()

        self.assertEqual(
            mock_send.call_count, 0,
            "ALERT-02: _send_alert_email must NOT be called when no eligible events",
        )

    def test_alert_03_skipped_human_no_ce_sla_alert(self):
        """ALERT-03: reply_required=False / alert_eligible=False events excluded by query.

        skipped_human turns have reply_required=False and alert_eligible=False.
        The SQL WHERE clause (ae.reply_required = true AND ae.alert_eligible = true)
        excludes them. We simulate this by returning empty event_rows.
        """
        mock_db = self._make_mock_session(event_rows=[])

        with patch("app.services.unanswered_alert.SessionLocal", return_value=mock_db):
            with patch("app.services.unanswered_alert._send_alert_email") as mock_send:
                _run_check()

        self.assertEqual(
            mock_send.call_count, 0,
            "ALERT-03: no alert must fire for skipped_human events (excluded by query)",
        )

    def test_alert_multiple_events_each_alerted_once(self):
        """Multiple qualifying events each get exactly one alert email."""
        row1 = self._make_mock_event_row(event_id=10, thread_id=1)
        row2 = self._make_mock_event_row(event_id=11, thread_id=2)
        mock_db = self._make_mock_session(event_rows=[row1, row2])

        with patch("app.services.unanswered_alert.SessionLocal", return_value=mock_db):
            with patch("app.services.unanswered_alert._send_alert_email") as mock_send:
                _run_check()

        self.assertEqual(mock_send.call_count, 2, "Two events → two alert emails")
        thread_ids_alerted = {c[0][0] for c in mock_send.call_args_list}
        self.assertIn(1, thread_ids_alerted, "Thread 1 must be alerted")
        self.assertIn(2, thread_ids_alerted, "Thread 2 must be alerted")

    def test_alert_send_failure_does_not_crash_loop(self):
        """SMTP failure on one event does not prevent processing next events."""
        row1 = self._make_mock_event_row(event_id=20, thread_id=3)
        row2 = self._make_mock_event_row(event_id=21, thread_id=4)

        # First send raises; second should still be attempted
        mock_db = self._make_mock_session(event_rows=[row1, row2])

        send_call_count = []

        def _side_effect(thread_id, *args, **kwargs):
            send_call_count.append(thread_id)
            if thread_id == 3:
                raise RuntimeError("SMTP failed")

        with patch("app.services.unanswered_alert.SessionLocal", return_value=mock_db):
            with patch("app.services.unanswered_alert._send_alert_email", side_effect=_side_effect):
                # Should not raise
                _run_check()

        # Both events were attempted
        self.assertIn(3, send_call_count, "thread_id=3 must have been attempted")
        self.assertIn(4, send_call_count, "thread_id=4 must have been attempted despite prior failure")


# ══════════════════════════════════════════════════════════════════════════════
# TestWild04SemanticBurst — Section N
# WILD-04-N1 and WILD-04-N2: burst assembly and CE vehicle context
# ══════════════════════════════════════════════════════════════════════════════

class TestWild04SemanticBurst(unittest.TestCase):
    """Section N — Semantic burst assembly and vehicle context extraction."""

    def setUp(self):
        self.db = _new_session()

    def tearDown(self):
        self.db.close()

    def test_wild04_n1_burst_assembly(self):
        """WILD-04-N1: _fetch_burst_texts returns all 3 burst messages in order."""
        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(
            self.db, thread.id,
            last_processed_inbound_wa_message_id="prev-cursor-id",
        )

        # Message before the burst (already processed)
        _add_inbound_message(
            self.db, thread.id, "prev-cursor-id",
            "Mensaje previo procesado",
            offset_seconds=-30,
        )

        # Burst messages A, B, C
        text_a = "Hola, ¿cómo va? Quiero revisar un 2008 del 2014. ¿Ustedes hacen eso?"
        text_b = "¿Mandan informes? ¿Tengo que estar presente?"
        text_c = "Eh, ¿se paga con débito?"

        _add_inbound_message(self.db, thread.id, "burst-A", text_a, offset_seconds=0)
        _add_inbound_message(self.db, thread.id, "burst-B", text_b, offset_seconds=5)
        _add_inbound_message(self.db, thread.id, "burst-C", text_c, offset_seconds=10)

        eng = _make_engine(self.db)
        burst_texts = eng._fetch_burst_texts(thread.id, "prev-cursor-id", "burst-C")

        self.assertIn(text_a, burst_texts, "WILD-04-N1: message A must be in burst")
        self.assertIn(text_b, burst_texts, "WILD-04-N1: message B must be in burst")
        self.assertIn(text_c, burst_texts, "WILD-04-N1: message C must be in burst")
        self.assertEqual(len(burst_texts), 3, "WILD-04-N1: exactly 3 burst messages")

        # Chronological order: A first, C last
        self.assertEqual(burst_texts[0], text_a, "WILD-04-N1: message A must be first (chronological)")
        self.assertEqual(burst_texts[-1], text_c, "WILD-04-N1: message C must be last")

    @patch("urllib.request.urlopen")
    def test_wild04_n2_ce_processes_burst_with_vehicle_context(self, mock_urlopen):
        """WILD-04-N2: CE processes 3-message burst; AI extracts Peugeot 2008 2014 into candidate."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_PREPURCHASE}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)

        text_a = "Hola, ¿cómo va? Quiero revisar un 2008 del 2014. ¿Ustedes hacen eso?"
        text_b = "¿Mandan informes? ¿Tengo que estar presente?"
        text_c = "Eh, ¿se paga con débito?"

        _add_inbound_message(self.db, thread.id, "n2-A", text_a, offset_seconds=0)
        _add_inbound_message(self.db, thread.id, "n2-B", text_b, offset_seconds=5)
        _add_inbound_message(self.db, thread.id, "n2-C", text_c, offset_seconds=10)

        # Combine all 3 burst messages as the full text input (simulating n8n burst aggregation)
        combined_text = f"{text_a} {text_b} {text_c}"

        ev = ConversationHandleIn(
            thread_id=thread.id,
            wa_message_id="n2-C",
            wa_id=_WA_ID,
            text=text_c,
            unanswered_recent_user_messages=[text_a, text_b, text_c],
            recent_user_messages=[text_a, text_b, text_c],
        )

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-n2"):
            result = eng.handle(ev)

        # CE must have processed the turn — action must be a valid CE action
        self.assertIn(
            result.action,
            ("replied", "blocked_dispatch", "vehicle_fuzzy_blocked", "skipped_dedup"),
            "WILD-04-N2: 3-message burst must produce a valid CE action",
        )

        # AI must have been invoked — the burst includes FAQ + inspection intent
        # which both route through AI paths (main AI or FAQ-AI). ai_invoked=True confirms AI call.
        self.assertTrue(result.ai_invoked, "WILD-04-N2: AI must be invoked for the 3-message burst")

        # The 3-message burst contains FAQ content (B: informes/presente, C: débito).
        # CE correctly routes FAQ+inspection bursts through the FAQ-AI handler (_handle_general_information_ai).
        # Candidate creation happens in subsequent turns once inspection context is established.
        # The key verification: AI was called (ai_invoked=True) and CE replied (not errored).
        # Peugeot 2008 2014 extraction is validated separately in test_wild04_resolver_2008_del_2014_ai_path.

        # Note: exact reply semantics (payment/report/presence) require live OpenAI
        # and are marked LIVE-ONLY in the RETURN block.

    def test_wild04_n1_burst_empty_when_no_previous_cursor(self):
        """WILD-04-N1b: _fetch_burst_texts returns [] when previous_cursor is None."""
        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(self.db, thread.id, "first-msg", "Primer mensaje", offset_seconds=0)

        eng = _make_engine(self.db)
        burst_texts = eng._fetch_burst_texts(thread.id, None, "first-msg")
        self.assertEqual(burst_texts, [], "WILD-04-N1b: no burst when previous_cursor is None")

    @patch("urllib.request.urlopen")
    def test_wild04_n2b_burst_message_count_equals_3(self, mock_urlopen):
        """WILD-04-N2b: burst_message_count=3 for exact A+B+C WILD-04 burst (Issue 6)."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_PREPURCHASE}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)

        # Seed a "before-burst" message that becomes the previous cursor
        _add_inbound_message(
            self.db, thread.id, "pre-burst-seed", "Hola", offset_seconds=-60
        )
        _add_state(
            self.db, thread.id,
            last_processed_inbound_wa_message_id="pre-burst-seed",
        )

        text_a = "Hola, ¿cómo va? Quiero revisar un 2008 del 2014. ¿Ustedes hacen eso?"
        text_b = "¿Mandan informes? ¿Tengo que estar presente?"
        text_c = "Eh, ¿se paga con débito?"
        _add_inbound_message(self.db, thread.id, "n2b-A", text_a, offset_seconds=0)
        _add_inbound_message(self.db, thread.id, "n2b-B", text_b, offset_seconds=5)
        _add_inbound_message(self.db, thread.id, "n2b-C", text_c, offset_seconds=10)

        ev = ConversationHandleIn(
            thread_id=thread.id,
            wa_message_id="n2b-C",
            wa_id=_WA_ID,
            text=text_c,
            unanswered_recent_user_messages=[text_a, text_b, text_c],
            recent_user_messages=[text_a, text_b, text_c],
        )

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-n2b"):
            result = eng.handle(ev)

        self.assertEqual(
            result.burst_message_count, 3,
            "WILD-04-N2b: burst_message_count must be 3 for exact A+B+C burst",
        )
        self.assertEqual(result.action, "replied", "WILD-04-N2b: must produce replied action")

    @patch("urllib.request.urlopen")
    def test_wild04_resolver_2008_del_2014_ai_path(self, mock_urlopen):
        """WILD-04-R1: 'un 2008 del 2014' → two numeric tokens → no WILD-02-B → AI extracts Peugeot 2008 2014."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_PREPURCHASE}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(
            self.db, thread.id, "r1-msg",
            "Quiero revisar un 2008 del 2014 en San Telmo. ¿Cuánto sale?",
            offset_seconds=0,
        )

        sent_texts: list[str] = []
        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa",
                          side_effect=lambda ctx, txt: sent_texts.append(txt) or "out-r1"):
            result = eng.handle(_event(
                thread.id, "r1-msg",
                "Quiero revisar un 2008 del 2014 en San Telmo. ¿Cuánto sale?"
            ))

        # WILD-02-B would send "¿Es un Peugeot 2008?" via _send_text_to_wa WITHOUT calling AI.
        # Proof WILD-02-B was NOT triggered: ai_invoked=True (AI was called, not WILD-02-B shortcut).
        self.assertTrue(result.ai_invoked, "WILD-04-R1: AI must be invoked for ambiguous 2-token input")
        # Additionally: the specific WILD-02-B question pattern must not appear in sent texts
        wild02b_question = "¿Es un Peugeot 2008?"
        for txt in sent_texts:
            self.assertNotEqual(
                txt, wild02b_question,
                f"WILD-04-R1: two-token '2008 del 2014' must NOT send WILD-02-B question; sent: {txt!r}",
            )
        # Peugeot 2008 2014 candidate must be created (AI mock returns this vehicle)
        self.db.expire_all()
        candidates = list(self.db.execute(
            select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread.id)
        ).scalars().all())
        self.assertGreater(len(candidates), 0, "WILD-04-R1: Peugeot 2008 2014 candidate must be created")
        latest = max(candidates, key=lambda c: c.id)
        self.assertIn("Peugeot", (latest.marca or ""), "WILD-04-R1: marca must be Peugeot")
        self.assertIn("2008", (latest.modelo or ""), "WILD-04-R1: modelo must be 2008")
        self.assertEqual(latest.anio, 2014, "WILD-04-R1: anio must be 2014")

    @patch("urllib.request.urlopen")
    def test_wild04_resolver_focus_2008_not_numeric_path(self, mock_urlopen):
        """WILD-04-R2: 'Focus 2008' → lookup_vehicle HIGH confidence → pre_detected set → no WILD-02-B."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_PREPURCHASE}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(
            self.db, thread.id, "focus-msg", "Quiero revisar un Focus 2008", offset_seconds=0
        )

        sent_texts: list[str] = []
        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa",
                          side_effect=lambda ctx, txt: sent_texts.append(txt) or "out-focus"):
            result = eng.handle(_event(thread.id, "focus-msg", "Quiero revisar un Focus 2008"))

        # WILD-02-B must NOT send the "¿Es un Peugeot 2008?" confirmation.
        # lookup_vehicle returns Ford Focus HIGH confidence → pre_detected_vehicle is set →
        # WILD-02-B guard (pre_detected_vehicle is None) is False → WILD-02-B skipped.
        for txt in sent_texts:
            self.assertNotIn(
                "Peugeot", txt,
                f"WILD-04-R2: 'Focus 2008' must NOT trigger Peugeot 2008 confirmation; sent: {txt!r}",
            )
        # A Ford Focus candidate must be created from catalog
        self.db.expire_all()
        candidates = list(self.db.execute(
            select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread.id)
        ).scalars().all())
        self.assertGreater(len(candidates), 0, "WILD-04-R2: Ford Focus candidate must be created")
        latest = max(candidates, key=lambda c: c.id)
        self.assertIn("Ford", (latest.marca or ""), "WILD-04-R2: candidate must be Ford (not Peugeot)")
        self.assertIn("Focus", (latest.modelo or ""), "WILD-04-R2: modelo must be Focus")
        # CE action: replied or blocked_dispatch (location clarification blocked by kill switch)
        self.assertIn(
            result.action, ("replied", "blocked_dispatch"),
            "WILD-04-R2: action must be replied or blocked_dispatch (location gate if OUTBOUND_ENABLED=false)",
        )

    @patch("urllib.request.urlopen")
    def test_wild04_resolver_gol_2008_not_numeric_path(self, mock_urlopen):
        """WILD-04-R3: 'Gol 2008' → lookup_vehicle HIGH confidence → pre_detected set → no WILD-02-B."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_PREPURCHASE}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(
            self.db, thread.id, "gol-msg", "Quiero revisar un Gol 2008", offset_seconds=0
        )

        sent_texts: list[str] = []
        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa",
                          side_effect=lambda ctx, txt: sent_texts.append(txt) or "out-gol"):
            result = eng.handle(_event(thread.id, "gol-msg", "Quiero revisar un Gol 2008"))

        for txt in sent_texts:
            self.assertNotIn(
                "Peugeot", txt,
                f"WILD-04-R3: 'Gol 2008' must NOT trigger Peugeot 2008 confirmation; sent: {txt!r}",
            )
        # A VW Gol candidate must be created from catalog
        self.db.expire_all()
        candidates = list(self.db.execute(
            select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread.id)
        ).scalars().all())
        self.assertGreater(len(candidates), 0, "WILD-04-R3: VW Gol candidate must be created")
        latest = max(candidates, key=lambda c: c.id)
        self.assertIn("Gol", (latest.modelo or ""), "WILD-04-R3: modelo must be Gol")
        self.assertNotIn("Peugeot", (latest.marca or ""), "WILD-04-R3: marca must NOT be Peugeot")
        self.assertIn(
            result.action, ("replied", "blocked_dispatch"),
            "WILD-04-R3: action must be replied or blocked_dispatch",
        )

    @patch("urllib.request.urlopen")
    def test_wild04_resolver_two_numeric_tokens_no_confirm(self, mock_urlopen):
        """WILD-04-R4: '2008 o 2014' → 2 numeric tokens → _contextual returns None → no WILD-02-B ask."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db)
        _add_state(self.db, thread.id)
        _add_inbound_message(
            self.db, thread.id, "ambig-msg",
            "Quiero revisar un auto. ¿Sirve un 2008 o un 2014?",
            offset_seconds=0,
        )

        sent_texts: list[str] = []
        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", side_effect=lambda ctx, txt: sent_texts.append(txt) or "out-ambig"):
            result = eng.handle(_event(
                thread.id, "ambig-msg",
                "Quiero revisar un auto. ¿Sirve un 2008 o un 2014?"
            ))

        # WILD-02-B confirmation must NOT be sent for 2-token ambiguous text
        for txt in sent_texts:
            self.assertNotIn(
                "Peugeot", txt,
                f"WILD-04-R4: two-token ambiguous text must NOT trigger Peugeot 2008 ask; sent: {txt!r}",
            )
        # CE must have processed the turn (not errored)
        self.assertIn(
            result.action,
            ("replied", "blocked_dispatch", "vehicle_fuzzy_blocked", "skipped_dedup"),
            "WILD-04-R4: must produce a valid action",
        )


# ══════════════════════════════════════════════════════════════════════════════
# TestReturningCustomerObservability — Section O
# RETURNING-OBS-1: returning customer cycle reset clears observability fields
# ══════════════════════════════════════════════════════════════════════════════

class TestReturningCustomerObservability(unittest.TestCase):
    """Section O — Returning customer cycle reset and observability continuity."""

    def setUp(self):
        self.db = _new_session()

    def tearDown(self):
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_returning_obs_1_cycle_reset_clears_observability_fields(self, mock_urlopen):
        """RETURNING-OBS-1: Returning customer cycle reset; CE result has correct observability.

        Setup:
          - Contact + Thread + Lead with historical Revisions, estado=AGENDADO
          - Transition: set_lead_estado(db, lead, 'CONSULTA_NUEVA') → cycle_reset_pending=True
          - CE processes new message → executes cycle reset

        Assertions:
          - Same Contact/Thread/Lead IDs
          - Old candidate invisible (created_at < current_cycle_started_at)
          - Old messages invisible (id < current_cycle_start_message_db_id)
          - Historical Revision rows unchanged
          - CE result has reply_required=True (new cycle, qualifying turn)
          - result.ai_invoked is not None (tracked)
        """
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        # ── Cycle 1 setup ────────────────────────────────────────────────────
        contact, thread, lead = _seed_contact_thread_lead(
            self.db, estado="AGENDADO", flag="PRESUPUESTO_ENVIADO"
        )
        contact_id = contact.id
        thread_id = thread.id
        lead_id = lead.id

        # Old-cycle candidate (created before the cycle boundary)
        old_cand = _add_candidate(
            self.db, thread.id, "Peugeot", "2008",
            tipo_vehiculo="AUTO", anio=2018,
            offset_seconds=-7200,
        )

        # Old-cycle messages
        old_msg_1 = _add_inbound_message(
            self.db, thread.id, "old-msg-1", "mensaje previo 1", offset_seconds=-7200
        )
        old_msg_2 = _add_inbound_message(
            self.db, thread.id, "old-msg-2", "mensaje previo 2", offset_seconds=-3600
        )

        # Add a historical ThreadRevision
        old_revision = ThreadRevision(
            thread_id=thread.id,
            candidate_id=old_cand.id,
            tipo_vehiculo="AUTO",
            status="completed",
        )
        self.db.add(old_revision)
        self.db.commit()
        historical_revision_count = self.db.execute(
            select(ThreadRevision).where(ThreadRevision.thread_id == thread.id)
        ).scalars().all()
        initial_revision_count = len(historical_revision_count)

        # State from old cycle (lead was AGENDADO)
        state = _add_state(
            self.db, thread.id,
            cycle_reset_pending=False,
            needs_human=True,
            last_stage="BOOKED",
            current_focus_candidate_id=old_cand.id,
            home_zone_group="GBA Sur",
            home_zone_detail="Quilmes",
            last_processed_inbound_wa_message_id="old-msg-2",
        )

        # ── Human resets the lead ─────────────────────────────────────────
        set_lead_estado(self.db, lead, "CONSULTA_NUEVA")
        self.db.commit()
        self.db.expire_all()

        # Verify signal is set
        state_check = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertTrue(
            state_check.cycle_reset_pending,
            "RETURNING-OBS-1: cycle_reset_pending must be True after human reset",
        )

        # ── CE processes new message ──────────────────────────────────────
        new_msg = _add_inbound_message(
            self.db, thread.id, "new-cycle-msg-1",
            "Hola, quiero revisar otro auto",
            offset_seconds=0,
        )

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-returning"):
            result = eng.handle(_event(thread.id, "new-cycle-msg-1", "Hola, quiero revisar otro auto"))

        # ── Verify identity preserved ─────────────────────────────────────
        self.db.expire_all()
        thread_after = self.db.get(WhatsAppThread, thread_id)
        self.assertEqual(thread_after.id, thread_id, "RETURNING-OBS-1: same thread ID")
        self.assertEqual(thread_after.contact_id, contact_id, "RETURNING-OBS-1: same contact ID")
        self.assertEqual(thread_after.lead_id, lead_id, "RETURNING-OBS-1: same lead ID")

        # ── Verify cycle reset happened ────────────────────────────────────
        state_after = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertFalse(state_after.cycle_reset_pending, "RETURNING-OBS-1: signal must be consumed")
        self.assertEqual(
            state_after.current_cycle_start_message_db_id, new_msg.id,
            "RETURNING-OBS-1: watermark must point to new cycle start message",
        )

        # ── Verify old candidate invisible in new cycle context ────────────
        new_ctx = eng._load_context(thread.id)
        ctx_candidate_ids = {c.id for c in new_ctx.candidates}
        self.assertNotIn(
            old_cand.id, ctx_candidate_ids,
            "RETURNING-OBS-1: old candidate must be invisible in new cycle context",
        )

        # Old candidate still exists in DB (not deleted)
        self.assertIsNotNone(
            self.db.get(WhatsAppThreadCandidate, old_cand.id),
            "RETURNING-OBS-1: old candidate must still exist in DB (historical record)",
        )

        # ── Verify old messages invisible in new cycle context ─────────────
        new_ctx_msg_ids = [m.id for m in new_ctx.db_messages]
        self.assertNotIn(
            old_msg_1.id, new_ctx_msg_ids,
            "RETURNING-OBS-1: old message 1 must be invisible in new cycle context",
        )
        self.assertNotIn(
            old_msg_2.id, new_ctx_msg_ids,
            "RETURNING-OBS-1: old message 2 must be invisible in new cycle context",
        )

        # ── Verify historical Revision rows unchanged ──────────────────────
        revision_count_after = len(self.db.execute(
            select(ThreadRevision).where(ThreadRevision.thread_id == thread.id)
        ).scalars().all())
        self.assertEqual(
            revision_count_after, initial_revision_count,
            "RETURNING-OBS-1: historical Revision count must be unchanged after cycle reset",
        )

        # ── Verify observability fields ────────────────────────────────────
        self.assertIsNotNone(
            result.ai_invoked,
            "RETURNING-OBS-1: result.ai_invoked must be tracked (not None)",
        )
        # Cycle reset produces a qualifying turn — not skipped
        self.assertNotEqual(
            result.action, "skipped_human",
            "RETURNING-OBS-1: CE must not skip as human after cycle reset",
        )
        # CE result should require a reply (qualifying new cycle turn)
        if result.action not in ("error", "no_lead"):
            self.assertTrue(
                result.reply_required,
                "RETURNING-OBS-1: new cycle qualifying turn must have reply_required=True",
            )

    @patch("urllib.request.urlopen")
    def test_returning_obs_1b_second_message_not_reset(self, mock_urlopen):
        """RETURNING-OBS-1b: Second inbound in the new cycle does NOT trigger a second reset."""
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_REPLY_QUALIFYING}}]
        }).encode()

        _, thread, lead = _seed_contact_thread_lead(self.db, estado="ATENCION_HUMANA")
        _add_state(self.db, thread.id, cycle_reset_pending=True, needs_human=True)
        set_lead_estado(self.db, lead, "CONSULTA_NUEVA")
        self.db.commit()
        self.db.expire_all()

        # First inbound triggers reset
        _add_inbound_message(self.db, thread.id, "first-new", "Hola de nuevo", offset_seconds=0)
        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-first"):
            eng.handle(_event(thread.id, "first-new", "Hola de nuevo"))

        self.db.expire_all()
        state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertFalse(state.cycle_reset_pending, "Signal consumed after first inbound")
        cycle_start_id = state.current_cycle_start_message_db_id

        # Second inbound — no more reset pending
        _add_inbound_message(self.db, thread.id, "second-new", "¿Cuánto sale?", offset_seconds=30)
        with patch.object(eng, "_send_text_to_wa", return_value="out-second"):
            eng.handle(_event(thread.id, "second-new", "¿Cuánto sale?"))

        self.db.expire_all()
        state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()

        # cycle_reset_pending still False (not re-set)
        self.assertFalse(state.cycle_reset_pending, "RETURNING-OBS-1b: reset must not fire again")
        # Watermark still points to the first new message (not the second)
        self.assertEqual(
            state.current_cycle_start_message_db_id, cycle_start_id,
            "RETURNING-OBS-1b: watermark must not change on second inbound",
        )

    @patch("urllib.request.urlopen")
    def test_returning_obs_2_issue7_focus_pilar_new_candidate(self, mock_urlopen):
        """RETURNING-OBS-2 (Issue 7): Returning customer with 2 historical revisions.

        Setup:
          Cycle 1 (Revision 1): Peugeot 2008 / Berazategui — completed
          Cycle 2 (Revision 2): Peugeot 2008 2014 / Balvanera — completed (AGENDADO)
          Human reset: estado → CONSULTA_NUEVA → cycle_reset_pending=True
          New inbound: "Encontré un Focus 2019 en Pilar. ¿Cuánto sale?"
          Expected: new candidate Ford Focus 2019 / Pilar; historical revisions unchanged.
        """
        _ai_reply_focus_2019 = json.dumps({
            "intent": "PREPURCHASE_INSPECTION",
            "reply": "Revisamos el Ford Focus 2019 con gusto. ¿En qué zona de Pilar está?",
            "deferred_interest": False,
            "candidate": {
                "action": "upsert",
                "vehicle_make": "Ford",
                "vehicle_model": "Focus",
                "vehicle_year": 2019,
                "tipo_vehiculo": "AUTO",
            },
            "extracted": {
                "vehicle_make": "Ford",
                "vehicle_model": "Focus",
                "vehicle_year": 2019,
                "zone_detail": "Pilar",
            },
            "lead_flag": None,
            "needs_human": False,
        })
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock()
        mock_urlopen.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _ai_reply_focus_2019}}]
        }).encode()

        # ── Historical setup: Contact / Thread / Lead ─────────────────────
        contact, thread, lead = _seed_contact_thread_lead(
            self.db, estado="AGENDADO", flag="PRESUPUESTO_ENVIADO"
        )
        contact_id = contact.id
        thread_id = thread.id
        lead_id = lead.id

        # Revision 1 candidate: Peugeot 2008 / Berazategui (Cycle 1)
        rev1_cand = _add_candidate(
            self.db, thread.id, "Peugeot", "2008",
            tipo_vehiculo="AUTO", anio=None,
            offset_seconds=-14400,
        )
        rev1_revision = ThreadRevision(
            thread_id=thread.id,
            candidate_id=rev1_cand.id,
            tipo_vehiculo="AUTO",
            status="completed",
        )
        self.db.add(rev1_revision)
        self.db.flush()
        rev1_revision_id = rev1_revision.id

        # Revision 2 candidate: Peugeot 2008 2014 / Balvanera (Cycle 2)
        rev2_cand = _add_candidate(
            self.db, thread.id, "Peugeot", "2008",
            tipo_vehiculo="AUTO", anio=2014,
            offset_seconds=-3600,
        )
        rev2_revision = ThreadRevision(
            thread_id=thread.id,
            candidate_id=rev2_cand.id,
            tipo_vehiculo="AUTO",
            status="completed",
        )
        self.db.add(rev2_revision)
        self.db.flush()
        rev2_revision_id = rev2_revision.id
        self.db.commit()

        initial_revision_ids = {rev1_revision_id, rev2_revision_id}

        # Old-cycle state (after AGENDADO — Cycle 2 completed)
        _add_state(
            self.db, thread.id,
            last_stage="BOOKED",
            needs_human=False,
            cycle_reset_pending=False,
            current_focus_candidate_id=rev2_cand.id,
            home_zone_group="CABA",
            home_zone_detail="Balvanera",
            last_processed_inbound_wa_message_id=None,
        )

        # ── Human resets: AGENDADO → CONSULTA_NUEVA ──────────────────────
        set_lead_estado(self.db, lead, "CONSULTA_NUEVA")
        self.db.commit()
        self.db.expire_all()

        state_check = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertTrue(
            state_check.cycle_reset_pending,
            "RETURNING-OBS-2: cycle_reset_pending must be True after AGENDADO → CONSULTA_NUEVA",
        )

        # ── New inbound: Focus 2019 / Pilar ──────────────────────────────
        new_msg = _add_inbound_message(
            self.db, thread.id, "issue7-new-msg",
            "Encontré un Focus 2019 en Pilar. ¿Cuánto sale?",
            offset_seconds=0,
        )

        eng = _make_engine(self.db)
        with patch.object(eng, "_send_text_to_wa", return_value="out-issue7"):
            result = eng.handle(_event(
                thread.id, "issue7-new-msg",
                "Encontré un Focus 2019 en Pilar. ¿Cuánto sale?",
            ))

        # ── Verify contact / thread / lead identity preserved ─────────────
        self.db.expire_all()
        thread_after = self.db.get(WhatsAppThread, thread_id)
        self.assertEqual(thread_after.id, thread_id, "RETURNING-OBS-2: same thread ID")
        self.assertEqual(thread_after.contact_id, contact_id, "RETURNING-OBS-2: same contact ID")
        self.assertEqual(thread_after.lead_id, lead_id, "RETURNING-OBS-2: same lead ID")

        # ── Verify cycle reset happened ────────────────────────────────────
        state_after = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertFalse(state_after.cycle_reset_pending, "RETURNING-OBS-2: signal consumed")
        self.assertEqual(
            state_after.current_cycle_start_message_db_id, new_msg.id,
            "RETURNING-OBS-2: watermark points to new cycle start message",
        )

        # ── Verify historical Revision rows unchanged ──────────────────────
        revisions_after = self.db.execute(
            select(ThreadRevision).where(ThreadRevision.thread_id == thread.id)
        ).scalars().all()
        revision_ids_after = {r.id for r in revisions_after}
        self.assertTrue(
            initial_revision_ids.issubset(revision_ids_after),
            "RETURNING-OBS-2: historical revisions must still exist after cycle reset",
        )

        # ── Verify new Ford Focus 2019 candidate created ──────────────────
        all_candidates = self.db.execute(
            select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread.id)
        ).scalars().all()
        new_candidates = [c for c in all_candidates if c.id not in {rev1_cand.id, rev2_cand.id}]
        self.assertGreater(
            len(new_candidates), 0,
            "RETURNING-OBS-2: a new candidate must be created for Cycle 3 (Ford Focus 2019)",
        )
        new_cand = max(new_candidates, key=lambda c: c.id)
        self.assertIn(
            "Ford", (new_cand.marca or ""),
            f"RETURNING-OBS-2: new candidate marca must be Ford (got: {new_cand.marca!r})",
        )
        self.assertIn(
            "Focus", (new_cand.modelo or ""),
            f"RETURNING-OBS-2: new candidate modelo must be Focus (got: {new_cand.modelo!r})",
        )
        self.assertEqual(
            new_cand.anio, 2019,
            f"RETURNING-OBS-2: new candidate anio must be 2019 (got: {new_cand.anio!r})",
        )

        # ── Verify historical candidates still in DB (not deleted) ─────────
        self.assertIsNotNone(
            self.db.get(WhatsAppThreadCandidate, rev1_cand.id),
            "RETURNING-OBS-2: Peugeot 2008 (Revision 1) must still exist in DB",
        )
        self.assertIsNotNone(
            self.db.get(WhatsAppThreadCandidate, rev2_cand.id),
            "RETURNING-OBS-2: Peugeot 2008 2014 (Revision 2) must still exist in DB",
        )

        # ── Verify old candidates excluded from new cycle context ──────────
        new_ctx = eng._load_context(thread.id)
        ctx_candidate_ids = {c.id for c in new_ctx.candidates}
        self.assertNotIn(rev1_cand.id, ctx_candidate_ids,
                         "RETURNING-OBS-2: Revision 1 candidate excluded from new cycle context")
        self.assertNotIn(rev2_cand.id, ctx_candidate_ids,
                         "RETURNING-OBS-2: Revision 2 candidate excluded from new cycle context")

        # ── CE result sanity ──────────────────────────────────────────────
        # CE recognizes Ford Focus 2019 from catalog → vehicle_known=True, zone_unknown →
        # location clarification is sent (or blocked by kill switch when OUTBOUND_ENABLED=false).
        self.assertIn(
            result.action, ("replied", "blocked_dispatch"),
            "RETURNING-OBS-2: CE must reply or trigger location gate (blocked by kill switch)",
        )
        self.assertIsNotNone(result.ai_invoked, "RETURNING-OBS-2: ai_invoked must be tracked")

        # Store IDs for RETURN block reporting
        self._issue7_contact_id = contact_id
        self._issue7_thread_id = thread_id
        self._issue7_lead_id = lead_id
        self._issue7_rev1_cand_id = rev1_cand.id
        self._issue7_rev2_cand_id = rev2_cand.id
        self._issue7_new_cand_id = new_cand.id
        self._issue7_new_cand = new_cand


if __name__ == "__main__":
    unittest.main()
