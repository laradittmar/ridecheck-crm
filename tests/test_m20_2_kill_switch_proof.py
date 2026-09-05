"""M20.2 — Kill Switch Proof.

Proves that RideCheck's OutboundSafetyGate correctly blocks all outbound
WhatsApp traffic when OUTBOUND_ENABLED is absent or not exactly 'true', and
that the kill switch is the ONLY reason sends fail in the test environment.

Test index:
  RC45 — Text outbound is blocked when OUTBOUND_ENABLED=false
  RC46 — Flow outbound is blocked when OUTBOUND_ENABLED=false
  RC47 — State flags are not falsely committed when outbound is blocked
  RC48 — Outbound succeeds (mocked) when OUTBOUND_ENABLED=true; dedup works

Architecture context (M21.0.0 audit):
  n8n → POST /api/conversation/handle → CE → OutboundSafetyGate → Meta API
  Kill switch is inside CE's OutboundSafetyGate. OUTBOUND_ENABLED must equal
  the literal string 'true' — any other value blocks all sends.

All tests are fully offline: SQLite in-memory, no containers, no Meta API.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory ─────────────────────────────────────────────
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


# ── Stub app.db ───────────────────────────────────────────────────────────────
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

# ── Stub optional heavy deps ──────────────────────────────────────────────────
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── Import models ─────────────────────────────────────────────────────────────
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

# Use the models' registered MetaData so tables are always created correctly
# regardless of which test file was imported first by pytest.
Lead.__table__.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.repositories.pricing_repository import BasePriceRow
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
from app.services.pricing import PricingService

# ── Shared constants ──────────────────────────────────────────────────────────
_WA_ID = "5491100000099"
_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)


# ── Fake pricing repository ───────────────────────────────────────────────────

@dataclass
class _FakeZoneRow:
    zone_group: str
    zone_detail: Optional[str]
    viaticos: int


class _FakePricingRepo:
    """SUV/4x4 → $140,000; Norte/Benavidez → viáticos $0."""

    def find_base_price(self, tipo_vehiculo: str) -> Optional[BasePriceRow]:
        if tipo_vehiculo == "SUV/4x4":
            return BasePriceRow(tipo_vehiculo="SUV/4x4", precio_base=140_000)
        return None

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        if (zone_detail or "").strip().lower() == "benavidez":
            return _FakeZoneRow("Norte", "Benavidez", 0)
        if (zone_group or "").strip().lower() == "norte":
            return _FakeZoneRow("Norte", None, 0)
        return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _clean_all(db: Session) -> None:
    for tbl in [
        "whatsapp_outbound_dedup", "whatsapp_messages",
        "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "viaticos_zones", "leads",
        "whatsapp_recipient_locks",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _seed(
    db: Session,
    *,
    vehicle_known: bool = True,
    zone_known: bool = False,
    vehicle_clarification_sent: bool = False,
    location_clarification_sent: bool = False,
    location_fallback_flow_sent: bool = False,
    vehicle_fallback_flow_sent: bool = False,
    needs_human: bool = False,
    last_stage: str = "QUALIFYING",
):
    """Seed contact, thread, lead, state, optional candidate and zone."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=_WA_ID, display_name="Kill Switch Test", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                nombre="Test", necesita_humano=needs_human)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(
        contact_id=contact.id, lead_id=lead.id,
        unread_count=0, created_at=_NOW,
    )
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id,
        needs_human=needs_human,
        last_stage=last_stage,
        vehicle_clarification_sent=vehicle_clarification_sent,
        location_clarification_sent=location_clarification_sent,
        vehicle_fallback_flow_sent=vehicle_fallback_flow_sent,
        location_fallback_flow_sent=location_fallback_flow_sent,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    if vehicle_known:
        db.add(WhatsAppThreadCandidate(
            thread_id=thread.id,
            marca="Peugeot", modelo="3008",
            tipo_vehiculo="SUV/4x4",
            status="current_focus",
            created_at=_NOW, updated_at=_NOW,
        ))

    if zone_known:
        db.add(ViaticosZone(zone_group="Norte", zone_detail="Benavidez", viaticos=0))
        db.add(ViaticosZone(zone_group="Norte", zone_detail=None, viaticos=0))

    db.commit()
    return thread, state, lead


def _make_settings(*, location_flow_id: str = "", vehicle_flow_id: str = ""):
    s = MagicMock()
    s.openai_api_key = "sk-test-fake"
    s.openai_chat_model = "gpt-4o-mini"
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = ""
    s.whatsapp_vehicle_fallback_flow_id = vehicle_flow_id
    s.whatsapp_location_fallback_flow_id = location_flow_id
    s.whatsapp_website_flow_id = ""
    return s


def _make_engine(db: Session, **settings_kwargs) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = _make_settings(**settings_kwargs)
    eng._pricing = PricingService(repository=_FakePricingRepo())
    from app.services.schedule import ScheduleService
    eng._schedule = ScheduleService(db=db)
    return eng


def _make_event(thread_id: int, text: str, wa_msg_id: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_msg_id,
        wa_id=_WA_ID,
        text=text,
        unanswered_recent_user_messages=[text],
    )


def _sent_count(db: Session) -> int:
    return db.execute(sql_text("SELECT COUNT(*) FROM whatsapp_messages WHERE status='sent'")).scalar() or 0


def _blocked_count(db: Session, thread_id: int) -> int:
    return len(db.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.thread_id == thread_id,
            WhatsAppMessage.status == "blocked",
        )
    ).scalars().all())


# ══════════════════════════════════════════════════════════════════════════════
# RC45 — Text outbound blocked when OUTBOUND_ENABLED=false
# ══════════════════════════════════════════════════════════════════════════════

class TestRC45TextOutboundBlocked(unittest.TestCase):
    """CE reaches the text-clarification path and the kill switch blocks it.

    Scenario: vehicle unknown (message has no vehicle name, no candidate with
    tipo_vehiculo resolved from text), QUALIFYING stage.
    CE tries to send: "Para cotizarte la revisión necesito saber qué vehículo tenés..."
    Kill switch fires before the Meta send.

    Note: we use vehicle_known=False (no candidate) so CE reaches the vehicle
    clarification text path deterministically, without an AI call.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.thread, self.state, self.lead = _seed(self.db, vehicle_known=False)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_rc45a_action_is_blocked_dispatch(self):
        """CE returns action=blocked_dispatch when OUTBOUND_ENABLED is absent."""
        result = self.eng.handle(_make_event(
            self.thread.id, "Hola, cuánto sale revisar un auto?", "wamid.rc45.a"
        ))
        self.assertEqual(result.action, "blocked_dispatch")

    def test_rc45b_ok_false_handled_false(self):
        """ok=False and handled=False — kill switch is not a successful delivery."""
        result = self.eng.handle(_make_event(
            self.thread.id, "Hola, cuánto sale revisar un auto?", "wamid.rc45.b"
        ))
        self.assertFalse(result.ok)
        # L4.7W1-F4: handled=True — CE owns a kill-switch decision.
        self.assertTrue(result.handled)
        self.assertIsNone(result.wa_message_id)

    def test_rc45c_kill_switch_detail_in_response(self):
        """detail field contains KILL_SWITCH — caller can distinguish from other blocks."""
        result = self.eng.handle(_make_event(
            self.thread.id, "Hola, cuánto sale revisar un auto?", "wamid.rc45.c"
        ))
        self.assertIn("KILL_SWITCH", (result.detail or "").upper())

    def test_rc45d_no_sent_message_row(self):
        """No WhatsApp message with status=sent exists after blocked delivery."""
        self.eng.handle(_make_event(
            self.thread.id, "Hola, cuánto sale revisar un auto?", "wamid.rc45.d"
        ))
        self.assertEqual(_sent_count(self.db), 0,
                         "No message may be marked 'sent' when kill switch fires")

    def test_rc45e_blocked_audit_row_committed(self):
        """A blocked audit record IS committed — it proves the gate evaluated the send."""
        self.eng.handle(_make_event(
            self.thread.id, "Hola, cuánto sale revisar un auto?", "wamid.rc45.e"
        ))
        self.db.expire_all()
        self.assertGreaterEqual(
            _blocked_count(self.db, self.thread.id), 1,
            "Gate must commit a blocked audit record",
        )

    def test_rc45f_meta_urlopen_never_called(self):
        """urlopen is never called for Meta's Graph API during a kill-switch block."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = AssertionError(
                "urlopen must not be called when kill switch is active"
            )
            # If this doesn't raise, no network call was attempted.
            result = self.eng.handle(_make_event(
                self.thread.id, "Hola, cuánto sale revisar un auto?", "wamid.rc45.f"
            ))
        self.assertEqual(result.action, "blocked_dispatch")


# ══════════════════════════════════════════════════════════════════════════════
# RC46 — Flow outbound blocked when OUTBOUND_ENABLED=false
# ══════════════════════════════════════════════════════════════════════════════

class TestRC46FlowOutboundBlocked(unittest.TestCase):
    """CE reaches the Location Flow dispatch path and the kill switch blocks it.

    Scenario: vehicle known (SUV/4x4 candidate), no zone resolved,
    location_clarification_sent=True → CE would dispatch Location Flow.
    Kill switch fires before _send_flow_button is called.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        # location_clarification already sent once, so next turn is Flow dispatch
        self.thread, self.state, self.lead = _seed(
            self.db,
            vehicle_known=True,
            zone_known=False,
            location_clarification_sent=True,
        )
        # Configure a fake flow ID so CE doesn't fall through to AI
        self.eng = _make_engine(self.db, location_flow_id="fake-loc-flow-id-test")

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_rc46a_flow_dispatch_returns_blocked_dispatch(self):
        """CE returns blocked_dispatch when a Flow dispatch is killed."""
        result = self.eng.handle(_make_event(
            self.thread.id, "El auto está en Villa Urquiza", "wamid.rc46.a"
        ))
        self.assertEqual(result.action, "blocked_dispatch")
        # L4.7W1-F4: handled=True — CE owns a kill-switch decision.
        self.assertTrue(result.handled)

    def test_rc46b_send_flow_button_never_called(self):
        """_send_flow_button is never called — kill switch fires before it."""
        with patch.object(self.eng, "_send_flow_button") as mock_flow:
            mock_flow.side_effect = AssertionError("_send_flow_button must not be called")
            result = self.eng.handle(_make_event(
                self.thread.id, "El auto está en Villa Urquiza", "wamid.rc46.b"
            ))
        # If we reach here, _send_flow_button was not called.
        self.assertEqual(result.action, "blocked_dispatch")

    def test_rc46c_no_sent_message_row(self):
        """No sent message row — Flow was not dispatched."""
        self.eng.handle(_make_event(
            self.thread.id, "El auto está en Villa Urquiza", "wamid.rc46.c"
        ))
        self.assertEqual(_sent_count(self.db), 0)

    def test_rc46d_blocked_audit_committed(self):
        """Blocked audit record committed even when a Flow (not text) is blocked."""
        self.eng.handle(_make_event(
            self.thread.id, "El auto está en Villa Urquiza", "wamid.rc46.d"
        ))
        self.db.expire_all()
        self.assertGreaterEqual(_blocked_count(self.db, self.thread.id), 1)

    def test_rc46e_meta_urlopen_never_called(self):
        """No HTTP call to Meta is made when Flow dispatch is killed."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = AssertionError(
                "urlopen must not be called during kill-switch Flow block"
            )
            result = self.eng.handle(_make_event(
                self.thread.id, "El auto está en Villa Urquiza", "wamid.rc46.e"
            ))
        self.assertEqual(result.action, "blocked_dispatch")


# ══════════════════════════════════════════════════════════════════════════════
# RC47 — State flags not falsely advanced when outbound is blocked
# ══════════════════════════════════════════════════════════════════════════════

class TestRC47StateFlagsNotFalselyAdvanced(unittest.TestCase):
    """The kill switch must not falsely advance state flags that represent
    'the customer received this message'.

    vehicle_clarification_sent and location_clarification_sent are only set
    to True AFTER the Meta send succeeds. When the kill switch fires before
    the send, these flags must remain False.

    The last_stage and lead.flag fields represent logical conversation state
    (what the bot computed) and may commit separately — this test focuses on
    the delivery-confirmation flags.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_rc47a_vehicle_clarification_sent_not_committed_when_blocked(self):
        """vehicle_clarification_sent stays False — bot did not deliver the clarification."""
        thread, state, _ = _seed(self.db, vehicle_known=False, zone_known=False,
                                  vehicle_clarification_sent=False)
        eng = _make_engine(self.db)

        result = eng.handle(_make_event(
            thread.id, "Hola, cuánto sale?", "wamid.rc47.a"
        ))

        self.assertEqual(result.action, "blocked_dispatch")
        self.db.expire_all()
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertFalse(
            fresh_state.vehicle_clarification_sent,
            "vehicle_clarification_sent must not be True — "
            "the clarification message was never delivered",
        )

    def test_rc47b_location_clarification_sent_not_committed_when_blocked(self):
        """location_clarification_sent stays False — bot did not deliver location ask."""
        thread, state, _ = _seed(self.db, vehicle_known=True, zone_known=False,
                                  location_clarification_sent=False)
        eng = _make_engine(self.db)

        result = eng.handle(_make_event(
            thread.id, "Hola, cuánto sale?", "wamid.rc47.b"
        ))

        self.assertEqual(result.action, "blocked_dispatch")
        self.db.expire_all()
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertFalse(
            fresh_state.location_clarification_sent,
            "location_clarification_sent must not be True — "
            "the location-ask message was never delivered",
        )

    def test_rc47c_location_fallback_flow_sent_not_committed_when_blocked(self):
        """location_fallback_flow_sent stays False — Flow was not dispatched."""
        thread, state, _ = _seed(self.db, vehicle_known=True, zone_known=False,
                                  location_clarification_sent=True,
                                  location_fallback_flow_sent=False)
        eng = _make_engine(self.db, location_flow_id="fake-loc-flow-id")

        result = eng.handle(_make_event(
            thread.id, "El auto está en Villa Urquiza", "wamid.rc47.c"
        ))

        self.assertEqual(result.action, "blocked_dispatch")
        self.db.expire_all()
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertFalse(
            fresh_state.location_fallback_flow_sent,
            "location_fallback_flow_sent must not be True — Flow was never sent",
        )

    @patch("urllib.request.urlopen")
    def test_rc47d_last_stage_does_not_advance_to_commercial_stage(self, mock_urlopen):
        """last_stage does not reach QUOTED/SCHEDULING/BOOKED when outbound is blocked.

        The kill switch fires before any commercial state advance. We block urlopen
        to prevent a real OpenAI call that would add noise and non-determinism.
        CE may reach any non-commercial stage (QUALIFYING, HUMAN_REQUIRED, etc.).
        """
        mock_urlopen.side_effect = OSError("network blocked in test")

        thread, state, _ = _seed(self.db, vehicle_known=False, zone_known=False,
                                  last_stage="QUALIFYING")
        eng = _make_engine(self.db)

        eng.handle(_make_event(thread.id, "Hola, cuánto sale?", "wamid.rc47.d"))

        self.db.expire_all()
        fresh_state = self.db.execute(
            select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread.id)
        ).scalar_one()
        self.assertNotIn(
            fresh_state.last_stage, ("QUOTED", "SCHEDULING", "BOOKED"),
            "last_stage must not advance to a commercial stage when "
            "the kill switch blocked all outbound",
        )

    def test_rc47e_no_sent_message_in_any_blocked_scenario(self):
        """No message is ever marked 'sent' across blocked text and flow paths."""
        scenarios = [
            dict(vehicle_known=False, zone_known=False),
            dict(vehicle_known=True,  zone_known=False,
                 location_clarification_sent=True,
                 location_flow_id="fake-flow"),
        ]
        for i, sc in enumerate(scenarios):
            with self.subTest(scenario=i):
                _clean_all = lambda: [
                    self.db.execute(sql_text(f"DELETE FROM {t}")) or None
                    for t in [
                        "whatsapp_outbound_dedup","whatsapp_messages",
                        "whatsapp_thread_candidates","whatsapp_thread_states",
                        "whatsapp_threads","whatsapp_contacts","viaticos_zones","leads",
                    ]
                ]
                _clean_all()
                self.db.commit()

                flow_id = sc.pop("location_flow_id", "")
                thread, _, _ = _seed(self.db, **sc)
                eng = _make_engine(self.db, location_flow_id=flow_id)
                eng.handle(_make_event(thread.id, "Hola", f"wamid.rc47.e.{i}"))

                self.assertEqual(
                    _sent_count(self.db), 0,
                    f"Scenario {i}: no message may be marked sent",
                )


# ══════════════════════════════════════════════════════════════════════════════
# RC48 — Outbound works inside the mock when OUTBOUND_ENABLED=true
# ══════════════════════════════════════════════════════════════════════════════

class TestRC48OutboundWorksWhenEnabled(unittest.TestCase):
    """Prove that the kill switch is the ONLY reason sends fail.

    When OUTBOUND_ENABLED=true and the Meta send is mocked:
      - CE returns action='replied' (not blocked_dispatch)
      - The mocked dispatch is called exactly once
      - Replaying the same inbound wa_message_id triggers skipped_dedup
        (inbound dedup) — CE does not send a second time for the same message
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.thread, self.state, self.lead = _seed(
            self.db, vehicle_known=False, zone_known=False,
        )
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_rc48a_replied_when_enabled_and_mocked(self):
        """With OUTBOUND_ENABLED=true and _send_text_to_wa mocked, action=replied."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        with patch.object(self.eng, "_send_text_to_wa", return_value="wamid.rc48.mock.1"):
            result = self.eng.handle(_make_event(
                self.thread.id, "Hola, cuánto sale?", "wamid.rc48.inbound.a"
            ))

        self.assertEqual(result.action, "replied",
                         "Successful mocked send must return action='replied'")
        self.assertTrue(result.ok)
        self.assertTrue(result.handled)
        self.assertEqual(result.wa_message_id, "wamid.rc48.mock.1")

    def test_rc48b_send_called_exactly_once(self):
        """_send_text_to_wa is called exactly once per message turn."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        with patch.object(self.eng, "_send_text_to_wa", return_value="wamid.rc48.mock.2") as mock_send:
            self.eng.handle(_make_event(
                self.thread.id, "Hola, cuánto sale?", "wamid.rc48.inbound.b"
            ))

        self.assertEqual(mock_send.call_count, 1,
                         "_send_text_to_wa must be called exactly once per turn")

    def test_rc48c_inbound_dedup_prevents_second_send(self):
        """Replaying the same wa_message_id returns skipped_dedup — no second send."""
        os.environ["OUTBOUND_ENABLED"] = "true"
        wa_msg_id = "wamid.rc48.inbound.c"

        with patch.object(self.eng, "_send_text_to_wa", return_value="wamid.rc48.mock.3") as mock_send:
            result1 = self.eng.handle(_make_event(
                self.thread.id, "Hola, cuánto sale?", wa_msg_id
            ))
            # Replay the exact same inbound message_id
            result2 = self.eng.handle(_make_event(
                self.thread.id, "Hola, cuánto sale?", wa_msg_id
            ))

        self.assertEqual(result1.action, "replied")
        self.assertEqual(result2.action, "skipped_dedup",
                         "Second send of same wa_message_id must be deduped")
        self.assertEqual(mock_send.call_count, 1,
                         "_send_text_to_wa must only be called once (dedup prevents replay)")

    def test_rc48d_blocked_dispatch_not_returned_when_enabled(self):
        """blocked_dispatch must NOT appear when OUTBOUND_ENABLED=true (kill switch is off)."""
        os.environ["OUTBOUND_ENABLED"] = "true"

        with patch.object(self.eng, "_send_text_to_wa", return_value="wamid.rc48.mock.4"):
            result = self.eng.handle(_make_event(
                self.thread.id, "Hola, cuánto sale?", "wamid.rc48.inbound.d"
            ))

        self.assertNotEqual(
            result.action, "blocked_dispatch",
            "blocked_dispatch must not appear when OUTBOUND_ENABLED=true",
        )

    def test_rc48e_no_real_network_call_possible(self):
        """The test environment can block all real network calls.

        With OUTBOUND_ENABLED=true but all urlopen mocked to raise, CE still
        returns the expected action because _send_text_to_wa is separately mocked.
        This proves the test is isolated from real network access.
        """
        os.environ["OUTBOUND_ENABLED"] = "true"

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = AssertionError("real network must not be called")
            with patch.object(self.eng, "_send_text_to_wa", return_value="wamid.rc48.mock.5"):
                result = self.eng.handle(_make_event(
                    self.thread.id, "Hola, cuánto sale?", "wamid.rc48.inbound.e"
                ))

        self.assertEqual(result.action, "replied",
                         "Test must succeed with real network blocked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
