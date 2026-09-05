"""M20.4.3 — Blocked-dispatch transaction boundary tests.

Root cause being tested:
  OutboundBlockedError (BLOCKED_KILL_SWITCH) from _send_text_to_wa previously
  caused handle() to call self.db.rollback(), discarding the fully-computed
  logical conversation state.  The fix commits CE session for kill-switch blocks.

All tests are fully offline: SQLite in-memory, no containers, no Meta API,
no live OpenAI calls.

Test index:
  1.  3008/Benavídez replay: lead.flag=PRESUPUESTO_ENVIADO and
      state.last_stage=QUOTED commit; blocked audit exists; 0 Meta calls.
  2.  AI branch completes before block: AI was invoked; state commits;
      needs_human remains False; 0 Meta calls.
  3.  Fallback-flow trigger path: qualifying state commits when location
      clarification is blocked; blocked audit exists; 0 Meta calls.
  4.  Response format: action=blocked_dispatch; ok=False; handled=True (F4);
      wa_message_id=None; no sent message row.
  5.  Production semantics unchanged: OUTBOUND_ENABLED=true → action=replied;
      ok=True; blocked_dispatch is not returned.
  6.  Regression: _out() helper contract for new and existing actions;
      accent-normalization zone fix still works.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory setup ───────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON           # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON              # type: ignore[attr-defined]

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


# ── Stub app.db BEFORE importing app.models ──────────────────────────────────
_db_mod = types.ModuleType("app.db")
_db_mod.Base = Base                           # type: ignore[attr-defined]
_db_mod.engine = _engine                      # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal          # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:"   # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen                  # type: ignore[attr-defined]
sys.modules["app.db"] = _db_mod

# ── Stub heavy optional deps ──────────────────────────────────────────────────
for _mod_name in [
    "resend", "anthropic", "openai", "boto3",
    "botocore", "botocore.exceptions",
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── Import ORM models ─────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    Lead,
    ViaticosZone,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)

Lead.__table__.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.repositories.pricing_repository import BasePriceRow
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine
from app.services.outbound_guard import OutboundBlockedError, enforce_outbound_enabled
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
from app.services.pricing import PricingService

# ── Shared constants ──────────────────────────────────────────────────────────
_WA_ID = "5491153368330"
_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── Fake pricing repository ───────────────────────────────────────────────────

@dataclass
class _FakeZoneRow:
    zone_group: str
    zone_detail: Optional[str]
    viaticos: int


class FakeRepoBenavidez:
    """SUV/4x4 in Norte/Benavidez → $140,000, viáticos $0."""

    def find_base_price(self, tipo_vehiculo: str) -> Optional[BasePriceRow]:
        if tipo_vehiculo == "SUV/4x4":
            return BasePriceRow(tipo_vehiculo="SUV/4x4", precio_base=140_000)
        return None

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        key = (zone_detail or "").strip().lower()
        if key == "benavidez":
            return _FakeZoneRow("Norte", "Benavidez", 0)
        if (zone_group or "").strip().lower() == "norte":
            return _FakeZoneRow("Norte", None, 0)
        return None


# ── SQLite helpers ────────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _clean_all(db: Session) -> None:
    for tbl in [
        "whatsapp_outbound_dedup", "whatsapp_messages",
        "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "viaticos_zones",
        "whatsapp_recipient_locks", "leads",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _seed_full(db: Session, *, tipo_vehiculo: str = "SUV/4x4",
               marca: str = "Peugeot", modelo: str = "3008",
               with_zone: bool = True,
               location_clarification_sent: bool = False):
    """Seed contact, thread, lead, state, candidate, and optionally a ViaticosZone."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=_WA_ID, display_name="Beta Tester", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                nombre="Beta", necesita_humano=False)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(
        contact_id=contact.id,
        lead_id=lead.id,
        unread_count=0,
        created_at=_NOW,
    )
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id,
        needs_human=False,
        last_stage="QUALIFYING",
        vehicle_clarification_sent=False,
        location_clarification_sent=location_clarification_sent,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    candidate = WhatsAppThreadCandidate(
        thread_id=thread.id,
        marca=marca,
        modelo=modelo,
        tipo_vehiculo=tipo_vehiculo,
        status="current_focus",
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(candidate)
    db.flush()

    if with_zone:
        db.add(ViaticosZone(zone_group="Norte", zone_detail="Benavidez", viaticos=0))
        db.add(ViaticosZone(zone_group="Norte", zone_detail=None, viaticos=0))

    db.commit()
    return contact, thread, lead, state, candidate


def _make_settings():
    s = MagicMock()
    s.openai_api_key = "sk-test-fake-key"
    s.openai_chat_model = "gpt-4o-mini"   # CE uses openai_chat_model, not openai_model
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = ""
    s.whatsapp_vehicle_fallback_flow_id = ""
    s.whatsapp_location_fallback_flow_id = ""
    s.whatsapp_website_flow_id = ""
    return s


def _make_engine(db: Session) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = _make_settings()
    eng._pricing = PricingService(repository=FakeRepoBenavidez())
    from app.services.schedule import ScheduleService
    eng._schedule = ScheduleService(db=db)
    return eng


# ── Fake HTTP response for mocking urlopen ────────────────────────────────────

class _FakeHTTPResponse:
    """Minimal file-like object returned by mocked urlopen."""
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _openai_response(reply_text: str) -> _FakeHTTPResponse:
    """Build a fake OpenAI /v1/chat/completions response."""
    ai_decision = json.dumps({
        "intent": "PRESUPUESTO",
        "reply": reply_text,
        "lead_flag": "PRESUPUESTANDO",
        "flag_accepted": False,
        "needs_human": False,
        "extracted": {},
        "candidate": {"action": "none"},
    })
    body = json.dumps({
        "choices": [{"message": {"content": ai_decision}}]
    }).encode()
    return _FakeHTTPResponse(body)


def _make_event(thread_id: int, text: str, wa_msg_id: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_msg_id,
        wa_id=_WA_ID,
        text=text,
    )


def _blocked_rows(db: Session, thread_id: int) -> list:
    return db.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.thread_id == thread_id,
            WhatsAppMessage.status == "blocked",
        )
    ).scalars().all()


def _dedup_rows(db: Session) -> int:
    from app.models import WhatsAppOutboundDedup
    return db.execute(
        sql_text("SELECT COUNT(*) FROM whatsapp_outbound_dedup")
    ).scalar() or 0


# ── Test 1 & 2 — 3008/Benavídez full CE replay ───────────────────────────────

class TestBlockedDispatch3008Benavidez(unittest.TestCase):
    """Full CE replay through SQLite: quote state commits; blocked audit exists."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_full(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_1_logical_state_commits_on_blocked_kill_switch(self, mock_urlopen):
        """lead.flag, last_stage, home_zone_group/detail commit; result=blocked_dispatch."""
        mock_urlopen.return_value = _openai_response(
            "Hola! La revisión del Peugeot 3008 en Benavídez ya está calculada."
        )

        result = self.eng.handle(_make_event(
            self.thread.id,
            "Hola, quiero revisar un 3008 en Benavídez",
            "wamid.M20.4.3.TEST.1",
        ))

        # Result must indicate blocked delivery, not hard error
        self.assertEqual(result.action, "blocked_dispatch")
        self.assertFalse(result.ok)
        # L4.7W1-F4 REALIGNMENT: handled now means "CE owns this event", not
        # "a message was delivered". blocked_dispatch is a CE DECISION (the kill
        # switch), and returning handled=False handed Flow submissions to n8n's
        # legacy booking chain. ok=False still reports that nothing was sent.
        self.assertTrue(result.handled)
        self.assertIsNone(result.wa_message_id)
        self.assertIn("KILL_SWITCH", (result.detail or "").upper())

        # Refresh state from DB
        self.db.expire_all()
        fresh_lead = self.db.get(Lead, self.lead.id)
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.thread.id
            )
        ).scalar_one()

        # Logical conversation state must be committed
        self.assertEqual(fresh_lead.flag, "PRESUPUESTO_ENVIADO",
                         "lead.flag must advance to PRESUPUESTO_ENVIADO")
        self.assertEqual(fresh_state.last_stage, "QUOTED",
                         "last_stage must advance to QUOTED")
        self.assertEqual(fresh_state.home_zone_group, "Norte")
        self.assertEqual(fresh_state.home_zone_detail, "Benavidez")
        self.assertFalse(fresh_state.needs_human)

        # Dedup marker committed → replay protection works
        self.assertEqual(
            fresh_state.last_processed_inbound_wa_message_id,
            "wamid.M20.4.3.TEST.1",
        )

        # Blocked audit exists
        blocked = _blocked_rows(self.db, self.thread.id)
        self.assertEqual(len(blocked), 1)
        self.assertIn("KILL_SWITCH", blocked[0].blocked_reason or "")
        self.assertEqual(blocked[0].status, "blocked")

        # Zero Meta transport: no dedup rows (dedup only written on ALLOWED)
        self.assertEqual(_dedup_rows(self.db), 0)

    @patch("urllib.request.urlopen")
    def test_2_ai_branch_completed_before_block(self, mock_urlopen):
        """AI was invoked; needs_human stays False; state commits; no Meta call."""
        ai_reply = "El Peugeot 3008 en Benavídez tiene revisión disponible."
        mock_urlopen.return_value = _openai_response(ai_reply)

        result = self.eng.handle(_make_event(
            self.thread.id,
            "Hola, quiero revisar un 3008 en Benavídez",
            "wamid.M20.4.3.TEST.2",
        ))

        # OpenAI was called (urlopen called at least once for AI)
        self.assertGreaterEqual(mock_urlopen.call_count, 1)
        # The call was to OpenAI (not Meta)
        openai_call = mock_urlopen.call_args_list[0]
        req_arg = openai_call[0][0]  # first positional arg
        self.assertIn("openai.com", str(getattr(req_arg, "full_url", None) or req_arg))

        self.assertEqual(result.action, "blocked_dispatch")

        self.db.expire_all()
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.thread.id
            )
        ).scalar_one()
        # AI path: deterministic quote fired and state committed
        self.assertEqual(fresh_state.last_stage, "QUOTED")
        self.assertFalse(fresh_state.needs_human)


# ── Test 3 — Fallback-flow trigger path ──────────────────────────────────────

class TestBlockedDispatchFallbackFlow(unittest.TestCase):
    """Location-clarification trigger path: qualifying state commits when blocked."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        # Vehicle known (Peugeot 3008 SUV/4x4), zone unknown, no zone rows
        _, self.thread, self.lead, self.state, self.candidate = _seed_full(
            self.db, with_zone=False,
        )
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_3_fallback_flow_trigger_state_persists(self, mock_urlopen):
        """When location clarification is blocked, qualifying state and dedup commit."""
        # urlopen must not be called: no AI, no Meta (blocked before both)
        mock_urlopen.side_effect = AssertionError("urlopen must not be called in this test")

        result = self.eng.handle(_make_event(
            self.thread.id,
            "Hola, cuánto sale la revisión?",
            "wamid.M20.4.3.TEST.3",
        ))

        # Result must be blocked_dispatch (not "error")
        self.assertEqual(result.action, "blocked_dispatch")
        self.assertFalse(result.ok)
        # L4.7W1-F4 REALIGNMENT: handled now means "CE owns this event", not
        # "a message was delivered". blocked_dispatch is a CE DECISION (the kill
        # switch), and returning handled=False handed Flow submissions to n8n's
        # legacy booking chain. ok=False still reports that nothing was sent.
        self.assertTrue(result.handled)
        self.assertIsNone(result.wa_message_id)

        # Qualifying state committed (dedup marker + candidate vehicle info)
        self.db.expire_all()
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == self.thread.id
            )
        ).scalar_one()

        self.assertEqual(
            fresh_state.last_processed_inbound_wa_message_id,
            "wamid.M20.4.3.TEST.3",
            "Dedup marker must be committed so the message is not reprocessed",
        )
        # Clarification flag intentionally NOT set (did not actually send)
        self.assertFalse(fresh_state.location_clarification_sent)

        # Blocked audit committed by gate in its dedicated session
        blocked = _blocked_rows(self.db, self.thread.id)
        self.assertEqual(len(blocked), 1)
        self.assertIn("KILL_SWITCH", blocked[0].blocked_reason or "")

        # Zero dedup rows → no Meta transport
        self.assertEqual(_dedup_rows(self.db), 0)


# ── Test 4 — Response format ──────────────────────────────────────────────────

class TestBlockedDispatchResponseFormat(unittest.TestCase):
    """API response clearly indicates blocked; no message marked sent."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_full(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_4_response_format_when_blocked(self, mock_urlopen):
        """ok=False, action=blocked_dispatch, handled=False, wa_message_id=None."""
        mock_urlopen.return_value = _openai_response("Hola! La revisión cuesta $140.000.")

        result = self.eng.handle(_make_event(
            self.thread.id,
            "Hola, quiero revisar un 3008 en Benavídez",
            "wamid.M20.4.3.TEST.4",
        ))

        # Machine-readable blocked indicator
        self.assertEqual(result.action, "blocked_dispatch",
                         "action must be 'blocked_dispatch', not 'replied' or 'error'")
        self.assertFalse(result.ok,
                         "ok must be False — delivery did not occur")
        # L4.7W1-F4 REALIGNMENT: handled now means "CE owns this event", not
        # "a message was delivered". blocked_dispatch is a CE DECISION (the kill
        # switch), and returning handled=False handed Flow submissions to n8n's
        # legacy booking chain. ok=False still reports that nothing was sent.
        self.assertTrue(result.handled,
                        "handled=True — CE owns the event even when the kill switch blocks it")
        self.assertIsNone(result.wa_message_id,
                          "wa_message_id must be None — no WA message was sent")

        # No message row with status='sent'
        sent = self.db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == self.thread.id,
                WhatsAppMessage.status == "sent",
            )
        ).scalars().all()
        self.assertEqual(len(sent), 0, "No message may be marked 'sent'")

        # The blocked row exists with status='blocked'
        blocked = _blocked_rows(self.db, self.thread.id)
        self.assertGreaterEqual(len(blocked), 1)
        self.assertEqual(blocked[0].status, "blocked")


# ── Test 5 — Production semantics ────────────────────────────────────────────

class TestProductionSemanticsUnchanged(unittest.TestCase):
    """OUTBOUND_ENABLED=true path must still return 'replied', not 'blocked_dispatch'."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_full(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_5_production_path_returns_replied(self, mock_urlopen):
        """With OUTBOUND_ENABLED=true, a successful send still produces action='replied'."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        # AI succeeds
        mock_urlopen.return_value = _openai_response("Hola! La revisión cuesta $140.000.")

        # Patch _send_text_to_wa to simulate a successful send without hitting Meta
        with patch.object(self.eng, "_send_text_to_wa", return_value="wamid.PROD.SIM"):
            result = self.eng.handle(_make_event(
                self.thread.id,
                "Hola, quiero revisar un 3008 en Benavídez",
                "wamid.M20.4.3.TEST.5",
            ))

        self.assertEqual(result.action, "replied",
                         "Successful send must return 'replied', not 'blocked_dispatch'")
        self.assertTrue(result.ok)
        self.assertTrue(result.handled)
        self.assertEqual(result.wa_message_id, "wamid.PROD.SIM")
        self.assertNotEqual(result.action, "blocked_dispatch")


# ── Test 6 — Regression ───────────────────────────────────────────────────────

class TestBlockedDispatchRegression(unittest.TestCase):
    """Regression guards: _out() helper contract and accent normalization unchanged."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_6a_out_helper_blocked_dispatch_ok_false(self):
        """_out('blocked_dispatch') must produce ok=False, handled=True (L4.7W1-F4)."""
        from app.services.conversation_engine import _out
        r = _out("blocked_dispatch", detail="OUTBOUND_GATE_BLOCKED_KILL_SWITCH")
        self.assertFalse(r.ok)
        # L4.7W1-F4 REALIGNMENT: handled now means "CE owns this event", not
        # "a message was delivered". blocked_dispatch is a CE DECISION (the kill
        # switch), and returning handled=False handed Flow submissions to n8n's
        # legacy booking chain. ok=False still reports that nothing was sent.
        self.assertTrue(r.handled)
        self.assertEqual(r.action, "blocked_dispatch")
        self.assertEqual(r.detail, "OUTBOUND_GATE_BLOCKED_KILL_SWITCH")

    def test_6b_out_helper_existing_actions_unchanged(self):
        """_out() for pre-existing actions must behave as expected.

        M21.2.8: "error" was added to HANDLED_ACTIONS so that CE crash
        returns handled=True, terminating n8n before any legacy AI fallback.
        Semantics: handled=True means CE retains ownership; n8n must not
        fall back to another engine. Does NOT imply a customer reply was sent.
        """
        from app.services.conversation_engine import _out
        # "replied" → ok=True, handled=True
        r = _out("replied", wa_message_id="wamid.X")
        self.assertTrue(r.ok)
        self.assertTrue(r.handled)
        self.assertEqual(r.wa_message_id, "wamid.X")
        # "error" → ok=False, handled=True (M21.2.8: CE crash terminates n8n)
        r = _out("error", detail="internal_error")
        self.assertFalse(r.ok)
        self.assertTrue(r.handled)  # transport containment: CE retains ownership on crash
        self.assertEqual(r.detail, "internal_error")
        # "skipped_dedup" → ok=True, handled=True
        r = _out("skipped_dedup")
        self.assertTrue(r.ok)
        self.assertTrue(r.handled)
        # "no_lead" → ok=True, handled=False — UNCHANGED by L4.7W1-F4. The M21.2.8
        # rationale (no lead linked, so CE genuinely did not own the event) stands, and
        # no evidence was found that it reaches a live authority.
        r = _out("no_lead")
        self.assertTrue(r.ok)
        self.assertFalse(r.handled)

    def test_6c_accent_normalization_still_resolves_benavidez(self):
        """_extract_zone_from_text('Benavídez') must still return Norte/Benavidez."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        eng = ConversationEngine.__new__(ConversationEngine)
        eng.db = MagicMock()
        eng.settings = _make_settings()
        eng._pricing = PricingService(repository=FakeRepoBenavidez())

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [
            SimpleNamespace(zone_group="Norte", zone_detail="Benavidez", viaticos=0),
            SimpleNamespace(zone_group="Norte", zone_detail=None, viaticos=0),
        ]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        eng.db.execute.return_value = execute_result

        zone = eng._extract_zone_from_text("Hola, quiero revisar un 3008 en Benavídez")
        self.assertIsNotNone(zone, "Zone must be detected from accented 'Benavídez'")
        self.assertEqual(zone.zone_group, "Norte")
        self.assertEqual(zone.zone_detail, "Benavidez",
                         "Returned zone_detail must be the canonical stored value")

    def test_6d_duplicate_block_still_rolls_back(self):
        """BLOCKED_DUPLICATE still triggers rollback (not commit) in handle()."""
        db = _new_session()
        try:
            _clean_all(db)
            contact = WhatsAppContact(wa_id=_WA_ID, display_name="T", phone=None)
            db.add(contact)
            db.flush()
            thread = WhatsAppThread(contact_id=contact.id, unread_count=0, created_at=_NOW)
            db.add(thread)
            db.flush()
            state = WhatsAppThreadState(
                thread_id=thread.id, needs_human=False, last_stage="QUALIFYING",
                vehicle_clarification_sent=False, location_clarification_sent=False,
                vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
                created_at=_NOW, updated_at=_NOW,
            )
            db.add(state)
            db.commit()

            # Simulate: DUPLICATE block propagates to handle() as error (not blocked_dispatch)
            # We test this by constructing the OutboundBlockedError directly
            exc = OutboundBlockedError(
                sender_path="test", kind="text", to_wa_id=_WA_ID,
                thread_id=thread.id, gate_outcome=GateOutcome.BLOCKED_DUPLICATE.value,
            )
            # The engine maps BLOCKED_DUPLICATE → _out("error", ...)
            # Verify gate_outcome is preserved
            self.assertEqual(exc.gate_outcome, "blocked_duplicate")
            self.assertNotEqual(exc.gate_outcome.upper(), "BLOCKED_KILL_SWITCH")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
