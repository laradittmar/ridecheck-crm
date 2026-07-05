"""test_m20_6d2_customer_reality.py — M20.6D.2B Acceptance Suite

Verifies the routing-gate, copy, and direct-Flow dispatch behavior introduced in M20.6D.2B.

Key invariant: direct Flow tests do NOT pre-set *_clarification_sent flags.
The routing gate must reach the Flow on the first relevant turn without manual state manipulation.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
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


# ── Stub app.db ───────────────────────────────────────────────────────────────
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

# ── Stub optional heavy deps ──────────────────────────────────────────────────
for _m in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

# ── Import models and engine ──────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    Lead, ViaticosZone,
    WhatsAppContact, WhatsAppMessage,
    WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState,
)

Base.metadata.create_all(_engine)

from app.repositories.pricing_repository import PricingRepository
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import (
    ConversationEngine,
    _COVERAGE_RESPONSE,
    _LOC_FLOW_BODY_CONCERN,
    _LOC_FLOW_BODY_STANDARD,
    _VEH_FLOW_BODY_GENERIC,
    _VEH_FLOW_BODY_GARBLED,
    _is_general_faq_or_soft_close,
)
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

# ── Constants ─────────────────────────────────────────────────────────────────
_NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
_WA_BASE = "54000000001"  # distinct from M20.6D.1 test IDs to avoid collision


# ── Zone rows (same subset as M20.6D.1 + Sur/NULL for SFS test) ───────────────
_ZONE_ROWS = [
    ("CABA", None, 0), ("CABA", "CABA", 0), ("CABA", "Palermo", 0),
    ("CABA", "Agronomía", 0), ("CABA", "Caballito", 0), ("CABA", "Villa del Parque", 0),
    ("Norte", None, 0), ("Norte", "Benavidez", 0), ("Norte", "San Isidro", 0),
    ("Sur", None, 30000),        # group-default: handles San Francisco Solano via Flow
    ("Sur", "Quilmes", 50000), ("Sur", "Avellaneda", 30000),
    ("Oeste", None, 30000), ("Oeste", "San Justo", 30000),
]

_zones_seeded = False


def _ensure_zones(db: Session) -> None:
    global _zones_seeded
    if _zones_seeded:
        return
    for g, d, v in _ZONE_ROWS:
        db.add(ViaticosZone(zone_group=g, zone_detail=d, viaticos=v))
    db.commit()
    _zones_seeded = True


# ── DB helpers ─────────────────────────────────────────────────────────────────
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


def _seed_fresh(db: Session, wa_suffix: str):
    _clean(db)
    _ensure_zones(db)
    wa_id = _WA_BASE + wa_suffix
    contact = WhatsAppContact(wa_id=wa_id, display_name="Anon", phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
                nombre=None, necesita_humano=False)
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id,
                            unread_count=0, created_at=_NOW)
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


def _add_candidate(db: Session, thread_id: int, marca: str, modelo: str, tipo: str,
                   zone_group: str | None = None, zone_detail: str | None = None):
    cand = WhatsAppThreadCandidate(
        thread_id=thread_id, marca=marca, modelo=modelo, tipo_vehiculo=tipo,
        zone_group=zone_group, zone_detail=zone_detail,
        status="current_focus", created_at=_NOW, updated_at=_NOW,
    )
    db.add(cand)
    db.flush()
    db.commit()
    return cand


def _seed_quoted(db: Session, wa_suffix: str, marca: str, modelo: str, tipo: str,
                 zone_group: str, zone_detail: str):
    contact, thread, lead, state = _seed_fresh(db, wa_suffix)
    cand = _add_candidate(db, thread.id, marca, modelo, tipo, zone_group, zone_detail)
    lead.flag = "PRESUPUESTO_ENVIADO"
    state.last_stage = "QUOTED"
    state.home_zone_group = zone_group
    state.home_zone_detail = zone_detail
    db.commit()
    return contact, thread, lead, state, cand


# ── Engine factory ─────────────────────────────────────────────────────────────
def _make_settings(vehicle_flow_id: str = "FAKE_VEH_FLOW",
                   location_flow_id: str = "FAKE_LOC_FLOW",
                   booking_flow_id: str = "FAKE_BOOKING_FLOW"):
    s = MagicMock()
    s.openai_api_key = "sk-test-fake"
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


def _event(thread_id: int, wa_id: str, text: str, msg_id: str,
           recent: list[str] | None = None) -> ConversationHandleIn:
    msgs = recent or [text]
    return ConversationHandleIn(
        thread_id=thread_id, wa_message_id=msg_id, wa_id=wa_id,
        text=text, recent_user_messages=msgs, unanswered_recent_user_messages=msgs,
    )


def _flow_event(thread_id: int, wa_id: str, msg_id: str,
                flow_data: dict, token: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id, wa_message_id=msg_id, wa_id=wa_id,
        text="", recent_user_messages=[], unanswered_recent_user_messages=[],
        message_type="flow_response", flow_response=flow_data, flow_token=token,
    )


# ── AI helpers ─────────────────────────────────────────────────────────────────
class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body
    def read(self) -> bytes:
        return self._body
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _ai_resp(reply: str, lead_flag: str | None = None,
             needs_human: bool = False, extracted: dict | None = None,
             candidate: dict | None = None) -> _FakeHTTPResponse:
    body = json.dumps({"choices": [{"message": {"content": json.dumps({
        "intent": "OTHER", "reply": reply, "lead_flag": lead_flag,
        "flag_accepted": bool(lead_flag), "needs_human": needs_human,
        "extracted": extracted or {},
        "candidate": candidate or {"action": "none"},
    })}}]}).encode()
    return _FakeHTTPResponse(body)


def _get_blocked(db: Session, thread_id: int) -> list[str]:
    rows = db.execute(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.thread_id == thread_id,
               WhatsAppMessage.status == "blocked")
        .order_by(WhatsAppMessage.id.desc())
    ).scalars().all()
    return [r.text for r in rows]


def _extract_system_prompt(mock_urlopen) -> str:
    """Pull the system prompt from the last mocked OpenAI call."""
    if not mock_urlopen.called:
        return ""
    req = mock_urlopen.call_args[0][0]
    body = json.loads(req.data.decode())
    for msg in body.get("messages", []):
        if msg.get("role") == "system":
            return msg["content"]
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# RC01 — Info-only reaches AI
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC01InfoOnly(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC01")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc01_info_only_reaches_ai(self, mock_urlopen):
        """General inquiry with no vehicle reaches AI, not Vehicle Flow."""
        mock_urlopen.return_value = _ai_resp(
            "¡Hola! Hacemos revisiones pre-compra de autos en CABA y GBA. "
            "¿Qué vehículo estás por comprar?",
        )
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Hola, quiero que revisen un vehículo que estoy por comprar.",
            "6D2RC01T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        # AI was called (routing gate sets skip_fallback=True → goes to AI)
        self.assertEqual(mock_urlopen.call_count, 1, "AI must be called for info-only turn")
        self.assertEqual(result.action, "blocked_dispatch")

        # No vehicle question (the old fallback clarification text must NOT appear)
        veh_q = any(
            "¿me podés indicar la marca" in t.lower() for t in blocked
        )
        self.assertFalse(veh_q, f"Must not ask vehicle clarification: {blocked}")

        # No Vehicle Flow (not a price request)
        veh_flow_body = any(_VEH_FLOW_BODY_GENERIC in t or _VEH_FLOW_BODY_GARBLED in t
                            for t in blocked)
        self.assertFalse(veh_flow_body, f"Must not dispatch Vehicle Flow: {blocked}")
        self.assertFalse(self.state.needs_human)

    @patch("urllib.request.urlopen")
    def test_rc01_msg2_passes_info_to_ai(self, mock_urlopen):
        """'¿Me pasás info?' also reaches AI without any Flow."""
        mock_urlopen.return_value = _ai_resp("Claro, hacemos revisiones pre-compra...")
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, "¿Me pasás info?", "6D2RC01T2",
        ))
        self.assertEqual(mock_urlopen.call_count, 1, "AI must be called for FAQ")
        self.assertEqual(result.action, "blocked_dispatch")


# ═══════════════════════════════════════════════════════════════════════════════
# RC02 — Location before vehicle
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC02LocationBeforeVehicle(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC02")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc02_palermo_retained_fiesta_quoted(self, mock_urlopen):
        """Zone from step 1 is retained; step 2 vehicle → AI produces correct quote."""
        # Step 1: "Está por Palermo" — vehicle unknown, goes to AI (no price question)
        mock_urlopen.return_value = _ai_resp(
            "¡Genial! Registré Palermo. ¿Qué vehículo vas a revisar?",
            extracted={"zone_detail": "Palermo"},
        )
        self.eng.handle(_event(
            self.thread.id, self.wa_id, "Está por Palermo", "6D2RC02T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        # Zone must be extracted by engine BEFORE AI call (deterministic pre-AI)
        self.assertEqual(self.state.home_zone_group, "CABA")
        self.assertEqual(self.state.home_zone_detail, "Palermo")

        # Step 2: "Fiesta Kinetic" → catalog→AUTO, Palermo already known → quote
        self.state.last_processed_inbound_wa_message_id = "6D2RC02T1"
        self.db.commit()

        mock_urlopen.reset_mock()
        mock_urlopen.return_value = _ai_resp(
            "Genial! La revisión del Ford Fiesta en Palermo es de $130.000. ¿Avanzamos?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"zone_detail": "Palermo"},
            candidate={"action": "create", "marca": "Ford", "modelo": "Fiesta",
                       "tipo_vehiculo": "AUTO", "status": "current_focus"},
        )
        result2 = self.eng.handle(_event(
            self.thread.id, self.wa_id, "Es un Fiesta Kinetic AT", "6D2RC02T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked2 = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result2.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "QUOTED")
        # Zone must NOT be re-asked
        self.assertFalse(any("¿En qué" in t for t in blocked2),
                         "Must not re-ask for location")
        # Price in reply
        self.assertTrue(any("130" in t for t in blocked2),
                        f"Quote must contain $130k: {blocked2}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC03 — Landmark resolved directly (no Location Flow)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC03Landmark(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC03")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc03_agronomia_landmark_resolves_no_flow(self, mock_urlopen):
        """'Facultad de Agronomía' resolves to CABA; no Location Flow; correct quote."""
        mock_urlopen.return_value = _ai_resp(
            "Genial! La revisión del Ford Focus en Agronomía (CABA) es de $130.000. "
            "¿Cuándo te queda bien?",
            lead_flag="PRESUPUESTO_ENVIADO",
            candidate={"action": "create", "marca": "Ford", "modelo": "Focus",
                       "tipo_vehiculo": "AUTO", "status": "current_focus"},
        )
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Quiero revisar un Ford Focus SE Plus 2017 por la Facultad de Agronomía.",
            "6D2RC03T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked(self.db, self.thread.id)

        # Zone resolved deterministically before AI
        self.assertEqual(self.state.home_zone_group, "CABA")
        self.assertEqual(self.state.home_zone_detail, "Agronomía")

        # No Location Flow (zone was known before routing gate ran)
        self.assertFalse(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                         "Must not dispatch Location Flow when landmark resolves")
        self.assertFalse(any(_LOC_FLOW_BODY_CONCERN in t for t in blocked))

        # Quote in reply
        self.assertTrue(any("130" in t for t in blocked),
                        f"Quote must be present: {blocked}")
        self.assertEqual(self.state.last_stage, "QUOTED")


# ═══════════════════════════════════════════════════════════════════════════════
# RC04 — Vehicle with concern → direct Location Flow (no plain-text question)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC04ConcernDirectFlow(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC04")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc04_concern_dispatches_location_flow_directly(self, mock_urlopen):
        """Ecosport + concern → Location Flow on first turn; no clarification_sent pre-set."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # PROOF: flags are False BEFORE the call
        self.assertFalse(self.state.location_clarification_sent)
        self.assertFalse(self.state.location_fallback_flow_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Quiero revisar una Ecosport 2011. Está levantando temperatura.",
            "6D2RC04T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0, "AI must not be called")

        # Concern-aware Flow body was dispatched
        self.assertTrue(any(_LOC_FLOW_BODY_CONCERN in t for t in blocked),
                        f"Concern-aware Location Flow body expected: {blocked}")

        # No plain-text locality question
        plain_q = any("¿En qué localidad" in t or "¿En qué barrio" in t for t in blocked)
        self.assertFalse(plain_q, f"Must not send plain-text location question: {blocked}")

        # Vehicle candidate created (Ecosport catalog hit)
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any(c.tipo_vehiculo == "SUV_4X4_DEPORTIVO" for c in cands),
                        f"Ecosport must be SUV_4X4_DEPORTIVO: {[c.tipo_vehiculo for c in cands]}")

        # Concern body must NOT diagnose or guarantee
        body_text = _LOC_FLOW_BODY_CONCERN
        self.assertNotIn("garantiz", body_text.lower())
        self.assertNotIn("reparar", body_text.lower())
        self.assertNotIn("seguro", body_text.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# RC05 — Re-questions from QUOTED (duration FAQ)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC05Requote(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state, self.cand = _seed_quoted(
            self.db, "6D2RC05", "Peugeot", "3008", "SUV/4x4", "Norte", "Benavidez"
        )
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc05_price_requote(self, mock_urlopen):
        """Re-ask of price returns stored quote; stage stays QUOTED."""
        mock_urlopen.return_value = _ai_resp(
            "El precio para la revisión del Peugeot 3008 en Benavidez es $140.000. ¿Avanzamos?",
        )
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, "¿Cuánto salía en esa zona?", "6D2RC05T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "QUOTED")
        self.assertTrue(any("140" in t for t in blocked),
                        f"Stored quote must be returned: {blocked}")

    @patch("urllib.request.urlopen")
    def test_rc05_duration_faq_prompt_contains_approved_wording(self, mock_urlopen):
        """Duration FAQ reaches AI; system prompt contains 'una hora' (policy rule 15)."""
        mock_urlopen.return_value = _ai_resp(
            "La revisión suele durar aproximadamente una hora. ¿Coordinamos?",
        )
        self.state.last_processed_inbound_wa_message_id = "6D2RC05T1"
        self.db.commit()

        self.eng.handle(_event(
            self.thread.id, self.wa_id, "¿Cuánto dura el check?", "6D2RC05T2",
        ))
        system_prompt = _extract_system_prompt(mock_urlopen)

        self.assertIn("una hora", system_prompt,
                      "System prompt must contain 'una hora' (rule 15)")
        self.assertNotIn("90 minutos", system_prompt,
                         "Must not use '90 minutos' — only 'una hora' is approved")


# ═══════════════════════════════════════════════════════════════════════════════
# RC06 — Generic auto + price + logistics → direct Vehicle Flow
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC06GenericAutoDirectFlow(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC06")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc06_generic_auto_price_question_dispatches_vehicle_flow_directly(self, mock_urlopen):
        """'un auto + ¿Cuánto sale?' → Vehicle Flow on first turn; no clarification_sent pre-set."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # PROOF: clarification flag is False BEFORE call
        self.assertFalse(self.state.vehicle_clarification_sent)
        self.assertFalse(self.state.vehicle_fallback_flow_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Quiero revisar un auto antes de comprarlo. ¿Cuánto sale y cómo hacen para ir hasta donde está?",
            "6D2RC06T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0, "AI must not be called")

        # Generic vehicle + logistics Flow body
        self.assertTrue(any(_VEH_FLOW_BODY_GENERIC in t for t in blocked),
                        f"Vehicle Flow with logistics body expected: {blocked}")

        # On-location explanation in the Flow body
        self.assertIn("donde está el auto", _VEH_FLOW_BODY_GENERIC)

        # No plain-text vehicle clarification
        plain_q = any("¿Me podés indicar" in t or "marca y el modelo" in t for t in blocked)
        self.assertFalse(plain_q, f"Must not send plain-text vehicle question: {blocked}")

        # No invented price
        self.assertFalse(any("$" in t and "Flow" not in t for t in blocked
                             if _VEH_FLOW_BODY_GENERIC not in t),
                         "No invented price before vehicle is known")

    @patch("urllib.request.urlopen")
    def test_rc06_valid_vehicle_flow_response_creates_auto_candidate(self, mock_urlopen):
        """After Vehicle Flow response AUTO → candidate created; pricing proceeds."""
        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "6D2RC06T1"
        self.db.commit()

        flow_data = {"tipo_vehiculo": "AUTO", "marca": "Volkswagen", "modelo": "Gol",
                     "anio": "2020"}
        mock_urlopen.side_effect = None
        mock_urlopen.return_value = _ai_resp(
            "¡Perfecto! ¿En qué zona está el Volkswagen Gol?",
            extracted={},
        )
        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "6D2RC06FLOW", flow_data, "fake-tok",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any(c.tipo_vehiculo == "AUTO" for c in cands),
                        f"AUTO candidate expected: {[c.tipo_vehiculo for c in cands]}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC07 — Ranger + San Francisco Solano
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC07RangerSanFranciscoSolano(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC07")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc07_ranger_known_no_zone_dispatches_location_flow(self, mock_urlopen):
        """Ranger (catalog→SUV_4X4_DEPORTIVO); San Francisco Solano not in DB → Location Flow."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # PROOF: clarification flag is False
        self.assertFalse(self.state.location_clarification_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "La Ranger está en San Francisco Solano y podría ser mañana.",
            "6D2RC07T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)

        # Location Flow dispatched (standard — no concern in message)
        self.assertTrue(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                        f"Standard Location Flow body expected: {blocked}")

        # Zone NOT silently resolved (Solano not in DB)
        self.assertIsNone(self.state.home_zone_group,
                          f"Zone must not be resolved: {self.state.home_zone_group}")

        # Ranger candidate created
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any("Ranger" in (c.modelo or "") for c in cands),
                        f"Ranger candidate expected: {[c.modelo for c in cands]}")

    @patch("urllib.request.urlopen")
    def test_rc07_flow_response_sur_solano_uses_group_default(self, mock_urlopen):
        """Flow response zona_general=SUR + localidad=San Francisco Solano → Sur group-default.

        Phase A audit finding: 'San Francisco Solano' has no exact zone row in crm_test.
        Location Flow response with zona_general='SUR' resolves to Sur/NULL/30000 (group-default).
        Pricing: SUV_4X4_DEPORTIVO ($140k) + $30k viáticos = $170k.
        """
        # Pre-seed: Ranger vehicle known, flow already dispatched
        _add_candidate(self.db, self.thread.id, "Ford", "Ranger", "SUV_4X4_DEPORTIVO")
        self.state.location_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "6D2RC07T1"
        self.db.commit()

        flow_data = {
            "zona_general": "SUR",
            "localidad": "San Francisco Solano",
            "referencia_ubicacion": "",
        }
        mock_urlopen.return_value = _ai_resp(
            "¡Perfecto! Para la revisión del Ford Ranger en San Francisco Solano "
            "el precio es de $170.000 (base $140.000 + viáticos $30.000). ¿Avanzamos?",
            lead_flag="PRESUPUESTO_ENVIADO",
        )
        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "6D2RC07FLOW", flow_data, "fake-tok",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        # Zone group resolved to Sur (via _FLOW_ZONE_GROUP_MAP)
        self.assertEqual(self.state.home_zone_group, "Sur",
                         f"Flow response must resolve to Sur: {self.state.home_zone_group}")
        # Price: $140k + $30k (group-default) = $170k
        self.assertTrue(any("170" in t for t in blocked),
                        f"Price must be $170k (Sur group-default viáticos): {blocked}")
        self.assertEqual(self.state.last_stage, "QUOTED")


# ═══════════════════════════════════════════════════════════════════════════════
# RC08 — Garbled vehicle + price → direct Vehicle Flow
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC08GarbledDirectFlow(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC08")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc08_garbled_vehicle_dispatches_vehicle_flow_directly(self, mock_urlopen):
        """'auditorio' + price question → Vehicle Flow on first turn; shorter body."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # PROOF: flag False before call
        self.assertFalse(self.state.vehicle_clarification_sent)
        self.assertFalse(self.state.vehicle_fallback_flow_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Quiero chequear un auditorio antes de comprarlo, ¿cuánto está el servicio?",
            "6D2RC08T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)

        # Shorter (garbled) body — not the generic one
        self.assertTrue(any(_VEH_FLOW_BODY_GARBLED in t for t in blocked),
                        f"Garbled Vehicle Flow body expected: {blocked}")

        # No plain-text vehicle question
        plain = any("¿Me podés indicar" in t or "marca y el modelo" in t for t in blocked)
        self.assertFalse(plain, f"Must not send plain-text vehicle question: {blocked}")

        # No invented vehicle category
        self.assertFalse(any("auditorio" in t for t in blocked),
                         "Must not echo 'auditorio' as a vehicle")

    @patch("urllib.request.urlopen")
    def test_rc08_vehicle_flow_response_creates_candidate(self, mock_urlopen):
        """Vehicle Flow response AUTO → correct candidate; location clarification next."""
        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "6D2RC08T1"
        self.db.commit()

        flow_data = {"tipo_vehiculo": "AUTO", "marca": "Peugeot", "modelo": "208", "anio": "2019"}
        mock_urlopen.side_effect = None
        # No zone yet → location flow fires (not AI)
        mock_urlopen.side_effect = AssertionError("AI not expected — location flow fires")

        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "6D2RC08FLOW", flow_data, "fake-tok",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any(c.tipo_vehiculo == "AUTO" for c in cands),
                        f"AUTO candidate expected: {[c.tipo_vehiculo for c in cands]}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC09 — Soft close reaches AI (no Flow, no vehicle question)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC09SoftClose(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC09")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc09_soft_close_reaches_ai(self, mock_urlopen):
        """'Dale, los tengo en cuenta' → AI handles it; no vehicle question; no escalation."""
        mock_urlopen.return_value = _ai_resp(
            "¡Perfecto! Cuando quieras, escribinos y coordinamos. ¡Éxito con la búsqueda!",
        )
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Dale, los tengo en cuenta si aparece algo.",
            "6D2RC09T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(mock_urlopen.call_count, 1, "AI must be called for soft close")
        self.assertEqual(result.action, "blocked_dispatch")
        self.assertFalse(self.state.needs_human)

        # No vehicle clarification
        veh_q = any("vehículo" in t and "podés indicar" in t for t in blocked)
        self.assertFalse(veh_q, f"Must not send vehicle question on soft close: {blocked}")

        # No Vehicle Flow
        self.assertFalse(any(_VEH_FLOW_BODY_GENERIC in t or _VEH_FLOW_BODY_GARBLED in t
                             for t in blocked),
                         "Must not dispatch Vehicle Flow on soft close")


# ═══════════════════════════════════════════════════════════════════════════════
# RC10 — Full data paste
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC10FullDataPaste(unittest.TestCase):
    _MSG = (
        "Hola! Me interesa coordinar una revisión. "
        "Vehículo: Focus 2.0 2019. Zona: Palermo. "
        "Vendedora: Ana Ejemplo (particular). "
        "Nombre: Carlos Test, tel 1100000099, email test@example.com. "
        "Me gustaría el próximo lunes a las 14 hs."
    )

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC10")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc10_full_data_quote_no_premature_booking(self, mock_urlopen):
        """Full data → quote only; no booking without explicit acceptance."""
        mock_urlopen.return_value = _ai_resp(
            "Genial! La revisión del Ford Focus en Palermo es $130.000. ¿Confirmás?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"customer_name": "Carlos Test", "zone_detail": "Palermo"},
            candidate={"action": "create", "marca": "Ford", "modelo": "Focus",
                       "tipo_vehiculo": "AUTO", "status": "current_focus"},
        )
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, self._MSG, "6D2RC10T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "QUOTED")
        self.assertNotEqual(self.state.last_stage, "SCHEDULING",
                            "Must not advance to SCHEDULING without acceptance")
        # No booking revision
        from app.models import ThreadRevision
        rev = self.db.execute(
            select(ThreadRevision).where(ThreadRevision.thread_id == self.thread.id)
        ).scalars().first()
        self.assertIsNone(rev, "No booking without acceptance")


# ═══════════════════════════════════════════════════════════════════════════════
# RC11 — Advance-notice FAQ reaches AI; prompt has approved wording
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC11AdvanceNoticeFAQ(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC11")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc11_faq_reaches_ai_prompt_has_approved_wording(self, mock_urlopen):
        """'con cuánto anticipo' reaches AI; system prompt has rule 15 + 16 wording."""
        mock_urlopen.return_value = _ai_resp(
            "Cuanto antes nos avisás, mejor — coordinamos según la disponibilidad. "
            "¿Ya tenés el vehículo en mente?",
        )
        self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Hola, con cuánto tiempo d anticipo te aviso para que me hagas un check?",
            "6D2RC11T1",
        ))
        system_prompt = _extract_system_prompt(mock_urlopen)

        self.assertEqual(mock_urlopen.call_count, 1, "FAQ must reach AI")
        # Rule 15: duration
        self.assertIn("una hora", system_prompt,
                      "System prompt must contain 'una hora' (rule 15)")
        # Rule 16: advance notice / availability
        self.assertIn("disponibilidad de agenda", system_prompt,
                      "System prompt must contain advance-notice wording (rule 16)")
        # Rule 14: on-location
        self.assertIn("en el lugar donde está el auto", system_prompt,
                      "System prompt must contain on-location statement (rule 14)")


# ═══════════════════════════════════════════════════════════════════════════════
# RC12a — Typo "Benavides" → direct Location Flow (no silent mapping)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC12aBenavidesDirect(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC12A")
        # Vehicle already known: Ford Focus / AUTO
        _add_candidate(self.db, self.thread.id, "Ford", "Focus", "AUTO")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc12a_benavides_typo_dispatches_location_flow_directly(self, mock_urlopen):
        """'Está en Benavides.' → Location Flow on first turn; no silent mapping."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # PROOF: clarification flag False before call
        self.assertFalse(self.state.location_clarification_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, "Está en Benavides.", "6D2RC12AT1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)

        # Standard Location Flow body
        self.assertTrue(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                        f"Standard Location Flow body expected: {blocked}")

        # NO silent mapping to Benavidez
        self.assertNotEqual(self.state.home_zone_detail, "Benavidez",
                            "Must NOT silently map 'Benavides' to 'Benavidez'")
        self.assertIsNone(self.state.home_zone_group,
                          f"Zone group must be None (typo not resolved): {self.state.home_zone_group}")

        # No plain-text locality question
        plain = any("¿En qué localidad" in t or "¿En qué barrio" in t for t in blocked)
        self.assertFalse(plain, f"Must not send plain-text location question: {blocked}")

    @patch("urllib.request.urlopen")
    def test_rc12a_location_flow_response_norte_benavidez_quotes_auto(self, mock_urlopen):
        """After Location Flow: NORTE/Benavidez → AUTO + $0 viáticos = $130k."""
        self.state.location_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "6D2RC12AT1"
        self.db.commit()

        flow_data = {"zona_general": "NORTE", "localidad": "Benavidez",
                     "referencia_ubicacion": ""}
        mock_urlopen.return_value = _ai_resp(
            "¡Perfecto! La revisión del Ford Focus en Benavidez es $130.000. ¿Avanzamos?",
            lead_flag="PRESUPUESTO_ENVIADO",
        )
        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "6D2RC12AFLOW", flow_data, "fake-tok",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.home_zone_group, "Norte")
        self.assertEqual(self.state.home_zone_detail, "Benavidez")
        self.assertTrue(any("130" in t for t in blocked),
                        f"AUTO/Benavidez quote must be $130k: {blocked}")
        self.assertEqual(self.state.last_stage, "QUOTED")


# ═══════════════════════════════════════════════════════════════════════════════
# RC12b — Córdoba Capital → exact coverage response; no Flow; needs_human=True
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC12bCordobaCoverage(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC12B")
        _add_candidate(self.db, self.thread.id, "Ford", "Focus", "AUTO")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc12b_cordoba_capital_coverage_response(self, mock_urlopen):
        """'Córdoba Capital' → exact coverage copy; needs_human=True; no Flow; no price."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, "Está en Córdoba Capital.", "6D2RC12BT1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)

        # Exact coverage response
        self.assertTrue(any(_COVERAGE_RESPONSE in t for t in blocked),
                        f"Exact coverage response expected: {blocked}")

        # No Flow
        self.assertFalse(any(_LOC_FLOW_BODY_STANDARD in t or _LOC_FLOW_BODY_CONCERN in t
                             for t in blocked),
                         "Must not dispatch Location Flow for out-of-coverage")

        # No price
        self.assertFalse(any("$" in t for t in blocked if _COVERAGE_RESPONSE not in t),
                         "No price for out-of-coverage location")

        # needs_human set
        self.assertTrue(self.state.needs_human, "needs_human must be True")
        self.assertTrue(self.lead.necesita_humano, "lead.necesita_humano must be True")


# ═══════════════════════════════════════════════════════════════════════════════
# RC13 — Vehicle correction 3008 → 208 from QUOTED
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC13VehicleCorrection(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state, self.cand = _seed_quoted(
            self.db, "6D2RC13", "Peugeot", "3008", "SUV/4x4", "Norte", "Benavidez"
        )
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc13_correction_3008_to_208_benavidez_retained_auto_price(self, mock_urlopen):
        """'No, era un 208' → AUTO/Benavidez; zone retained; $130k; no old $140k price."""
        mock_urlopen.return_value = _ai_resp(
            "Claro, actualicé el vehículo. La revisión del Peugeot 208 en Benavidez "
            "es de $130.000. ¿Avanzamos?",
            lead_flag="PRESUPUESTO_ENVIADO",
            extracted={"zone_detail": "Benavidez"},
            candidate={"action": "update", "id": None,
                       "marca": "Peugeot", "modelo": "208", "tipo_vehiculo": "AUTO"},
        )
        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, "No, era un 208.", "6D2RC13T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked(self.db, self.thread.id)

        self.assertEqual(result.action, "blocked_dispatch")
        # Zone retained
        self.assertEqual(self.state.home_zone_group, "Norte")
        self.assertEqual(self.state.home_zone_detail, "Benavidez")
        # Correct price for AUTO
        self.assertTrue(any("130" in t for t in blocked),
                        f"AUTO price $130k expected: {blocked}")
        # Old SUV price must NOT appear
        self.assertFalse(any("140" in t for t in blocked),
                         f"Old SUV $140k must not appear: {blocked}")
        # No vehicle mixing
        self.assertFalse(any("3008" in t for t in blocked),
                         f"Old vehicle 3008 must not appear in reply: {blocked}")
        self.assertEqual(self.state.last_stage, "QUOTED")


# ═══════════════════════════════════════════════════════════════════════════════
# Safety — zero Meta calls; no clarification-flag pre-sets for direct Flow tests
# ═══════════════════════════════════════════════════════════════════════════════
class TestSafetyAndFlagIntegrity(unittest.TestCase):
    """Section H proof: direct Flow routes do not require pre-set clarification flags."""

    def test_no_clarification_flags_needed_for_direct_vehicle_flow(self):
        """Routing gate dispatches Vehicle Flow on turn 1 without pre-set flags.
        Asserts: state.vehicle_clarification_sent=False before AND after the call.
        """
        os.environ.pop("OUTBOUND_ENABLED", None)
        db = _SessionLocal()
        try:
            contact, thread, lead, state = _seed_fresh(db, "6D2SAFEVEH")
            self.assertFalse(state.vehicle_clarification_sent, "Must start False")

            eng = _make_engine(db)
            with patch("urllib.request.urlopen") as mu:
                mu.side_effect = AssertionError("AI not expected")
                eng.handle(_event(
                    thread.id, contact.wa_id,
                    "Quiero cotizar un auto. ¿Cuánto cuesta?",
                    "6D2SAFEVEH_T1",
                ))
            db.expire_all()
            db.refresh(state)
            # clarification_sent remains False (direct path sets flow_sent NOT clarification_sent)
            self.assertFalse(state.vehicle_clarification_sent,
                             "vehicle_clarification_sent must NOT be set by direct dispatch")
        finally:
            db.close()
            os.environ.pop("OUTBOUND_ENABLED", None)

    def test_no_clarification_flags_needed_for_direct_location_flow(self):
        """Routing gate dispatches Location Flow on turn 1 without pre-set flags."""
        os.environ.pop("OUTBOUND_ENABLED", None)
        db = _SessionLocal()
        try:
            contact, thread, lead, state = _seed_fresh(db, "6D2SAFELOC")
            _add_candidate(db, thread.id, "Ford", "Ecosport", "SUV_4X4_DEPORTIVO")
            self.assertFalse(state.location_clarification_sent, "Must start False")

            eng = _make_engine(db)
            with patch("urllib.request.urlopen") as mu:
                mu.side_effect = AssertionError("AI not expected")
                eng.handle(_event(
                    thread.id, contact.wa_id,
                    "El auto está en Quilmes.",
                    "6D2SAFELOC_T1",
                ))
            db.expire_all()
            db.refresh(state)
            self.assertFalse(state.location_clarification_sent,
                             "location_clarification_sent must NOT be set by direct dispatch")
        finally:
            db.close()
            os.environ.pop("OUTBOUND_ENABLED", None)

    @patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text")
    @patch("app.ui.whatsapp_ui._send_whatsapp_cloud_flow")
    def test_zero_meta_calls_outbound_disabled(self, mock_flow, mock_text):
        """With OUTBOUND_ENABLED unset, Meta endpoints are never invoked."""
        os.environ.pop("OUTBOUND_ENABLED", None)
        db = _SessionLocal()
        try:
            contact, thread, lead, state = _seed_fresh(db, "6D2SAFEMETA")
            eng = _make_engine(db)
            with patch("urllib.request.urlopen") as mu:
                mu.side_effect = AssertionError("AI not needed")
                eng.handle(_event(
                    thread.id, contact.wa_id,
                    "Quiero cotizar un auto. ¿Cuánto cuesta?",
                    "6D2SAFEMETA_T1",
                ))
        finally:
            db.close()
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(mock_text.call_count, 0,
                         "_send_whatsapp_cloud_text must never be called")
        self.assertEqual(mock_flow.call_count, 0,
                         "_send_whatsapp_cloud_flow must never be called")


# ═══════════════════════════════════════════════════════════════════════════════
# RC14 — Known vehicle + service FAQ + missing location → AI (M20.6D.2B.1)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC14FAQWithKnownVehicle(unittest.TestCase):
    """Vehicle is known, zone is missing, but message is a service FAQ.
    Expected: routing gate detects FAQ → AI, NOT Location Flow.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC14")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_unit_faq_helper_fires_for_que_revisan(self):
        """_is_general_faq_or_soft_close fires for 'qué revisan' regardless of vehicle prefix."""
        self.assertTrue(_is_general_faq_or_soft_close("Es un Focus 2019, ¿qué revisan?"))
        self.assertTrue(_is_general_faq_or_soft_close("¿qué incluye la revisión?"))
        self.assertTrue(_is_general_faq_or_soft_close("¿cuánto dura el check?"))
        self.assertTrue(_is_general_faq_or_soft_close("¿cómo trabajan?"))

    def test_unit_faq_helper_does_not_fire_for_price_question(self):
        """_is_general_faq_or_soft_close does NOT fire for pure price questions."""
        self.assertFalse(_is_general_faq_or_soft_close("¿cuánto sale?"))
        self.assertFalse(_is_general_faq_or_soft_close("quiero el precio"))
        self.assertFalse(_is_general_faq_or_soft_close("necesito una cotización"))

    @patch("urllib.request.urlopen")
    def test_rc14_faq_with_known_vehicle_reaches_ai_not_location_flow(self, mock_urlopen):
        """'Es un Focus 2019, ¿qué revisan?' → AI; no Location Flow; vehicle recognized."""
        mock_urlopen.return_value = _ai_resp(
            "¡Hola! En la revisión pre-compra inspeccionamos más de 100 puntos del vehículo. "
            "¿En qué zona está el Focus?",
        )
        # PROOF: no clarification flags pre-set
        self.assertFalse(self.state.location_clarification_sent)
        self.assertFalse(self.state.location_fallback_flow_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Es un Focus 2019, ¿qué revisan?",
            "6D2RC14T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        # AI called exactly once
        self.assertEqual(mock_urlopen.call_count, 1, "AI must be called for FAQ turn")
        self.assertEqual(result.action, "blocked_dispatch")

        # No Location Flow
        self.assertFalse(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                         f"Must not dispatch standard Location Flow: {blocked}")
        self.assertFalse(any(_LOC_FLOW_BODY_CONCERN in t for t in blocked),
                         f"Must not dispatch concern Location Flow: {blocked}")

        # No Vehicle Flow
        self.assertFalse(any(_VEH_FLOW_BODY_GENERIC in t or _VEH_FLOW_BODY_GARBLED in t
                             for t in blocked),
                         f"Must not dispatch Vehicle Flow: {blocked}")

        # No plain-text locality clarification
        plain = any("¿En qué localidad" in t or "¿En qué barrio" in t for t in blocked)
        self.assertFalse(plain, f"Must not send plain-text location question: {blocked}")

        # No human escalation
        self.assertFalse(self.state.needs_human, "needs_human must be False")

        # Stage stays QUALIFYING (AI didn't advance it)
        self.assertIn(self.state.last_stage, ("QUALIFYING", None),
                      f"Stage must remain QUALIFYING: {self.state.last_stage}")

        # Vehicle candidate Focus → AUTO
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any(c.tipo_vehiculo == "AUTO" for c in cands),
                        f"Focus must be recognized as AUTO: {[c.tipo_vehiculo for c in cands]}")

    @patch("urllib.request.urlopen")
    def test_rc14_price_question_after_faq_dispatches_location_flow(self, mock_urlopen):
        """After FAQ handled by AI, an explicit price question dispatches Location Flow."""
        # Step 1: FAQ → AI
        mock_urlopen.return_value = _ai_resp("Revisamos más de 100 puntos. ¿En qué zona está?")
        self.eng.handle(_event(
            self.thread.id, self.wa_id, "Es un Focus 2019, ¿qué revisan?", "6D2RC14T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)

        # Step 2: Explicit price question → Location Flow (vehicle now in DB as Focus/AUTO)
        self.state.last_processed_inbound_wa_message_id = "6D2RC14T1"
        self.db.commit()

        mock_urlopen.reset_mock()
        mock_urlopen.side_effect = AssertionError("AI must not be called for price turn")

        result2 = self.eng.handle(_event(
            self.thread.id, self.wa_id, "¿Y cuánto sale?", "6D2RC14T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked2 = _get_blocked(self.db, self.thread.id)

        # Standard Location Flow (vehicle known, zone still unknown, price intent)
        self.assertTrue(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked2),
                        f"Location Flow expected for price question after FAQ: {blocked2}")
        self.assertEqual(result2.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# RC15 — Known vehicle + soft close + missing location → AI (M20.6D.2B.1)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC15SoftCloseWithKnownVehicle(unittest.TestCase):
    """Vehicle is known, zone is missing, but message is a soft close.
    Expected: routing gate detects soft close → AI, NOT Location Flow.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC15")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_unit_soft_close_helper_fires_for_expected_phrases(self):
        """_is_general_faq_or_soft_close fires for all spec-listed soft-close phrases."""
        self.assertTrue(_is_general_faq_or_soft_close("gracias"))
        self.assertTrue(_is_general_faq_or_soft_close("Dale, lo tengo en cuenta"))
        self.assertTrue(_is_general_faq_or_soft_close("cualquier cosa te escribo"))
        self.assertTrue(_is_general_faq_or_soft_close("por ahora no"))
        self.assertTrue(_is_general_faq_or_soft_close("más adelante lo consulto"))
        self.assertTrue(_is_general_faq_or_soft_close("te aviso si aparece algo"))

    def test_unit_soft_close_does_not_suppress_concern(self):
        """Soft-close phrase combined with concern word must NOT suppress concern path.
        Caller checks NOT _has_vehicle_concern — this unit test verifies the helpers
        are independent so the caller's AND condition works correctly.
        """
        from app.services.conversation_engine import _has_vehicle_concern
        # "gracias" matches FAQ helper
        self.assertTrue(_is_general_faq_or_soft_close("gracias, pero el motor falla"))
        # Concern also fires — caller's condition (_is_faq AND NOT concern) → False → no suppression
        self.assertTrue(_has_vehicle_concern("gracias, pero el motor falla"))

    @patch("urllib.request.urlopen")
    def test_rc15_soft_close_with_known_vehicle_reaches_ai(self, mock_urlopen):
        """EcoSport + soft close → AI; no Location Flow; no qualification question; no escalation."""
        mock_urlopen.return_value = _ai_resp(
            "Perfecto, cuando estés listo escribinos y coordinamos. ¡Éxito con la búsqueda!",
        )
        # PROOF: no flags pre-set
        self.assertFalse(self.state.location_clarification_sent)
        self.assertFalse(self.state.location_fallback_flow_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Es una EcoSport, pero por ahora lo dejo para más adelante. Gracias.",
            "6D2RC15T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        # AI called exactly once
        self.assertEqual(mock_urlopen.call_count, 1, "AI must be called for soft close")
        self.assertEqual(result.action, "blocked_dispatch")

        # No Location Flow
        self.assertFalse(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                         f"Must not dispatch Location Flow on soft close: {blocked}")
        self.assertFalse(any(_LOC_FLOW_BODY_CONCERN in t for t in blocked))

        # No Vehicle Flow
        self.assertFalse(any(_VEH_FLOW_BODY_GENERIC in t or _VEH_FLOW_BODY_GARBLED in t
                             for t in blocked),
                         f"Must not dispatch Vehicle Flow on soft close: {blocked}")

        # No qualification question (no "¿dónde" / "¿en qué" in blocked)
        qual_q = any("¿En qué" in t or "¿Dónde" in t or "¿dónde" in t for t in blocked)
        self.assertFalse(qual_q, f"Must not ask qualification question on soft close: {blocked}")

        # No human escalation
        self.assertFalse(self.state.needs_human, "Must not escalate to human on soft close")

        # Ecosport recognized as SUV_4X4_DEPORTIVO
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any(c.tipo_vehiculo == "SUV_4X4_DEPORTIVO" for c in cands),
                        f"EcoSport must be SUV_4X4_DEPORTIVO: {[c.tipo_vehiculo for c in cands]}")


# ═══════════════════════════════════════════════════════════════════════════════
# RC16 — Known vehicle + explicit price question + missing location → Location Flow
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC16PriceWithKnownVehicleDirectFlow(unittest.TestCase):
    """Vehicle is known, zone is missing, message has explicit price intent.
    Expected: routing gate priority 5 → standard Location Flow (NOT AI, NOT concern body).
    This is the direct analogue of RC04 (concern) and RC06 (unknown vehicle + price).
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D2RC16")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc16_known_vehicle_price_question_dispatches_standard_location_flow(self, mock_urlopen):
        """'Es un Focus 2019, ¿cuánto sale?' → standard Location Flow; AI not called; no price invented."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # PROOF: clarification flags False before call
        self.assertFalse(self.state.location_clarification_sent)
        self.assertFalse(self.state.location_fallback_flow_sent)

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id,
            "Es un Focus 2019, ¿cuánto sale?",
            "6D2RC16T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        # No AI call
        self.assertEqual(mock_urlopen.call_count, 0, "AI must not be called")
        self.assertEqual(result.action, "blocked_dispatch")

        # Standard Location Flow body (not concern body)
        self.assertTrue(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                        f"Standard Location Flow body expected: {blocked}")
        self.assertFalse(any(_LOC_FLOW_BODY_CONCERN in t for t in blocked),
                         f"Concern body must NOT fire (no concern in message): {blocked}")

        # No plain-text locality clarification
        plain = any("¿En qué localidad" in t or "¿En qué barrio" in t for t in blocked)
        self.assertFalse(plain, f"Must not send plain-text location question: {blocked}")

        # No price invented before location is known
        price_invented = any(
            "$" in t and _LOC_FLOW_BODY_STANDARD not in t
            for t in blocked
        )
        self.assertFalse(price_invented, f"Must not invent price before location known: {blocked}")

        # location_clarification_sent remains False (direct dispatch uses flow_sent, not clarification_sent)
        self.assertFalse(self.state.location_clarification_sent,
                         "location_clarification_sent must NOT be set by direct dispatch")

        # Focus recognized as AUTO
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        self.assertTrue(any(c.tipo_vehiculo == "AUTO" for c in cands),
                        f"Focus must be recognized as AUTO: {[c.tipo_vehiculo for c in cands]}")

    @patch("urllib.request.urlopen")
    def test_rc16_bare_vehicle_message_dispatches_location_flow(self, mock_urlopen):
        """'Es un Focus 2019.' with no price, no FAQ → standard Location Flow (D.2B preserved).

        A bare vehicle identification with known vehicle and missing zone follows D.2B behavior:
        routing gate priority 4 dispatches the standard Location Flow.
        Spec: 'Es un Focus 2019.' → 'existing normal qualification behavior' = Location Flow.
        The FAQ gate (priority 3) must NOT match bare vehicle messages.
        """
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        result = self.eng.handle(_event(
            self.thread.id, self.wa_id, "Es un Focus 2019.", "6D2RC16T2",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        blocked = _get_blocked(self.db, self.thread.id)

        # Standard Location Flow dispatched (D.2B behavior preserved for bare vehicle message)
        self.assertTrue(any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
                        f"Standard Location Flow must fire for bare vehicle + missing zone: {blocked}")
        # NOT concern body (no concern word in message)
        self.assertFalse(any(_LOC_FLOW_BODY_CONCERN in t for t in blocked),
                         f"Concern body must not fire: {blocked}")
        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(mock_urlopen.call_count, 0, "AI must not be called")


# ═══════════════════════════════════════════════════════════════════════════════
# RC17 — Live Scenario 2 parity: Vehicle Flow response → direct Location Flow
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC17VehicleFlowResponseToLocationFlow(unittest.TestCase):
    """After a valid Vehicle Flow response with no zone known, the engine must
    dispatch the Location Fallback Flow directly — not call AI or send a
    plain-text locality question.  Fixes the gap found in M20.6D.4 Scenario 2.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D4RC17")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc17_vehicle_flow_response_dispatches_location_flow_direct(self, mock_urlopen):
        """Peugeot 208 AUTO 2019 Vehicle Flow response → standard Location Flow; no AI."""
        mock_urlopen.side_effect = AssertionError(
            "AI must not be called after Vehicle Flow response"
        )
        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "RC17_VEH_MSG"
        self.db.commit()

        flow_data = {
            "tipo_vehiculo": "AUTO",
            "marca": "Peugeot",
            "modelo": "208",
            "anio": "2019",
        }
        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "RC17_FLOW_RESP", flow_data, "tok-rc17",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        blocked = _get_blocked(self.db, self.thread.id)

        # Candidate persists correctly
        self.assertTrue(
            any(c.tipo_vehiculo == "AUTO" and c.marca == "Peugeot"
                and c.modelo == "208" and c.anio == 2019 for c in cands),
            f"Candidate not found: {[(c.tipo_vehiculo, c.marca, c.modelo, c.anio) for c in cands]}",
        )

        # AI not called
        self.assertEqual(mock_urlopen.call_count, 0, "AI must not be called")

        # Location Flow dispatched (blocked — OUTBOUND_ENABLED not set)
        self.assertEqual(result.action, "blocked_dispatch")
        self.assertTrue(
            any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
            f"Standard Location Flow body expected: {blocked}",
        )

        # No plain-text locality question
        plain = any(
            ("¿en qué barrio" in t.lower() or "zona de buenos aires" in t.lower())
            and _LOC_FLOW_BODY_STANDARD not in t
            for t in blocked
        )
        self.assertFalse(plain, f"Must not send plain-text locality question: {blocked}")

        # No price
        self.assertFalse(
            any("$" in t for t in blocked if _LOC_FLOW_BODY_STANDARD not in t),
            "No invented price before location known",
        )

        # No booking
        self.assertIsNone(self.state.flow_booking_token, "No booking token")

        # Stage remains QUALIFYING
        self.assertEqual(self.state.last_stage, "QUALIFYING",
                         f"Stage must remain QUALIFYING: {self.state.last_stage}")

        # needs_human = False
        self.assertFalse(self.state.needs_human)

        # location_clarification_sent not set
        self.assertFalse(self.state.location_clarification_sent,
                         "location_clarification_sent must not be set by direct dispatch")


# ═══════════════════════════════════════════════════════════════════════════════
# RC18 — Outbound blocked: candidate persists, Location Flow recorded, flag stays False
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC18OutboundBlockedSemantics(unittest.TestCase):
    """With OUTBOUND_ENABLED unset, the Location Flow attempt is recorded as
    blocked_dispatch; candidate data persists; location_fallback_flow_sent stays False.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D4RC18")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc18_outbound_blocked_candidate_persists_flag_false(self, mock_urlopen):
        """Blocked dispatch: candidate persists; Location Flow body in audit; flag stays False."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "RC18_VEH_MSG"
        self.db.commit()

        flow_data = {
            "tipo_vehiculo": "AUTO",
            "marca": "Peugeot",
            "modelo": "208",
            "anio": "2019",
        }
        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "RC18_FLOW_RESP", flow_data, "tok-rc18",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        from sqlalchemy import select as _sel
        cands = self.db.execute(
            _sel(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread.id)
        ).scalars().all()
        blocked = _get_blocked(self.db, self.thread.id)

        # result is blocked_dispatch
        self.assertEqual(result.action, "blocked_dispatch")

        # Candidate persists
        self.assertTrue(any(c.tipo_vehiculo == "AUTO" for c in cands),
                        f"Candidate must persist: {[c.tipo_vehiculo for c in cands]}")

        # Standard Location Flow body recorded in audit
        self.assertTrue(
            any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
            f"Standard Location Flow body expected in blocked audit: {blocked}",
        )

        # AI not called
        self.assertEqual(mock_urlopen.call_count, 0)

        # location_fallback_flow_sent remains False when dispatch is blocked
        self.assertFalse(
            self.state.location_fallback_flow_sent,
            "location_fallback_flow_sent must remain False when dispatch is blocked",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RC19 — Successful delivery and duplicate replay prevention
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC19SuccessfulDeliveryNoDuplicate(unittest.TestCase):
    """With a mocked successful outbound send, location_fallback_flow_sent becomes True.
    A replay of the same flow_response wa_message_id is caught by message dedup and
    does not trigger a second Location Flow dispatch.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D4RC19")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    @patch(
        "app.services.conversation_engine._send_whatsapp_cloud_flow",
        return_value=("wa-rc19-flow-001", {}),
    )
    @patch("app.services.conversation_engine.reset_unanswered_alert", return_value=None)
    def test_rc19_successful_delivery_sets_flag_and_dedup_blocks_replay(
        self, _mock_reset_alert, mock_send_flow, mock_urlopen
    ):
        """Successful Location Flow send → flag True; replay same msg_id → skipped_dedup."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")
        os.environ["OUTBOUND_ENABLED"] = "true"

        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "RC19_VEH_MSG"
        self.db.commit()

        flow_data = {
            "tipo_vehiculo": "AUTO",
            "marca": "Peugeot",
            "modelo": "208",
            "anio": "2019",
        }

        # First submit: successful Location Flow delivery
        result1 = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "RC19_FLOW_RESP", flow_data, "tok-rc19",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)

        self.assertEqual(result1.action, "replied",
                         f"Expected successful reply, got: {result1.action}")
        self.assertTrue(
            self.state.location_fallback_flow_sent,
            "location_fallback_flow_sent must be True after successful delivery",
        )
        self.assertEqual(mock_send_flow.call_count, 1,
                         "Exactly one Location Flow must be sent")

        # Replay same wa_message_id → dedup catches it
        result2 = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "RC19_FLOW_RESP", flow_data, "tok-rc19",
        ))
        self.assertEqual(result2.action, "skipped_dedup",
                         f"Replay must be caught by dedup, got: {result2.action}")

        # Still exactly one Location Flow sent
        self.assertEqual(mock_send_flow.call_count, 1,
                         "No second Location Flow must be sent on duplicate replay")


# ═══════════════════════════════════════════════════════════════════════════════
# RC20 — Known zone preserved: Vehicle Flow response → quote (no Location Flow)
# ═══════════════════════════════════════════════════════════════════════════════
class TestRC20KnownZonePreserved(unittest.TestCase):
    """When zone is already known (CABA/Palermo), a Vehicle Flow response with
    AUTO/Peugeot/208/2019 must produce the deterministic quote ($130.000)
    and advance the stage to QUOTED — no Location Flow sent.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _SessionLocal()
        self.contact, self.thread, self.lead, self.state = _seed_fresh(self.db, "6D4RC20")
        self.eng = _make_engine(self.db)
        self.wa_id = self.contact.wa_id

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_rc20_known_zone_produces_quote_not_location_flow(self, mock_urlopen):
        """CABA/Palermo pre-seeded + Vehicle Flow response → $130.000 quote; stage QUOTED."""
        mock_urlopen.side_effect = AssertionError("AI must not be called")

        # Pre-seed zone on state
        self.state.home_zone_group = "CABA"
        self.state.home_zone_detail = "Palermo"
        self.state.vehicle_fallback_flow_sent = True
        self.state.last_processed_inbound_wa_message_id = "RC20_VEH_MSG"
        self.db.commit()

        flow_data = {
            "tipo_vehiculo": "AUTO",
            "marca": "Peugeot",
            "modelo": "208",
            "anio": "2019",
        }
        result = self.eng.handle(_flow_event(
            self.thread.id, self.wa_id, "RC20_FLOW_RESP", flow_data, "tok-rc20",
        ))
        self.db.expire_all()
        self.db.refresh(self.state)
        self.db.refresh(self.lead)
        blocked = _get_blocked(self.db, self.thread.id)

        # Result is blocked_dispatch (outbound disabled, but quote text was attempted)
        self.assertEqual(result.action, "blocked_dispatch")

        # No Location Flow dispatched
        self.assertFalse(
            any(_LOC_FLOW_BODY_STANDARD in t for t in blocked),
            f"Must NOT dispatch Location Flow when zone is already known: {blocked}",
        )
        self.assertFalse(
            any(_LOC_FLOW_BODY_CONCERN in t for t in blocked),
            f"Must NOT dispatch concern Location Flow: {blocked}",
        )

        # Quote text contains $130.000
        quote_texts = [t for t in blocked if "$" in t]
        self.assertTrue(
            any("$130.000" in t for t in quote_texts),
            f"Quote must be $130.000 (AUTO/CABA/Palermo): {quote_texts}",
        )

        # Stage advanced to QUOTED
        self.assertEqual(self.state.last_stage, "QUOTED",
                         f"Stage must be QUOTED: {self.state.last_stage}")

        # Lead flag set
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO",
                         f"Lead flag must be PRESUPUESTO_ENVIADO: {self.lead.flag}")

        # AI not called
        self.assertEqual(mock_urlopen.call_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
