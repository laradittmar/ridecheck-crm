"""M20.6B.1 — Acceptance reply must not repeat the quote.

Root cause tested:
  _is_acceptance() used exact-match only. "Si avancemos" / "Sí, avancemos" are
  two-word combinations not present verbatim in _ACCEPTANCE_KEYWORDS, so the
  function returned False, the CE fell through to the AI path, and the AI
  included the price in the acceptance-confirmation reply.

Fix applied:
  After the exact-match fails, _is_acceptance() now checks whether every
  individual word (after stripping punctuation) is itself an acceptance keyword.
  "Si avancemos" → ["si", "avancemos"] → both in keywords → True.

Test index:
  1.  _is_acceptance unit — "Si avancemos" must return True.
  2.  _is_acceptance unit — "Sí, avancemos" must return True.
  3.  _is_acceptance unit — existing single keywords still work.
  4.  _is_acceptance unit — non-acceptance text still returns False.
  5.  Quote scenario (OUTBOUND=false): initial quote reply contains $ and
      viáticos; stage=QUOTED. (Existing quote behavior unchanged.)
  6.  Acceptance "Sí, avancemos" (OUTBOUND=false): stage=SCHEDULING;
      blocked reply contains no $, no "precio", no "viático".
  7.  Acceptance "Si avancemos" (OUTBOUND=false): same as 6.
  8.  Regression — duplicate/dedup protection unchanged.
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

Base.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.repositories.pricing_repository import BasePriceRow
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine, _is_acceptance
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
from app.services.pricing import PricingService

# ── Shared constants ──────────────────────────────────────────────────────────
_WA_ID = "5491153368330"
_NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


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


def _seed_qualifying(db: Session):
    """Seed a fresh QUALIFYING thread with Peugeot 3008 / no zone."""
    _clean_all(db)
    contact = WhatsAppContact(wa_id=_WA_ID, display_name="Beta Tester", phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                nombre="Beta", necesita_humano=False)
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(
        contact_id=contact.id, lead_id=lead.id,
        unread_count=0, created_at=_NOW,
    )
    db.add(thread)
    db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id, needs_human=False, last_stage="QUALIFYING",
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()
    candidate = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="3008",
        tipo_vehiculo="SUV/4x4", status="current_focus",
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(candidate)
    db.flush()
    db.add(ViaticosZone(zone_group="Norte", zone_detail="Benavidez", viaticos=0))
    db.add(ViaticosZone(zone_group="Norte", zone_detail=None, viaticos=0))
    db.commit()
    return contact, thread, lead, state, candidate


def _seed_quoted(db: Session):
    """Seed a thread already in QUOTED state: Peugeot 3008 / Norte/Benavidez / $140k."""
    contact, thread, lead, state, candidate = _seed_qualifying(db)
    # Advance to QUOTED with zone and price flag already set
    lead.flag = "PRESUPUESTO_ENVIADO"
    state.last_stage = "QUOTED"
    state.home_zone_group = "Norte"
    state.home_zone_detail = "Benavidez"
    candidate.zone_group = "Norte"
    candidate.zone_detail = "Benavidez"
    db.commit()
    return contact, thread, lead, state, candidate


def _make_settings():
    s = MagicMock()
    s.openai_api_key = "sk-test-fake-key"
    s.openai_chat_model = "gpt-4o-mini"
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


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _openai_quote_response() -> _FakeHTTPResponse:
    """AI reply for the initial quote turn."""
    ai_decision = json.dumps({
        "intent": "PRESUPUESTO",
        "reply": "Hola! La revisión del Peugeot 3008 en Benavídez ya está calculada.",
        "lead_flag": "PRESUPUESTANDO",
        "flag_accepted": False,
        "needs_human": False,
        "extracted": {"zone_detail": "Benavidez", "zone_group": "Norte"},
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
        recent_user_messages=[text],
        unanswered_recent_user_messages=[text],
    )


def _blocked_text(db: Session, thread_id: int) -> str:
    """Return the text of the most recent blocked outbound row for a thread."""
    rows = db.execute(
        select(WhatsAppMessage)
        .where(
            WhatsAppMessage.thread_id == thread_id,
            WhatsAppMessage.status == "blocked",
        )
        .order_by(WhatsAppMessage.id.desc())
    ).scalars().all()
    return rows[0].text if rows else ""


# ── Tests 1–4: _is_acceptance unit tests ─────────────────────────────────────

class TestIsAcceptanceUnit(unittest.TestCase):
    """Direct unit tests for the _is_acceptance() function."""

    def test_1_si_avancemos_no_accent(self):
        """'Si avancemos' (no accent) must return True — was the live bug trigger."""
        self.assertTrue(_is_acceptance(["Si avancemos"]))

    def test_2_si_avancemos_with_accent_and_comma(self):
        """'Sí, avancemos' (accent + comma) must return True."""
        self.assertTrue(_is_acceptance(["Sí, avancemos"]))

    def test_3_existing_single_keywords_unchanged(self):
        """Single-word acceptance keywords must still work."""
        for kw in ["dale", "ok", "sí", "avancemos", "listo", "claro", "perfecto"]:
            with self.subTest(kw=kw):
                self.assertTrue(_is_acceptance([kw]))

    def test_3b_existing_phrase_keywords_unchanged(self):
        """Multi-word acceptance phrases must still work."""
        for phrase in ["de acuerdo", "me sirve", "por supuesto", "quiero avanzar"]:
            with self.subTest(phrase=phrase):
                self.assertTrue(_is_acceptance([phrase]))

    def test_4_non_acceptance_returns_false(self):
        """Text that is NOT acceptance must return False."""
        for msg in [
            "hola",
            "quiero revisar un 3008",
            "hola si",           # "hola" is not an acceptance word
            "si te digo que si", # "te", "digo", "que" are not acceptance words
            "cuanto cuesta",
        ]:
            with self.subTest(msg=msg):
                self.assertFalse(_is_acceptance([msg]))


# ── Test 5: Initial quote scenario (unchanged behavior) ───────────────────────

class TestInitialQuoteUnchanged(unittest.TestCase):
    """Verify the initial quote reply still includes price / viáticos / QUOTED stage."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_qualifying(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_5_quote_reply_contains_price_and_stage_is_quoted(self, mock_urlopen):
        """Initial quote: blocked text must contain $140.000, viáticos $0; stage=QUOTED."""
        mock_urlopen.return_value = _openai_quote_response()

        result = self.eng.handle(_make_event(
            self.thread.id,
            "Hola, quiero revisar un 3008 en Benavídez",
            "wamid.M20.6B1.TEST.5",
        ))

        self.db.expire_all()
        self.db.refresh(self.lead)
        self.db.refresh(self.state)

        self.assertEqual(result.action, "blocked_dispatch",
                         f"Expected blocked_dispatch, got {result.action}")
        self.assertFalse(result.ok)

        # State must be QUOTED
        self.assertEqual(self.state.last_stage, "QUOTED",
                         f"Expected QUOTED, got {self.state.last_stage}")
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO")
        self.assertFalse(self.state.needs_human)

        # Zone committed
        self.assertEqual(self.state.home_zone_group, "Norte")
        self.assertEqual(self.state.home_zone_detail, "Benavidez")

        # Blocked reply must contain the price and viáticos
        text = _blocked_text(self.db, self.thread.id)
        self.assertIn("140", text,
                      f"Expected price in blocked text, got: {text!r}")
        self.assertIn("viático", text.lower(),
                      f"Expected viáticos in blocked text, got: {text!r}")


# ── Tests 6–7: Acceptance scenarios (core fix) ───────────────────────────────

class TestAcceptanceNoQuoteRepeat(unittest.TestCase):
    """After acceptance from QUOTED, reply must not repeat price or quote wording."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_quoted(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def _run_acceptance(self, text: str, wa_msg_id: str):
        """Run acceptance turn and return (result, reply_text)."""
        result = self.eng.handle(_make_event(self.thread.id, text, wa_msg_id))
        self.db.expire_all()
        self.db.refresh(self.lead)
        self.db.refresh(self.state)
        reply_text = _blocked_text(self.db, self.thread.id)
        return result, reply_text

    @patch("urllib.request.urlopen")
    def test_6_acceptance_si_avancemos_with_accent(self, mock_urlopen):
        """'Sí, avancemos' → SCHEDULING; reply has no $, no precio, no viático."""
        # AI should NOT be called for acceptance — if urlopen is called, it's a bug.
        mock_urlopen.side_effect = AssertionError("AI must NOT be called on acceptance path")

        result, reply_text = self._run_acceptance("Sí, avancemos", "wamid.M20.6B1.TEST.6")

        self.assertEqual(result.action, "blocked_dispatch",
                         f"Expected blocked_dispatch, got {result.action}")
        self.assertFalse(result.ok)

        # Stage advanced to SCHEDULING
        self.assertEqual(self.state.last_stage, "SCHEDULING",
                         f"Expected SCHEDULING, got {self.state.last_stage}")
        self.assertEqual(self.lead.flag, "ACEPTADO",
                         f"Expected ACEPTADO, got {self.lead.flag}")
        self.assertFalse(self.state.needs_human)

        # Forbidden content — no price, no quote wording
        lower = reply_text.lower()
        self.assertNotIn("$", reply_text,
                         f"Reply must not contain '$': {reply_text!r}")
        self.assertNotIn("precio", lower,
                         f"Reply must not contain 'precio': {reply_text!r}")
        self.assertNotIn("viático", lower,
                         f"Reply must not contain 'viático': {reply_text!r}")
        self.assertNotIn("viatico", lower,
                         f"Reply must not contain 'viatico': {reply_text!r}")
        self.assertNotIn("140", reply_text,
                         f"Reply must not contain '140': {reply_text!r}")
        self.assertNotIn("base", lower,
                         f"Reply must not contain base-price wording: {reply_text!r}")

        # Reply must ask for scheduling (day/time)
        self.assertTrue(
            any(kw in lower for kw in ["día", "horario", "fecha", "turno"]),
            f"Reply must ask for day/time: {reply_text!r}",
        )

    @patch("urllib.request.urlopen")
    def test_7_acceptance_si_avancemos_no_accent(self, mock_urlopen):
        """'Si avancemos' (no accent, live bug input) → SCHEDULING; no $ in reply."""
        mock_urlopen.side_effect = AssertionError("AI must NOT be called on acceptance path")

        result, reply_text = self._run_acceptance("Si avancemos", "wamid.M20.6B1.TEST.7")

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "SCHEDULING")
        self.assertEqual(self.lead.flag, "ACEPTADO")
        self.assertFalse(self.state.needs_human)
        self.assertNotIn("$", reply_text,
                         f"Reply must not contain '$': {reply_text!r}")
        self.assertNotIn("precio", reply_text.lower())
        self.assertNotIn("viático", reply_text.lower())
        self.assertNotIn("140", reply_text)


# ── Test 8: Regression — dedup/duplicate protection unchanged ─────────────────

class TestAcceptanceRegression(unittest.TestCase):
    """Duplicate-send protection must not be degraded by the acceptance fix."""

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_quoted(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_8_duplicate_message_id_is_skipped(self, mock_urlopen):
        """Re-sending same wa_message_id after acceptance is deduped (skipped)."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        wa_msg_id = "wamid.M20.6B1.TEST.8"

        # First send — should succeed as blocked_dispatch (acceptance)
        result1 = self.eng.handle(_make_event(self.thread.id, "Si avancemos", wa_msg_id))
        self.assertEqual(result1.action, "blocked_dispatch")

        # Re-open a fresh engine on the same db to avoid session state issues
        self.db.expire_all()
        eng2 = _make_engine(self.db)

        # Second send with same wa_message_id — must be deduped
        result2 = eng2.handle(_make_event(self.thread.id, "Si avancemos", wa_msg_id))
        self.assertIn(result2.action, ("skipped_dedup", "error"),
                      f"Duplicate wa_message_id must be skipped or error, got {result2.action}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
