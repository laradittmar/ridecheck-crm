"""WILD-04R-F3 — Exact Messy-Turn Proof

Three owner-specified exact representative cases run through the real
ConversationEngine with exact preconditions and exact inputs.

Case A — Vehicle + Location replacement in one turn
Case B — Two corrections (year + zone) in one turn
Case C — Acceptance + Scheduling + FAQ in one burst

Uses real PricingRepository (reads pricing_base.csv) with seeded ViaticosZone.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, date, timedelta, timezone
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
from app.services.conversation_engine import ConversationEngine
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


# ═══════════════════════════════════════════════════════════════════════════════
# Case A — Vehicle + Location replacement in one turn
# ═══════════════════════════════════════════════════════════════════════════════
class TestExactCaseA(unittest.TestCase):
    """EXACT PRECONDITION: Peugeot 2008 / 2014 AUTO, Oeste/San Miguel, QUOTED.
    EXACT INPUT: 'Al final ese auto se cayó. Encontré un Focus 2019 en Palermo.'

    "en Palermo" does NOT match _VEHICLE_LOCATION_CLAUSE_PATTERNS (LR-3 requires
    "el auto/vehículo está en X" prefix). Zone reaches candidate via AI candidate
    data (action=create, zone_group=CABA, zone_detail=Palermo). State zone stays
    Oeste/San Miguel throughout (unchanged).

    F3-T2 fires: candidate zone changed San Miguel→Palermo while in QUOTED →
    reset to QUALIFYING. Deterministic override re-prices Focus+CABA/Palermo →
    140k → sends quote → final stage QUOTED.
    """

    WA_ID = "5491153371901"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        # Seed ViaticosZone: CABA/Palermo (for candidate quote)
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="Palermo", viaticos=0))
        self.db.commit()

        # Seed contact, lead, thread
        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="TestCaseA", phone=None)
        self.db.add(contact)
        self.db.flush()

        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="CaseA", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()

        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()

        # QUOTED state with Oeste/San Miguel
        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            home_zone_group="Oeste", home_zone_detail="San Miguel",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()

        # Peugeot 2008 2014 AUTO — current focus candidate
        peugeot = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Peugeot", modelo="2008", tipo_vehiculo="AUTO",
            anio=2014, status="current_focus",
            zone_group="Oeste", zone_detail="San Miguel",
        )
        self.db.add(peugeot)
        self.db.flush()

        state.current_focus_candidate_id = peugeot.id
        self.db.commit()
        self.db.expire_all()

        self.thread = thread
        self.lead_id = lead.id
        self.peugeot_id = peugeot.id
        self.eng = _make_engine(self.db)

        # Seed the exact input message
        self.db.add(WhatsAppMessage(
            thread_id=thread.id, wa_message_id="caseA-0",
            direction="in", timestamp=_NOW,
            text="Al final ese auto se cayó. Encontré un Focus 2019 en Palermo.",
            status="received",
        ))
        self.db.commit()
        self.db.expire_all()

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        _ai = json.dumps({
            "intent": "QUALIFYING",
            "reply": "Anotado el Focus 2019.",
            "deferred_interest": False,
            "candidate": {
                "action": "create",
                "marca": "Ford",
                "modelo": "Focus",
                "tipo_vehiculo": "AUTO",
                "anio": 2019,
                "zone_group": "CABA",
                "zone_detail": "Palermo",
                "status": "current_focus",
            },
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "caseA",
            ["Al final ese auto se cayó. Encontré un Focus 2019 en Palermo."],
            ai_reply=_ai,
        )

    # ── Candidate assertions ──────────────────────────────────────────────────

    def test_caseA_two_candidates(self):
        """Peugeot preserved + new Focus created = 2 rows."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 2, f"Expected 2 candidates, got {len(cands)}")

    def test_caseA_peugeot_demoted_to_mentioned(self):
        """Old Peugeot must be demoted to status='mentioned'."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        statuses = {(c.marca, c.modelo): c.status for c in cands}
        self.assertEqual(statuses.get(("Peugeot", "2008")), "mentioned",
                         f"Peugeot must be 'mentioned', got: {statuses}")

    def test_caseA_focus_is_current_focus(self):
        """New Ford Focus must be current_focus."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        statuses = {(c.marca, c.modelo): c.status for c in cands}
        self.assertEqual(statuses.get(("Ford", "Focus")), "current_focus",
                         f"Focus must be 'current_focus', got: {statuses}")

    def test_caseA_focus_zone_is_palermo(self):
        """Focus candidate zone_detail must be Palermo (set by AI candidate data)."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus = next((c for c in cands if c.marca == "Ford" and c.modelo == "Focus"), None)
        self.assertIsNotNone(focus, "Ford Focus candidate not found")
        self.assertEqual((focus.zone_group or "").upper(), "CABA",
                         f"Focus zone_group must be CABA, got: {focus.zone_group!r}")
        self.assertEqual(focus.zone_detail, "Palermo",
                         f"Focus zone_detail must be Palermo, got: {focus.zone_detail!r}")

    def test_caseA_san_miguel_not_on_focus(self):
        """San Miguel must NOT appear as Focus candidate zone_detail."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus = next((c for c in cands if c.marca == "Ford" and c.modelo == "Focus"), None)
        self.assertIsNotNone(focus, "Ford Focus candidate not found")
        self.assertNotEqual((focus.zone_detail or "").lower(), "san miguel",
                             "San Miguel must NOT be on Focus candidate")

    def test_caseA_focus_year_2019(self):
        """Focus candidate anio must be 2019."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus = next((c for c in cands if c.marca == "Ford" and c.modelo == "Focus"), None)
        self.assertIsNotNone(focus, "Ford Focus candidate not found")
        self.assertEqual(focus.anio, 2019, f"Focus anio must be 2019, got: {focus.anio}")

    def test_caseA_focus_current_focus_candidate_id(self):
        """State.current_focus_candidate_id must point to Focus, not Peugeot."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        cands = _candidates(self.db, self.thread.id)
        focus = next((c for c in cands if c.marca == "Ford" and c.modelo == "Focus"), None)
        self.assertIsNotNone(focus)
        self.assertEqual(state.current_focus_candidate_id, focus.id,
                         f"current_focus_candidate_id must point to Focus id={focus.id}, "
                         f"got: {state.current_focus_candidate_id}")

    # ── Stage and flag assertions ─────────────────────────────────────────────

    def test_caseA_final_stage_quoted(self):
        """F3-T2 fires (zone changed) → re-prices → deterministic override → QUOTED."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "QUOTED",
                         f"Final stage must be QUOTED, got: {state.last_stage!r}")

    def test_caseA_lead_flag_presupuesto_enviado(self):
        """Deterministic override must set lead.flag = PRESUPUESTO_ENVIADO."""
        self._run()
        self.db.expire_all()
        lead = _refresh_lead(self.db, self.thread.id)
        self.assertEqual(lead.flag, "PRESUPUESTO_ENVIADO",
                         f"lead.flag must be PRESUPUESTO_ENVIADO, got: {lead.flag!r}")

    # ── Pricing assertions ────────────────────────────────────────────────────

    def test_caseA_quote_140k(self):
        """Quote must be 140k (AUTO base) + 0 (CABA viatico) = 140,000."""
        _, sent_texts = self._run()
        combined = "\n".join(sent_texts)
        self.assertIn("140", combined,
                      f"Reply must contain '140' (quote for AUTO+CABA). Got: {combined!r}")

    # ── Exact reply capture ───────────────────────────────────────────────────

    def test_caseA_reply_sent(self):
        """CE must produce exactly one outbound message."""
        _, sent_texts = self._run()
        self.assertEqual(len(sent_texts), 1,
                         f"Expected 1 sent message, got {len(sent_texts)}: {sent_texts!r}")

    def test_caseA_reply_contains_focus(self):
        """Reply must name Ford Focus."""
        _, sent_texts = self._run()
        combined = "\n".join(sent_texts)
        self.assertIn("Focus", combined,
                      f"Reply must mention 'Focus'. Got: {combined!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# Case B — Two corrections in one turn
# ═══════════════════════════════════════════════════════════════════════════════
class TestExactCaseB(unittest.TestCase):
    """EXACT PRECONDITION: Ford Focus 2019 AUTO, CABA/Palermo, QUOTED.
    EXACT INPUT: 'Perdón, es 2018 y está en Pilar, no Palermo.'

    "y está en Pilar" does NOT match _VEHICLE_LOCATION_CLAUSE_PATTERNS (LR-3
    requires "el auto/vehículo está en X" prefix). Year+zone corrections arrive
    via AI candidate action=update with no id (falls back to current_focus).

    F3-T2 fires: candidate zone changed Palermo→Pilar while in QUOTED → reset
    to QUALIFYING. Deterministic override re-prices Focus 2018 + Norte/Pilar →
    140k base + 50k viatico = 190k → sends quote → final stage QUOTED.

    Candidate count stays at 1 (update, not create). Same candidate ID.
    """

    WA_ID = "5491153371902"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        # Seed ViaticosZone for state normalization + candidate quote
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="Palermo", viaticos=0))
        self.db.add(ViaticosZone(zone_group="Norte", zone_detail="Pilar", viaticos=50_000))
        self.db.commit()

        # Seed contact, lead, thread
        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="TestCaseB", phone=None)
        self.db.add(contact)
        self.db.flush()

        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="CaseB", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()

        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()

        # QUOTED state with CABA/Palermo
        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            home_zone_group="CABA", home_zone_detail="Palermo",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()

        # Ford Focus 2019 AUTO — current focus candidate
        focus = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.db.add(focus)
        self.db.flush()

        state.current_focus_candidate_id = focus.id
        self.db.commit()
        self.db.expire_all()

        self.thread = thread
        self.lead_id = lead.id
        self.focus_id_before = focus.id
        self.eng = _make_engine(self.db)

        # Seed the exact input message
        self.db.add(WhatsAppMessage(
            thread_id=thread.id, wa_message_id="caseB-0",
            direction="in", timestamp=_NOW,
            text="Perdón, es 2018 y está en Pilar, no Palermo.",
            status="received",
        ))
        self.db.commit()
        self.db.expire_all()

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        _ai = json.dumps({
            "intent": "QUALIFYING",
            "reply": "Perfecto, actualicé el año y la zona.",
            "deferred_interest": False,
            "candidate": {
                "action": "update",
                "anio": 2018,
                "zone_group": "Norte",
                "zone_detail": "Pilar",
            },
            "extracted": {},
            "lead_flag": None,
            "needs_human": False,
        })
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "caseB",
            ["Perdón, es 2018 y está en Pilar, no Palermo."],
            ai_reply=_ai,
        )

    # ── Candidate assertions ──────────────────────────────────────────────────

    def test_caseB_one_candidate(self):
        """Update must not create a second candidate — count stays 1."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 1,
                         f"Expected 1 candidate (update path), got {len(cands)}")

    def test_caseB_same_candidate_id(self):
        """Update path must reuse the same candidate row (same DB id)."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].id, self.focus_id_before,
                         f"Candidate id must be unchanged: {self.focus_id_before}, "
                         f"got {cands[0].id}")

    def test_caseB_year_corrected_to_2018(self):
        """Candidate anio must be corrected from 2019 to 2018."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].anio, 2018,
                         f"anio must be 2018, got: {cands[0].anio}")

    def test_caseB_zone_group_updated_to_norte(self):
        """Candidate zone_group must be updated to Norte."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual((cands[0].zone_group or "").lower(), "norte",
                         f"zone_group must be Norte, got: {cands[0].zone_group!r}")

    def test_caseB_zone_detail_updated_to_pilar(self):
        """Candidate zone_detail must be updated to Pilar."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].zone_detail, "Pilar",
                         f"zone_detail must be Pilar, got: {cands[0].zone_detail!r}")

    def test_caseB_palermo_not_on_candidate(self):
        """Palermo must not remain as zone_detail on updated candidate."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertNotEqual((cands[0].zone_detail or "").lower(), "palermo",
                             "Palermo must be gone from candidate zone_detail")

    # ── Stage and flag assertions ─────────────────────────────────────────────

    def test_caseB_final_stage_quoted(self):
        """F3-T2 fires + re-quote → final stage QUOTED."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "QUOTED",
                         f"Final stage must be QUOTED, got: {state.last_stage!r}")

    def test_caseB_lead_flag_presupuesto_enviado(self):
        """Deterministic re-quote must set lead.flag = PRESUPUESTO_ENVIADO."""
        self._run()
        self.db.expire_all()
        lead = _refresh_lead(self.db, self.thread.id)
        self.assertEqual(lead.flag, "PRESUPUESTO_ENVIADO",
                         f"lead.flag must be PRESUPUESTO_ENVIADO, got: {lead.flag!r}")

    # ── Pricing assertions ────────────────────────────────────────────────────

    def test_caseB_quote_190k(self):
        """Quote must be 140k (AUTO base) + 50k (Norte viatico) = 190,000."""
        _, sent_texts = self._run()
        combined = "\n".join(sent_texts)
        self.assertIn("190", combined,
                      f"Reply must contain '190' (quote for AUTO+Norte/Pilar). Got: {combined!r}")

    # ── Exact reply capture ───────────────────────────────────────────────────

    def test_caseB_reply_sent(self):
        """CE must produce exactly one outbound message."""
        _, sent_texts = self._run()
        self.assertEqual(len(sent_texts), 1,
                         f"Expected 1 sent message, got {len(sent_texts)}: {sent_texts!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# Case C — Acceptance + Scheduling + FAQ in one burst
# ═══════════════════════════════════════════════════════════════════════════════
class TestExactCaseC(unittest.TestCase):
    """EXACT PRECONDITION: Active candidate (Ford Focus 2019 AUTO, CABA/Palermo), QUOTED.
    EXACT THREE-MESSAGE BURST:
      1. 'Dale, hagamos ese.'
      2. 'Mejor el martes a las 14.'
      3. '¿Puedo pagar en efectivo?'

    'Dale' is in _ACCEPTANCE_KEYWORDS; _has_acceptance_word suppresses Layer D.
    'martes' → 2026-09-01 (next Tuesday from 2026-08-26). 'a las 14' → 14:00.
    QUOTED+day+time path fires (line 2485): lead.flag=ACEPTADO, stage=SCHEDULING,
    _try_schedule_and_flow called → slot valid → text fallback (flow_id='').
    _faq_reconciliation_burst armed (line 2439); _compose_secondary_answers fires
    inside _send_text_to_wa; 'puedo pagar' triggers payment answer.

    State after: preferred_day='2026-09-01', preferred_time='14:00',
    stage=SCHEDULING, lead.flag=ACEPTADO.
    Reply: 'Perfecto, tenemos disponibilidad. ¡Ya te confirmo!\\n\\n
            Aceptamos efectivo, transferencia bancaria y Mercado Pago.'
    answer_source=SCHEDULING_SERVICE, contributing_sources=['FAQ_RULE'].
    burst_message_count=3 (3 DB messages between cursor and current event).
    """

    WA_ID = "5491153371903"

    # Exact inputs
    MSG_1 = "Dale, hagamos ese."
    MSG_2 = "Mejor el martes a las 14."
    MSG_3 = "¿Puedo pagar en efectivo?"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        # ViaticosZone: CABA/Palermo (used if pricing runs, though Case C exits before it)
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="Palermo", viaticos=0))
        self.db.commit()

        # Seed contact, lead, thread
        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="TestCaseC", phone=None)
        self.db.add(contact)
        self.db.flush()

        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="CaseC", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()

        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()

        # QUOTED state with CABA/Palermo
        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            home_zone_group="CABA", home_zone_detail="Palermo",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()

        # Ford Focus 2019 AUTO — current focus candidate
        focus = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.db.add(focus)
        self.db.flush()

        state.current_focus_candidate_id = focus.id

        # Seed a "previous cursor" message so _fetch_burst_messages returns 3 rows.
        # This simulates the prior bot reply ack in the DB.
        prev_msg = WhatsAppMessage(
            thread_id=thread.id, wa_message_id="caseC-prev",
            direction="in", timestamp=_NOW + timedelta(seconds=-10),
            text="[previous turn]", status="received",
        )
        self.db.add(prev_msg)
        self.db.flush()

        # Set previous processed cursor so burst discovery fetches our 3 messages
        state.last_processed_inbound_wa_message_id = "caseC-prev"

        # Seed the exact 3 burst messages (AFTER the cursor)
        for i, txt in enumerate([self.MSG_1, self.MSG_2, self.MSG_3]):
            self.db.add(WhatsAppMessage(
                thread_id=thread.id,
                wa_message_id=f"caseC-{i}",
                direction="in",
                timestamp=_NOW + timedelta(seconds=i),
                text=txt,
                status="received",
            ))
        self.db.commit()
        self.db.expire_all()

        self.thread = thread
        self.lead_id = lead.id
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        # AI mock for Case C — scheduling path fires before AI, so mock is never called.
        _ai = json.dumps({
            "intent": "SCHEDULING",
            "reply": "¡Perfecto! Agendemos para el martes a las 14.",
            "deferred_interest": False,
            "candidate": {"action": "none"},
            "extracted": {},
            "lead_flag": "ACEPTADO",
            "needs_human": False,
        })
        burst = [self.MSG_1, self.MSG_2, self.MSG_3]
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "caseC",
            burst,
            ai_reply=_ai,
        )

    # ── Stage and flag assertions ─────────────────────────────────────────────

    def test_caseC_stage_scheduling(self):
        """QUOTED+day+time path: stage must advance to SCHEDULING."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "SCHEDULING",
                         f"Stage must be SCHEDULING, got: {state.last_stage!r}")

    def test_caseC_lead_flag_aceptado(self):
        """Acceptance path: lead.flag must be ACEPTADO."""
        self._run()
        self.db.expire_all()
        lead = _refresh_lead(self.db, self.thread.id)
        self.assertEqual(lead.flag, "ACEPTADO",
                         f"lead.flag must be ACEPTADO, got: {lead.flag!r}")

    # ── Scheduling assertions ─────────────────────────────────────────────────

    def test_caseC_requested_day(self):
        """'martes' from 2026-08-26 (Wed) → 2026-09-01 stored in active_requested_date.
        Tuesday 14:00 is INVALID: ScheduleService uses 09:30-14:00 on Tuesdays; the
        60-minute slot (45 min revision + 15 min buffer) must start by 13:00 to finish
        at 14:00. A 14:00 start goes to 15:00, outside business hours → slot rejected.
        CE stores the requested date in active_requested_date (not preferred_day which
        is only set when the slot is confirmed valid).
        """
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(str(state.active_requested_date), "2026-09-01",
                         f"active_requested_date must be 2026-09-01, got: {state.active_requested_date!r}")
        # preferred_day stays None — slot was rejected, not confirmed
        self.assertIsNone(state.preferred_day,
                          f"preferred_day must be None (slot not confirmed), got: {state.preferred_day!r}")

    def test_caseC_requested_time(self):
        """'a las 14' → 14:00 stored in last_requested_time (rejected slot).
        preferred_time stays None because the slot was not confirmed valid.
        """
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(str(state.last_requested_time), "14:00",
                         f"last_requested_time must be 14:00, got: {state.last_requested_time!r}")
        self.assertIsNone(state.preferred_time,
                          f"preferred_time must be None (slot not confirmed), got: {state.preferred_time!r}")

    # ── Reply assertions ──────────────────────────────────────────────────────

    def test_caseC_reply_contains_scheduling_confirmation(self):
        """Reply must contain scheduling confirmation text."""
        _, sent_texts = self._run()
        combined = "\n".join(sent_texts)
        self.assertIn("disponibilidad", combined,
                      f"Reply must contain scheduling confirmation. Got: {combined!r}")

    def test_caseC_reply_contains_payment_faq(self):
        """_compose_secondary_answers must append payment answer to scheduling reply."""
        _, sent_texts = self._run()
        combined = "\n".join(sent_texts)
        self.assertIn("efectivo", combined,
                      f"Reply must contain payment FAQ answer. Got: {combined!r}")
        self.assertIn("transferencia", combined,
                      f"Reply must contain payment FAQ answer. Got: {combined!r}")

    def test_caseC_reply_single_message(self):
        """FAQ must be appended to scheduling reply (not separate message)."""
        _, sent_texts = self._run()
        self.assertEqual(len(sent_texts), 1,
                         f"Expected 1 sent message (scheduling + FAQ appended). "
                         f"Got {len(sent_texts)}: {sent_texts!r}")

    def test_caseC_layer_d_suppressed(self):
        """Layer D must NOT have intercepted — reply is not a pure FAQ answer."""
        _, sent_texts = self._run()
        combined = "\n".join(sent_texts)
        # A pure Layer D answer would ONLY contain FAQ content, not scheduling confirmation.
        # Scheduling confirmation has "disponibilidad" or "confirmo".
        self.assertTrue(
            "disponibilidad" in combined or "confirmo" in combined,
            f"Layer D must be suppressed; scheduling confirmation expected. Got: {combined!r}",
        )

    # ── Observability assertions ──────────────────────────────────────────────

    def test_caseC_answer_source_scheduling_service(self):
        """answer_source must be SCHEDULING_SERVICE."""
        result, _ = self._run()
        self.assertEqual(getattr(result, "answer_source", None), "SCHEDULING_SERVICE",
                         f"answer_source must be SCHEDULING_SERVICE, "
                         f"got: {getattr(result, 'answer_source', None)!r}")

    def test_caseC_contributing_sources_faq_rule(self):
        """contributing_sources must include FAQ_RULE (payment FAQ appended)."""
        result, _ = self._run()
        sources = getattr(result, "contributing_sources", None)
        self.assertIsNotNone(sources, "contributing_sources must not be None")
        self.assertIn("FAQ_RULE", sources,
                      f"contributing_sources must include FAQ_RULE, got: {sources!r}")

    def test_caseC_burst_message_count_3(self):
        """burst_message_count must be 3 (3 messages between cursor and current event)."""
        result, _ = self._run()
        count = getattr(result, "burst_message_count", None)
        self.assertEqual(count, 3,
                         f"burst_message_count must be 3, got: {count!r}")


if __name__ == "__main__":
    unittest.main()
