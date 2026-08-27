"""WILD-04R-F5 — Cycle-safe candidate authority invariants.

Verifies three defect classes fixed by WILD-04R-F5:

  D1 — AI prompt: zone question is mandatory when Zona='desconocida' (rule 20).
       Tested indirectly: system prompt contains the rule text.

  D2 — Cycle archive: _execute_cycle_reset() archives all current_focus candidates
       before establishing new-cycle watermarks.
       INV-1  After cycle reset, all prior current_focus candidates are archived.
       INV-2  After cycle reset, ctx.candidates is empty (watermark excludes prior).
       INV-3  After cycle reset, state.current_focus_candidate_id is None.
       INV-4  _focus_candidate returns None on empty candidates list.
       INV-5  _focus_candidate returns new-cycle candidate after reset + create.
       INV-6  Same-cycle candidate switch-back works (A → B → A within one cycle).
       INV-7  _get_active_inspection_location returns (None, None) when cycle reset,
              zone not yet collected.
       INV-8  After zone provided in new cycle, accessor returns new zone.
       INV-11 Historical candidate preserved in DB (status=archived, not deleted).
       INV-12 At most one current_focus per cycle (switch demotes old to mentioned).

  D3 — tipo normalization: SUV/4x4 → SUV_4X4_DEPORTIVO on create and update.
       INV-9  _normalize_tipo_vehiculo("SUV/4x4") == "SUV_4X4_DEPORTIVO"
       INV-10 After _apply_candidate with tipo_vehiculo="SUV/4x4", stored as
              "SUV_4X4_DEPORTIVO".

  F4 regression guard (INV-13):
       Candidate zone used for pricing / display, not state zone.

All tests use SQLite in-memory.  No containers, no Meta API, no live AI calls.
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
from app.repositories.pricing_repository import PricingRepository
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import (
    ConversationEngine,
    _Context,
    _normalize_tipo_vehiculo,
)
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

_NOW = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
_WA_ID = "5491153369000"


# ── DB helpers ────────────────────────────────────────────────────────────────

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


def _seed_viaticos(db: Session) -> None:
    for grp, det, via in [
        ("CABA", "Palermo", 0),
        ("Norte", "Pilar", 50_000),
        ("Oeste", "San Miguel", 90_000),
    ]:
        exists = db.execute(
            sql_text("SELECT id FROM viaticos_zones WHERE zone_group=:g AND zone_detail=:d"),
            {"g": grp, "d": det},
        ).fetchone()
        if not exists:
            db.add(ViaticosZone(zone_group=grp, zone_detail=det, viaticos=via))
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


def _seed_thread(
    db: Session,
    *,
    thread_id_hint: int,
    wa_id: str,
    flag: str = "PRESUPUESTANDO",
    stage: str = "QUALIFYING",
    state_zone_group: str | None = None,
    state_zone_detail: str | None = None,
) -> tuple[int, Lead, WhatsAppThreadState]:
    lead = Lead(nombre="F5 User", telefono=wa_id, flag=flag)
    db.add(lead)
    db.flush()
    contact = WhatsAppContact(wa_id=wa_id, display_name="F5 User")
    db.add(contact)
    db.flush()
    thread = WhatsAppThread(id=thread_id_hint, lead_id=lead.id, contact_id=contact.id)
    db.add(thread)
    db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id,
        last_stage=stage,
        home_zone_group=state_zone_group,
        home_zone_detail=state_zone_detail,
        current_focus_candidate_id=None,
        current_revision_id=None,
    )
    db.add(state)
    db.commit()
    return thread.id, lead, state


def _add_candidate(
    db: Session,
    thread_id: int,
    marca: str = "Ford",
    modelo: str = "Focus",
    anio: int = 2019,
    tipo: str = "AUTO",
    zone_group: str | None = None,
    zone_detail: str | None = None,
    status: str = "current_focus",
    created_at: datetime | None = None,
) -> WhatsAppThreadCandidate:
    ts = created_at or _NOW
    cand = WhatsAppThreadCandidate(
        thread_id=thread_id,
        marca=marca,
        modelo=modelo,
        anio=anio,
        tipo_vehiculo=tipo,
        zone_group=zone_group,
        zone_detail=zone_detail,
        status=status,
        created_at=ts,
        updated_at=ts,
    )
    db.add(cand)
    db.commit()
    return cand


def _add_inbound_message(
    db: Session,
    thread_id: int,
    wa_message_id: str,
    text: str = "Hola",
    offset_seconds: int = 0,
) -> WhatsAppMessage:
    ts = _NOW + timedelta(seconds=offset_seconds)
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


def _build_ctx(db: Session, thread_id: int) -> tuple[_Context, WhatsAppThreadState]:
    thread = db.get(WhatsAppThread, thread_id)
    state = db.execute(
        select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)
    ).scalar_one()
    contact = db.get(WhatsAppContact, thread.contact_id)
    lead = db.get(Lead, thread.lead_id)
    candidates = list(db.execute(
        select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread_id)
    ).scalars().all())
    msgs = list(db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.thread_id == thread_id)
    ).scalars().all())
    ctx = _Context(
        thread=thread,
        contact=contact,
        lead=lead,
        state=state,
        candidates=candidates,
        db_messages=msgs,
    )
    return ctx, state


def _run_cycle_reset(
    eng: ConversationEngine,
    ctx: _Context,
    state: WhatsAppThreadState,
    wa_message_id: str,
) -> None:
    """Call _execute_cycle_reset with a minimal event."""
    event = ConversationHandleIn(
        thread_id=ctx.thread.id,
        wa_message_id=wa_message_id,
        wa_id=_WA_ID,
        text="Hola de nuevo",
        unanswered_recent_user_messages=["Hola de nuevo"],
        recent_user_messages=["Hola de nuevo"],
    )
    eng._execute_cycle_reset(ctx, state, event, previous_cursor=None)


def _run_ce(
    db: Session,
    eng: ConversationEngine,
    thread_id: int,
    wa_id: str,
    base_msg_id: str,
    texts: list[str],
    ai_reply: str | None = None,
) -> tuple[object, list[str]]:
    ev = ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=f"{base_msg_id}-{len(texts) - 1}",
        wa_id=wa_id,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )
    _ai_payload = ai_reply or json.dumps({
        "intent": "QUALIFYING", "reply": "Sin datos aún.",
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


# ═══════════════════════════════════════════════════════════════════════════════
# D3 unit — _normalize_tipo_vehiculo
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeTipoVehiculo(unittest.TestCase):
    """INV-9: _normalize_tipo_vehiculo maps all SUV/4x4 aliases to canonical form."""

    def test_inv9_suv_slash_lowercase(self):
        """INV-9: 'SUV/4x4' → 'SUV_4X4_DEPORTIVO'"""
        self.assertEqual(_normalize_tipo_vehiculo("SUV/4x4"), "SUV_4X4_DEPORTIVO")

    def test_inv9_suv_slash_uppercase(self):
        """INV-9: 'SUV/4X4' → 'SUV_4X4_DEPORTIVO'"""
        self.assertEqual(_normalize_tipo_vehiculo("SUV/4X4"), "SUV_4X4_DEPORTIVO")

    def test_inv9_suv_underscore(self):
        """INV-9: 'SUV_4X4' → 'SUV_4X4_DEPORTIVO'"""
        self.assertEqual(_normalize_tipo_vehiculo("SUV_4X4"), "SUV_4X4_DEPORTIVO")

    def test_inv9_bare_4x4(self):
        """INV-9: '4X4' → 'SUV_4X4_DEPORTIVO'"""
        self.assertEqual(_normalize_tipo_vehiculo("4X4"), "SUV_4X4_DEPORTIVO")

    def test_inv9_canonical_passthrough(self):
        """INV-9: 'SUV_4X4_DEPORTIVO' remains unchanged."""
        self.assertEqual(_normalize_tipo_vehiculo("SUV_4X4_DEPORTIVO"), "SUV_4X4_DEPORTIVO")

    def test_inv9_auto_passthrough(self):
        """INV-9: 'AUTO' remains unchanged."""
        self.assertEqual(_normalize_tipo_vehiculo("AUTO"), "AUTO")

    def test_inv9_none_passthrough(self):
        """INV-9: None → None."""
        self.assertIsNone(_normalize_tipo_vehiculo(None))

    def test_inv9_empty_string(self):
        """INV-9: empty string is falsy — returned as-is (same as None guard path)."""
        result = _normalize_tipo_vehiculo("")
        # empty string is falsy; the guard returns it as-is, no mapping applied
        self.assertFalse(result, f"Expected falsy result for empty string, got {result!r}")

    def test_inv9_whitespace_stripped(self):
        """INV-9: leading/trailing whitespace is stripped before lookup."""
        self.assertEqual(_normalize_tipo_vehiculo("  SUV/4x4  "), "SUV_4X4_DEPORTIVO")


# ═══════════════════════════════════════════════════════════════════════════════
# INV-10 — tipo stored as SUV_4X4_DEPORTIVO after _apply_candidate
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyCandidateTipoNormalization(unittest.TestCase):
    """INV-10: AI returns tipo_vehiculo='SUV/4x4', candidate persisted as 'SUV_4X4_DEPORTIVO'."""

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state = _seed_thread(
            self.db, thread_id_hint=5101, wa_id="5491153369001",
        )

    def tearDown(self):
        self.db.close()

    def _ctx(self) -> tuple[_Context, WhatsAppThreadState]:
        return _build_ctx(self.db, self.thread_id)

    def test_inv10_create_normalizes_suv_slash(self):
        """INV-10a: action=create with tipo_vehiculo='SUV/4x4' stores 'SUV_4X4_DEPORTIVO'."""
        ctx, state = self._ctx()
        self.eng._apply_candidate(ctx, {
            "action": "create",
            "marca": "Peugeot",
            "modelo": "2008",
            "anio": 2014,
            "tipo_vehiculo": "SUV/4x4",
            "status": "current_focus",
        })
        self.db.flush()
        cand = ctx.candidates[0]
        self.assertEqual(cand.tipo_vehiculo, "SUV_4X4_DEPORTIVO",
            f"Expected 'SUV_4X4_DEPORTIVO' but got {cand.tipo_vehiculo!r}")

    def test_inv10_update_normalizes_suv_slash(self):
        """INV-10b: action=update with tipo_vehiculo='SUV/4x4' stores 'SUV_4X4_DEPORTIVO'."""
        existing = _add_candidate(
            self.db, self.thread_id, marca="Peugeot", modelo="2008",
            tipo="AUTO", status="current_focus",
        )
        ctx, state = self._ctx()
        ctx.state = state
        state.current_focus_candidate_id = existing.id
        self.eng._apply_candidate(ctx, {
            "action": "update",
            "id": existing.id,
            "tipo_vehiculo": "SUV/4x4",
        })
        self.db.flush()
        self.db.expire(existing)
        target = self.db.get(WhatsAppThreadCandidate, existing.id)
        self.assertEqual(target.tipo_vehiculo, "SUV_4X4_DEPORTIVO",
            f"Expected 'SUV_4X4_DEPORTIVO' but got {target.tipo_vehiculo!r}")

    def test_inv10_suv_4x4_uppercase_variant(self):
        """INV-10c: action=create with tipo_vehiculo='SUV/4X4' (uppercase) also normalizes."""
        ctx, state = self._ctx()
        self.eng._apply_candidate(ctx, {
            "action": "create",
            "marca": "Toyota",
            "modelo": "Prado",
            "tipo_vehiculo": "SUV/4X4",
            "status": "mentioned",
        })
        self.db.flush()
        cand = ctx.candidates[0]
        self.assertEqual(cand.tipo_vehiculo, "SUV_4X4_DEPORTIVO")


# ═══════════════════════════════════════════════════════════════════════════════
# D2 — Cycle archive invariants
# ═══════════════════════════════════════════════════════════════════════════════

class TestCycleArchiveOnReset(unittest.TestCase):
    """INV-1, INV-2, INV-3, INV-11: _execute_cycle_reset archives current_focus candidates."""

    WA_ID = "5491153369002"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5102, wa_id=self.WA_ID,
            flag="PRESUPUESTANDO", stage="QUOTED",
        )
        # Create two prior-cycle current_focus candidates
        self.cand_a = _add_candidate(
            self.db, self.thread_id, marca="Ford", modelo="Focus",
            anio=2019, tipo="AUTO", status="current_focus",
        )
        self.cand_b = _add_candidate(
            self.db, self.thread_id, marca="Toyota", modelo="Yaris",
            anio=2021, tipo="AUTO", status="current_focus",
        )
        # One archived from a previous sub-cycle
        self.cand_c = _add_candidate(
            self.db, self.thread_id, marca="Honda", modelo="Civic",
            anio=2018, tipo="AUTO", status="archived",
        )
        # Persist state
        self.state_obj.current_focus_candidate_id = self.cand_b.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _do_reset(self) -> tuple[_Context, WhatsAppThreadState]:
        msg = _add_inbound_message(
            self.db, self.thread_id, "reset-msg-01", text="Quiero ver otro auto",
            offset_seconds=100,
        )
        ctx, state = _build_ctx(self.db, self.thread_id)
        _run_cycle_reset(self.eng, ctx, state, wa_message_id=msg.wa_message_id)
        return ctx, state

    def test_inv1_prior_current_focus_archived(self):
        """INV-1: After cycle reset, all prior current_focus candidates are status=archived."""
        self._do_reset()
        self.db.expire_all()
        for cand_id in (self.cand_a.id, self.cand_b.id):
            cand = self.db.get(WhatsAppThreadCandidate, cand_id)
            self.assertEqual(cand.status, "archived",
                f"INV-1: candidate {cand_id} must be archived, got {cand.status!r}")

    def test_inv2_ctx_candidates_empty_after_reset(self):
        """INV-2: ctx.candidates is empty after reset (new watermark excludes prior candidates)."""
        ctx, _ = self._do_reset()
        self.assertEqual(len(ctx.candidates), 0,
            f"INV-2: ctx.candidates must be empty after reset, got {len(ctx.candidates)}")

    def test_inv3_focus_candidate_id_cleared(self):
        """INV-3: state.current_focus_candidate_id is None after cycle reset."""
        _, state = self._do_reset()
        self.assertIsNone(state.current_focus_candidate_id,
            "INV-3: current_focus_candidate_id must be None after reset")

    def test_inv11_historical_candidate_preserved(self):
        """INV-11: Archived candidate row is preserved in DB (not deleted)."""
        self._do_reset()
        self.db.expire_all()
        for cand_id in (self.cand_a.id, self.cand_b.id, self.cand_c.id):
            cand = self.db.get(WhatsAppThreadCandidate, cand_id)
            self.assertIsNotNone(cand, f"INV-11: candidate {cand_id} must exist in DB after reset")
            self.assertEqual(cand.status, "archived",
                f"INV-11: candidate {cand_id} must be archived, got {cand.status!r}")


class TestFocusCandidateAfterReset(unittest.TestCase):
    """INV-4, INV-5: _focus_candidate behavior after cycle reset."""

    WA_ID = "5491153369003"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5103, wa_id=self.WA_ID,
        )
        self.prior_cand = _add_candidate(
            self.db, self.thread_id, marca="Ford", modelo="Focus",
            tipo="AUTO", status="current_focus",
        )
        self.state_obj.current_focus_candidate_id = self.prior_cand.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_inv4_focus_candidate_returns_none_on_empty_list(self):
        """INV-4: _focus_candidate returns None when ctx.candidates is empty."""
        # Simulate a post-reset context where candidates is empty
        ctx, state = _build_ctx(self.db, self.thread_id)
        ctx.candidates = []
        state.current_focus_candidate_id = None
        result = self.eng._focus_candidate(ctx)
        self.assertIsNone(result, "INV-4: _focus_candidate must return None on empty list")

    def test_inv5_focus_candidate_returns_new_cycle_candidate(self):
        """INV-5: After reset + new candidate created, _focus_candidate returns the new one."""
        msg = _add_inbound_message(
            self.db, self.thread_id, "f5-reset-msg-01", text="Nuevo auto",
            offset_seconds=200,
        )
        ctx, state = _build_ctx(self.db, self.thread_id)
        _run_cycle_reset(self.eng, ctx, state, wa_message_id=msg.wa_message_id)

        # Create new candidate in the new cycle
        new_ts = _NOW + timedelta(seconds=210)
        new_cand = WhatsAppThreadCandidate(
            thread_id=self.thread_id,
            marca="Toyota", modelo="Corolla", anio=2022,
            tipo_vehiculo="AUTO", status="current_focus",
            created_at=new_ts, updated_at=new_ts,
        )
        self.db.add(new_cand)
        self.db.flush()
        state.current_focus_candidate_id = new_cand.id
        ctx.candidates = [new_cand]

        result = self.eng._focus_candidate(ctx)
        self.assertIsNotNone(result, "INV-5: _focus_candidate must return new-cycle candidate")
        self.assertEqual(result.id, new_cand.id,
            f"INV-5: expected new candidate id={new_cand.id}, got id={result.id}")
        self.assertNotEqual(result.id, self.prior_cand.id,
            "INV-5: _focus_candidate must NOT return prior-cycle candidate")


class TestSameCycleCandidateSwitch(unittest.TestCase):
    """INV-6: Same-cycle candidate switch A → B → A within one cycle."""

    WA_ID = "5491153369004"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5104, wa_id=self.WA_ID,
        )
        now = _NOW + timedelta(seconds=300)
        self.cand_a = WhatsAppThreadCandidate(
            thread_id=self.thread_id, marca="Ford", modelo="Focus",
            anio=2019, tipo_vehiculo="AUTO", status="current_focus",
            created_at=now, updated_at=now,
        )
        self.db.add(self.cand_a)
        self.db.flush()
        self.cand_b = WhatsAppThreadCandidate(
            thread_id=self.thread_id, marca="Toyota", modelo="Yaris",
            anio=2021, tipo_vehiculo="AUTO", status="mentioned",
            created_at=now + timedelta(seconds=1), updated_at=now + timedelta(seconds=1),
        )
        self.db.add(self.cand_b)
        self.db.flush()
        self.state_obj.current_focus_candidate_id = self.cand_a.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_inv6_switch_a_to_b_to_a(self):
        """INV-6: Switch A→B→A preserves both candidates and returns A as focus after re-switch."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        ctx.state = state

        # Switch to B
        self.eng._apply_candidate(ctx, {
            "action": "update",
            "id": self.cand_b.id,
            "status": "current_focus",
        })
        focus_after_b = self.eng._focus_candidate(ctx)
        self.assertEqual(focus_after_b.id, self.cand_b.id,
            "INV-6: After switch to B, focus must be B")

        # Switch back to A
        self.eng._apply_candidate(ctx, {
            "action": "update",
            "id": self.cand_a.id,
            "status": "current_focus",
        })
        focus_after_a = self.eng._focus_candidate(ctx)
        self.assertEqual(focus_after_a.id, self.cand_a.id,
            "INV-6: After switch back to A, focus must be A")

        # Both candidates still in ctx
        ids_in_ctx = {c.id for c in ctx.candidates}
        self.assertIn(self.cand_a.id, ids_in_ctx, "INV-6: Candidate A must still be in ctx")
        self.assertIn(self.cand_b.id, ids_in_ctx, "INV-6: Candidate B must still be in ctx")


class TestInspectionLocationAfterReset(unittest.TestCase):
    """INV-7, INV-8: _get_active_inspection_location after cycle reset."""

    WA_ID = "5491153369005"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5105, wa_id=self.WA_ID,
            state_zone_group="CABA", state_zone_detail="Palermo",
        )
        self.prior_cand = _add_candidate(
            self.db, self.thread_id, marca="Ford", modelo="Focus",
            zone_group="CABA", zone_detail="Palermo", status="current_focus",
        )
        self.state_obj.current_focus_candidate_id = self.prior_cand.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_inv7_location_returns_none_after_reset(self):
        """INV-7: After cycle reset (zone cleared), accessor returns (None, None)."""
        msg = _add_inbound_message(
            self.db, self.thread_id, "inv7-reset-msg", text="Nuevo auto",
            offset_seconds=300,
        )
        ctx, state = _build_ctx(self.db, self.thread_id)
        _run_cycle_reset(self.eng, ctx, state, wa_message_id=msg.wa_message_id)
        # state.home_zone_* are cleared by reset; ctx.candidates is empty
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        self.assertIsNone(grp, f"INV-7: zone_group must be None after reset, got {grp!r}")
        self.assertIsNone(det, f"INV-7: zone_detail must be None after reset, got {det!r}")

    def test_inv8_location_returns_new_zone_after_new_candidate(self):
        """INV-8: After zone provided in new cycle, accessor returns new zone."""
        msg = _add_inbound_message(
            self.db, self.thread_id, "inv8-reset-msg", text="Nuevo auto",
            offset_seconds=400,
        )
        ctx, state = _build_ctx(self.db, self.thread_id)
        _run_cycle_reset(self.eng, ctx, state, wa_message_id=msg.wa_message_id)

        # New-cycle candidate with a different zone
        new_ts = _NOW + timedelta(seconds=410)
        new_cand = WhatsAppThreadCandidate(
            thread_id=self.thread_id, marca="Toyota", modelo="Corolla",
            anio=2022, tipo_vehiculo="AUTO",
            zone_group="Norte", zone_detail="Pilar",
            status="current_focus",
            created_at=new_ts, updated_at=new_ts,
        )
        self.db.add(new_cand)
        self.db.flush()
        state.current_focus_candidate_id = new_cand.id
        ctx.candidates = [new_cand]

        grp, det = self.eng._get_active_inspection_location(ctx, state)
        self.assertEqual(grp, "Norte", f"INV-8: expected zone_group=Norte, got {grp!r}")
        self.assertEqual(det, "Pilar", f"INV-8: expected zone_detail=Pilar, got {det!r}")


class TestAtMostOneFocusPerCycle(unittest.TestCase):
    """INV-12: At most one current_focus per active cycle after a focus switch."""

    WA_ID = "5491153369006"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5106, wa_id=self.WA_ID,
        )
        now = _NOW + timedelta(seconds=500)
        self.cand_a = WhatsAppThreadCandidate(
            thread_id=self.thread_id, marca="Ford", modelo="Focus",
            anio=2019, tipo_vehiculo="AUTO", status="current_focus",
            created_at=now, updated_at=now,
        )
        self.db.add(self.cand_a)
        self.db.flush()
        self.state_obj.current_focus_candidate_id = self.cand_a.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_inv12_at_most_one_focus_after_switch(self):
        """INV-12: After switching focus from A to B, only B has current_focus status."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        ctx.state = state

        # Create candidate B and switch focus to it
        self.eng._apply_candidate(ctx, {
            "action": "create",
            "marca": "Toyota",
            "modelo": "Yaris",
            "anio": 2021,
            "tipo_vehiculo": "AUTO",
            "status": "current_focus",
        })

        focus_count = sum(1 for c in ctx.candidates if c.status == "current_focus")
        self.assertEqual(focus_count, 1,
            f"INV-12: Exactly one candidate must have current_focus, found {focus_count}")

        # Ensure old A is no longer current_focus
        a_in_ctx = next((c for c in ctx.candidates if c.id == self.cand_a.id), None)
        if a_in_ctx is not None:
            self.assertNotEqual(a_in_ctx.status, "current_focus",
                "INV-12: Prior focus candidate A must not be current_focus after switch")


# ═══════════════════════════════════════════════════════════════════════════════
# INV-13 — F4 regression: candidate zone used for pricing, not state zone
# ═══════════════════════════════════════════════════════════════════════════════

class TestF4LocationAuthorityRegression(unittest.TestCase):
    """INV-13: F4 location authority: candidate zone authoritative over state zone.
    Ensures the D2 archive fix did not regress the F4 accessor.
    """

    WA_ID = "5491153369007"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)
        # State says San Miguel, candidate says Palermo — candidate wins
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5107, wa_id=self.WA_ID,
            state_zone_group="Oeste", state_zone_detail="San Miguel",
        )
        self.cand = _add_candidate(
            self.db, self.thread_id, marca="Ford", modelo="Focus",
            anio=2019, tipo="AUTO",
            zone_group="CABA", zone_detail="Palermo",
            status="current_focus",
        )
        self.state_obj.current_focus_candidate_id = self.cand.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_inv13_candidate_zone_wins(self):
        """INV-13: _get_active_inspection_location returns candidate zone, not state zone."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        self.assertEqual(grp, "CABA",
            f"INV-13: Expected CABA (candidate), got {grp!r} (state says Oeste)")
        self.assertEqual(det, "Palermo",
            f"INV-13: Expected Palermo (candidate), got {det!r} (state says San Miguel)")

    def test_inv13_pricing_agrees_with_accessor(self):
        """INV-13: _compute_price_quote zone_group equals _get_active_inspection_location."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        quote = self.eng._compute_price_quote(ctx, state)
        if quote:
            self.assertEqual(grp, quote.zone_group,
                f"INV-13: display zone {grp!r} != pricing zone {quote.zone_group!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# D1 — AI prompt rule 20 present in system prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestAIPromptRule20(unittest.TestCase):
    """Verify that rule 20 is present in the system prompt when zone is 'desconocida'."""

    WA_ID = "5491153369008"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.lead, self.state_obj = _seed_thread(
            self.db, thread_id_hint=5108, wa_id=self.WA_ID,
        )

    def tearDown(self):
        self.db.close()

    def test_d1_rule20_in_system_prompt_when_zone_unknown(self):
        """D1: Rule 20 must appear in AI system prompt when zone is 'desconocida'."""
        # Build ctx with no zone → zone variable will be 'desconocida'
        ctx, state = _build_ctx(self.db, self.thread_id)
        # Provide minimal state for _build_ai_messages (it asserts state is not None)
        ctx.state = state

        ev = ConversationHandleIn(
            thread_id=self.thread_id,
            wa_message_id="d1-test-msg",
            wa_id=self.WA_ID,
            text="Quiero revisar un auto",
            unanswered_recent_user_messages=["Quiero revisar un auto"],
            recent_user_messages=["Quiero revisar un auto"],
        )
        # No candidates, no state zone → zone == 'desconocida'
        messages = self.eng._build_ai_messages(
            ctx=ctx,
            event=ev,
            ai_input_messages=["Quiero revisar un auto"],
            include_narrative=False,
            pre_detected_vehicle=None,
        )
        system_prompt = messages[0]["content"]
        self.assertIn(
            "desconocida",
            system_prompt,
            "System prompt must reference 'desconocida' when zone is unknown",
        )
        self.assertIn(
            "20.",
            system_prompt,
            "System prompt must contain rule numbered '20.'",
        )
        self.assertIn(
            "terminá tu mensaje con la pregunta de zona",
            system_prompt,
            "Rule 20 must instruct AI to end with zone question",
        )

    def test_d1_suv_4x4_deportivo_is_canonical_in_tipos_line(self):
        """D1: TIPOS DE VEHÍCULO VÁLIDOS line uses SUV_4X4_DEPORTIVO (not SUV/4x4 duplicate)."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        ctx.state = state
        ev = ConversationHandleIn(
            thread_id=self.thread_id,
            wa_message_id="d1-tipos-msg",
            wa_id=self.WA_ID,
            text="test",
            unanswered_recent_user_messages=["test"],
            recent_user_messages=["test"],
        )
        messages = self.eng._build_ai_messages(
            ctx=ctx, event=ev,
            ai_input_messages=["test"],
            include_narrative=False, pre_detected_vehicle=None,
        )
        system_prompt = messages[0]["content"]
        # Canonical form must appear
        self.assertIn("SUV_4X4_DEPORTIVO", system_prompt)


if __name__ == "__main__":
    unittest.main()
