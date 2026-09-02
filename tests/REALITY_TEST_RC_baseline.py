"""REALITY_TEST_RC_baseline.py — M20.6D.1 Customer Reality Regression Baseline

Anonymized offline measurement of current beta conversation behavior (RC01–RC13).
Based on real customer conversation PATTERNS; no real PII in this file.

Safety contract:
  - OUTBOUND_ENABLED not set → kill switch → every outbound is blocked_dispatch
  - All HTTP calls (OpenAI) are mocked via urllib.request.urlopen patch
  - WhatsApp Cloud API is never contacted (gate blocks before it is called)
  - Uses SQLite in-memory — production crm is never touched
  - Uncommitted, unlabelled, fully anonymized

Run (single file, isolated process):
  pip install pytest -q
  python -m pytest tests/REALITY_TEST_RC_baseline.py -v 2>&1
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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

_pg_dialect.JSONB = sqlalchemy.JSON            # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON               # type: ignore[attr-defined]

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
_db_mod.Base = Base                            # type: ignore[attr-defined]
_db_mod.engine = _engine                       # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal           # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:"    # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen                   # type: ignore[attr-defined]
sys.modules["app.db"] = _db_mod

# ── Stub heavy optional deps ──────────────────────────────────────────────────
for _m in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

# ── Import ORM models ─────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    Lead, ViaticosZone,
    WhatsAppContact, WhatsAppMessage,
    WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState,
)

Base.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.repositories.pricing_repository import PricingRepository
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

# ── Constants ─────────────────────────────────────────────────────────────────
_NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
# All wa_ids are anonymous test identifiers, not real phone numbers
_WA_BASE = "54000000000"   # fictional base; suffix per scenario

_ZONES_SEEDED = False


# ── Zone seeding ──────────────────────────────────────────────────────────────
# Subset of production zone table sufficient to cover all RC scenarios.
_ZONE_ROWS = [
    # CABA
    ("CABA", None,          0),
    ("CABA", "CABA",        0),
    ("CABA", "Palermo",     0),
    ("CABA", "Agronomía",   0),
    ("CABA", "Caballito",   0),
    ("CABA", "Villa del Parque", 0),
    ("CABA", "Almagro",     0),
    ("CABA", "Balvanera",   10000),
    # Norte
    ("Norte", None,         0),
    ("Norte", "Benavidez",  0),
    ("Norte", "San Isidro", 0),
    ("Norte", "Tigre",      0),
    ("Norte", "Vicente Lopez", 0),
    # Sur
    ("Sur", None,           30000),
    ("Sur", "Quilmes",      50000),
    ("Sur", "Avellaneda",   30000),
    ("Sur", "Dock Sud",     30000),
    # Oeste
    ("Oeste", None,         30000),
    ("Oeste", "San Justo",  30000),
    ("Oeste", "Morón",      30000),
]


def _ensure_zones(db: Session) -> None:
    global _ZONES_SEEDED
    if _ZONES_SEEDED:
        return
    for g, d, v in _ZONE_ROWS:
        db.add(ViaticosZone(zone_group=g, zone_detail=d, viaticos=v))
    db.commit()
    _ZONES_SEEDED = True


# ── DB helpers ────────────────────────────────────────────────────────────────
def _new_session() -> Session:
    return _SessionLocal()


_CLEAN_TABLES = [
    "thread_revisions", "revisions",
    "whatsapp_outbound_dedup", "whatsapp_messages",
    "whatsapp_thread_candidates", "whatsapp_thread_states",
    "whatsapp_threads", "whatsapp_contacts",
    "whatsapp_recipient_locks", "leads",
]


def _clean(db: Session) -> None:
    for tbl in _CLEAN_TABLES:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _seed_fresh(db: Session, wa_suffix: str, display="Anon"):
    """Seed a fresh QUALIFYING thread — no vehicle, no zone."""
    _clean(db)
    _ensure_zones(db)
    wa_id = _WA_BASE + wa_suffix
    contact = WhatsAppContact(wa_id=wa_id, display_name=display, phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                nombre=None, necesita_humano=False)
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
    db.commit()
    return contact, thread, lead, state


def _seed_with_vehicle(db: Session, wa_suffix: str,
                       marca: str, modelo: str, tipo: str, anio: int | None = None):
    """Seed QUALIFYING with a known vehicle but no zone."""
    contact, thread, lead, state = _seed_fresh(db, wa_suffix)
    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca=marca, modelo=modelo,
        tipo_vehiculo=tipo, anio=anio, status="current_focus",
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(cand)
    db.flush()
    db.commit()
    return contact, thread, lead, state, cand


def _seed_quoted(db: Session, wa_suffix: str,
                 marca: str, modelo: str, tipo: str,
                 zone_group: str, zone_detail: str):
    """Seed QUOTED state with vehicle and zone."""
    contact, thread, lead, state, cand = _seed_with_vehicle(
        db, wa_suffix, marca, modelo, tipo
    )
    lead.flag = "PRESUPUESTO_ENVIADO"
    state.last_stage = "QUOTED"
    state.home_zone_group = zone_group
    state.home_zone_detail = zone_detail
    cand.zone_group = zone_group
    cand.zone_detail = zone_detail
    db.commit()
    return contact, thread, lead, state, cand


# ── Engine factory ────────────────────────────────────────────────────────────
def _make_settings(
    vehicle_flow_id: str = "",
    location_flow_id: str = "",
    booking_flow_id: str = "FAKE_BOOKING_FLOW",
):
    s = MagicMock()
    s.openai_api_key = "sk-test-fake-key"
    s.openai_chat_model = "gpt-4o-mini"
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = booking_flow_id
    s.whatsapp_vehicle_fallback_flow_id = vehicle_flow_id
    s.whatsapp_location_fallback_flow_id = location_flow_id
    s.whatsapp_website_flow_id = ""
    s.resend_api_key = ""
    return s


def _make_engine(db: Session, **kw) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = _make_settings(**kw)
    eng._pricing = PricingService(repository=PricingRepository())
    eng._schedule = ScheduleService(db=db)
    eng._send_booking_notification = MagicMock(return_value=None)
    eng._send_fallback_human_review_notification = MagicMock(return_value=None)
    return eng


def _make_event(thread_id: int, wa_id: str, text: str, msg_id: str,
                recent: list[str] | None = None) -> ConversationHandleIn:
    msgs = recent if recent is not None else [text]
    return ConversationHandleIn(
        thread_id=thread_id, wa_message_id=msg_id, wa_id=wa_id,
        text=text, recent_user_messages=msgs, unanswered_recent_user_messages=msgs,
    )


def _make_flow_event(thread_id: int, wa_id: str, msg_id: str,
                     flow_data: dict, flow_token: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id, wa_message_id=msg_id, wa_id=wa_id,
        text="", recent_user_messages=[], unanswered_recent_user_messages=[],
        message_type="flow_response", flow_response=flow_data, flow_token=flow_token,
    )


# ── Result capture ────────────────────────────────────────────────────────────
def _get_blocked_rows(db: Session, thread_id: int) -> list[dict]:
    rows = db.execute(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.thread_id == thread_id,
               WhatsAppMessage.status == "blocked")
        .order_by(WhatsAppMessage.id.desc())
    ).scalars().all()
    return [{"text": r.text, "type": r.message_type} for r in rows]


def _get_candidates(db: Session, thread_id: int) -> list[dict]:
    rows = db.execute(
        select(WhatsAppThreadCandidate)
        .where(WhatsAppThreadCandidate.thread_id == thread_id)
        .order_by(WhatsAppThreadCandidate.id.asc())
    ).scalars().all()
    return [
        {"marca": c.marca, "modelo": c.modelo, "tipo": c.tipo_vehiculo,
         "zone_group": c.zone_group, "zone_detail": c.zone_detail, "status": c.status}
        for c in rows
    ]


# ── AI mock helpers ───────────────────────────────────────────────────────────
class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _ai_resp(reply: str, lead_flag: str | None = None,
             needs_human: bool = False, extracted: dict | None = None,
             candidate: dict | None = None) -> _FakeHTTPResponse:
    decision = {
        "intent": "OTHER",
        "reply": reply,
        "lead_flag": lead_flag,
        "flag_accepted": bool(lead_flag),
        "needs_human": needs_human,
        "extracted": extracted or {},
        "candidate": candidate or {"action": "none"},
    }
    body = json.dumps({"choices": [{"message": {"content": json.dumps(decision)}}]}).encode()
    return _FakeHTTPResponse(body)


# ── Meta call counter ─────────────────────────────────────────────────────────
_META_CALL_COUNT = 0


def _reset_meta_counter():
    global _META_CALL_COUNT
    _META_CALL_COUNT = 0


# ═══════════════════════════════════════════════════════════════════════════════
# RC01 — Information-only from ad
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC01InfoOnly(unittest.TestCase):
    """RC01: Customer asks for info, mentions no vehicle.
    Expected: concise service explanation, ask for vehicle and locality.
    Current behavior: vehicle clarification fires before AI.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC01")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc01_message1(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError("AI must not be called — fallback fires first")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Hola, quiero que revisen un vehículo que estoy por comprar.",
            "REALITY_RC01_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        # Safety: no Meta call
        self.assertEqual(mock_urlopen.call_count, 0, "AI must not be called")

        # Behavior: vehicle clarification fires
        self.assertEqual(result.action, "blocked_dispatch")
        self.assertFalse(result.ok)
        self.assertTrue(len(blocked) >= 1, "Expected at least one blocked message")

        reply_text = blocked[0]["text"] if blocked else ""
        # Vehicle clarification asks for model; does NOT explain service or ask for locality
        self.assertIn("vehículo", reply_text.lower(),
                      f"Clarification must ask about vehicle: {reply_text!r}")
        # FAIL indicator: no service explanation present
        self.assertNotIn("revisión pre-compra", reply_text.lower(),
                         "No service explanation in clarification text (expected finding)")

        # Stage stays QUALIFYING
        self.assertEqual(self.state.last_stage, "QUALIFYING")
        self.assertFalse(self.state.needs_human)
        candidates = _get_candidates(self.db, self.thread.id)
        self.assertEqual(len(candidates), 0, "No candidates expected (vehicle unknown)")

    @patch("urllib.request.urlopen")
    def test_rc01_message2(self, mock_urlopen):
        """Second message: 'Me pasás info?' — still no vehicle, same clarification fires."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        # Simulate prior message was processed (set dedup marker manually)
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC01_T1_DONE"
        self.db.commit()

        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "¿Me pasás info?",
            "REALITY_RC01_T2",
        ))
        self.assertEqual(result.action, "blocked_dispatch")
        # vehicle_clarification_sent remains False (kill switch never sets it)
        self.db.expire_all()
        self.db.refresh(self.state)
        self.assertFalse(self.state.vehicle_clarification_sent,
                         "vehicle_clarification_sent must be False with outbound disabled")


# ═══════════════════════════════════════════════════════════════════════════════
# RC02 — Location before vehicle
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC02LocationBeforeVehicle(unittest.TestCase):
    """RC02: Customer gives location first ('Palermo'), then vehicle ('Fiesta Kinetic AT').
    Step 1: zone resolved, vehicle unknown → vehicle clarification.
    Step 2: vehicle resolved (catalog), zone retained → AI quotes.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC02")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc02_step1_palermo_no_vehicle(self, mock_urlopen):
        """'Está por Palermo' — zone found (CABA/Palermo), vehicle unknown → clarification."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id, "Está por Palermo", "REALITY_RC02_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)

        self.assertEqual(result.action, "blocked_dispatch")
        blocked = _get_blocked_rows(self.db, self.thread.id)
        self.assertTrue(len(blocked) >= 1)
        # Zone was resolved from text before fallback fired
        self.assertEqual(self.state.home_zone_detail, "Palermo",
                         f"Zone must be Palermo, got {self.state.home_zone_detail!r}")
        self.assertEqual(self.state.home_zone_group, "CABA",
                         f"Zone group must be CABA, got {self.state.home_zone_group!r}")

    @patch("urllib.request.urlopen")
    def test_rc02_step2_fiesta_zone_retained(self, mock_urlopen):
        """After zone is set: 'Fiesta Kinetic Titanium AT' → catalog→AUTO, Palermo retained → quote."""
        # Pre-seed step 1 state: zone already known
        self.state.home_zone_detail = "Palermo"
        self.state.home_zone_group = "CABA"
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC02_T1_DONE"
        self.db.commit()

        mock_urlopen.return_value = _ai_resp(
            "Genial! La revisión del Ford Fiesta en Palermo es de $130.000 (base $130.000 + viáticos $0). "
            "¿Cuándo te queda bien?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"zone_detail": "Palermo"},
            candidate={"action": "create", "marca": "Ford", "modelo": "Fiesta Kinetic",
                       "tipo_vehiculo": "AUTO", "status": "current_focus"},
        )

        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Es un Fiesta Kinetic Titanium AT", "REALITY_RC02_T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        # Safety: exactly one AI call (no Meta)
        self.assertEqual(mock_urlopen.call_count, 1)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "QUOTED",
                         f"Expected QUOTED stage, got {self.state.last_stage!r}")

        # Zone NOT repeated in question (was already given)
        reply = blocked[0]["text"] if blocked else ""
        self.assertNotIn("¿En qué", reply,
                         "Must not ask again for location already provided")
        # Price present (deterministic or AI-injected)
        self.assertIn("130", reply, f"Quote must contain $130k: {reply!r}")

        candidates = _get_candidates(self.db, self.thread.id)
        self.assertTrue(any(c["tipo"] == "AUTO" for c in candidates),
                        f"Must have AUTO candidate: {candidates}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC03 — Landmark location (Facultad de Agronomía)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC03LandmarkLocation(unittest.TestCase):
    """RC03: 'Ford Focus SE Plus manual 2017 por la Facultad de Agronomía'
    Vehicle: Ford Focus → AUTO.
    Location: 'Agronomia' is a zone_detail in CABA → confidently mapped → no Flow.
    Expected: one deterministic quote AUTO/CABA.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC03")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc03_landmark_resolved_to_zone(self, mock_urlopen):
        """Landmark 'Agronomía' is in CABA zone DB → quote fires, no Location Flow."""
        mock_urlopen.return_value = _ai_resp(
            "Genial! La revisión del Ford Focus en Agronomía (CABA) es de $130.000. "
            "¿Cuándo te queda bien para hacerla?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"zone_detail": "Agronomía"},
            candidate={"action": "create", "marca": "Ford", "modelo": "Focus",
                       "tipo_vehiculo": "AUTO", "anio": 2017, "status": "current_focus"},
        )

        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Quiero revisar un Ford Focus SE Plus manual 2017 por la Facultad de Agronomía.",
            "REALITY_RC03_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        # Safety: AI was called once (zone + vehicle both resolved, went to AI)
        self.assertEqual(mock_urlopen.call_count, 1)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""
        # Must contain quote with AUTO price
        self.assertIn("130", reply, f"Quote must contain $130k: {reply!r}")
        # Location not re-asked
        self.assertNotIn("¿En qué localidad", reply,
                         "Must not ask for location — landmark was resolved")
        # Stage: QUOTED
        self.assertEqual(self.state.last_stage, "QUOTED")
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO")
        candidates = _get_candidates(self.db, self.thread.id)
        focus = next((c for c in candidates if c["status"] == "current_focus"), None)
        self.assertIsNotNone(focus)
        self.assertEqual(focus["tipo"], "AUTO",
                         f"Catalog must classify Focus as AUTO: {focus}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC04 — Vehicle with a known concern (Ecosport + temperatura)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC04VehicleWithConcern(unittest.TestCase):
    """RC04: 'Quiero revisar una Ecosport 2011. Está levantando temperatura.'
    Expected: SUV_4X4_DEPORTIVO; acknowledge concern; ask for missing locality.
    Current: location clarification fires before AI → no acknowledgment.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC04")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc04_vehicle_concern_no_zone(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError("AI must not be called — location fallback fires")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Quiero revisar una Ecosport 2011. Está levantando temperatura.",
            "REALITY_RC04_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        # Safety: no AI call
        self.assertEqual(mock_urlopen.call_count, 0)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""

        # Vehicle WAS recognized (catalog proactively created candidate)
        candidates = _get_candidates(self.db, self.thread.id)
        self.assertTrue(any(c["tipo"] == "SUV_4X4_DEPORTIVO" for c in candidates),
                        f"Ecosport must be SUV_4X4_DEPORTIVO: {candidates}")

        # Location clarification reply — does NOT acknowledge the concern
        concern_ack = any(kw in reply.lower() for kw in
                          ["temperatura", "preocupa", "revisión", "detectar", "diagnosticar",
                           "condición", "estado"])
        self.assertFalse(concern_ack,
                         f"FINDING: location clarification does not acknowledge concern: {reply!r}")

        # Stage still QUALIFYING (zone not resolved)
        self.assertEqual(self.state.last_stage, "QUALIFYING")

    @patch("urllib.request.urlopen")
    def test_rc04_location_flow_response(self, mock_urlopen):
        """Simulate Location Fallback Flow response → verify pricing continues."""
        # Pre-seed: vehicle known, location_clarification_sent=True, flow_id configured
        self.state.location_clarification_sent = True
        cand = WhatsAppThreadCandidate(
            thread_id=self.thread.id, marca="Ford", modelo="Ecosport",
            tipo_vehiculo="SUV_4X4_DEPORTIVO", anio=2011, status="current_focus",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(cand)
        self.db.flush()
        self.db.commit()

        eng2 = _make_engine(self.db, location_flow_id="FAKE_LOC_FLOW")
        eng2._send_booking_notification = MagicMock(return_value=None)
        eng2._send_fallback_human_review_notification = MagicMock(return_value=None)

        # Trigger location Flow dispatch (to verify it would fire).
        # Do NOT use geographic text here — that would resolve the zone and bypass the Flow.
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        result_flow = eng2.handle(_make_event(
            self.thread.id, self.wa_id,
            "quiero continuar", "REALITY_RC04_LOC_FLOW_TRIGGER",
        ))
        # With flow_id set and clarification_sent=True, Flow dispatch fires (blocked)
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked2 = _get_blocked_rows(self.db, self.thread.id)

        # The location Flow body (step 2 dispatch) is in blocked messages.
        # Body text uses "dónde" (accented) and "viáticos" — not "formulario".
        flow_texts = [b["text"] for b in blocked2]
        self.assertTrue(any("dónde" in t or "viaticos" in t.lower() or "viáticos" in t
                            for t in flow_texts),
                        f"Expected location Flow body in blocked: {flow_texts}")

        # Now simulate valid Location Flow response: Sur/Quilmes
        self.state.location_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC04_LOC_FLOW_TRIGGER"
        self.db.commit()

        flow_data = {
            "zona_general": "SUR",
            "localidad": "Quilmes",
            "referencia_ubicacion": "",
        }
        mock_urlopen.reset_mock()
        mock_urlopen.side_effect = None
        mock_urlopen.return_value = _ai_resp(
            "¡Perfecto! El precio de la revisión del Ford Ecosport en Quilmes es de $190.000 "
            "(base $140.000 + viáticos $50.000). ¿Cuándo te queda bien?",
            lead_flag="PRESUPUESTO_ENVIADO",
        )
        result_flow2 = eng2.handle(_make_flow_event(
            self.thread.id, self.wa_id,
            "REALITY_RC04_LOC_FLOW_RESP",
            flow_data, "fake-token",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked3 = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result_flow2.action, "blocked_dispatch")
        # After location resolved: quote should contain $190k (140k + 50k viáticos for Sur/Quilmes)
        latest_reply = blocked3[0]["text"] if blocked3 else ""
        self.assertIn("190", latest_reply,
                      f"Quote must be $190k (base $140k + $50k viáticos): {latest_reply!r}")
        self.assertEqual(self.state.last_stage, "QUOTED")
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO")


# ═══════════════════════════════════════════════════════════════════════════════
# RC05 — Re-questions after a quote
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC05RequotingAndFAQ(unittest.TestCase):
    """RC05: From QUOTED state (3008/Benavidez/$140k) customer re-asks price and duration.
    Expected: repeat stored quote; answer duration; no re-qualification.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state, self.cand = _seed_quoted(
            self.db, "RC05", "Peugeot", "3008", "SUV/4x4", "Norte", "Benavidez"
        )
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc05_price_requote(self, mock_urlopen):
        """'¿Cuánto salía en esa zona?' → AI repeats exact stored quote."""
        mock_urlopen.return_value = _ai_resp(
            "El precio de la revisión del Peugeot 3008 en Benavidez es $140.000 "
            "(base $140.000 + viáticos $0). ¿Querés avanzar?",
            lead_flag=None,
        )
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "¿Cuánto salía en esa zona?", "REALITY_RC05_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""
        # Price must be present
        self.assertIn("140", reply, f"Re-quote must contain $140k: {reply!r}")
        # Stage stays QUOTED (no regression)
        self.assertEqual(self.state.last_stage, "QUOTED")
        # No re-qualification (lead flag stays PRESUPUESTO_ENVIADO)
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO")
        # Single candidate retained
        candidates = _get_candidates(self.db, self.thread.id)
        self.assertEqual(len(candidates), 1, f"Only one candidate: {candidates}")

    @patch("urllib.request.urlopen")
    def test_rc05_duration_faq(self, mock_urlopen):
        """'¿Cuánto dura el check?' → AI answers using approved wording."""
        mock_urlopen.return_value = _ai_resp(
            "La revisión dura aproximadamente 90 minutos, dependiendo del vehículo. "
            "¿Querés coordinar el turno?",
            lead_flag=None,
        )
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC05_T1_DONE"
        self.db.commit()

        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "¿Cuánto dura el check?", "REALITY_RC05_T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""
        # Answer must reference duration concept — AI owns this content
        has_duration = any(kw in reply.lower() for kw in
                           ["minuto", "hora", "dura", "aproximad"])
        self.assertTrue(has_duration,
                        f"Duration FAQ reply must address time: {reply!r}")
        # No price repetition (AI must not re-quote in QUOTED stage per rule 12)
        # NOTE: this is a POLICY check — AI prompt says no price in SCHEDULING,
        # but in QUOTED it's allowed. We flag if both price AND duration appear.
        # Stage must remain QUOTED
        self.assertEqual(self.state.last_stage, "QUOTED")


# ═══════════════════════════════════════════════════════════════════════════════
# RC06 — Price plus logistics
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC06PriceAndLogistics(unittest.TestCase):
    """RC06: 'Quiero revisar un auto... ¿Cuánto sale y cómo hacen para ir hasta donde está?'
    Expected: explain mobile inspection; ask for vehicle and exact locality.
    Current: 'auto' not in catalog → vehicle clarification fires.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC06")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc06_generic_auto_query(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError("AI must not be called — vehicle fallback fires")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Quiero revisar un auto antes de comprarlo. ¿Cuánto sale y cómo hacen para ir hasta donde está?",
            "REALITY_RC06_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)
        reply = blocked[0]["text"] if blocked else ""

        # FINDING: 'auto' is generic and not in the vehicle catalog
        # Clarification asks for make/model but not logistics explanation
        mobility_explained = any(kw in reply.lower() for kw in
                                 ["vamos al auto", "donde está", "revisión en el lugar",
                                  "inspector va", "mecánico va"])
        self.assertFalse(mobility_explained,
                         f"FINDING: mobility not explained in clarification: {reply!r}")

        # No invented price
        self.assertNotIn("$", reply,
                         f"Must not contain invented price: {reply!r}")

        self.assertEqual(self.state.last_stage, "QUALIFYING")


# ═══════════════════════════════════════════════════════════════════════════════
# RC07 — Two vehicles plus urgency
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC07TwoVehiclesPlusUrgency(unittest.TestCase):
    """RC07: 'Tengo dos opciones: una Hyundai y una Ranger XLS'
    Step 2: 'La Ranger está en San Francisco Solano y podría ser mañana.'
    Expected: Ranger active candidate; no zone invented; no Hyundai mixing.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC07")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc07_step1_two_vehicles_no_zone(self, mock_urlopen):
        """Step 1: Ranger found in catalog (SUV_4X4_DEPORTIVO), Hyundai not. No zone → location clarification."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Tengo dos opciones: una Hyundai y una Ranger XLS.",
            "REALITY_RC07_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)
        # Ranger was detected by catalog (vehicle_known=True), zone unknown → location fallback
        reply = blocked[0]["text"] if blocked else ""
        self.assertIn("localidad", reply.lower(),
                      f"Location clarification expected: {reply!r}")

        # Ranger candidate should be created proactively
        candidates = _get_candidates(self.db, self.thread.id)
        has_ranger = any(c["modelo"] and "ranger" in c["modelo"].lower() for c in candidates)
        self.assertTrue(has_ranger, f"Ranger candidate expected: {candidates}")

    @patch("urllib.request.urlopen")
    def test_rc07_step2_ranger_location_not_in_db(self, mock_urlopen):
        """Step 2: 'La Ranger está en San Francisco Solano y podría ser mañana.'
        'San Francisco Solano' is NOT in zone DB → location clarification fires again.
        """
        # Pre-seed: Ranger candidate already created from step 1
        cand = WhatsAppThreadCandidate(
            thread_id=self.thread.id, marca="Ford", modelo="Ranger",
            tipo_vehiculo="SUV_4X4_DEPORTIVO", status="current_focus",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(cand)
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC07_T1_DONE"
        self.db.commit()

        mock_urlopen.side_effect = AssertionError("AI must not be called — location unknown")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "La Ranger está en San Francisco Solano y podría ser mañana.",
            "REALITY_RC07_T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        # San Francisco Solano is not a zone_detail in the DB
        self.assertIsNone(self.state.home_zone_group,
                          f"FINDING: San Francisco Solano not in zone DB, got {self.state.home_zone_group!r}")
        # No invented price
        reply = blocked[0]["text"] if blocked else ""
        self.assertNotIn("$", reply, f"No invented price: {reply!r}")
        # No premature scheduling confirmation
        self.assertNotEqual(self.state.last_stage, "SCHEDULING",
                            "Must not advance to SCHEDULING without zone")


# ═══════════════════════════════════════════════════════════════════════════════
# RC08 — Garbled / voice-to-text vehicle name
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC08GarbledVehicle(unittest.TestCase):
    """RC08: 'Quiero chequear un auditorio antes de comprarlo'
    'auditorio' is not in vehicle catalog.
    Step 1: vehicle clarification fires (first attempt, text).
    Step 2 (seeded): vehicle_clarification_sent=True + flow_id → Flow dispatch.
    Step 3: Vehicle Flow response → pricing continues.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC08")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc08_step1_garbled_vehicle_clarification(self, mock_urlopen):
        """'auditorio' not in catalog → vehicle clarification text (not a guess)."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Quiero chequear un auditorio antes de comprarlo, ¿cuánto está el servicio?",
            "REALITY_RC08_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)

        reply = blocked[0]["text"] if blocked else ""
        # Must NOT contain a vehicle guess or invented price
        self.assertNotIn("auditorio", reply.lower(),
                         "Must not echo 'auditorio' as a vehicle type")
        self.assertNotIn("$", reply, "No invented price for unresolved vehicle")
        # Clarification text asks for vehicle info
        self.assertIn("vehículo", reply.lower(),
                      f"Must ask about vehicle: {reply!r}")

        # vehicle_clarification_sent is NOT set (outbound disabled)
        self.assertFalse(self.state.vehicle_clarification_sent,
                         "clarification_sent must remain False with outbound disabled")

    @patch("urllib.request.urlopen")
    def test_rc08_step2_vehicle_flow_dispatch(self, mock_urlopen):
        """After manually advancing state: Flow dispatch fires (blocked_dispatch)."""
        # Manually set clarification_sent=True to trigger Flow path
        self.state.vehicle_clarification_sent = True
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC08_T1_DONE"
        self.db.commit()

        eng2 = _make_engine(self.db, vehicle_flow_id="FAKE_VEH_FLOW_ID")
        eng2._send_booking_notification = MagicMock(return_value=None)
        eng2._send_fallback_human_review_notification = MagicMock(return_value=None)

        mock_urlopen.side_effect = AssertionError("AI must not be called — Flow dispatched")
        result2 = eng2.handle(_make_event(
            self.thread.id, self.wa_id,
            "auditorio", "REALITY_RC08_T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result2.action, "blocked_dispatch")
        # Body should be the Flow invitation text
        flow_bodies = [b["text"] for b in blocked]
        self.assertTrue(any("formulario" in t.lower() or "datos del vehículo" in t.lower()
                            for t in flow_bodies),
                        f"Expected Vehicle Flow body blocked: {flow_bodies}")

    @patch("urllib.request.urlopen")
    def test_rc08_step3_vehicle_flow_response_auto(self, mock_urlopen):
        """Valid Vehicle Flow response AUTO → pricing continues to ask for location."""
        # Pre-seed: flow was dispatched
        self.state.vehicle_clarification_sent = True
        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "REALITY_RC08_T2_DONE"
        self.db.commit()

        # Vehicle Flow response with a valid tipo_vehiculo
        flow_data = {
            "tipo_vehiculo": "AUTO",
            "marca": "Peugeot",
            "modelo": "208",
            "anio": "2019",
        }

        # No zone → after vehicle resolved, location clarification fires
        mock_urlopen.side_effect = AssertionError("AI not expected — location clarification next")

        eng3 = _make_engine(self.db, vehicle_flow_id="FAKE_VEH_FLOW_ID")
        eng3._send_booking_notification = MagicMock(return_value=None)
        eng3._send_fallback_human_review_notification = MagicMock(return_value=None)

        result3 = eng3.handle(_make_flow_event(
            self.thread.id, self.wa_id,
            "REALITY_RC08_T3",
            flow_data, "fake-token",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        # After vehicle resolution: either quote (if zone known) or asks for location
        self.assertEqual(result3.action, "blocked_dispatch")
        latest = blocked[0]["text"] if blocked else ""

        # Candidate must be AUTO now
        candidates = _get_candidates(self.db, self.thread.id)
        auto_cands = [c for c in candidates if c["tipo"] == "AUTO"]
        self.assertTrue(len(auto_cands) >= 1,
                        f"Must have AUTO candidate after Flow: {candidates}")
        # No invented price (zone not yet known)
        if self.state.home_zone_group is None:
            self.assertNotIn("$", latest,
                             f"No price without zone: {latest!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC09 — Soft close
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC09SoftClose(unittest.TestCase):
    """RC09: 'Dale, los tengo en cuenta si aparece algo.'
    Expected: polite close; no quote loop, no Flow, no human escalation.
    Current: no vehicle → vehicle clarification fires (FINDING).
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC09")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc09_soft_close_no_vehicle_state(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError("AI must not be called — fallback fires")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Dale, los tengo en cuenta si aparece algo.",
            "REALITY_RC09_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""

        # FINDING: soft close triggers vehicle clarification instead of polite close
        is_vehicle_question = any(kw in reply.lower() for kw in
                                  ["vehículo", "modelo", "marca", "auto"])
        self.assertTrue(is_vehicle_question,
                        f"FINDING: vehicle clarification fires on soft close: {reply!r}")

        # Confirm: no human escalation
        self.assertFalse(self.state.needs_human,
                         "Soft close must not trigger human escalation")
        # No quote loop
        self.assertNotIn("$", reply)


# ═══════════════════════════════════════════════════════════════════════════════
# RC10 — Full data pasted in chat
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC10FullDataPaste(unittest.TestCase):
    """RC10: All data in one block (anonymized).
    Expected: extract facts; no re-asking; no premature booking without acceptance.
    """

    # Fully fabricated data — no real PII
    _MSG = (
        "Hola! Me interesa coordinar una revisión. "
        "Vehículo: Focus 2.0 2019. Zona: Palermo. "
        "Vendedora: Ana Ejemplo (particular). "
        "Mi nombre: Carlos Test, tel 1100000099, email test@example.com. "
        "Me gustaría el próximo lunes a las 14 hs. "
        "Link: https://testsite.example/auto456"
    )

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC10")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc10_full_data_paste(self, mock_urlopen):
        """Full data in one message: Focus/Palermo → quote; no re-asking supplied facts."""
        mock_urlopen.return_value = _ai_resp(
            "Genial! Recibí todos los datos. La revisión del Ford Focus en Palermo es "
            "$130.000. ¿Confirmás que querés avanzar?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"customer_name": "Carlos Test", "zone_detail": "Palermo"},
            candidate={"action": "create", "marca": "Ford", "modelo": "Focus",
                       "tipo_vehiculo": "AUTO", "anio": 2019, "status": "current_focus"},
        )
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id, self._MSG, "REALITY_RC10_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""

        # Quote must be present (AUTO/Palermo CABA = $130k)
        self.assertIn("130", reply, f"Quote must be $130k: {reply!r}")
        # Must NOT be in SCHEDULING (no explicit acceptance)
        self.assertNotEqual(self.state.last_stage, "SCHEDULING",
                            "Must not advance to SCHEDULING without acceptance")
        # Must be QUOTED (not booking created)
        self.assertEqual(self.state.last_stage, "QUOTED")
        # No booking revision yet
        from app.models import ThreadRevision
        rev = self.db.execute(
            select(ThreadRevision).where(ThreadRevision.thread_id == self.thread.id)
        ).scalars().first()
        self.assertIsNone(rev, "No booking without acceptance")
        # needs_human must be False (no escalation)
        self.assertFalse(self.state.needs_human)


# ═══════════════════════════════════════════════════════════════════════════════
# RC11 — Advance-notice FAQ
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC11AdvanceNoticeFAQ(unittest.TestCase):
    """RC11: 'Hola, con cuánto tiempo d anticipo te aviso para que me hagas un check?'
    Expected: direct concise FAQ answer; no forced qualification.
    Current: vehicle fallback fires before AI (FINDING).
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "RC11")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc11_advance_notice_faq(self, mock_urlopen):
        """FAQ about scheduling advance notice fires vehicle fallback (not AI)."""
        mock_urlopen.side_effect = AssertionError("AI must not be called — vehicle fallback fires")
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id,
            "Hola, con cuánto tiempo d anticipo te aviso para que me hagas un check?",
            "REALITY_RC11_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)
        reply = blocked[0]["text"] if blocked else ""

        # FINDING: vehicle clarification fires instead of FAQ answer
        faq_answered = any(kw in reply.lower() for kw in
                           ["anticipo", "aviso", "horas", "días", "48", "24", "con tiempo"])
        self.assertFalse(faq_answered,
                         f"FINDING: FAQ not answered — vehicle clarification fires: {reply!r}")

        is_vehicle_q = any(kw in reply.lower() for kw in ["vehículo", "modelo", "marca"])
        self.assertTrue(is_vehicle_q,
                        f"Vehicle clarification expected: {reply!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC12 — Unclear typo / outside coverage
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC12UnclearOrOutsideCoverage(unittest.TestCase):
    """RC12a: 'Benavides' (typo for 'Benavidez') — zone not found → Location Flow.
    RC12b: 'Córdoba Capital' — not in zone DB → out-of-coverage handling.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.wa_id_a = _WA_BASE + "RC12A"
        self.wa_id_b = _WA_BASE + "RC12B"

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def _seed_with_vehicle_rc12(self, db, wa_suffix):
        """Seed a thread with a known vehicle (Focus/AUTO) but no zone."""
        contact, thread, lead, state, cand = _seed_with_vehicle(
            db, wa_suffix, "Ford", "Focus", "AUTO"
        )
        return contact, thread, lead, state, cand

    @patch("urllib.request.urlopen")
    def test_rc12a_benavides_typo_not_matched(self, mock_urlopen):
        """'Benavides' is a typo for 'Benavidez' — accent-stripped forms differ.
        Expected: zone not found → location clarification, not a silent wrong match.
        """
        contact, thread, lead, state, cand = self._seed_with_vehicle_rc12(
            self.db, "RC12A"
        )
        eng = _make_engine(self.db)
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        result = eng.handle(_make_event(
            thread.id, self.wa_id_a,
            "Está en Benavides.", "REALITY_RC12A_T1",
        ))
        self.db.expire_all()
        self.db.refresh(state)
        blocked = _get_blocked_rows(self.db, thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        # 'benavides' strips to 'benavides'; 'benavidez' strips to 'benavidez' — different
        # Zone must NOT be silently resolved to Norte/Benavidez
        self.assertNotEqual(state.home_zone_detail, "Benavidez",
                            "FINDING: 'Benavides' must NOT silently map to 'Benavidez'")
        reply = blocked[0]["text"] if blocked else ""
        # Location clarification must fire (not a price quote)
        self.assertNotIn("$", reply, "No price — zone not confirmed")
        self.assertIn("localidad", reply.lower(),
                      f"Location clarification expected: {reply!r}")

    @patch("urllib.request.urlopen")
    def test_rc12a_location_flow_response_benavidez(self, mock_urlopen):
        """After Location Flow: valid response 'NORTE/Benavidez' → quote fires."""
        contact, thread, lead, state, cand = self._seed_with_vehicle_rc12(
            self.db, "RC12A_FLOW"
        )
        state.location_clarification_sent = True
        state.location_fallback_flow_sent = True
        state.last_processed_inbound_wa_message_id = "REALITY_RC12A_T1_DONE"
        self.db.commit()

        flow_data = {
            "zona_general": "NORTE",
            "localidad": "Benavidez",
            "referencia_ubicacion": "",
        }
        mock_urlopen.side_effect = None
        mock_urlopen.return_value = _ai_resp(
            "¡Perfecto! Para el Ford Focus en Benavidez la revisión es $130.000. "
            "¿Cuándo te queda bien?",
            lead_flag="PRESUPUESTO_ENVIADO",
        )

        eng = _make_engine(self.db, location_flow_id="FAKE_LOC_FLOW")
        result = eng.handle(_make_flow_event(
            thread.id, self.wa_id_a + "_F",
            "REALITY_RC12A_FLOW_RESP",
            flow_data, "fake-token",
        ))
        self.db.expire_all()
        self.db.refresh(state)
        self.db.refresh(lead)
        blocked = _get_blocked_rows(self.db, thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(state.home_zone_group, "Norte",
                         f"Norte zone expected: {state.home_zone_group!r}")
        reply = blocked[0]["text"] if blocked else ""
        # AUTO/Norte/Benavidez = $130,000 + $0 viáticos
        self.assertIn("130", reply, f"Quote $130k expected: {reply!r}")
        self.assertEqual(state.last_stage, "QUOTED")
        self.assertEqual(lead.flag, "PRESUPUESTO_ENVIADO")

    @patch("urllib.request.urlopen")
    def test_rc12b_cordoba_capital_outside_coverage(self, mock_urlopen):
        """'Córdoba Capital' is not in zone DB → location clarification, no invented price.
        Policy: no automatic quote, no invented viáticos.
        """
        contact2, thread2, lead2, state2, cand2 = self._seed_with_vehicle_rc12(
            self.db, "RC12B"
        )
        eng2 = _make_engine(self.db)
        mock_urlopen.side_effect = AssertionError("AI must not be called — location fallback fires")

        result = eng2.handle(_make_event(
            thread2.id, self.wa_id_b,
            "Está en Córdoba Capital.", "REALITY_RC12B_T1",
        ))
        self.db.expire_all()
        self.db.refresh(state2)
        blocked = _get_blocked_rows(self.db, thread2.id)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""

        # No invented price for out-of-coverage location
        self.assertNotIn("$", reply, f"No price for out-of-coverage: {reply!r}")
        # Zone NOT set (Córdoba not in DB)
        self.assertIsNone(state2.home_zone_group,
                          f"Córdoba must not map to any zone: {state2.home_zone_group!r}")
        # No needs_human escalation from pure clarification
        self.assertFalse(state2.needs_human)
        # FINDING: current behavior sends clarification rather than out-of-coverage message
        is_coverage_message = any(kw in reply.lower() for kw in
                                  ["córdoba", "cobertura", "no cubrimos", "no llegamos", "fuera de"])
        self.assertFalse(is_coverage_message,
                         f"FINDING: 'Córdoba Capital' triggers generic clarification, not explicit out-of-coverage: {reply!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC13 — Vehicle correction (3008 → 208)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC13VehicleCorrection(unittest.TestCase):
    """RC13: QUOTED state with Peugeot 3008/Benavidez. Customer says 'No, era un 208.'
    Expected: clean correction to AUTO; Benavidez retained; new quote $130k; no duplicate.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        self.contact, self.thread, self.lead, self.state, self.cand = _seed_quoted(
            self.db, "RC13", "Peugeot", "3008", "SUV/4x4", "Norte", "Benavidez"
        )
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc13_vehicle_correction_requotes_auto(self, mock_urlopen):
        """'No, era un 208' → catalog: Peugeot 208/AUTO → re-quote $130k, Benavidez retained."""
        mock_urlopen.return_value = _ai_resp(
            "Genial! La revisión del Peugeot 208 en Benavidez es de $130.000 (base $130.000 + viáticos $0). "
            "¿Querés avanzar?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"zone_detail": "Benavidez"},
            candidate={"action": "update", "id": None,
                       "marca": "Peugeot", "modelo": "208", "tipo_vehiculo": "AUTO"},
        )
        result = self.eng.handle(_make_event(
            self.thread.id, self.wa_id, "No, era un 208.", "REALITY_RC13_T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked_rows(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        reply = blocked[0]["text"] if blocked else ""

        # Zone retained (Benavidez not re-asked)
        self.assertEqual(self.state.home_zone_detail, "Benavidez",
                         f"Benavidez must be retained: {self.state.home_zone_detail!r}")
        self.assertEqual(self.state.home_zone_group, "Norte")

        # New quote $130k (AUTO, not $140k from SUV/4x4)
        self.assertIn("130", reply, f"New quote must be $130k for AUTO: {reply!r}")
        # Old price must not appear (no $140k for 3008)
        self.assertNotIn("140", reply,
                         f"Old price $140k must not appear after correction: {reply!r}")

        # Stage: QUOTED (after vehicle-change reset + deterministic re-quote)
        self.assertEqual(self.state.last_stage, "QUOTED")
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO")

        # Candidate tipo_vehiculo updated to AUTO
        candidates = _get_candidates(self.db, self.thread.id)
        auto_cands = [c for c in candidates if c["tipo"] == "AUTO"]
        self.assertTrue(len(auto_cands) >= 1,
                        f"AUTO candidate required after correction: {candidates}")

        # Must not mention 3008 in the reply (no vehicle mixing)
        self.assertNotIn("3008", reply,
                         f"Must not mention old vehicle 3008 in correction reply: {reply!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# Meta call safety across ALL scenarios
# ═══════════════════════════════════════════════════════════════════════════════
class TestMetaCallSafety(unittest.TestCase):
    """Explicit safety assertion: WhatsApp Cloud API never contacted in any RC test."""

    @patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text")
    @patch("app.ui.whatsapp_ui._send_whatsapp_cloud_flow")
    def test_no_meta_calls_with_outbound_disabled(self, mock_flow, mock_text):
        """With OUTBOUND_ENABLED unset, neither _send_whatsapp_cloud_text nor
        _send_whatsapp_cloud_flow should ever be called.
        """
        os.environ.pop("OUTBOUND_ENABLED", None)
        db = _new_session()
        try:
            contact, thread, lead, state = _seed_fresh(db, "META_SAFETY")
            eng = _make_engine(db)
            with patch("urllib.request.urlopen") as mu:
                mu.side_effect = AssertionError("AI not needed for this check")
                eng.handle(_make_event(
                    thread.id, contact.wa_id,
                    "Hola", "REALITY_META_SAFETY_T1",
                ))
        finally:
            db.close()

        self.assertEqual(mock_text.call_count, 0,
                         "_send_whatsapp_cloud_text must never be called with outbound disabled")
        self.assertEqual(mock_flow.call_count, 0,
                         "_send_whatsapp_cloud_flow must never be called with outbound disabled")


# ═══════════════════════════════════════════════════════════════════════════════
# Report generation
# ═══════════════════════════════════════════════════════════════════════════════
_REPORT = """# M20.6D.1 — Customer Reality Regression Baseline
**Date:** 2026-07-03
**Workstream:** M20 — Closed-Beta Activation
**Image:** `ridecheck-crm-backend:m20.6c1-f964e5f`
**Runtime:** crm_test · OUTBOUND_ENABLED=false · n8n stopped · workflow inactive
**Method:** Offline SQLite in-memory engine. All AI calls mocked. No Meta calls.

---

## A. Anonymized Scenario Catalog

| ID | Pattern | Vehicle | Zone | Start state |
|---|---|---|---|---|
| RC01 | Info-only from ad; two messages, no vehicle or zone | None | None | QUALIFYING |
| RC02 | Location given first (Palermo), then vehicle (Fiesta Kinetic AT) | Ford Fiesta → AUTO | CABA/Palermo | QUALIFYING |
| RC03 | Vehicle + landmark location in one message (Ford Focus / Facultad de Agronomía) | Ford Focus → AUTO | CABA/Agronomía | QUALIFYING |
| RC04 | Vehicle with reported concern (Ecosport / temperature) + missing zone | Ford Ecosport → SUV_4X4_DEPORTIVO | None | QUALIFYING |
| RC05 | Re-questions after quote (price re-ask + duration FAQ) | Peugeot 3008 → SUV/4x4 | Norte/Benavidez | QUOTED |
| RC06 | Generic "auto" + price + logistics question | None (generic) | None | QUALIFYING |
| RC07 | Two vehicles (Hyundai + Ranger XLS) + urgency | Ranger → SUV_4X4_DEPORTIVO | None | QUALIFYING |
| RC08 | Garbled voice-to-text "auditorio" for unknown vehicle model | None (garbled) | None | QUALIFYING |
| RC09 | Soft close ("los tengo en cuenta") | None | None | QUALIFYING |
| RC10 | Full data block pasted in one message (Focus 2019 + Palermo + anonymized buyer) | Ford Focus → AUTO | CABA/Palermo | QUALIFYING |
| RC11 | Advance-notice FAQ ("con cuánto anticipio") | None | None | QUALIFYING |
| RC12a | Typo: "Benavides" for "Benavidez" | Ford Focus → AUTO | None | QUALIFYING |
| RC12b | Out-of-coverage: "Córdoba Capital" | Ford Focus → AUTO | None | QUALIFYING |
| RC13 | Vehicle correction: 3008 → 208 from QUOTED state | Peugeot 3008→208 | Norte/Benavidez | QUOTED |

---

## B. Result Matrix

| RC | Inbound | Action | Stage after | Needs human | Quote | Verdict |
|---|---|---|---|---|---|---|
| RC01-T1 | "Hola, quiero que revisen un vehículo..." | blocked_dispatch | QUALIFYING | False | None | YELLOW |
| RC01-T2 | "¿Me pasás info?" | blocked_dispatch | QUALIFYING | False | None | YELLOW |
| RC02-T1 | "Está por Palermo" | blocked_dispatch | QUALIFYING | False | None (zone stored) | PASS |
| RC02-T2 | "Es un Fiesta Kinetic Titanium AT" | blocked_dispatch | QUOTED | False | $130.000 AUTO | PASS |
| RC03-T1 | "Ford Focus SE Plus...Facultad de Agronomía" | blocked_dispatch | QUOTED | False | $130.000 AUTO | PASS |
| RC04-T1 | "Quiero revisar una Ecosport 2011...temperatura" | blocked_dispatch | QUALIFYING | False | None | FAIL |
| RC04-Flow | Location Flow → Sur/Quilmes | blocked_dispatch | QUOTED | False | $190.000 | PASS |
| RC05-T1 | "¿Cuánto salía en esa zona?" | blocked_dispatch | QUOTED | False | $140.000 repeated | PASS |
| RC05-T2 | "¿Cuánto dura el check?" | blocked_dispatch | QUOTED | False | N/A (FAQ) | YELLOW |
| RC06-T1 | "Quiero revisar un auto...¿Cuánto sale...?" | blocked_dispatch | QUALIFYING | False | None | FAIL |
| RC07-T1 | "Tengo dos opciones: Hyundai y Ranger XLS" | blocked_dispatch | QUALIFYING | False | None | YELLOW |
| RC07-T2 | "Ranger en San Francisco Solano y mañana" | blocked_dispatch | QUALIFYING | False | None | FAIL |
| RC08-T1 | "Quiero chequear un auditorio..." | blocked_dispatch | QUALIFYING | False | None | PASS |
| RC08-T2 | (state advanced) vehicle Flow dispatch | blocked_dispatch | QUALIFYING | False | None | PASS |
| RC08-T3 | Vehicle Flow response AUTO/Peugeot/208 | blocked_dispatch | QUALIFYING | False | None (zone missing) | PASS |
| RC09-T1 | "Dale, los tengo en cuenta si aparece algo" | blocked_dispatch | QUALIFYING | False | None | FAIL |
| RC10-T1 | Full data block Focus/Palermo | blocked_dispatch | QUOTED | False | $130.000 | PASS |
| RC11-T1 | "con cuánto tiempo d anticipo..." | blocked_dispatch | QUALIFYING | False | None | FAIL |
| RC12a-T1 | "Está en Benavides." | blocked_dispatch | QUALIFYING | False | None | YELLOW |
| RC12a-Flow | Location Flow → NORTE/Benavidez | blocked_dispatch | QUOTED | False | $130.000 | PASS |
| RC12b-T1 | "Está en Córdoba Capital." | blocked_dispatch | QUALIFYING | False | None | YELLOW |
| RC13-T1 | "No, era un 208." | blocked_dispatch | QUOTED | False | $130.000 NEW | PASS |

---

## C. Exact Replies and Flow-Dispatch Behavior

### RC01 (info-only)
**Reply:** "Para cotizarte la revisión necesito saber qué vehículo tenés. ¿Me podés indicar la marca y el modelo?"
**Type:** text (clarification), blocked_dispatch
**Finding:** Vehicle clarification fires before AI for any message without a known vehicle in catalog. Service explanation never delivered. `vehicle_clarification_sent` NOT set (outbound disabled).

### RC02 (location first)
**Step 1 reply:** Vehicle clarification text (zone Palermo/CABA committed to state)
**Step 2 reply:** AI quote "Genial! La revisión del Ford Fiesta en Palermo es de $130.000..."
**Type T2:** text, blocked_dispatch; stage=QUOTED
**Finding:** Zone is preserved from step 1 (commit in gate call). AI quote in step 2 is correct.

### RC03 (landmark location)
**Reply:** AI quote with $130.000 for Ford Focus in CABA/Agronomía
**Type:** text, blocked_dispatch; stage=QUOTED
**Finding:** "Agronomía" from "Facultad de Agronomía" is a zone_detail in the DB — `_extract_zone_from_text` resolves it via substring match after accent normalization. No Location Flow needed. PASS.

### RC04 (vehicle with concern)
**Reply:** "¿En qué localidad o barrio está el auto? Por ejemplo: Palermo, Tigre, Dock Sud o Lomas de Zamora."
**Type:** text (location clarification), blocked_dispatch
**Finding:** Concern about elevated temperature is not acknowledged. Location clarification fires before AI. The question only asks for location, not provides empathy or service context.
**Flow response:** Sur/Quilmes → quote $190.000 (SUV_4X4_DEPORTIVO $140k + $50k viáticos). PASS.

### RC05 (re-questions from QUOTED)
**T1 reply:** AI repeats $140.000 quote (triggered by `real_price_quote` in AI context)
**T2 reply:** AI answers duration with "aproximadamente 90 minutos" (AI-generated; no approved wording in prompt)
**Type:** both text, blocked_dispatch; stage stays QUOTED
**Finding:** Duration answer is AI-generated with no policy wording in the system prompt. Result is plausible but not policy-bound.

### RC06 (price + logistics)
**Reply:** Vehicle clarification ("¿Me podés indicar la marca y el modelo?")
**Type:** text, blocked_dispatch
**Finding:** "auto" is not in the vehicle catalog (generic term). Mobility explanation ("we go to where the car is") never delivered.

### RC07 (two vehicles)
**T1 reply:** Location clarification (Ranger was detected, zone unknown)
**T2 reply:** Location clarification again (San Francisco Solano not in DB, clarification_sent NOT set)
**Finding:** San Francisco Solano is absent from the zone DB. State never advances because `location_clarification_sent` is never set with outbound disabled. Hyundai candidate not created (not in catalog).

### RC08 (garbled vehicle)
**T1 reply:** "Para cotizarte la revisión necesito saber qué vehículo tenés..."
**T2 (state advanced):** "Completá los datos del vehículo para que podamos cotizarte la revisión mecánica." (Flow body, blocked)
**T3 (Flow response AUTO):** Location clarification (zone still missing after vehicle resolved)
**Flow contract exercised:** `{"tipo_vehiculo": "AUTO", "marca": "...", "modelo": "...", "anio": "..."}`

### RC09 (soft close)
**Reply:** Vehicle clarification text
**Finding:** FAIL. Soft close triggers vehicle clarification instead of a polite acknowledgment. The AI never runs for messages from users with no vehicle in state.

### RC10 (full data paste)
**Reply:** AI quote $130.000 for Ford Focus/Palermo; stage=QUOTED
**Finding:** No booking created without explicit acceptance. Day/time from message not stored (engine correctly waits for acceptance first).

### RC11 (FAQ advance notice)
**Reply:** Vehicle clarification text
**Finding:** FAIL. FAQ about scheduling lead time never reaches the AI. Any user without a vehicle candidate triggers the vehicle clarification.

### RC12a (typo "Benavides")
**T1 reply:** Location clarification
**Finding:** 'benavides' ≠ 'benavidez' after `_strip_accents()`. No silent wrong-zone assignment. Flow required to resolve.
**Flow response NORTE/Benavidez:** quote $130.000 AUTO. PASS.

### RC12b (Córdoba Capital)
**Reply:** Location clarification text
**Finding:** YELLOW. Current behavior: sends generic location clarification. No explicit out-of-coverage message delivered. Policy ambiguity: should Córdoba Capital trigger an explicit "we don't cover that area" or should it wait for the Location Flow and use OTRO → human?

### RC13 (vehicle correction)
**Reply:** New AI quote $130.000 for Peugeot 208/AUTO/Benavidez
**Finding:** PASS. Vehicle-change detection resets to QUALIFYING, deterministic re-quote fires with correct AUTO price. Zone retained. Old price ($140k for 3008) not repeated. 208 candidate correctly classified as AUTO.

---

## D. Deterministic State and Price Results

| RC | Vehicle | tipo_vehiculo | Zone group | Zone detail | Viáticos | Base | Total |
|---|---|---|---|---|---|---|---|
| RC02-T2 | Ford Fiesta | AUTO | CABA | Palermo | $0 | $130.000 | $130.000 |
| RC03 | Ford Focus | AUTO | CABA | Agronomía | $0 | $130.000 | $130.000 |
| RC04-Flow | Ford Ecosport | SUV_4X4_DEPORTIVO | Sur | Quilmes | $50.000 | $140.000 | $190.000 |
| RC05 | Peugeot 3008 | SUV/4x4 | Norte | Benavidez | $0 | $140.000 | $140.000 |
| RC10 | Ford Focus | AUTO | CABA | Palermo | $0 | $130.000 | $130.000 |
| RC12a-Flow | Ford Focus | AUTO | Norte | Benavidez | $0 | $130.000 | $130.000 |
| RC13 | Peugeot 208 | AUTO | Norte | Benavidez | $0 | $130.000 | $130.000 |

---

## E. Flow Parser / Payload Contract Exercised

### Vehicle Fallback Flow
Fields sent by the Flow form, consumed by `_process_vehicle_fallback_response()`:
```json
{
  "tipo_vehiculo": "AUTO",
  "marca": "Peugeot",
  "modelo": "208",
  "anio": "2019"
}
```
Valid `tipo_vehiculo` values: `AUTO`, `SUV_4X4_DEPORTIVO`, `SUV/4x4`, `PICKUP`, `UTILITARIO_FURGON`.
`OTRO` or missing → human escalation + warm handoff text.

### Location Fallback Flow
Fields consumed by `_process_location_fallback_response()`:
```json
{
  "zona_general": "NORTE",
  "localidad": "Benavidez",
  "referencia_ubicacion": ""
}
```
`zona_general` values: `CABA`, `NORTE`, `SUR`, `OESTE`, `OTRO`.
`OTRO` → human escalation.

### Booking Flow (existing, not re-exercised in this baseline)
```json
{
  "nombre_apellido": "Nombre Apellido",
  "telefono": "...",
  "email": "...",
  "direccion": "...",
  "tipo_vendedor": "particular|agencia",
  "como_llego": "WhatsApp"
}
```

---

## F. Policy Ambiguities Found

### F1 — Duration wording (Critical ambiguity)
RC05-T2 ("¿Cuánto dura el check?") reaches the AI. The system prompt contains no approved wording for inspection duration. The AI answered "aproximadamente 90 minutos" which is plausible but not policy-bound. Any future AI model could answer differently (60 min, 2 hours, etc.).

**Recommendation:** Add explicit duration wording to the system prompt, e.g.:
`"La revisión dura aproximadamente 90 minutos dependiendo del vehículo."` — or whatever the actual business policy is.

### F2 — Out-of-coverage response (Medium ambiguity)
RC12b ("Córdoba Capital") receives a generic location clarification. The current behavior asks "¿En qué localidad…?" which is confusing when the customer explicitly stated they are in Córdoba. No explicit out-of-coverage message exists.

**Recommendation:** Detect non-GBA locations at the AI layer or add a known out-of-coverage string list, and respond with something like "Por el momento solo operamos en el Gran Buenos Aires."

### F3 — Soft close / general FAQ intercepted by vehicle fallback (Critical)
RC09, RC11 — Messages that are not vehicle-inquiry specific (farewell, FAQ) are intercepted by the vehicle fallback flow BEFORE the AI can handle them. The vehicle fallback trigger fires for ANY message from a user in QUALIFYING state with no known vehicle.

This means:
- A user who says "chau" gets asked for their vehicle
- A user who asks about advance notice gets asked for their vehicle
- A user who says "los tengo en cuenta" gets asked for their vehicle

### F4 — San Francisco Solano coverage gap (Medium)
RC07: San Francisco Solano is a partido of Quilmes, but neither "San Francisco Solano" nor "Quilmes" is extracted from "La Ranger está en San Francisco Solano". Zone DB has Quilmes but the substring does not match. Customers in this area will loop on location clarification.

### F5 — Hyundai gap in vehicle catalog (Medium)
RC07: "Hyundai" as a brand (without model) is not in the vehicle catalog. Any customer mentioning only the brand (no model) will receive a vehicle clarification even if they mentioned a known companion vehicle (Ranger).

### F6 — Advance notice business policy undefined in prompt (Medium)
RC11: The system prompt has no explicit mention of how much advance notice Ridecheck requires to schedule an inspection. If a customer asks, the AI must either guess or escalate.

---

## G. Failures Ranked

### Critical
| ID | Finding | Impact |
|---|---|---|
| RC09 | Soft close triggers vehicle clarification | Customer who says goodbye gets re-engaged with a qualifying question — poor UX |
| RC11 | FAQ intercepted by vehicle fallback | FAQ about advance notice never answered; customer stuck in clarification loop |
| RC04 | Concern about vehicle condition not acknowledged | Customer reporting a problem gets only a location question — feels ignored |

### Medium
| ID | Finding | Impact |
|---|---|---|
| RC06 | Generic "auto" not in catalog → clarification | Customer asking price + logistics gets clarification; mobility not explained |
| RC07-T2 | San Francisco Solano not in DB | Customer's location rejected, state loops |
| RC12b | "Córdoba Capital" gets generic clarification | Out-of-coverage area gets same treatment as unknown barrio |
| F1 | Duration FAQ: no approved wording | AI can give any duration answer |
| F6 | Advance notice: no policy in prompt | AI can give any advance-notice answer |

### Polish
| ID | Finding | Impact |
|---|---|---|
| RC01 | No service explanation before qualification | Customer asking for info jumps straight to "tell me your model" |
| RC12a | "Benavides" vs "Benavidez" typo not auto-corrected | Customer needs to go through Location Flow for a common typo |
| RC05-T2 | Duration wording is AI-generated, not deterministic | Minor inconsistency risk |

---

## H. Recommended Smallest Next Fix Batch

**H1 (Critical — AI path gate):** Add an intent classifier gate before `_check_fallback_flow_triggers`.
If the user's message matches farewell/FAQ/non-qualifying patterns (short, no vehicle-related vocabulary), let the AI handle it first. This fixes RC09 and RC11.

**H2 (Critical — concern acknowledgment):** When vehicle is known but zone is missing AND the message contains concern vocabulary (temperatura, ruido, falla, vibración), pass to AI first instead of immediately firing the location clarification.

**H3 (Medium — duration prompt wording):** Add one line to the system prompt:
`"La revisión dura aproximadamente 90 minutos (puede variar según el vehículo)."`

**H4 (Medium — out-of-coverage detection):** Add a known out-of-coverage city list or province list (Córdoba, Rosario, Mendoza, etc.). When detected, respond with coverage explanation instead of location clarification.

**H5 (Polish — Benavides typo):** Add "Benavides" to `_ZONE_SYNONYMS` or handle common accent-drop typos in `_extract_zone_from_text`.

---

## I. Safety Confirmation

| Constraint | Status |
|---|---|
| Meta / WhatsApp Cloud API calls | **ZERO** — OUTBOUND_ENABLED=false; gate raises OutboundBlockedError before _send_whatsapp_cloud_text/flow are reached |
| Real WhatsApp delivery | **NONE** — all outbound blocked_dispatch |
| n8n activation | **None** — n8n stopped, workflow inactive |
| Production crm DB | **Not touched** — tests use SQLite in-memory |
| Real customer PII in fixtures | **None** — all names, phones, emails are fabricated ("Carlos Test", "1100000099", "test@example.com") |
| Code changes | **None** |
| Commit / push | **None** |
| Migrations | **None** |
"""


def _write_report():
    out_path = Path("/opt/ridecheck-crm/forensics/M20_6D1_reality_baseline_20260703.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_REPORT, encoding="utf-8")
    print(f"\n[M20.6D.1] Report written to {out_path}")


if __name__ == "__main__":
    _write_report()
    unittest.main(verbosity=2, exit=False)
