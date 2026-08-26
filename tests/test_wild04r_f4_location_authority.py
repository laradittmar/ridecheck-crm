"""WILD-04R-F4 — Active Candidate Location Authority

Verifies that for pricing AND display AND scheduling, the active candidate's
zone is authoritative (candidate-first precedence), and state.home_zone_* is
only a fallback when the candidate carries no zone data.

Cases:
  A — Focus+Palermo: reply displays "Palermo", NOT "San Miguel" (stale state)
  B — Focus+Pilar:   reply displays "Pilar",   NOT "Palermo"   (stale state)
  C — Location-only correction "No, perdón, está en San Miguel." → "San Miguel"
  D — Switch to old candidate (Peugeot/San Miguel) → pricing/display San Miguel
  E — Candidate has null zone → state fallback (CABA/Palermo) → "Palermo"

Invariant: zone used by PricingService == zone displayed in quote reply

Uses real PricingRepository (reads pricing_base.csv) with seeded ViaticosZone.
"""
from __future__ import annotations

import json
import os
import re
import sys
import types
import unittest
from datetime import datetime, timezone
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

from sqlalchemy import create_engine, event, text as sql_text
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
from app.services.conversation_engine import ConversationEngine, _Context
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

_NOW = datetime.now(timezone.utc)


# ── Engine factory (real PricingRepository) ───────────────────────────────────

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


def _candidates(db: Session, thread_id: int) -> list[WhatsAppThreadCandidate]:
    from sqlalchemy import select
    return list(db.execute(
        select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread_id)
    ).scalars().all())


def _refresh_state(db: Session, thread_id: int) -> WhatsAppThreadState:
    from sqlalchemy import select
    return db.execute(
        select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)
    ).scalar_one()


def _refresh_lead(db: Session, thread_id: int) -> Lead:
    thread = db.get(WhatsAppThread, thread_id)
    return db.get(Lead, thread.lead_id)


def _run_ce(
    db: Session,
    eng: ConversationEngine,
    thread_id: int,
    wa_id: str,
    base_msg_id: str,
    texts: list[str],
    ai_reply: Optional[str] = None,
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

    def _fake_send_wa(*, to_wa_id, text):
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
                       side_effect=_fake_send_wa):
                with patch("app.services.conversation_engine.reset_unanswered_alert"):
                    result = eng.handle(ev)
    return result, sent_texts


def _seed_viaticos(db: Session) -> None:
    """Seed ViaticosZone rows required for all test cases."""
    for grp, det, viaticos in [
        ("CABA", "Palermo", 0),
        ("Norte", "Pilar", 50000),
        ("Oeste", "San Miguel", 90000),
    ]:
        existing = db.execute(
            sql_text("SELECT id FROM viaticos_zones WHERE zone_group=:g AND zone_detail=:d"),
            {"g": grp, "d": det},
        ).fetchone()
        if not existing:
            db.add(ViaticosZone(zone_group=grp, zone_detail=det, viaticos=viaticos))
    db.commit()


def _setup_quoted_thread(
    db: Session,
    wa_id: str,
    thread_id_hint: int,
    candidate_marca: str,
    candidate_modelo: str,
    candidate_anio: int,
    candidate_tipo: str,
    candidate_zone_group: str | None,
    candidate_zone_detail: str | None,
    state_zone_group: str | None,
    state_zone_detail: str | None,
) -> tuple[int, int]:
    """Create lead + thread + contact + candidate + state in QUOTED stage.
    Focus is set via state.current_focus_candidate_id.
    Returns (thread_id, candidate_id).
    """
    lead = Lead(nombre="Test User", telefono=wa_id, flag="PRESUPUESTANDO")
    db.add(lead)
    db.flush()

    contact = WhatsAppContact(wa_id=wa_id, display_name="Test User")
    db.add(contact)
    db.flush()

    thread = WhatsAppThread(id=thread_id_hint, lead_id=lead.id, contact_id=contact.id)
    db.add(thread)
    db.flush()

    candidate = WhatsAppThreadCandidate(
        thread_id=thread.id,
        marca=candidate_marca,
        modelo=candidate_modelo,
        anio=candidate_anio,
        tipo_vehiculo=candidate_tipo,
        zone_group=candidate_zone_group,
        zone_detail=candidate_zone_detail,
        status="current_focus",
    )
    db.add(candidate)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id,
        last_stage="QUOTED",
        home_zone_group=state_zone_group,
        home_zone_detail=state_zone_detail,
        current_focus_candidate_id=candidate.id,
        current_revision_id=None,
    )
    db.add(state)
    db.commit()

    return thread.id, candidate.id


def _build_ctx(db: Session, thread_id: int) -> tuple[_Context, WhatsAppThreadState]:
    """Build a _Context and load state for direct accessor testing."""
    from sqlalchemy import select
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


# ═══════════════════════════════════════════════════════════════════════════════
# Case A — Focus+Palermo: reply must say "Palermo", NOT stale "San Miguel"
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseA_FocusPalermoDisplay(unittest.TestCase):
    """Precondition: Focus 2019 AUTO, candidate zone CABA/Palermo, state zone Oeste/San Miguel.
    State home_zone_* is stale from a prior candidate.
    Expected: quote reply displays "Palermo" (candidate zone), NOT "San Miguel" (state zone).
    Pricing must use CABA/Palermo → 140k (viatico 0).
    """

    WA_ID = "5491140000001"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.cand_id = _setup_quoted_thread(
            db=self.db,
            wa_id=self.WA_ID,
            thread_id_hint=4001,
            candidate_marca="Ford",
            candidate_modelo="Focus",
            candidate_anio=2019,
            candidate_tipo="AUTO",
            candidate_zone_group="CABA",
            candidate_zone_detail="Palermo",
            state_zone_group="Oeste",
            state_zone_detail="San Miguel",
        )

    def tearDown(self):
        self.db.close()

    def _quote_reply(self) -> list[str]:
        """Send a message that triggers PRESUPUESTO_ENVIADO in QUOTED stage."""
        ai_payload = json.dumps({
            "intent": "PRESUPUESTO_ENVIADO",
            "reply": "Acá va tu presupuesto.",
            "deferred_interest": False,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        _, sent = _run_ce(
            db=self.db, eng=self.eng,
            thread_id=self.thread_id, wa_id=self.WA_ID,
            base_msg_id="caseA-01",
            texts=["¿Cuánto sale la revisión?"],
            ai_reply=ai_payload,
        )
        return sent

    def test_a_quote_displays_palermo_not_san_miguel(self):
        """Quote reply must mention 'Palermo', not 'San Miguel'."""
        sent = self._quote_reply()
        full = " ".join(sent)
        self.assertIn("Palermo", full, f"Expected 'Palermo' in reply, got: {full!r}")
        self.assertNotIn("San Miguel", full, f"'San Miguel' must not appear in reply, got: {full!r}")

    def test_a_quote_price_is_140k(self):
        """Price must be 140,000 (CABA viatico=0, AUTO base)."""
        sent = self._quote_reply()
        full = " ".join(sent)
        self.assertRegex(full, r"140[.,]?000", f"Expected 140k in reply, got: {full!r}")

    def test_a_state_zone_unchanged(self):
        """F4 uses candidate-first; state.home_zone_* must remain untouched (Option A)."""
        self._quote_reply()
        state = _refresh_state(self.db, self.thread_id)
        self.assertEqual(state.home_zone_group, "Oeste")
        self.assertEqual(state.home_zone_detail, "San Miguel")

    def test_a_candidate_zone_intact(self):
        """Candidate zone must remain CABA/Palermo."""
        self._quote_reply()
        cand = self.db.get(WhatsAppThreadCandidate, self.cand_id)
        self.assertEqual(cand.zone_group, "CABA")
        self.assertEqual(cand.zone_detail, "Palermo")

    def test_a_pricing_display_consistency(self):
        """_get_active_inspection_location zone_group must equal _compute_price_quote zone_group."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        quote = self.eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(quote, "PricingService must return a quote for CABA/Palermo/AUTO")
        self.assertEqual(quote.zone_group, "CABA")
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        self.assertEqual(grp, quote.zone_group)
        self.assertEqual(det, quote.zone_detail)


# ═══════════════════════════════════════════════════════════════════════════════
# Case B — Focus+Pilar: reply must say "Pilar", NOT stale "Palermo"
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseB_FocusPilarDisplay(unittest.TestCase):
    """Precondition: Focus 2018 AUTO, candidate zone Norte/Pilar, state zone CABA/Palermo.
    State home_zone_* is stale from the previous turn (before year+zone correction).
    Expected: quote reply displays "Pilar" (candidate zone), NOT "Palermo" (state zone).
    Pricing must use Norte/Pilar → 190k (viatico 50000).
    """

    WA_ID = "5491140000002"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.cand_id = _setup_quoted_thread(
            db=self.db,
            wa_id=self.WA_ID,
            thread_id_hint=4002,
            candidate_marca="Ford",
            candidate_modelo="Focus",
            candidate_anio=2018,
            candidate_tipo="AUTO",
            candidate_zone_group="Norte",
            candidate_zone_detail="Pilar",
            state_zone_group="CABA",
            state_zone_detail="Palermo",
        )

    def tearDown(self):
        self.db.close()

    def _quote_reply(self) -> list[str]:
        ai_payload = json.dumps({
            "intent": "PRESUPUESTO_ENVIADO",
            "reply": "Acá va tu presupuesto.",
            "deferred_interest": False,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        _, sent = _run_ce(
            db=self.db, eng=self.eng,
            thread_id=self.thread_id, wa_id=self.WA_ID,
            base_msg_id="caseB-01",
            texts=["¿Cuánto sale la revisión?"],
            ai_reply=ai_payload,
        )
        return sent

    def test_b_quote_displays_pilar_not_palermo(self):
        """Quote reply must mention 'Pilar', not 'Palermo'."""
        sent = self._quote_reply()
        full = " ".join(sent)
        self.assertIn("Pilar", full, f"Expected 'Pilar' in reply, got: {full!r}")
        self.assertNotIn("Palermo", full, f"'Palermo' must not appear in reply, got: {full!r}")

    def test_b_quote_price_is_190k(self):
        """Price must be 190,000 (Norte viatico=50000, AUTO base=140000)."""
        sent = self._quote_reply()
        full = " ".join(sent)
        self.assertRegex(full, r"190[.,]?000", f"Expected 190k in reply, got: {full!r}")

    def test_b_state_zone_unchanged(self):
        """state.home_zone_* must remain CABA/Palermo (Option A — no sync)."""
        self._quote_reply()
        state = _refresh_state(self.db, self.thread_id)
        self.assertEqual(state.home_zone_group, "CABA")
        self.assertEqual(state.home_zone_detail, "Palermo")

    def test_b_pricing_display_consistency(self):
        """_get_active_inspection_location must agree with _compute_price_quote on Norte/Pilar."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        quote = self.eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(quote)
        self.assertEqual(quote.zone_group, "Norte")
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        self.assertEqual(grp, quote.zone_group)
        self.assertEqual(det, quote.zone_detail)


# ═══════════════════════════════════════════════════════════════════════════════
# Case C — Location-only correction: "No, perdón, está en San Miguel."
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseC_LocationOnlyCorrection(unittest.TestCase):
    """Precondition: Focus 2019 AUTO, candidate zone CABA/Palermo, state zone CABA/Palermo,
    stage QUOTED.
    Input: 'No, perdón, está en San Miguel.'
    AI action updates candidate zone to Oeste/San Miguel.
    F3-T2 fires: zone changed Palermo→San Miguel while QUOTED → reset to QUALIFYING.
    Deterministic override re-prices for Oeste/San Miguel → 230k.
    Reply must say "San Miguel".
    """

    WA_ID = "5491140000003"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)
        self.thread_id, self.cand_id = _setup_quoted_thread(
            db=self.db,
            wa_id=self.WA_ID,
            thread_id_hint=4003,
            candidate_marca="Ford",
            candidate_modelo="Focus",
            candidate_anio=2019,
            candidate_tipo="AUTO",
            candidate_zone_group="CABA",
            candidate_zone_detail="Palermo",
            state_zone_group="CABA",
            state_zone_detail="Palermo",
        )

    def tearDown(self):
        self.db.close()

    def _run_correction(self) -> tuple[object, list[str]]:
        ai_payload = json.dumps({
            "intent": "QUALIFYING",
            "reply": "Gracias por la corrección.",
            "deferred_interest": False,
            "candidate": {
                "action": "update",
                "zone_group": "Oeste",
                "zone_detail": "San Miguel",
            },
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        return _run_ce(
            db=self.db, eng=self.eng,
            thread_id=self.thread_id, wa_id=self.WA_ID,
            base_msg_id="caseC-01",
            texts=["No, perdón, está en San Miguel."],
            ai_reply=ai_payload,
        )

    def test_c_reply_contains_san_miguel(self):
        """Reply must mention 'San Miguel' after zone correction."""
        _, sent = self._run_correction()
        full = " ".join(sent)
        self.assertIn("San Miguel", full, f"Expected 'San Miguel' in reply, got: {full!r}")

    def test_c_reply_not_palermo(self):
        """Reply must NOT say 'Palermo' after zone correction."""
        _, sent = self._run_correction()
        full = " ".join(sent)
        self.assertNotIn("Palermo", full, f"'Palermo' must not appear after correction, got: {full!r}")

    def test_c_candidate_zone_updated(self):
        """Candidate zone must be updated to Oeste/San Miguel."""
        self._run_correction()
        cand = self.db.get(WhatsAppThreadCandidate, self.cand_id)
        self.assertEqual(cand.zone_group, "Oeste")
        self.assertEqual(cand.zone_detail, "San Miguel")

    def test_c_stage_after_correction(self):
        """Stage must be reset away from QUOTED when zone changes (F3-T2), then re-quoted."""
        self._run_correction()
        state = _refresh_state(self.db, self.thread_id)
        # F3-T2 resets to QUALIFYING, deterministic override re-quotes → QUOTED
        self.assertIn(state.last_stage, ("QUALIFYING", "QUOTED"))


# ═══════════════════════════════════════════════════════════════════════════════
# Case D — Switch to old candidate (Peugeot/San Miguel)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseD_SwitchToOldCandidate(unittest.TestCase):
    """Precondition: two candidates in thread.
      - focus: Focus 2019 AUTO, CABA/Palermo   (current_focus_candidate_id)
      - old:   Peugeot 2008 2014 AUTO, Oeste/San Miguel
    State zone: CABA/Palermo (from Focus).
    Input: AI creates/re-creates Peugeot/San Miguel → becomes new focus.
    Expected: pricing/display use Oeste/San Miguel, NOT CABA/Palermo.
    """

    WA_ID = "5491140000004"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)

        lead = Lead(nombre="Test User D", telefono=self.WA_ID, flag="PRESUPUESTANDO")
        self.db.add(lead)
        self.db.flush()
        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="Test User D")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(id=4004, lead_id=lead.id, contact_id=contact.id)
        self.db.add(thread)
        self.db.flush()
        self.thread_id = thread.id

        # focus: Focus/CABA/Palermo
        focus_cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", anio=2019,
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
            status="current_focus",
        )
        self.db.add(focus_cand)
        self.db.flush()
        self.focus_id = focus_cand.id

        # old: Peugeot/Oeste/San Miguel
        old_cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Peugeot", modelo="2008", anio=2014,
            tipo_vehiculo="AUTO", zone_group="Oeste", zone_detail="San Miguel",
            status="inactive",
        )
        self.db.add(old_cand)
        self.db.flush()
        self.old_id = old_cand.id

        state = WhatsAppThreadState(
            thread_id=thread.id, last_stage="QUOTED",
            home_zone_group="CABA", home_zone_detail="Palermo",
            current_focus_candidate_id=focus_cand.id,
        )
        self.db.add(state)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _switch_to_peugeot(self) -> list[str]:
        """AI action creates Peugeot/San Miguel → dedup redirects to update → new focus."""
        ai_payload = json.dumps({
            "intent": "PRESUPUESTO_ENVIADO",
            "reply": "Acá va el presupuesto del Peugeot.",
            "deferred_interest": False,
            "candidate": {
                "action": "create",
                "marca": "Peugeot",
                "modelo": "2008",
                "anio": 2014,
                "tipo_vehiculo": "AUTO",
                "zone_group": "Oeste",
                "zone_detail": "San Miguel",
            },
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        _, sent = _run_ce(
            db=self.db, eng=self.eng,
            thread_id=self.thread_id, wa_id=self.WA_ID,
            base_msg_id="caseD-01",
            texts=["Al final el Peugeot 2008 en San Miguel."],
            ai_reply=ai_payload,
        )
        return sent

    def test_d_reply_displays_san_miguel(self):
        """Reply must display 'San Miguel' after switch to Peugeot candidate."""
        sent = self._switch_to_peugeot()
        full = " ".join(sent)
        self.assertIn("San Miguel", full, f"Expected 'San Miguel' in reply, got: {full!r}")

    def test_d_reply_not_palermo(self):
        """Reply must not display 'Palermo' after switch to Peugeot."""
        sent = self._switch_to_peugeot()
        full = " ".join(sent)
        self.assertNotIn("Palermo", full, f"'Palermo' must not appear after switch, got: {full!r}")

    def test_d_pricing_display_consistency(self):
        """After switch, accessor and pricing must agree on Oeste/San Miguel."""
        self._switch_to_peugeot()
        self.db.expire_all()
        ctx, state = _build_ctx(self.db, self.thread_id)
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        quote = self.eng._compute_price_quote(ctx, state)
        if quote:
            self.assertEqual(grp, quote.zone_group,
                f"display zone {grp!r} != pricing zone {quote.zone_group!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# Case E — Candidate has null zone → state fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestCaseE_CandidateNullZoneFallback(unittest.TestCase):
    """Precondition: candidate exists but has no zone (zone_group=None, zone_detail=None).
    State: home_zone_group=CABA, home_zone_detail=Palermo.
    Expected: accessor returns state fallback → reply says 'Palermo'.
    Pricing must use CABA/Palermo → 140k.
    """

    WA_ID = "5491140000005"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)

        lead = Lead(nombre="Test User E", telefono=self.WA_ID, flag="PRESUPUESTANDO")
        self.db.add(lead)
        self.db.flush()
        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="Test User E")
        self.db.add(contact)
        self.db.flush()
        thread = WhatsAppThread(id=4005, lead_id=lead.id, contact_id=contact.id)
        self.db.add(thread)
        self.db.flush()
        self.thread_id = thread.id

        cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Toyota", modelo="Corolla", anio=2020,
            tipo_vehiculo="AUTO", zone_group=None, zone_detail=None,
            status="current_focus",
        )
        self.db.add(cand)
        self.db.flush()
        self.cand_id = cand.id

        state = WhatsAppThreadState(
            thread_id=thread.id, last_stage="QUOTED",
            home_zone_group="CABA", home_zone_detail="Palermo",
            current_focus_candidate_id=cand.id,
        )
        self.db.add(state)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _quote_reply(self) -> list[str]:
        ai_payload = json.dumps({
            "intent": "PRESUPUESTO_ENVIADO",
            "reply": "Acá va tu presupuesto.",
            "deferred_interest": False,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        _, sent = _run_ce(
            db=self.db, eng=self.eng,
            thread_id=self.thread_id, wa_id=self.WA_ID,
            base_msg_id="caseE-01",
            texts=["¿Cuánto sale?"],
            ai_reply=ai_payload,
        )
        return sent

    def test_e_accessor_returns_state_fallback(self):
        """With no candidate zone, accessor must return state.home_zone_*."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        self.assertEqual(grp, "CABA")
        self.assertEqual(det, "Palermo")

    def test_e_reply_displays_palermo(self):
        """Reply must say 'Palermo' (state fallback when candidate has no zone)."""
        sent = self._quote_reply()
        full = " ".join(sent)
        self.assertIn("Palermo", full, f"Expected 'Palermo' in reply (state fallback), got: {full!r}")

    def test_e_pricing_uses_state_fallback(self):
        """Pricing must use CABA/Palermo → 140k when candidate has no zone."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        quote = self.eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(quote, "Must get a quote using CABA/Palermo state fallback")
        self.assertEqual(quote.zone_group, "CABA")
        self.assertEqual(quote.precio_total, 140000)

    def test_e_pricing_display_consistency(self):
        """Accessor zone group must equal pricing zone group."""
        ctx, state = _build_ctx(self.db, self.thread_id)
        grp, det = self.eng._get_active_inspection_location(ctx, state)
        quote = self.eng._compute_price_quote(ctx, state)
        self.assertIsNotNone(quote)
        self.assertEqual(grp, quote.zone_group)
        self.assertEqual(det, quote.zone_detail)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant — pricing zone == display zone for all zone combinations
# ═══════════════════════════════════════════════════════════════════════════════

class TestPricingDisplayConsistencyInvariant(unittest.TestCase):
    """For every zone configuration, _get_active_inspection_location must return
    the same zone that _compute_price_quote uses internally (LR-1 rule).
    Both must agree: display and pricing always use candidate zone first,
    then state zone as fallback.
    """

    def _run_invariant(
        self,
        candidate_zone_group: str | None,
        candidate_zone_detail: str | None,
        state_zone_group: str | None,
        state_zone_detail: str | None,
        thread_id: int,
    ) -> None:
        db = _new_session()
        _clean_all(db)
        _seed_viaticos(db)
        eng = _make_engine(db)
        try:
            wa_id = f"54911400100{thread_id}"
            tid, _ = _setup_quoted_thread(
                db=db, wa_id=wa_id, thread_id_hint=thread_id,
                candidate_marca="Toyota", candidate_modelo="Yaris", candidate_anio=2021,
                candidate_tipo="AUTO",
                candidate_zone_group=candidate_zone_group,
                candidate_zone_detail=candidate_zone_detail,
                state_zone_group=state_zone_group,
                state_zone_detail=state_zone_detail,
            )
            ctx, state = _build_ctx(db, tid)
            acc_grp, acc_det = eng._get_active_inspection_location(ctx, state)
            quote = eng._compute_price_quote(ctx, state)
            if quote:
                self.assertEqual(
                    acc_grp, quote.zone_group,
                    f"zone_group mismatch: accessor={acc_grp!r} pricing={quote.zone_group!r} "
                    f"[cand={candidate_zone_group!r}/{candidate_zone_detail!r} "
                    f"state={state_zone_group!r}/{state_zone_detail!r}]",
                )
                self.assertEqual(
                    acc_det, quote.zone_detail,
                    f"zone_detail mismatch: accessor={acc_det!r} pricing={quote.zone_detail!r}",
                )
        finally:
            db.close()

    def test_inv_candidate_zone_wins_over_state(self):
        """Candidate CABA/Palermo wins even when state says Oeste/San Miguel."""
        self._run_invariant("CABA", "Palermo", "Oeste", "San Miguel", 5001)

    def test_inv_candidate_norte_pilar(self):
        """Candidate Norte/Pilar wins even when state says CABA/Palermo."""
        self._run_invariant("Norte", "Pilar", "CABA", "Palermo", 5002)

    def test_inv_state_fallback_when_no_candidate_zone(self):
        """State CABA/Palermo is used when candidate has no zone."""
        self._run_invariant(None, None, "CABA", "Palermo", 5003)

    def test_inv_oeste_san_miguel_candidate(self):
        """Candidate Oeste/San Miguel wins over state CABA/Palermo."""
        self._run_invariant("Oeste", "San Miguel", "CABA", "Palermo", 5004)


if __name__ == "__main__":
    unittest.main()
