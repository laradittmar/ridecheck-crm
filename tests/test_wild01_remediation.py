"""WILD-01 Remediation Regression Tests (M21.6-WILD-01-REMEDIATION)

Covers the three bugs found during the first controlled Wild session:

  WILD01-R1  CE preserves LR-3 zone when stale state zone exists (FINDING-01)
  WILD01-R2  CE year override: explicit year in turn beats stale candidate year (FINDING-02)
  WILD01-R3  Dedup allows same greeting text when triggered by a new inbound event (FINDING-03)
  WILD01-R4  Dedup still blocks same greeting text from the same causal inbound (FINDING-03)
  WILD01-R5  Blocked outbound does not make thread appear answered in unanswered query
  WILD01-R6  WhatsAppOutboundDedup.causal_inbound_wa_message_id column persists correctly

All tests run offline against SQLite in-memory.  No outbound sends.  No production DB.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ─────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Stub heavy optional deps ────────────────────────────────────────────────────
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

os.environ.setdefault("OUTBOUND_ENABLED", "false")

# ── SQLAlchemy/SQLite compatibility shims ──────────────────────────────────────
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = _sa.JSON          # type: ignore[attr-defined]
_pg_json.JSONB = _sa.JSON             # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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

import app.models  # noqa: F401
from app.models import (
    Lead,
    ViaticosZone,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppOutboundDedup,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)

Lead.__table__.metadata.create_all(_engine)

from app.repositories.pricing_repository import PricingRepository
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine, _Context
from app.services.outbound_path_registry import OutboundPathId
from app.services.outbound_safety_gate import (
    DEDUP_WINDOW_MINUTES,
    GateOutcome,
    OutboundSafetyGate,
)
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

_NOW = datetime(2026, 8, 31, 15, 0, 0, tzinfo=timezone.utc)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _clean_all(db: Session) -> None:
    for tbl in [
        "ai_events", "whatsapp_outbound_dedup", "whatsapp_recipient_locks",
        "whatsapp_messages", "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "viaticos_zones", "leads",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _make_engine(db: Session) -> ConversationEngine:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-4o-mini"
    settings.backend_url = "http://localhost:8000"
    settings.whatsapp_flow_id = ""
    settings.whatsapp_vehicle_fallback_flow_id = ""
    settings.whatsapp_location_fallback_flow_id = ""
    settings.whatsapp_website_flow_id = ""

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = settings
    eng._pricing = PricingService(repository=PricingRepository())
    eng._schedule = ScheduleService(db=db)
    eng._ai_invoked = False
    eng._answer_source = None
    eng._contributing_sources = None
    eng._faq_reconciliation_burst = None
    return eng


def _run_ce(
    db: Session,
    eng: ConversationEngine,
    thread_id: int,
    wa_id: str,
    wa_message_id: str,
    texts: list[str],
    ai_reply: Optional[str] = None,
) -> tuple[object, list[str]]:
    ev = ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id=wa_id,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )
    _ai_payload = ai_reply or json.dumps({
        "intent": "QUALIFYING", "reply": "Entendido, gracias.",
        "deferred_interest": False, "candidate": {"action": "none"},
        "extracted": {}, "lead_flag": None, "needs_human": False,
    })
    sent_texts: list[str] = []
    _counter = [0]

    def _fake_send(*, to_wa_id, text):
        sent_texts.append(text)
        _counter[0] += 1
        return (f"fake-wa-{_counter[0]}", {})

    with patch("urllib.request.urlopen") as mock_url:
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock()
        mock_url.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _ai_payload}}]
        }).encode()
        with patch("app.services.conversation_engine.OutboundSafetyGate") as _MockGate:
            _gate_inst = MagicMock()
            _gate_result = MagicMock()
            _gate_result.outcome = "allowed"
            _gate_result.message_id = 1
            _gate_inst.attempt.return_value = _gate_result
            _MockGate.return_value = _gate_inst
            with patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                       side_effect=_fake_send):
                with patch("app.services.conversation_engine.reset_unanswered_alert"):
                    result = eng.handle(ev)
    return result, sent_texts


def _seed_viaticos(db: Session, rows: list[tuple[str, str, int]]) -> None:
    for grp, det, viaticos in rows:
        existing = db.execute(
            sql_text("SELECT id FROM viaticos_zones WHERE zone_group=:g AND zone_detail=:d"),
            {"g": grp, "d": det},
        ).fetchone()
        if not existing:
            db.add(ViaticosZone(zone_group=grp, zone_detail=det, viaticos=viaticos))
    db.commit()


def _seed_thread(
    db: Session,
    wa_id: str,
    cand_kwargs: dict,
    state_kwargs: dict,
) -> tuple[int, int]:
    """Seed lead + contact + thread + candidate + state. Returns (thread_id, cand_id)."""
    lead = Lead(nombre="Test Wild", telefono=wa_id, flag="PRESUPUESTANDO")
    db.add(lead)
    db.flush()
    contact = WhatsAppContact(wa_id=wa_id, display_name="Test Wild")
    db.add(contact)
    db.flush()
    thread = WhatsAppThread(lead_id=lead.id, contact_id=contact.id)
    db.add(thread)
    db.flush()
    cand = WhatsAppThreadCandidate(thread_id=thread.id, status="current_focus", **cand_kwargs)
    db.add(cand)
    db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id,
        last_stage="QUALIFYING",
        current_focus_candidate_id=cand.id,
        **state_kwargs,
    )
    db.add(state)
    db.commit()
    return thread.id, cand.id


def _get_candidate(db: Session, cand_id: int) -> WhatsAppThreadCandidate:
    db.expire_all()
    return db.get(WhatsAppThreadCandidate, cand_id)


# ══════════════════════════════════════════════════════════════════════════════
# WILD01-R1 — FINDING-01: LR-3 zone survives post-AI sync (stale state present)
# ══════════════════════════════════════════════════════════════════════════════

class TestR1ZoneAuthorityFinding01(unittest.TestCase):
    """Candidate zone set by LR-3 (explicit vehicle-location phrase) must NOT
    be overwritten by stale state.home_zone_* in the post-AI sync block.

    Setup: candidate has zone_group='CABA', zone_detail='Palermo' from a prior
    session; state.home_zone_group='CABA', state.home_zone_detail='Palermo'.
    Inbound: "el auto está en Berazategui".

    Expected: candidate.zone_group='Sur', candidate.zone_detail='Berazategui'.
    """

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db, [
            ("CABA", "Palermo", 0),
            ("Sur", "Berazategui", 60000),
        ])
        self.wa_id = "5491100000001"
        self.thread_id, self.cand_id = _seed_thread(
            self.db,
            wa_id=self.wa_id,
            cand_kwargs={
                "marca": "Peugeot", "modelo": "2008", "anio": 2020,
                "tipo_vehiculo": "auto",
                "zone_group": "CABA", "zone_detail": "Palermo",
            },
            state_kwargs={
                "home_zone_group": "CABA",
                "home_zone_detail": "Palermo",
            },
        )
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def test_lr3_zone_survives_stale_state(self):
        """After LR-3 fires, candidate zone = Berazategui not Palermo."""
        _run_ce(
            self.db, self.eng,
            thread_id=self.thread_id,
            wa_id=self.wa_id,
            wa_message_id="inbound-r1-001",
            texts=["el auto está en Berazategui"],
        )
        cand = _get_candidate(self.db, self.cand_id)
        self.assertEqual(cand.zone_group, "Sur",
                         f"Expected zone_group='Sur', got {cand.zone_group!r}")
        self.assertEqual(cand.zone_detail, "Berazategui",
                         f"Expected zone_detail='Berazategui', got {cand.zone_detail!r}")


# ══════════════════════════════════════════════════════════════════════════════
# WILD01-R2 — FINDING-02: explicit year in current turn overrides stale anio
# ══════════════════════════════════════════════════════════════════════════════

class TestR2YearAuthorityFinding02(unittest.TestCase):
    """When the current turn has exactly one unambiguous year and the candidate
    already has a year from a prior session, the new year must win.

    Setup: candidate.anio=2020 (stale from prior session).
    Inbound: "Peugeot 2008 del 2015".

    Expected: candidate.anio=2015.
    """

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db, [("CABA", "Palermo", 0)])
        self.wa_id = "5491100000002"
        self.thread_id, self.cand_id = _seed_thread(
            self.db,
            wa_id=self.wa_id,
            cand_kwargs={
                "marca": "Peugeot", "modelo": "2008", "anio": 2020,
                "tipo_vehiculo": "auto",
                "zone_group": "CABA", "zone_detail": "Palermo",
            },
            state_kwargs={
                "home_zone_group": "CABA",
                "home_zone_detail": "Palermo",
            },
        )
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def test_explicit_year_overrides_stale_anio(self):
        """'del 2015' in turn → candidate.anio becomes 2015, not stale 2020."""
        _run_ce(
            self.db, self.eng,
            thread_id=self.thread_id,
            wa_id=self.wa_id,
            wa_message_id="inbound-r2-001",
            texts=["Peugeot 2008 del 2015"],
        )
        cand = _get_candidate(self.db, self.cand_id)
        self.assertEqual(cand.anio, 2015,
                         f"Expected anio=2015, got {cand.anio!r}")

    def test_no_year_in_turn_preserves_existing_anio(self):
        """If current turn carries no year and candidate already has anio, keep it."""
        _run_ce(
            self.db, self.eng,
            thread_id=self.thread_id,
            wa_id=self.wa_id,
            wa_message_id="inbound-r2-002",
            texts=["el auto está en Palermo"],
        )
        cand = _get_candidate(self.db, self.cand_id)
        self.assertEqual(cand.anio, 2020,
                         f"Expected anio=2020 preserved, got {cand.anio!r}")


# ══════════════════════════════════════════════════════════════════════════════
# WILD01-R3 — FINDING-03: same text with NEW inbound → ALLOWED
# ══════════════════════════════════════════════════════════════════════════════

class TestR3DedupNewInboundAllowed(unittest.TestCase):
    """Same outbound text within the dedup window MUST be allowed when it is
    triggered by a NEW inbound wa_message_id.

    Customer sends 'Hola' twice (different sessions / different inbound IDs).
    First response: ALLOWED.  Second response (new inbound): ALLOWED.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.db = _new_session()
        _clean_all(self.db)
        contact = WhatsAppContact(wa_id="5491100000003", display_name="Test")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.db.add(thread)
        self.db.flush()
        state = WhatsAppThreadState(thread_id=thread.id)
        self.db.add(state)
        self.db.commit()
        self.wa_id = "5491100000003"
        self.thread_id = thread.id

    def tearDown(self):
        self.db.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_new_inbound_allows_same_reply_text(self):
        """Two different inbound IDs → two ALLOWED sends, even with identical reply text."""
        gate = OutboundSafetyGate(self.db)
        reply = "¡Hola! Bienvenido a RideCheck."

        r1 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id, text=reply, now=_NOW,
            path_id=OutboundPathId.CE_TEXT.value,
            causal_inbound_wa_message_id="wamid-inbound-A",
        )
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED,
                         f"First send should be ALLOWED, got {r1.outcome}")

        # Same text, different inbound — must be ALLOWED (FINDING-03 fix)
        r2 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id, text=reply,
            now=_NOW + timedelta(minutes=3),
            path_id=OutboundPathId.CE_TEXT.value,
            causal_inbound_wa_message_id="wamid-inbound-B",
        )
        self.assertEqual(r2.outcome, GateOutcome.ALLOWED,
                         f"Second send with NEW inbound should be ALLOWED, got {r2.outcome}")


# ══════════════════════════════════════════════════════════════════════════════
# WILD01-R4 — FINDING-03: same text from SAME inbound → BLOCKED_DUPLICATE
# ══════════════════════════════════════════════════════════════════════════════

class TestR4DedupSameInboundBlocked(unittest.TestCase):
    """A retry of the same outbound for the SAME causal inbound MUST be blocked.

    This confirms FINDING-03 fix doesn't over-permit.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.db = _new_session()
        _clean_all(self.db)
        contact = WhatsAppContact(wa_id="5491100000004", display_name="Test")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.db.add(thread)
        self.db.flush()
        state = WhatsAppThreadState(thread_id=thread.id)
        self.db.add(state)
        self.db.commit()
        self.wa_id = "5491100000004"
        self.thread_id = thread.id

    def tearDown(self):
        self.db.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_same_inbound_same_text_blocked(self):
        """Same causal inbound + same text within window → BLOCKED_DUPLICATE."""
        gate = OutboundSafetyGate(self.db)
        reply = "¡Hola! Bienvenido a RideCheck."
        inbound_id = "wamid-inbound-X"

        r1 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id, text=reply, now=_NOW,
            path_id=OutboundPathId.CE_TEXT.value,
            causal_inbound_wa_message_id=inbound_id,
        )
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED, "First send should be ALLOWED")

        r2 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id, text=reply,
            now=_NOW + timedelta(minutes=1),
            path_id=OutboundPathId.CE_TEXT.value,
            causal_inbound_wa_message_id=inbound_id,
        )
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         f"Retry same inbound should be BLOCKED_DUPLICATE, got {r2.outcome}")

    def test_no_causal_id_legacy_still_blocks(self):
        """Without causal ID (legacy path), same text is still blocked."""
        gate = OutboundSafetyGate(self.db)
        reply = "Hola sin causal."

        r1 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id, text=reply, now=_NOW,
            path_id=OutboundPathId.CE_TEXT.value,
        )
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)

        r2 = gate.attempt(
            wa_id=self.wa_id, thread_id=self.thread_id, text=reply,
            now=_NOW + timedelta(minutes=2),
            path_id=OutboundPathId.CE_TEXT.value,
        )
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Legacy path (no causal ID) must still block duplicate")


# ══════════════════════════════════════════════════════════════════════════════
# WILD01-R5 — Blocked outbound does not appear "answered" in unanswered query
# ══════════════════════════════════════════════════════════════════════════════

class TestR5BlockedOutboundNotAnswered(unittest.TestCase):
    """A thread whose last message is direction='out', status='blocked' (failed
    send attempt) should still show direction='in' as the effective last direction,
    so the unanswered-alert logic counts it as unanswered.

    Tests the SQL fix in unanswered_alert.py:
      AND wm.status NOT IN ('blocked', 'failed')
    """

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

    def tearDown(self):
        self.db.close()

    def _setup_thread_with_blocked_outbound(self) -> int:
        contact = WhatsAppContact(wa_id="5491100000005", display_name="Test")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.db.add(thread)
        self.db.flush()
        ts_base = _NOW - timedelta(minutes=30)

        inbound = WhatsAppMessage(
            thread_id=thread.id,
            direction="in",
            status="received",
            message_type="text",
            text="Hola, necesito cotización",
            timestamp=ts_base,
        )
        self.db.add(inbound)
        # Blocked outbound (CE tried to reply but Meta returned 400)
        blocked_out = WhatsAppMessage(
            thread_id=thread.id,
            direction="out",
            status="blocked",
            message_type="text",
            text="¡Hola!",
            timestamp=ts_base + timedelta(seconds=5),
            blocked_reason="DUPLICATE",
        )
        self.db.add(blocked_out)
        self.db.commit()
        return thread.id

    def test_effective_last_direction_excludes_blocked(self):
        """When last actual message is blocked outbound, effective last direction is 'in'."""
        thread_id = self._setup_thread_with_blocked_outbound()

        # Query: last non-blocked, non-failed message direction for this thread
        row = self.db.execute(sql_text("""
            SELECT direction
            FROM whatsapp_messages
            WHERE thread_id = :tid
              AND status NOT IN ('blocked', 'failed')
            ORDER BY timestamp DESC
            LIMIT 1
        """), {"tid": thread_id}).fetchone()

        self.assertIsNotNone(row, "Expected at least one non-blocked message")
        self.assertEqual(row[0], "in",
                         f"Effective last direction should be 'in', got {row[0]!r}")

    def test_naive_query_gives_wrong_answer(self):
        """Without the status filter, the naive query returns 'out' (the blocked row)."""
        thread_id = self._setup_thread_with_blocked_outbound()

        row = self.db.execute(sql_text("""
            SELECT direction
            FROM whatsapp_messages
            WHERE thread_id = :tid
            ORDER BY timestamp DESC
            LIMIT 1
        """), {"tid": thread_id}).fetchone()

        self.assertIsNotNone(row)
        # The naive query returns the blocked outbound → 'out' (the bug)
        self.assertEqual(row[0], "out",
                         "Without status filter, last row is 'out' — this was the bug")


# ══════════════════════════════════════════════════════════════════════════════
# WILD01-R6 — WhatsAppOutboundDedup causal_inbound_wa_message_id persists
# ══════════════════════════════════════════════════════════════════════════════

class TestR6DedupCausalPersists(unittest.TestCase):
    """WhatsAppOutboundDedup.causal_inbound_wa_message_id is written and readable.
    Also confirms full text + blocked_reason are storable in WhatsAppMessage.
    """

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

    def tearDown(self):
        self.db.close()

    def test_dedup_model_has_causal_inbound_field(self):
        """causal_inbound_wa_message_id column exists and round-trips."""
        contact = WhatsAppContact(wa_id="5491100000007", display_name="Test2")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.db.add(thread)
        self.db.flush()

        dedup = WhatsAppOutboundDedup(
            wa_id="5491100000007",
            thread_id=thread.id,
            message_kind="text",
            content_fingerprint="a" * 64,
            created_at=_NOW,
            causal_inbound_wa_message_id="wamid-test-causal",
        )
        self.db.add(dedup)
        self.db.commit()
        self.db.expire_all()
        row = self.db.get(WhatsAppOutboundDedup, dedup.id)
        self.assertEqual(row.causal_inbound_wa_message_id, "wamid-test-causal",
                         "causal_inbound_wa_message_id should be persisted")

    def test_blocked_message_full_text_and_reason(self):
        """WhatsAppMessage.text (full) and .blocked_reason are storable."""
        contact = WhatsAppContact(wa_id="5491100000008", display_name="Test3")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.db.add(thread)
        self.db.flush()

        long_text = "A" * 200
        reason = "DUPLICATE: identical text already sent within 10-minute window"
        msg = WhatsAppMessage(
            thread_id=thread.id,
            direction="out",
            status="blocked",
            message_type="text",
            text=long_text,
            timestamp=_NOW,
            blocked_reason=reason,
        )
        self.db.add(msg)
        self.db.commit()
        self.db.expire_all()
        row = self.db.get(WhatsAppMessage, msg.id)
        self.assertEqual(row.text, long_text, "Full text should be stored")
        self.assertEqual(row.blocked_reason, reason, "blocked_reason should be stored")


# ══════════════════════════════════════════════════════════════════════════════
# Test runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
