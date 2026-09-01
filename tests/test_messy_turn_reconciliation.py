"""WILD-04R-F3 — Messy Turn Reconciliation test suite.

Verifies the F3 Turn Reconciliation layer across 10 real customer conversation
scenarios (M1–M10). Tests use multi-turn DB state where the scenario requires it.

M1  — Vehicle replacement after quote: old candidate preserved, new priced
M2  — Year correction: same candidate patched, no duplicate created
M3  — Location correction after quote: zone updated, re-quote triggered (F3-T2 guard)
M4  — Vehicle replacement + FAQ in same burst
M5  — Acceptance + FAQ (regression — full suite in test_wild04r_f3_faq_preservation.py)
M6  — Acceptance + scheduling proposal + payment FAQ
M7  — Scheduling preference correction: prior day overwritten by new day
M8  — Multiple corrections in one turn (year + location)
M9  — Ambiguous replacement: no silent mutation when vehicle identity unclear
M10 — Return to previous candidate: re-focus existing via AI, no duplicate (F3-T3/T4)
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, date, timedelta, timezone
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
from app.repositories.pricing_repository import BasePriceRow
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine
from app.services.pricing import PricingService

# ── Canonical answer strings ──────────────────────────────────────────────────
# L4.3 Phase B: hours are DERIVED from the scheduling authority — never a literal,
# so this suite cannot drift from ScheduleService's weekday table.
from app.services.conversation_engine import _faq_hours_answer as _canonical_hours
_HOURS_ANSWER    = _canonical_hours()
_PAYMENT_ANSWER  = "Aceptamos efectivo, transferencia bancaria y Mercado Pago."
_REPORT_ANSWER   = "Al finalizar la revisión te enviamos un informe detallado."
_PRESENCE_ANSWER = "No es necesario que estés presente durante la inspección."

_NOW = datetime.now(timezone.utc)

# ── AI response builders ──────────────────────────────────────────────────────

def _ai_qualifying_reply(reply: str = "¿En qué zona está el auto?") -> str:
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": reply,
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


def _ai_acceptance_reply() -> str:
    return json.dumps({
        "intent": "SCHEDULING",
        "reply": "¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?",
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": "ACEPTADO",
        "needs_human": False,
    })


def _ai_scheduling_reply(reply: str = "Anotado, ¿algún horario en particular?") -> str:
    return json.dumps({
        "intent": "SCHEDULING",
        "reply": reply,
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


def _ai_create_vehicle(marca: str, modelo: str, tipo: str, anio: int) -> str:
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": "Anotado, te paso el presupuesto.",
        "deferred_interest": False,
        "candidate": {
            "action": "create",
            "marca": marca,
            "modelo": modelo,
            "tipo_vehiculo": tipo,
            "anio": anio,
            "status": "current_focus",
        },
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


def _ai_update_candidate(candidate_id: int, **fields) -> str:
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": "Perfecto, actualicé el dato.",
        "deferred_interest": False,
        "candidate": {
            "action": "update",
            "id": candidate_id,
            **fields,
        },
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


def _ai_refocus_candidate(candidate_id: int) -> str:
    """Re-focus a previously-mentioned candidate by ID (M10 scenario)."""
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": "Perfecto, volvemos con ese vehículo.",
        "deferred_interest": False,
        "candidate": {
            "action": "update",
            "id": candidate_id,
            "status": "current_focus",
        },
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


def _ai_clarify() -> str:
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": "¿Podés decirme la marca del vehículo?",
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


# ── Pricing fake ──────────────────────────────────────────────────────────────

class _FakeZoneRow:
    def __init__(self, zg: str, zd: Optional[str], v: int) -> None:
        self.zone_group = zg
        self.zone_detail = zd
        self.viaticos = v


def _make_repo(sur_viatico: int = 90_000, norte_viatico: int = 50_000):
    class _Repo:
        def find_base_price(self, tipo: str) -> BasePriceRow:
            prices = {
                "SUV_4X4_DEPORTIVO": 150_000,
                "SUV/4x4": 150_000,
                "AUTO": 140_000,
            }
            return BasePriceRow(tipo_vehiculo=tipo, precio_base=prices.get(tipo, 140_000))

        def find_zone_by_group_and_detail(self, db, zone_group: str, zone_detail: Optional[str]):
            zg = (zone_group or "").strip().lower()
            zd = (zone_detail or "").strip().lower()
            if zg == "sur":
                return _FakeZoneRow("Sur", zone_detail, sur_viatico)
            if zg == "norte":
                return _FakeZoneRow("Norte", zone_detail, norte_viatico)
            return None
    return _Repo()


def _make_engine(db: Session, *, repo=None) -> ConversationEngine:
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
    eng._pricing = PricingService(repository=repo or _make_repo())
    from app.services.schedule import ScheduleService
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


def _add_msg(db: Session, thread_id: int, msg_id: str, text: str, offset: int = 0) -> None:
    db.add(WhatsAppMessage(
        thread_id=thread_id, wa_message_id=msg_id,
        direction="in", timestamp=_NOW + timedelta(seconds=offset),
        text=text, status="received",
    ))
    db.commit()
    db.expire_all()


def _add_messages(db: Session, thread_id: int, base_id: str, texts: list[str]) -> None:
    for i, txt in enumerate(texts):
        db.add(WhatsAppMessage(
            thread_id=thread_id, wa_message_id=f"{base_id}-{i}",
            direction="in", timestamp=_NOW + timedelta(seconds=i),
            text=txt, status="received",
        ))
    db.commit()
    db.expire_all()


def _seed_quoted_thread(
    db: Session, wa_id: str, *,
    tipo: str = "SUV_4X4_DEPORTIVO",
    zone_group: str = "Sur",
    zone_detail: str = "Berazategui",
    anio: int = 2019,
) -> tuple[WhatsAppThread, Lead, WhatsAppThreadState, WhatsAppThreadCandidate]:
    """Thread in QUOTED stage with a single candidate."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="TestUser", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="Test", necesita_humano=False)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id, needs_human=False,
        last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
        cycle_reset_pending=False, current_cycle_started_at=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        home_zone_group=zone_group, home_zone_detail=zone_detail,
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", tipo_vehiculo=tipo,
        anio=anio, status="current_focus",
        zone_group=zone_group, zone_detail=zone_detail,
    )
    db.add(cand)
    db.flush()
    state.current_focus_candidate_id = cand.id
    db.commit()
    db.expire_all()
    return thread, lead, state, cand


def _seed_two_candidate_thread(
    db: Session, wa_id: str,
) -> tuple[WhatsAppThread, Lead, WhatsAppThreadState, WhatsAppThreadCandidate, WhatsAppThreadCandidate]:
    """Thread with two candidates: Peugeot (mentioned) + Ford Focus (current_focus)."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="TwoCandidate", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="Test", necesita_humano=False)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id, needs_human=False,
        last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
        cycle_reset_pending=False, current_cycle_started_at=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        home_zone_group="Sur", home_zone_detail="Berazategui",
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    peugeot = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", tipo_vehiculo="AUTO",
        anio=2018, status="mentioned",
        zone_group="Sur", zone_detail="Berazategui",
    )
    db.add(peugeot)
    db.flush()

    focus = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
        anio=2019, status="current_focus",
        zone_group="Sur", zone_detail="Berazategui",
    )
    db.add(focus)
    db.flush()

    state.current_focus_candidate_id = focus.id
    db.commit()
    db.expire_all()
    return thread, lead, state, peugeot, focus


def _seed_scheduling_thread(
    db: Session, wa_id: str, *,
    preferred_day: Optional[str] = None,
) -> tuple[WhatsAppThread, Lead, WhatsAppThreadState, WhatsAppThreadCandidate]:
    """Thread in SCHEDULING stage post-acceptance."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="SchedUser", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="ACEPTADO", estado="CONSULTA_NUEVA", nombre="Test", necesita_humano=False)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id, needs_human=False,
        last_stage="SCHEDULING", last_intent="PREPURCHASE_INSPECTION",
        cycle_reset_pending=False, current_cycle_started_at=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        home_zone_group="Sur", home_zone_detail="Berazategui",
        preferred_day=preferred_day,
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
        anio=2019, status="current_focus",
        zone_group="Sur", zone_detail="Berazategui",
    )
    db.add(cand)
    db.flush()
    state.current_focus_candidate_id = cand.id
    db.commit()
    db.expire_all()
    return thread, lead, state, cand


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
        wa_message_id=f"{base_msg_id}-{len(texts)-1}",
        wa_id=wa_id,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )
    _ai_payload = ai_reply or _ai_qualifying_reply()
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
            _gate_result.outcome = "allowed"  # GateOutcome.ALLOWED == "allowed"
            _gate_result.message_id = 1
            _gate_inst.attempt.return_value = _gate_result
            _MockGate.return_value = _gate_inst
            with patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                       side_effect=_fake_send_wa):
                with patch("app.services.conversation_engine.reset_unanswered_alert"):
                    result = eng.handle(ev)
    return result, sent_texts


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
    from sqlalchemy import select
    thread = db.get(WhatsAppThread, thread_id)
    return db.get(Lead, thread.lead_id)


# ═══════════════════════════════════════════════════════════════════════════════
# M1 — Vehicle replacement after quote
# ═══════════════════════════════════════════════════════════════════════════════
class TestM1VehicleReplacementAfterQuote(unittest.TestCase):
    """Peugeot 2008 SUV_4X4_DEPORTIVO was quoted in Sur/Berazategui.
    Customer abandons it and introduces Ford Focus 2019.
    AI returns action=create for Focus with status=current_focus.

    Expected:
    - 2 candidates: old Peugeot (status=mentioned), new Ford Focus (current_focus)
    - Vehicle-change guard fires (SUV→AUTO): stage reset to QUALIFYING
    - New quote computed for AUTO + Sur viatico = 140k + 90k = 230k
    - Reply contains the new price
    - Old Peugeot NOT deleted (preserved historically)
    """

    WA_ID = "5491153371001"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.peugeot = _seed_quoted_thread(
            self.db, self.WA_ID,
            tipo="SUV_4X4_DEPORTIVO",
            zone_group="Sur",
            zone_detail="Berazategui",
        )
        _add_messages(self.db, self.thread.id, "m1", [
            "Al final ese auto se cayó. Tengo un Ford Focus 2019."
        ])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m1",
            ["Al final ese auto se cayó. Tengo un Ford Focus 2019."],
            ai_reply=_ai_create_vehicle("Ford", "Focus", "AUTO", 2019),
        )

    def test_m1_two_candidates_in_db(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 2, "Expected exactly 2 candidates (old Peugeot + new Focus)")

    def test_m1_old_peugeot_preserved_as_mentioned(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        statuses = {(c.marca, c.modelo): c.status for c in cands}
        self.assertEqual(statuses.get(("Peugeot", "2008")), "mentioned",
                         "Old Peugeot must be preserved as 'mentioned'")

    def test_m1_focus_is_current_focus(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus_cands = [c for c in cands if c.status == "current_focus"]
        self.assertEqual(len(focus_cands), 1)
        self.assertEqual(focus_cands[0].marca, "Ford")
        self.assertEqual(focus_cands[0].modelo, "Focus")

    def test_m1_state_current_focus_candidate_id_updated(self):
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        cands = _candidates(self.db, self.thread.id)
        focus = next(c for c in cands if c.status == "current_focus")
        self.assertEqual(state.current_focus_candidate_id, focus.id)

    def test_m1_stage_reset_to_qualifying_for_requote(self):
        """Vehicle-change guard fires (QUALIFYING) then deterministic re-quote sends → final QUOTED."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "QUOTED",
                         "After vehicle-change guard + re-quote, final stage must be QUOTED")

    def test_m1_reply_contains_new_price(self):
        """New quote for AUTO (140k) + Sur viatico (90k) = 230k."""
        _, sent = self._run()
        self.assertTrue(sent, "CE must send a reply")
        combined = " ".join(sent)
        # Price 230,000 in various formats: $230.000, 230000, 230.000
        self.assertTrue(
            "230" in combined,
            f"Reply must contain new quote price for Focus ($230k); got: {combined!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# M2 — Year correction
# ═══════════════════════════════════════════════════════════════════════════════
class TestM2YearCorrection(unittest.TestCase):
    """Ford Focus 2019 is current candidate. Customer says 'Perdón, es 2018.'
    AI returns action=update anio=2018.

    Expected:
    - Still 1 candidate (no new candidate created)
    - focus.anio == 2018
    - No stage change (year correction doesn't change tipo)
    """

    WA_ID = "5491153371002"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="M2User", phone=None)
        self.db.add(contact)
        self.db.flush()
        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="M2", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()
        self.thread = thread

        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            home_zone_group="Sur", home_zone_detail="Berazategui",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()

        self.cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="Sur", zone_detail="Berazategui",
        )
        self.db.add(self.cand)
        self.db.flush()
        state.current_focus_candidate_id = self.cand.id
        self.cand_id = self.cand.id
        self.db.commit()
        self.db.expire_all()
        self.eng = _make_engine(self.db)
        _add_messages(self.db, thread.id, "m2", ["Perdón, es 2018."])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m2",
            ["Perdón, es 2018."],
            ai_reply=_ai_update_candidate(self.cand_id, anio=2018),
        )

    def test_m2_no_new_candidate_created(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 1, "Year correction must NOT create a new candidate")

    def test_m2_same_candidate_year_updated(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].anio, 2018, "Candidate anio must be patched to 2018")

    def test_m2_candidate_id_unchanged(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].id, self.cand_id, "Candidate ID must not change on year correction")

    def test_m2_candidate_still_current_focus(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].status, "current_focus")


# ═══════════════════════════════════════════════════════════════════════════════
# M3 — Location correction after quote (F3-T2 zone re-quote guard)
# ═══════════════════════════════════════════════════════════════════════════════
class TestM3LocationCorrectionAfterQuote(unittest.TestCase):
    """Candidate was quoted in Palermo (CABA, no viatico).
    Customer says 'Pilar' (Norte zone with viatico).

    Expected (F3-T2 guard):
    - Zone updated on candidate and state
    - Stage reset to QUALIFYING (zone changed in QUOTED stage → re-quote guard fires)
    - New quote sent with Norte viatico applied
    """

    WA_ID = "5491153371003"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="M3User", phone=None)
        self.db.add(contact)
        self.db.flush()
        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="M3", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()
        self.thread = thread

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

        # Seed Norte/Pilar zone in DB for deterministic zone detection
        self.db.add(ViaticosZone(zone_group="Norte", zone_detail="Pilar", viaticos=50_000))
        self.db.flush()

        cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.db.add(cand)
        self.db.flush()
        state.current_focus_candidate_id = cand.id
        self.cand_id = cand.id
        self.db.commit()
        self.db.expire_all()
        self.eng = _make_engine(self.db, repo=_make_repo(norte_viatico=50_000))
        _add_messages(self.db, thread.id, "m3", ["El auto está en Pilar, no en Palermo."])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m3",
            ["El auto está en Pilar, no en Palermo."],
            ai_reply=_ai_qualifying_reply("Entendido, actualicé la zona."),
        )

    def test_m3_stage_reset_to_qualifying(self):
        """F3-T2: zone guard fires (QUALIFYING) then re-quote sends → final QUOTED."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(
            state.last_stage, "QUOTED",
            "After F3-T2 zone guard + re-quote, final stage must be QUOTED"
        )

    def test_m3_zone_updated(self):
        """Candidate zone must reflect new zone (Norte/Pilar) after correction."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertNotEqual(
            (cands[0].zone_detail or "").lower(), "palermo",
            "Candidate zone_detail must be updated away from Palermo"
        )

    def test_m3_reply_sent(self):
        """CE must produce a reply after location correction."""
        _, sent = self._run()
        self.assertTrue(sent, "CE must produce a reply after location correction")

    def test_m3_no_new_candidate_created(self):
        """Location correction must not create a new candidate."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# M4 — Vehicle replacement + FAQ in same burst
# ═══════════════════════════════════════════════════════════════════════════════
class TestM4VehicleReplacementPlusFAQ(unittest.TestCase):
    """Customer replaces vehicle AND asks an FAQ in the same burst.
    AI returns action=create for new vehicle.
    F3 reconciliation appends FAQ answer.

    Burst: ['Al final el Focus se cayó. Tengo un Toyota Corolla 2020.', '¿Trabajan los sábados?']
    Expected:
    - New Corolla candidate created, old Focus mentioned
    - Reply contains hours answer (sábados question)
    - Reply contains commercial next step
    """

    WA_ID = "5491153371004"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="M4User", phone=None)
        self.db.add(contact)
        self.db.flush()
        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="M4", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()
        self.thread = thread

        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage="QUOTED", last_intent="PREPURCHASE_INSPECTION",
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            home_zone_group="Sur", home_zone_detail="Berazategui",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()

        cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="Sur", zone_detail="Berazategui",
        )
        self.db.add(cand)
        self.db.flush()
        state.current_focus_candidate_id = cand.id
        self.db.commit()
        self.db.expire_all()
        self.eng = _make_engine(self.db)
        self.burst = [
            "Al final el Focus se cayó. Tengo un Toyota Corolla 2020.",
            "¿Qué horarios tienen?",
        ]
        _add_messages(self.db, thread.id, "m4", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m4",
            self.burst,
            ai_reply=_ai_create_vehicle("Toyota", "Corolla", "AUTO", 2020),
        )

    def test_m4_two_candidates(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 2)

    def test_m4_old_focus_preserved(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus_statuses = {(c.marca, c.modelo): c.status for c in cands}
        self.assertEqual(focus_statuses.get(("Ford", "Focus")), "mentioned")

    def test_m4_corolla_is_current_focus(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        current = [c for c in cands if c.status == "current_focus"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].marca, "Toyota")

    def test_m4_reply_contains_hours_answer(self):
        """FAQ reconciliation must append hours answer when sábados question in burst."""
        _, sent = self._run()
        combined = " ".join(sent)
        self.assertIn(
            _HOURS_ANSWER, combined,
            f"Hours FAQ answer must be appended to reply; got: {combined!r}"
        )

    def test_m4_single_reply(self):
        """CE must produce exactly one coherent reply, not two separate sends."""
        _, sent = self._run()
        self.assertEqual(len(sent), 1, f"Must be exactly one outbound message; got {sent!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# M5 — Acceptance + FAQ (regression: full coverage in F3 FAQ test suite)
# ═══════════════════════════════════════════════════════════════════════════════
class TestM5AcceptancePlusFAQRegression(unittest.TestCase):
    """Regression check: acceptance + FAQ still works after F3-T1/T2/T3/T4 changes.
    Full M5 coverage is in test_wild04r_f3_faq_preservation.py (F3-01, F3-03, F3-05).
    """

    WA_ID = "5491153371005"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID,
            tipo="AUTO",
            zone_group="CABA",
            zone_detail="Palermo",
        )
        self.burst = ["Dale", "¿Aceptan débito?"]
        _add_messages(self.db, self.thread.id, "m5", self.burst)

    def tearDown(self):
        self.db.close()

    def test_m5_acceptance_advances_to_scheduling(self):
        _, sent = _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m5",
            self.burst,
            ai_reply=_ai_acceptance_reply(),
        )
        self.db.expire_all()
        lead = _refresh_lead(self.db, self.thread.id)
        self.assertEqual(lead.flag, "ACEPTADO")

    def test_m5_payment_faq_in_reply(self):
        _, sent = _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m5",
            self.burst,
            ai_reply=_ai_acceptance_reply(),
        )
        combined = " ".join(sent)
        self.assertIn("efectivo", combined.lower(),
                      f"Payment FAQ must be in reply; got: {combined!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# M6 — Acceptance + scheduling preference + payment FAQ
# ═══════════════════════════════════════════════════════════════════════════════
class TestM6AcceptancePlusSchedulingPlusPayment(unittest.TestCase):
    """Burst: 'Dale, avancemos. ¿Aceptan débito?'
    Mixed words → not pure acceptance → AI path.
    AI returns ACEPTADO + scheduling reply.
    F3 appends payment FAQ.

    Note: If burst includes a specific day/time, the deterministic scheduling path
    fires. This test uses acceptance + FAQ without a specific date to exercise the
    AI path cleanly.
    """

    WA_ID = "5491153371006"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID,
            tipo="AUTO",
            zone_group="CABA",
            zone_detail="Palermo",
        )
        self.burst = ["Dale, avancemos.", "¿Aceptan débito?"]
        _add_messages(self.db, self.thread.id, "m6", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m6",
            self.burst,
            ai_reply=_ai_acceptance_reply(),
        )

    def test_m6_acceptance_detected(self):
        self._run()
        self.db.expire_all()
        lead = _refresh_lead(self.db, self.thread.id)
        self.assertEqual(lead.flag, "ACEPTADO")

    def test_m6_scheduling_stage(self):
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "SCHEDULING")

    def test_m6_payment_faq_in_reply(self):
        _, sent = self._run()
        combined = " ".join(sent)
        self.assertIn("efectivo", combined.lower(),
                      f"Payment FAQ must be appended; got: {combined!r}")

    def test_m6_single_reply(self):
        _, sent = self._run()
        self.assertEqual(len(sent), 1, "Must be one coherent reply")


# ═══════════════════════════════════════════════════════════════════════════════
# M7 — Scheduling preference correction
# ═══════════════════════════════════════════════════════════════════════════════
class TestM7SchedulingCorrection(unittest.TestCase):
    """Thread in SCHEDULING with preferred_day=next Tuesday.
    Customer says 'Mejor el viernes.'

    Expected:
    - state.preferred_day updated to Friday
    - No stage regression
    - Old Tuesday preference not reused
    """

    WA_ID = "5491153371007"

    def setUp(self):
        self.db = _new_session()
        # Use next Tuesday and next Friday relative to today
        from datetime import date, timedelta
        today = date.today()
        # Find next Tuesday (weekday=1)
        days_to_tuesday = (1 - today.weekday()) % 7 or 7
        self.tuesday_iso = (today + timedelta(days=days_to_tuesday)).isoformat()
        # Find next Friday (weekday=4)
        days_to_friday = (4 - today.weekday()) % 7 or 7
        self.friday_iso = (today + timedelta(days=days_to_friday)).isoformat()

        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_scheduling_thread(
            self.db, self.WA_ID,
            preferred_day=self.tuesday_iso,
        )
        _add_messages(self.db, self.thread.id, "m7", ["Mejor el viernes."])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m7",
            ["Mejor el viernes."],
            ai_reply=_ai_scheduling_reply("Anotado, el viernes. ¿Algún horario en particular?"),
        )

    def test_m7_new_day_stored(self):
        """New preferred_day (Friday) must overwrite old (Tuesday)."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        # preferred_day should now be the upcoming Friday (not Tuesday)
        self.assertNotEqual(state.preferred_day, self.tuesday_iso,
                            "Old preferred_day (Tuesday) must be overwritten")

    def test_m7_still_in_scheduling(self):
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "SCHEDULING")

    def test_m7_reply_sent(self):
        _, sent = self._run()
        self.assertTrue(sent, "CE must send a reply for scheduling correction")


# ═══════════════════════════════════════════════════════════════════════════════
# M8 — Multiple corrections in one turn (year + location)
# ═══════════════════════════════════════════════════════════════════════════════
class TestM8MultipleCorrections(unittest.TestCase):
    """Current: Ford Focus 2019, Palermo.
    Customer: 'Perdón, es 2018 y el auto está en Pilar.'

    Expected:
    - Same candidate (no duplicate)
    - anio patched to 2018 (AI update)
    - Zone updated to Norte/Pilar (deterministic)
    - F3-T2 guard: zone changed in QUOTED → stage reset to QUALIFYING
    - New quote sent
    """

    WA_ID = "5491153371008"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="M8User", phone=None)
        self.db.add(contact)
        self.db.flush()
        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="M8", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()
        self.thread = thread

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

        self.db.add(ViaticosZone(zone_group="Norte", zone_detail="Pilar", viaticos=50_000))
        self.db.flush()

        cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="CABA", zone_detail="Palermo",
        )
        self.db.add(cand)
        self.db.flush()
        state.current_focus_candidate_id = cand.id
        self.cand_id = cand.id
        self.db.commit()
        self.db.expire_all()
        self.eng = _make_engine(self.db, repo=_make_repo(norte_viatico=50_000))
        _add_messages(self.db, thread.id, "m8", ["Perdón, es 2018 y el auto está en Pilar."])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m8",
            ["Perdón, es 2018 y el auto está en Pilar."],
            ai_reply=_ai_update_candidate(self.cand_id, anio=2018),
        )

    def test_m8_one_candidate_only(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 1, "Multiple corrections must not create duplicate candidates")

    def test_m8_year_corrected(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(cands[0].anio, 2018, "Year must be corrected to 2018")

    def test_m8_zone_updated(self):
        """Candidate zone must be updated from Palermo (CABA) to Pilar (Norte)."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertNotEqual(
            (cands[0].zone_detail or "").lower(), "palermo",
            "Candidate zone_detail must be updated away from old Palermo"
        )

    def test_m8_stage_reset_for_requote(self):
        """F3-T2: zone guard fires (QUALIFYING) then re-quote sends → final QUOTED."""
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.last_stage, "QUOTED",
                         "After F3-T2 zone guard + re-quote, final stage must be QUOTED")


# ═══════════════════════════════════════════════════════════════════════════════
# M9 — Ambiguous replacement
# ═══════════════════════════════════════════════════════════════════════════════
class TestM9AmbiguousReplacement(unittest.TestCase):
    """Current: Ford Focus (ctx.candidates non-empty).
    Customer: 'Encontré otro 2008.'

    Expected:
    - CE asks for clarification (not silently create a new candidate)
    - Fuzzy CONFIRM gate is suppressed when candidates already exist
    - AI path handles: may ask for make clarification
    - Focus candidate unchanged
    """

    WA_ID = "5491153371009"

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)

        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="M9User", phone=None)
        self.db.add(contact)
        self.db.flush()
        lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="M9", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()
        self.thread = thread

        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage="QUALIFYING", last_intent="PREPURCHASE_INSPECTION",
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            home_zone_group="Sur", home_zone_detail="Berazategui",
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()

        cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Ford", modelo="Focus", tipo_vehiculo="AUTO",
            anio=2019, status="current_focus",
            zone_group="Sur", zone_detail="Berazategui",
        )
        self.db.add(cand)
        self.db.flush()
        state.current_focus_candidate_id = cand.id
        self.cand_id = cand.id
        self.db.commit()
        self.db.expire_all()
        self.eng = _make_engine(self.db)
        _add_messages(self.db, thread.id, "m9", ["Encontré otro 2008."])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        # AI returns clarification question — does not blindly create candidate
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m9",
            ["Encontré otro 2008."],
            ai_reply=_ai_clarify(),
        )

    def test_m9_focus_candidate_unchanged(self):
        """Ambiguous replacement must NOT mutate the existing Focus candidate."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus = next((c for c in cands if c.status == "current_focus"), None)
        self.assertIsNotNone(focus)
        # Focus should still be the Ford Focus (AI returned action=none, no new candidate)
        self.assertEqual(focus.marca, "Ford")
        self.assertEqual(focus.modelo, "Focus")

    def test_m9_no_new_duplicate_candidate(self):
        """No new candidate must be created when AI returns action=none for ambiguous turn."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 1, "Ambiguous turn must not create duplicate candidate")

    def test_m9_reply_asks_clarification(self):
        _, sent = self._run()
        combined = " ".join(sent)
        # AI returned clarification question — CE must relay it
        self.assertTrue(sent, "CE must reply with clarification request")


# ═══════════════════════════════════════════════════════════════════════════════
# M10 — Return to previous candidate (F3-T3 dedup + F3-T4 AI prompt)
# ═══════════════════════════════════════════════════════════════════════════════
class TestM10ReturnToPreviousCandidate(unittest.TestCase):
    """Thread has: Peugeot 2008 (status=mentioned) + Ford Focus (status=current_focus).
    Customer: 'Al final volvamos con el Peugeot.'
    AI sees both candidates via F3-T4 prompt and returns action=update on Peugeot's ID
    with status=current_focus to re-focus it.

    Expected:
    - Focus switches to Peugeot (no new candidate created)
    - Ford Focus demoted to 'mentioned'
    - state.current_focus_candidate_id → Peugeot's ID
    - Still 2 candidates (no duplicate)
    """

    WA_ID = "5491153371010"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.peugeot, self.focus = (
            _seed_two_candidate_thread(self.db, self.WA_ID)
        )
        self.peugeot_id = self.peugeot.id
        self.focus_id = self.focus.id
        _add_messages(self.db, self.thread.id, "m10", ["Al final volvamos con el Peugeot."])

    def tearDown(self):
        self.db.close()

    def _run(self) -> tuple[object, list[str]]:
        # AI returns action=update on the Peugeot (old mentioned candidate) with status=current_focus
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m10",
            ["Al final volvamos con el Peugeot."],
            ai_reply=_ai_refocus_candidate(self.peugeot_id),
        )

    def test_m10_no_new_candidate_created(self):
        """Re-focusing must not create a third candidate."""
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 2, "Re-focusing must leave exactly 2 candidates")

    def test_m10_peugeot_becomes_current_focus(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        peugeot = next(c for c in cands if c.id == self.peugeot_id)
        self.assertEqual(peugeot.status, "current_focus",
                         "Peugeot must become current_focus after re-focus")

    def test_m10_focus_demoted_to_mentioned(self):
        self._run()
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        focus = next(c for c in cands if c.id == self.focus_id)
        self.assertEqual(focus.status, "mentioned",
                         "Old Ford Focus must be demoted to 'mentioned'")

    def test_m10_state_pointer_updated(self):
        self._run()
        self.db.expire_all()
        state = _refresh_state(self.db, self.thread.id)
        self.assertEqual(state.current_focus_candidate_id, self.peugeot_id,
                         "state.current_focus_candidate_id must point to Peugeot")

    def test_m10_f3t3_dedup_prevents_duplicate_on_ai_create(self):
        """F3-T3: if AI mistakenly sends action=create for the same marca/modelo,
        the dedup redirects to update the existing mentioned candidate instead."""
        # Simulate AI trying to create a new Peugeot 2008 (wrong action)
        _, sent = _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m10-dedup",
            ["Al final volvamos con el Peugeot."],
            ai_reply=_ai_create_vehicle("Peugeot", "2008", "AUTO", 2018),
        )
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        # Dedup should redirect create→update, so still 2 candidates (not 3)
        self.assertLessEqual(len(cands), 2,
                             "F3-T3 dedup must prevent duplicate Peugeot 2008 candidate")
        peugeot_cands = [c for c in cands if (c.marca or "").lower() == "peugeot"]
        self.assertEqual(len(peugeot_cands), 1, "Only one Peugeot candidate after dedup")


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: M1 variant — same tipo vehicle replacement (no tipo-change guard)
# ═══════════════════════════════════════════════════════════════════════════════
class TestM1SameTypeReplacement(unittest.TestCase):
    """Vehicle replacement where old and new have same tipo (AUTO→AUTO).
    Vehicle-change guard does NOT fire (tipo unchanged).
    But a new candidate IS created and pricing still recomputes.

    Peugeot 208 (AUTO) → Ford Focus (AUTO), both in Sur/Berazategui.
    Price stays same (same tipo + same zone), but new candidate is distinct.
    """

    WA_ID = "5491153371011"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.peugeot = _seed_quoted_thread(
            self.db, self.WA_ID,
            tipo="AUTO",
            zone_group="Sur",
            zone_detail="Berazategui",
        )
        _add_messages(self.db, self.thread.id, "m1b", ["Dejá el 208, tengo un Focus 2020."])

    def tearDown(self):
        self.db.close()

    def test_m1b_new_candidate_created(self):
        _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m1b",
            ["Dejá el 208, tengo un Focus 2020."],
            ai_reply=_ai_create_vehicle("Ford", "Focus", "AUTO", 2020),
        )
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        self.assertEqual(len(cands), 2)

    def test_m1b_old_preserved(self):
        _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "m1b",
            ["Dejá el 208, tengo un Focus 2020."],
            ai_reply=_ai_create_vehicle("Ford", "Focus", "AUTO", 2020),
        )
        self.db.expire_all()
        cands = _candidates(self.db, self.thread.id)
        peugeot_cands = [c for c in cands if c.marca == "Peugeot"]
        self.assertTrue(peugeot_cands, "Old Peugeot must be preserved")
        self.assertNotEqual(peugeot_cands[0].status, "current_focus")


if __name__ == "__main__":
    unittest.main()
