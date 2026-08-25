"""WILD-04R-F2 — Post-reset context reload + pricing+FAQ composition tests.

Defect A: _load_context ran before _execute_cycle_reset and used the OLD cycle
watermark, causing prior-cycle candidates/messages to appear in ctx.  The first
new-cycle turn therefore skipped candidate creation because ctx.candidates was
non-empty.  Fix: reload ctx.candidates and ctx.db_messages after reset.

Defect B: when a burst contained zone evidence + FAQ question, the deterministic
quote override replaced decision["reply"] with _build_quote_reply(), silently
dropping the FAQ answer.  Fix: detect FAQ signals in the burst and compose a
combined quote+FAQ reply.

W4F2-A1  First-turn-after-reset: old candidate excluded, new candidate created.
W4F2-A2  Post-reset message watermark: state reflects new cycle start message id.
W4F2-B1  Pricing + hours FAQ composed in one reply (exact live case).
W4F2-B2  Pricing only — no FAQ — unchanged template output.
W4F2-B3  Pricing + payment FAQ composed.
W4F2-B4  Pricing + report FAQ composed.
W4F2-B5  Pricing + presence FAQ composed.
W4F2-B6  Pricing + multiple FAQs in one coherent reply.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory setup ──────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON          # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON             # type: ignore[attr-defined]

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
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

os.environ.setdefault("OUTBOUND_ENABLED", "false")

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
from app.services.pricing import PricingService

# ── Time anchors (relative to actual clock so SQLite server_default created_at aligns) ──
_NOW = datetime.now(timezone.utc)
_T_OLD_CYCLE = _NOW - timedelta(hours=4)   # old cycle watermark (before old candidate)
_T_OLD_CAND = _NOW - timedelta(hours=2)    # old candidate created_at (within old cycle)
_T_NEW = _NOW - timedelta(seconds=5)       # new burst arrival time (just before now)

_WA_ID_A = "5491153370001"   # Defect A tests
_WA_ID_B = "5491153370002"   # Defect B tests


# ── Standard AI mock responses ────────────────────────────────────────────────
_AI_QUALIFYING = json.dumps({
    "intent": "QUALIFYING",
    "reply": "¿En qué zona está el auto?",
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})

_AI_FAQ_COMBINED = json.dumps({
    "intent": "FAQ",
    "reply": (
        "Sí, hacemos revisiones preventa. Mandamos informe. "
        "No necesitás estar presente. "
        "Aceptamos transferencia y Mercado Pago. "
        "¿En qué zona está el auto?"
    ),
    "deferred_interest": False,
    "candidate": {"action": "none"},
    "extracted": {},
    "lead_flag": None,
    "needs_human": False,
})


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def _make_engine(db: Session, *, with_sur_pricing: bool = False) -> ConversationEngine:
    class _FakeZoneRow:
        def __init__(self, zg: str, zd: Optional[str], v: int) -> None:
            self.zone_group = zg
            self.zone_detail = zd
            self.viaticos = v

    class _FakeRepo:
        def find_base_price(self, tipo: str) -> BasePriceRow:
            if tipo in ("SUV_4X4_DEPORTIVO", "SUV/4x4"):
                return BasePriceRow(tipo_vehiculo=tipo, precio_base=150_000)
            return BasePriceRow(tipo_vehiculo=tipo, precio_base=140_000)

        def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
            if not with_sur_pricing:
                return None
            if (zone_detail or "").strip().lower() == "berazategui":
                return _FakeZoneRow("Sur", "Berazategui", 90_000)
            if (zone_group or "").strip().lower() == "sur" and not zone_detail:
                return _FakeZoneRow("Sur", None, 90_000)
            return None

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
    eng._pricing = PricingService(repository=_FakeRepo())
    from app.services.schedule import ScheduleService
    eng._schedule = ScheduleService(db=db)
    eng._ai_invoked = False
    eng._answer_source = None
    eng._contributing_sources = None
    return eng


def _event(thread_id: int, wa_message_id: str, texts: list[str]) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id=_WA_ID_A,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )


def _event_b(thread_id: int, wa_message_id: str, texts: list[str]) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id=_WA_ID_B,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )


def _seed_returning_customer(
    db: Session, wa_id: str, *,
    old_candidate_tipo: str = "SUV_4X4_DEPORTIVO",
    old_candidate_zone: str = "CABA",
    n_old_messages: int = 2,
) -> tuple[WhatsAppContact, WhatsAppThread, Lead, WhatsAppThreadState, WhatsAppThreadCandidate]:
    """Seed a returning customer thread with:
    - A historical (prior-cycle) candidate
    - A few historical messages
    - cycle_reset_pending = True (owner already reset the lead)
    - current_cycle_started_at = _T_OLD_CYCLE (old watermark)
    """
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="F2Test", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(
        flag=None,
        estado="CONSULTA_NUEVA",
        nombre="F2Test",
        necesita_humano=False,
    )
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(
        contact_id=contact.id,
        lead_id=lead.id,
        unread_count=0,
        created_at=_T_OLD_CAND - timedelta(hours=1),
    )
    db.add(thread)
    db.flush()

    # Historical candidate from prior cycle — created WITHIN the old cycle window.
    # created_at is set via the ORM constructor so SQLAlchemy serialises it with the
    # same "YYYY-MM-DD HH:MM:SS.ffffff" format it uses in WHERE-clause parameters.
    # Using isoformat() in a raw UPDATE would produce a "T"-separated string that
    # compares greater than the space-separated watermark in SQLite string ordering,
    # causing the filter to incorrectly include the old candidate.
    old_cand = WhatsAppThreadCandidate(
        thread_id=thread.id,
        marca="Peugeot",
        modelo="208",
        tipo_vehiculo=old_candidate_tipo,
        zone_group=old_candidate_zone,
        zone_detail="Balvanera" if old_candidate_zone == "CABA" else None,
        anio=2018,
        status="archived",
        source_text="historical candidate from prior cycle",
        created_at=_T_OLD_CAND,
    )
    db.add(old_cand)
    db.flush()

    # Historical inbound messages from prior cycle
    for i in range(n_old_messages):
        old_msg = WhatsAppMessage(
            thread_id=thread.id,
            wa_message_id=f"old-msg-{i}",
            direction="in",
            timestamp=_T_OLD_CAND - timedelta(minutes=30 - i),
            text=f"Mensaje histórico {i} del ciclo anterior.",
            status="received",
        )
        db.add(old_msg)

    db.flush()

    # Thread state: returning customer, cycle reset pending
    state = WhatsAppThreadState(
        thread_id=thread.id,
        needs_human=False,
        last_stage=None,
        last_intent=None,
        cycle_reset_pending=True,
        # OLD watermark — includes old candidate (T_OLD_CAND > T_OLD_CYCLE)
        current_cycle_started_at=_T_OLD_CYCLE,
        current_focus_candidate_id=old_cand.id,
        # last_processed_inbound_wa_message_id must point to the last old-cycle message
        # so _execute_cycle_reset can establish the correct burst boundary and watermark.
        last_processed_inbound_wa_message_id=f"old-msg-{n_old_messages - 1}",
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        created_at=_T_OLD_CYCLE,
        updated_at=_T_OLD_CYCLE,
    )
    db.add(state)
    db.commit()
    db.expire_all()

    return contact, thread, lead, state, old_cand


def _seed_new_burst_message(
    db: Session, thread_id: int, wa_message_id: str, text: str, ts: datetime
) -> WhatsAppMessage:
    """Persist the first new-cycle inbound message so _execute_cycle_reset finds it."""
    # created_at is set via the ORM constructor so SQLAlchemy serialises it with the
    # same "YYYY-MM-DD HH:MM:SS.ffffff" format it uses in WHERE-clause parameters.
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
    db.expire_all()
    return db.get(WhatsAppMessage, msg.id)


def _seed_b_candidate(
    db: Session, thread_id: int, state: WhatsAppThreadState
) -> WhatsAppThreadCandidate:
    """Seed current-focus candidate for Defect B tests (Peugeot 2008, 2014)."""
    cand = WhatsAppThreadCandidate(
        thread_id=thread_id,
        marca="Peugeot",
        modelo="2008",
        tipo_vehiculo="SUV_4X4_DEPORTIVO",
        anio=2014,
        status="current_focus",
        source_text="test candidate for B tests",
    )
    db.add(cand)
    db.flush()
    state.current_focus_candidate_id = cand.id
    state.last_intent = "PREPURCHASE_INSPECTION"
    db.commit()
    db.expire_all()
    return db.get(WhatsAppThreadCandidate, cand.id)


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-A1: First-turn-after-reset context reload
# ══════════════════════════════════════════════════════════════════════════════

class TestPostResetContextReload(unittest.TestCase):
    """W4F2-A1 / W4F2-A2: Post-cycle-reset context reloads with new watermark.

    Without the fix: _load_context used the OLD watermark before _execute_cycle_reset
    updated it.  The old candidate (created within the old cycle) appeared in
    ctx.candidates, blocking new candidate creation via WILD-02-A / WILD-04-F1.

    With the fix: ctx.candidates and ctx.db_messages are reloaded after reset using
    the new watermark.  The first new-cycle turn behaves identically to later turns.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._patcher = patch("urllib.request.urlopen")
        mock_url = cls._patcher.start()
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock()
        mock_url.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": _AI_FAQ_COMBINED}}]
        }).encode()

        cls._db = _new_session()

        contact, thread, lead, state, old_cand = _seed_returning_customer(
            cls._db, _WA_ID_A,
        )
        cls._old_cand_id = old_cand.id
        cls._thread_id = thread.id
        cls._lead_id = lead.id

        # Persist the first new-cycle message so _execute_cycle_reset can find it
        burst_msg = _seed_new_burst_message(
            cls._db, thread.id, "msg-f2-a1-burst",
            "Hola, quería revisar un 2008 del 2014. Ustedes hacen eso, ¿no?",
            _T_NEW,
        )
        cls._new_msg_id = burst_msg.id

        # Fire Turn 1: returning customer sends vehicle+FAQ burst
        texts = [
            "Hola, quería revisar un 2008 del 2014. Ustedes hacen eso, ¿no?",
            "¿Mandan informes?",
            "¿Aceptan débito?",
        ]
        ev = _event(thread.id, "msg-f2-a1-burst", texts)
        eng = _make_engine(cls._db, with_sur_pricing=False)
        _sent_a: list[str] = []
        with patch.object(
            eng, "_send_text_to_wa",
            side_effect=lambda ctx, txt: _sent_a.append(txt) or "out-a",
        ):
            cls._result = eng.handle(ev)
        cls._sent_a = " ".join(_sent_a)

        cls._db.expire_all()

        # Collect all candidates after the turn
        cls._all_cands = list(cls._db.execute(
            select(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == thread.id
            ).order_by(WhatsAppThreadCandidate.id)
        ).scalars().all())

        # Final state
        cls._state = cls._db.execute(
            select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == thread.id
            )
        ).scalar_one()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._patcher.stop()
        cls._db.close()

    def test_w4f2_a1_new_candidate_created(self):
        """W4F2-A1: A new Peugeot 2008/2014 candidate is created in the new cycle."""
        new_cands = [c for c in self._all_cands if c.id != self._old_cand_id]
        self.assertGreater(
            len(new_cands), 0,
            "W4F2-A1: WILD-02-A must create a new candidate for the new cycle. "
            "Old archived candidate must NOT block this. "
            "Defect A: ctx.candidates loaded before cycle reset included old candidate.",
        )
        new_c = new_cands[0]
        self.assertEqual(
            new_c.marca, "Peugeot",
            f"W4F2-A1: new candidate.marca must be 'Peugeot', got '{new_c.marca}'",
        )
        self.assertIn(
            "2008", str(new_c.modelo),
            f"W4F2-A1: new candidate.modelo must contain '2008', got '{new_c.modelo}'",
        )

    def test_w4f2_a1_old_candidate_not_current_focus(self):
        """W4F2-A1: current_focus_candidate_id must NOT point to the old candidate."""
        self.assertNotEqual(
            self._state.current_focus_candidate_id, self._old_cand_id,
            "W4F2-A1: current_focus_candidate_id must be the new candidate, "
            "not the old archived one from the prior cycle.",
        )

    def test_w4f2_a1_cycle_reset_consumed(self):
        """W4F2-A1: cycle_reset_pending must be False after the turn."""
        self.assertFalse(
            self._state.cycle_reset_pending,
            "W4F2-A1: cycle_reset_pending must be False — reset must be consumed exactly once.",
        )

    def test_w4f2_a1_new_cycle_watermark_set(self):
        """W4F2-A1: current_cycle_started_at reflects the new burst (not old cycle)."""
        self.assertIsNotNone(
            self._state.current_cycle_started_at,
            "W4F2-A1: current_cycle_started_at must be set after reset.",
        )
        self.assertGreaterEqual(
            self._state.current_cycle_started_at.replace(tzinfo=timezone.utc)
            if self._state.current_cycle_started_at.tzinfo is None
            else self._state.current_cycle_started_at,
            _T_OLD_CAND,
            "W4F2-A1: new cycle watermark must be >= old candidate created_at "
            "(i.e. the old candidate falls outside the new cycle window).",
        )

    def test_w4f2_a2_old_message_watermark(self):
        """W4F2-A2: current_cycle_start_message_db_id set to new burst message id."""
        self.assertEqual(
            self._state.current_cycle_start_message_db_id, self._new_msg_id,
            "W4F2-A2: current_cycle_start_message_db_id must point to the first "
            "new-cycle inbound message, excluding all prior-cycle messages from context.",
        )

    def test_w4f2_a1_replied(self):
        """W4F2-A1: CE must produce a reply (not skip or error)."""
        self.assertEqual(
            self._result.action, "replied",
            f"W4F2-A1: expected action='replied', got '{self._result.action}'",
        )


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B: Pricing + FAQ composition helpers
# ══════════════════════════════════════════════════════════════════════════════

def _run_b_turn(
    burst_texts: list[str],
    *,
    ai_reply: str = _AI_QUALIFYING,
    with_sur_pricing: bool = True,
) -> tuple[str, "ConversationHandleOut"]:  # type: ignore[name-defined]
    """Helper: seed a B-test thread with current-focus candidate, run one turn.

    Returns (sent_text, result).
    """
    from app.schemas.conversation import ConversationHandleOut  # noqa
    db = _new_session()
    _clean_all(db)

    # Contact + lead + thread + state
    contact = WhatsAppContact(wa_id=_WA_ID_B, display_name="B-test", phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(flag=None, estado="CONSULTA_NUEVA", nombre="B-test", necesita_humano=False)
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
    db.add(thread)
    db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id,
        needs_human=False,
        last_stage="QUALIFYING",
        last_intent="PREPURCHASE_INSPECTION",
        cycle_reset_pending=False,
        current_cycle_started_at=None,  # no watermark: include all candidates
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    # Add ViaticosZone to DB for _extract_zone_from_text
    db.add(ViaticosZone(zone_group="Sur", zone_detail="Berazategui", viaticos=90_000))
    db.add(ViaticosZone(zone_group="Sur", zone_detail=None, viaticos=90_000))
    db.flush()

    # Current-focus candidate
    _seed_b_candidate(db, thread.id, state)

    # Seed a prior inbound message so _fetch_burst_messages can establish the burst boundary.
    # Without a previous_cursor the CE cannot distinguish a burst from a single message.
    _base_wa_id = f"msg-b-{id(burst_texts)}"
    _prior_wa_id = f"{_base_wa_id}-prior"
    db.add(WhatsAppMessage(
        thread_id=thread.id,
        wa_message_id=_prior_wa_id,
        direction="in",
        timestamp=_NOW - timedelta(minutes=5),
        text="(prior turn)",
        status="received",
    ))
    db.flush()
    # Seed burst messages in DB so burst_message_count is accurate
    for i, txt in enumerate(burst_texts):
        db.add(WhatsAppMessage(
            thread_id=thread.id,
            wa_message_id=f"{_base_wa_id}-{i}",
            direction="in",
            timestamp=_NOW + timedelta(seconds=i),
            text=txt,
            status="received",
        ))
    # Set last_processed_inbound_wa_message_id to prior message so burst window is correct.
    state.last_processed_inbound_wa_message_id = _prior_wa_id
    db.commit()
    db.expire_all()

    eng = _make_engine(db, with_sur_pricing=with_sur_pricing)
    ev = _event_b(thread.id, f"{_base_wa_id}-{len(burst_texts)-1}", burst_texts)

    sent_texts: list[str] = []

    with patch("urllib.request.urlopen") as mock_url:
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock()
        mock_url.return_value.read = lambda: json.dumps({
            "choices": [{"message": {"content": ai_reply}}]
        }).encode()

        with patch.object(eng, "_send_text_to_wa",
                          side_effect=lambda ctx, txt: sent_texts.append(txt) or f"out-b-{len(sent_texts)}"):
            result = eng.handle(ev)

    db.close()
    combined = "\n".join(sent_texts)
    return combined, result


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B1: Pricing + hours FAQ (exact live case)
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingHoursComposition(unittest.TestCase):
    """W4F2-B1: 'El auto está en Berazategui.' + '¿En qué horarios laburan?'
    → reply contains $240.000 AND canonical business hours in one message.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._sent, cls._result = _run_b_turn([
            "El auto está en Berazategui.",
            "¿En qué horarios laburan?",
        ])

    def test_w4f2_b1_quote_in_reply(self):
        """B1: Reply must contain the $240.000 pricing."""
        self.assertIn(
            "240", self._sent,
            f"W4F2-B1: pricing ($240.000) must appear in reply; sent={self._sent!r}",
        )

    def test_w4f2_b1_hours_in_reply(self):
        """B1: Reply must contain canonical business hours (Mon-Fri + Sat)."""
        sent_lower = self._sent.lower()
        has_weekday = any(w in sent_lower for w in ["lunes", "viernes", "9 a 18", "9:00", "09:00"])
        has_saturday = any(w in sent_lower for w in ["sabado", "sábado", "9 a 15", "9:00 a 15"])
        self.assertTrue(
            has_weekday,
            f"W4F2-B1: reply must include weekday hours (lunes a viernes / 9 a 18); sent={self._sent!r}",
        )
        self.assertTrue(
            has_saturday,
            f"W4F2-B1: reply must include Saturday hours (sábados / 9 a 15); sent={self._sent!r}",
        )

    def test_w4f2_b1_cta_present(self):
        """B1: Reply must contain the continuation CTA."""
        self.assertIn(
            "avanzar", self._sent.lower(),
            f"W4F2-B1: reply must contain CTA 'podemos avanzar'; sent={self._sent!r}",
        )

    def test_w4f2_b1_no_vehicle_question(self):
        """B1: Reply must NOT ask for vehicle type (candidate already known)."""
        sent_lower = self._sent.lower()
        self.assertNotIn(
            "qué vehículo", sent_lower,
            f"W4F2-B1: must not ask for vehicle; sent={self._sent!r}",
        )
        self.assertNotIn(
            "marca y modelo", sent_lower,
            f"W4F2-B1: must not ask for vehicle; sent={self._sent!r}",
        )

    def test_w4f2_b1_no_location_question(self):
        """B1: Reply must NOT ask for location (Berazategui provided in burst)."""
        sent_lower = self._sent.lower()
        self.assertNotIn(
            "en qué zona", sent_lower,
            f"W4F2-B1: must not re-ask for zone; sent={self._sent!r}",
        )

    def test_w4f2_b1_primary_source_pricing(self):
        """B1: answer_source must be PRICING_SERVICE."""
        self.assertEqual(
            self._result.answer_source, "PRICING_SERVICE",
            f"W4F2-B1: answer_source must be PRICING_SERVICE, got '{self._result.answer_source}'",
        )

    def test_w4f2_b1_contributing_faq_rule(self):
        """B1: contributing_sources must include FAQ_RULE."""
        cs = self._result.contributing_sources or []
        self.assertIn(
            "FAQ_RULE", cs,
            f"W4F2-B1: contributing_sources must include FAQ_RULE; got {cs!r}",
        )

    def test_w4f2_b1_burst_count_2(self):
        """B1: burst_message_count reflects 2 inbound messages."""
        self.assertEqual(
            self._result.burst_message_count, 2,
            f"W4F2-B1: burst_message_count must be 2, got {self._result.burst_message_count}",
        )

    def test_w4f2_b1_single_outbound(self):
        """B1: exactly one outbound message (no double-send)."""
        self.assertEqual(
            self._sent.count("Genial!"), 1,
            f"W4F2-B1: only one outbound 'Genial!' must appear; sent={self._sent!r}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B2: Pricing only — no FAQ — unchanged behavior
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingOnlyNoChange(unittest.TestCase):
    """W4F2-B2: Pure zone message → pricing only, no FAQ supplement injected."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._sent, cls._result = _run_b_turn([
            "El auto está en Berazategui.",
        ])

    def test_w4f2_b2_quote_in_reply(self):
        """B2: Pricing quote must appear."""
        self.assertIn(
            "240", self._sent,
            f"W4F2-B2: pricing ($240.000) must appear; sent={self._sent!r}",
        )

    def test_w4f2_b2_no_hours_injected(self):
        """B2: No FAQ hours must appear (no hours question in burst)."""
        sent_lower = self._sent.lower()
        self.assertNotIn(
            "lunes a viernes", sent_lower,
            f"W4F2-B2: hours must NOT appear when no hours question in burst; sent={self._sent!r}",
        )

    def test_w4f2_b2_no_contributing_sources(self):
        """B2: contributing_sources must be None (no FAQ supplement)."""
        cs = self._result.contributing_sources
        self.assertIsNone(
            cs,
            f"W4F2-B2: contributing_sources must be None (pricing-only burst); got {cs!r}",
        )

    def test_w4f2_b2_source_pricing(self):
        """B2: answer_source = PRICING_SERVICE (unchanged)."""
        self.assertEqual(
            self._result.answer_source, "PRICING_SERVICE",
            f"W4F2-B2: answer_source must be PRICING_SERVICE; got '{self._result.answer_source}'",
        )


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B3: Pricing + payment FAQ
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingPaymentComposition(unittest.TestCase):
    """W4F2-B3: Zone + payment FAQ → quote + payment answer in one reply."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._sent, cls._result = _run_b_turn([
            "Está en Berazategui.",
            "¿Aceptan débito?",
        ])

    def test_w4f2_b3_quote_present(self):
        self.assertIn("240", self._sent, f"B3: pricing must appear; sent={self._sent!r}")

    def test_w4f2_b3_payment_present(self):
        sent_lower = self._sent.lower()
        has_payment = any(w in sent_lower for w in [
            "transferencia", "mercado pago", "efectivo", "debito", "débito",
        ])
        self.assertTrue(
            has_payment,
            f"W4F2-B3: payment answer must appear; sent={self._sent!r}",
        )

    def test_w4f2_b3_contributing_faq_rule(self):
        cs = self._result.contributing_sources or []
        self.assertIn("FAQ_RULE", cs, f"B3: FAQ_RULE must be in contributing_sources; got {cs!r}")


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B4: Pricing + report FAQ
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingReportComposition(unittest.TestCase):
    """W4F2-B4: Zone + report FAQ → quote + report answer in one reply."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._sent, cls._result = _run_b_turn([
            "Está en Berazategui.",
            "¿Mandan informe?",
        ])

    def test_w4f2_b4_quote_present(self):
        self.assertIn("240", self._sent, f"B4: pricing must appear; sent={self._sent!r}")

    def test_w4f2_b4_report_present(self):
        sent_lower = self._sent.lower()
        self.assertTrue(
            any(w in sent_lower for w in ["informe", "reporte", "detallado"]),
            f"W4F2-B4: report answer must appear; sent={self._sent!r}",
        )

    def test_w4f2_b4_contributing_faq_rule(self):
        cs = self._result.contributing_sources or []
        self.assertIn("FAQ_RULE", cs, f"B4: FAQ_RULE must be in contributing_sources; got {cs!r}")


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B5: Pricing + presence FAQ
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingPresenceComposition(unittest.TestCase):
    """W4F2-B5: Zone + presence FAQ → quote + presence answer in one reply."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._sent, cls._result = _run_b_turn([
            "Está en Berazategui.",
            "¿Tengo que estar presente?",
        ])

    def test_w4f2_b5_quote_present(self):
        self.assertIn("240", self._sent, f"B5: pricing must appear; sent={self._sent!r}")

    def test_w4f2_b5_presence_present(self):
        sent_lower = self._sent.lower()
        self.assertTrue(
            any(w in sent_lower for w in ["presente", "presencia", "necesario", "no es necesario"]),
            f"W4F2-B5: presence answer must appear; sent={self._sent!r}",
        )

    def test_w4f2_b5_contributing_faq_rule(self):
        cs = self._result.contributing_sources or []
        self.assertIn("FAQ_RULE", cs, f"B5: FAQ_RULE must be in contributing_sources; got {cs!r}")


# ══════════════════════════════════════════════════════════════════════════════
# W4F2-B6: Pricing + multiple FAQs
# ══════════════════════════════════════════════════════════════════════════════

class TestPricingMultiFaqComposition(unittest.TestCase):
    """W4F2-B6: Zone + hours + payment + presence FAQs → all answered with quote."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._sent, cls._result = _run_b_turn([
            "Está en Berazategui.",
            "¿En qué horarios trabajan?",
            "¿Aceptan débito?",
            "¿Tengo que estar presente?",
        ])

    def test_w4f2_b6_quote_present(self):
        self.assertIn("240", self._sent, f"B6: pricing must appear; sent={self._sent!r}")

    def test_w4f2_b6_hours_present(self):
        sent_lower = self._sent.lower()
        self.assertTrue(
            any(w in sent_lower for w in ["lunes", "viernes", "9 a 18", "horario"]),
            f"W4F2-B6: hours answer must appear; sent={self._sent!r}",
        )

    def test_w4f2_b6_payment_present(self):
        sent_lower = self._sent.lower()
        self.assertTrue(
            any(w in sent_lower for w in ["transferencia", "mercado pago", "efectivo"]),
            f"W4F2-B6: payment answer must appear; sent={self._sent!r}",
        )

    def test_w4f2_b6_presence_present(self):
        sent_lower = self._sent.lower()
        self.assertTrue(
            any(w in sent_lower for w in ["presente", "necesario"]),
            f"W4F2-B6: presence answer must appear; sent={self._sent!r}",
        )

    def test_w4f2_b6_contributing_faq_rule(self):
        cs = self._result.contributing_sources or []
        self.assertIn("FAQ_RULE", cs, f"B6: FAQ_RULE must be in contributing_sources; got {cs!r}")

    def test_w4f2_b6_single_outbound(self):
        """B6: all content in one message — not multiple sends."""
        self.assertEqual(
            self._sent.count("Genial!"), 1,
            f"W4F2-B6: only one 'Genial!' — one combined message; sent={self._sent!r}",
        )


if __name__ == "__main__":
    unittest.main()
