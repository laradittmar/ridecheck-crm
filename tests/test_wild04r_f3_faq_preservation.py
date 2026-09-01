"""WILD-04R-F3 — Global same-burst FAQ preservation tests.

One bounded pre-outbound reconciliation layer (_compose_secondary_answers, fired
from _send_text_to_wa via _faq_reconciliation_burst) appends unanswered FAQ signals
from the same DB-authoritative burst to any primary reply produced by a commercial-
progression handler (acceptance, scheduling, pricing).

Probe-based duplicate detection prevents double-answers when the primary reply
already contains canonical FAQ content.

Layer D guard (F3 companion): in QUOTED stage, Layer D (FAQ bypass) is suppressed
when the burst contains an acceptance word, so "Dale ¿Aceptan débito?" advances to
SCHEDULING + answers the FAQ instead of answering the FAQ only.

F3-01  Exact live failure: acceptance + horarios → scheduling + hours
F3-02  Acceptance alone → scheduling only (no spurious FAQ)
F3-03  Acceptance + payment FAQ → scheduling + payment
F3-04  Acceptance + report FAQ → scheduling + report
F3-05  Acceptance + presence FAQ → scheduling + presence
F3-06  Acceptance + three FAQs → scheduling + all three (no duplicates)
F3-R1  Regression: pricing + horarios → quote ($240k) + hours (F2 preserved, F3 mechanism)
F3-R2  Regression: WILD-04 qualifying+FAQ burst → AI covers FAQs, NO duplicate answers
F3-R3  Regression: SCHEDULING turn + horarios → scheduling continues, hours answered
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

# ── Canonical answer strings (mirrors conversation_engine.py constants) ───────
# L4.3 Phase B: hours are DERIVED from the scheduling authority — never a literal,
# so this suite cannot drift from ScheduleService's weekday table.
from app.services.conversation_engine import _faq_hours_answer as _canonical_hours
_HOURS_ANSWER    = _canonical_hours()
_REPORT_ANSWER   = "Al finalizar la revisión te enviamos un informe detallado."
_PRESENCE_ANSWER = "No es necesario que estés presente durante la inspección."
_PAYMENT_ANSWER  = "Aceptamos efectivo, transferencia bancaria y Mercado Pago."

_NOW = datetime.now(timezone.utc)

# ── Standard AI mocks ─────────────────────────────────────────────────────────

def _ai_acceptance_reply():
    """AI detects acceptance, advances to SCHEDULING, returns scheduling question."""
    return json.dumps({
        "intent": "SCHEDULING",
        "reply": "¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?",
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": "ACEPTADO",
        "needs_human": False,
    })


def _ai_qualifying_reply():
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": "¿En qué zona está el auto?",
        "deferred_interest": False,
        "candidate": {"action": "none"},
        "extracted": {},
        "lead_flag": None,
        "needs_human": False,
    })


def _ai_scheduling_reply(reply="Anotado, ¿hay algún horario de preferencia?"):
    return json.dumps({
        "intent": "SCHEDULING",
        "reply": reply,
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
    eng._faq_reconciliation_burst = None
    return eng


def _seed_quoted_thread(
    db: Session, wa_id: str, *,
    tipo: str = "AUTO",
    with_sur_pricing: bool = False,
) -> tuple[WhatsAppThread, Lead, WhatsAppThreadState, WhatsAppThreadCandidate]:
    """Seed a thread already in QUOTED stage (flag=PRESUPUESTO_ENVIADO)."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="F3Test", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="PRESUPUESTO_ENVIADO", estado="CONSULTA_NUEVA", nombre="F3Test", necesita_humano=False)
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
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    if with_sur_pricing:
        db.add(ViaticosZone(zone_group="Sur", zone_detail="Berazategui", viaticos=90_000))
        db.add(ViaticosZone(zone_group="Sur", zone_detail=None, viaticos=90_000))
        db.flush()

    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", tipo_vehiculo=tipo,
        anio=2020, status="current_focus", source_text="test",
        zone_group="Sur" if with_sur_pricing else "CABA",
        zone_detail="Berazategui" if with_sur_pricing else "Palermo",
    )
    db.add(cand)
    db.flush()
    state.current_focus_candidate_id = cand.id
    db.commit()
    db.expire_all()
    return thread, lead, state, cand


def _seed_qualifying_thread(
    db: Session, wa_id: str, *,
    tipo: str = "SUV_4X4_DEPORTIVO",
    with_sur_pricing: bool = True,
) -> tuple[WhatsAppThread, Lead, WhatsAppThreadState]:
    """Seed a fresh QUALIFYING thread with a Berazategui candidate already set."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="F3Qualify", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag=None, estado="CONSULTA_NUEVA", nombre="F3Qualify", necesita_humano=False)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
    db.add(thread)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id, needs_human=False,
        last_stage="QUALIFYING", last_intent="PREPURCHASE_INSPECTION",
        cycle_reset_pending=False, current_cycle_started_at=None,
        vehicle_clarification_sent=False, location_clarification_sent=False,
        vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    if with_sur_pricing:
        db.add(ViaticosZone(zone_group="Sur", zone_detail="Berazategui", viaticos=90_000))
        db.add(ViaticosZone(zone_group="Sur", zone_detail=None, viaticos=90_000))
        db.flush()

    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", tipo_vehiculo=tipo,
        anio=2014, status="current_focus", source_text="test",
        zone_group="Sur", zone_detail="Berazategui",
    )
    db.add(cand)
    db.flush()
    state.current_focus_candidate_id = cand.id
    db.commit()
    db.expire_all()
    return thread, lead, state


def _seed_scheduling_thread(
    db: Session, wa_id: str,
) -> tuple[WhatsAppThread, Lead, WhatsAppThreadState]:
    """Seed a thread already in SCHEDULING stage (post-acceptance)."""
    _clean_all(db)

    contact = WhatsAppContact(wa_id=wa_id, display_name="F3Sched", phone=None)
    db.add(contact)
    db.flush()

    lead = Lead(flag="ACEPTADO", estado="CONSULTA_NUEVA", nombre="F3Sched", necesita_humano=False)
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
        created_at=_NOW, updated_at=_NOW,
    )
    db.add(state)
    db.flush()

    cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", tipo_vehiculo="AUTO",
        anio=2020, status="current_focus", source_text="test",
        zone_group="CABA", zone_detail="Palermo",
    )
    db.add(cand)
    db.flush()
    state.current_focus_candidate_id = cand.id
    db.commit()
    db.expire_all()
    return thread, lead, state


def _add_messages(db: Session, thread_id: int, base_id: str, texts: list[str]) -> None:
    for i, txt in enumerate(texts):
        db.add(WhatsAppMessage(
            thread_id=thread_id, wa_message_id=f"{base_id}-{i}",
            direction="in", timestamp=_NOW + timedelta(seconds=i),
            text=txt, status="received",
        ))
    db.commit()
    db.expire_all()


def _run_ce(
    db: Session,
    eng: ConversationEngine,
    thread_id: int,
    wa_id: str,
    base_msg_id: str,
    texts: list[str],
    ai_reply: Optional[str] = None,
) -> tuple[object, list[str]]:
    """Fire CE for a burst, return (result, sent_texts).

    texts is the full burst, passed as unanswered_recent_user_messages.
    The triggering message is texts[-1].
    """
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


# ═══════════════════════════════════════════════════════════════════════════════
# F3-01 — Exact live failure: acceptance + horarios signal in same burst
# ═══════════════════════════════════════════════════════════════════════════════
class TestF301AcceptancePlusHours(unittest.TestCase):
    """Burst 'Okay !' + '¿Qué horarios hacen?' while in QUOTED stage.

    'horarios' is not in Layer D's _FAQ_PATTERNS → Layer D does not intercept.
    The burst has mixed words so _is_acceptance returns False → AI path.
    AI detects acceptance, returns scheduling question.
    F3 appends the hours answer before outbound.
    """

    WA_ID = "5491153371001"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID
        )
        self.burst = ["Okay !", "¿Qué horarios hacen?"]
        _add_messages(self.db, self.thread.id, "f3-01", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-01", self.burst,
            ai_reply=_ai_acceptance_reply(),
        )

    def test_f3_01_replied(self):
        result, sent = self._run()
        self.assertEqual(result.action, "replied")
        self.assertEqual(len(sent), 1)

    def test_f3_01_contains_hours_answer(self):
        _, sent = self._run()
        self.assertIn(_HOURS_ANSWER, sent[0],
                      "Hours FAQ answer missing from acceptance+horarios reply")

    def test_f3_01_contains_scheduling_question(self):
        _, sent = self._run()
        reply_lower = sent[0].lower()
        self.assertTrue(
            "horario" in reply_lower or "día" in reply_lower or "dia" in reply_lower,
            f"Scheduling question missing from reply: {sent[0]!r}"
        )

    def test_f3_01_faq_rule_in_contributing_sources(self):
        result, _ = self._run()
        self.assertIn("FAQ_RULE", result.contributing_sources or [],
                      "FAQ_RULE not reported in contributing_sources")

    def test_f3_01_stage_advanced_to_scheduling(self):
        self._run()
        self.db.expire_all()
        state = self.db.get(WhatsAppThreadState, self.state.id)
        self.assertEqual(state.last_stage, "SCHEDULING")

    def test_f3_01_flag_advanced_to_aceptado(self):
        self._run()
        self.db.expire_all()
        lead = self.db.get(Lead, self.lead.id)
        self.assertEqual(lead.flag, "ACEPTADO")


# ═══════════════════════════════════════════════════════════════════════════════
# F3-02 — Acceptance alone: no FAQ supplement
# ═══════════════════════════════════════════════════════════════════════════════
class TestF302AcceptanceAlone(unittest.TestCase):
    """'Okay!' alone while in QUOTED stage → deterministic acceptance gate fires.

    _is_acceptance(['Okay!']) = True → _handle_quoted_acceptance called directly.
    F3 checks burst for FAQ signals → none found → no supplement.
    """

    WA_ID = "5491153371002"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID
        )
        self.burst = ["Okay!"]
        _add_messages(self.db, self.thread.id, "f3-02", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(self.db, self.eng, self.thread.id, self.WA_ID, "f3-02", self.burst)

    def test_f3_02_replied(self):
        result, sent = self._run()
        self.assertEqual(result.action, "replied")
        self.assertEqual(len(sent), 1)

    def test_f3_02_no_hours_answer(self):
        _, sent = self._run()
        self.assertNotIn("lunes a viernes", sent[0].lower(),
                         "Hours answer spuriously appended when no FAQ signal present")

    def test_f3_02_no_payment_answer(self):
        _, sent = self._run()
        self.assertNotIn("efectivo", sent[0].lower())

    def test_f3_02_no_report_answer(self):
        _, sent = self._run()
        self.assertNotIn("informe detallado", sent[0].lower())

    def test_f3_02_no_presence_answer(self):
        _, sent = self._run()
        # "presente" appears in the scheduling question too — check canonical presence answer
        self.assertNotIn(_PRESENCE_ANSWER, sent[0])

    def test_f3_02_stage_advanced_to_scheduling(self):
        self._run()
        self.db.expire_all()
        state = self.db.get(WhatsAppThreadState, self.state.id)
        self.assertEqual(state.last_stage, "SCHEDULING")


# ═══════════════════════════════════════════════════════════════════════════════
# F3-03 — Acceptance + payment FAQ
# ═══════════════════════════════════════════════════════════════════════════════
class TestF303AcceptancePlusPayment(unittest.TestCase):
    """'Dale' + '¿Aceptan débito?' while in QUOTED stage.

    Layer D guard: QUOTED + 'Dale' (acceptance word) → Layer D does not intercept.
    _is_acceptance(['Dale', '¿Aceptan débito?']) = False → AI path.
    AI detects acceptance, returns scheduling question.
    F3 detects payment signal → appends payment answer.
    """

    WA_ID = "5491153371003"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID
        )
        self.burst = ["Dale", "¿Aceptan débito?"]
        _add_messages(self.db, self.thread.id, "f3-03", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-03", self.burst,
            ai_reply=_ai_acceptance_reply(),
        )

    def test_f3_03_payment_answer_present(self):
        _, sent = self._run()
        self.assertIn(_PAYMENT_ANSWER, sent[0])

    def test_f3_03_no_hours_answer(self):
        _, sent = self._run()
        self.assertNotIn("lunes a viernes", sent[0].lower())

    def test_f3_03_scheduling_present(self):
        _, sent = self._run()
        reply_lower = sent[0].lower()
        self.assertTrue("horario" in reply_lower or "día" in reply_lower or "dia" in reply_lower)

    def test_f3_03_faq_rule_reported(self):
        result, _ = self._run()
        self.assertIn("FAQ_RULE", result.contributing_sources or [])

    def test_f3_03_stage_advanced_to_scheduling(self):
        self._run()
        self.db.expire_all()
        state = self.db.get(WhatsAppThreadState, self.state.id)
        self.assertEqual(state.last_stage, "SCHEDULING")


# ═══════════════════════════════════════════════════════════════════════════════
# F3-04 — Acceptance + report FAQ
# ═══════════════════════════════════════════════════════════════════════════════
class TestF304AcceptancePlusReport(unittest.TestCase):
    """'Dale' + '¿Mandan informe?' → acceptance + report answer + scheduling.

    'mandan informe' is in _REPORT_FAQ_DETECTION but NOT in _FAQ_PATTERNS,
    so Layer D does not intercept regardless of stage.
    """

    WA_ID = "5491153371004"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID
        )
        self.burst = ["Dale", "¿Mandan informe?"]
        _add_messages(self.db, self.thread.id, "f3-04", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-04", self.burst,
            ai_reply=_ai_acceptance_reply(),
        )

    def test_f3_04_report_answer_present(self):
        _, sent = self._run()
        self.assertIn(_REPORT_ANSWER, sent[0])

    def test_f3_04_no_payment_answer(self):
        _, sent = self._run()
        self.assertNotIn("efectivo", sent[0].lower())

    def test_f3_04_faq_rule_reported(self):
        result, _ = self._run()
        self.assertIn("FAQ_RULE", result.contributing_sources or [])

    def test_f3_04_stage_advanced_to_scheduling(self):
        self._run()
        self.db.expire_all()
        state = self.db.get(WhatsAppThreadState, self.state.id)
        self.assertEqual(state.last_stage, "SCHEDULING")


# ═══════════════════════════════════════════════════════════════════════════════
# F3-05 — Acceptance + presence FAQ
# ═══════════════════════════════════════════════════════════════════════════════
class TestF305AcceptancePlusPresence(unittest.TestCase):
    """'Sí, avancemos' + '¿Tengo que estar presente?' → acceptance + presence + scheduling.

    'tengo que estar presente' IS in _FAQ_PATTERNS (would normally trigger Layer D).
    Layer D guard: QUOTED + 'Sí' (acceptance word) → Layer D suppressed.
    AI detects acceptance; F3 appends presence answer.
    """

    WA_ID = "5491153371005"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID
        )
        self.burst = ["Sí, avancemos", "¿Tengo que estar presente?"]
        _add_messages(self.db, self.thread.id, "f3-05", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-05", self.burst,
            ai_reply=_ai_acceptance_reply(),
        )

    def test_f3_05_presence_answer_present(self):
        _, sent = self._run()
        self.assertIn(_PRESENCE_ANSWER, sent[0])

    def test_f3_05_no_hours_answer(self):
        _, sent = self._run()
        self.assertNotIn("lunes a viernes", sent[0].lower())

    def test_f3_05_faq_rule_reported(self):
        result, _ = self._run()
        self.assertIn("FAQ_RULE", result.contributing_sources or [])

    def test_f3_05_stage_advanced_to_scheduling(self):
        self._run()
        self.db.expire_all()
        state = self.db.get(WhatsAppThreadState, self.state.id)
        self.assertEqual(state.last_stage, "SCHEDULING")


# ═══════════════════════════════════════════════════════════════════════════════
# F3-06 — Acceptance + three FAQs (horarios + report + presence)
# ═══════════════════════════════════════════════════════════════════════════════
class TestF306AcceptancePlusThreeFAQs(unittest.TestCase):
    """'Dale' + horarios + report + presence → all three answered + scheduling.

    No duplicates. Single outbound message.
    """

    WA_ID = "5491153371006"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state, self.cand = _seed_quoted_thread(
            self.db, self.WA_ID
        )
        self.burst = [
            "Dale",
            "¿En qué horarios laburan?",
            "¿Mandan informe?",
            "¿Hay que estar presente?",
        ]
        _add_messages(self.db, self.thread.id, "f3-06", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-06", self.burst,
            ai_reply=_ai_acceptance_reply(),
        )

    def test_f3_06_hours_present(self):
        _, sent = self._run()
        self.assertIn(_HOURS_ANSWER, sent[0])

    def test_f3_06_report_present(self):
        _, sent = self._run()
        self.assertIn(_REPORT_ANSWER, sent[0])

    def test_f3_06_presence_present(self):
        _, sent = self._run()
        self.assertIn(_PRESENCE_ANSWER, sent[0])

    def test_f3_06_no_duplicate_hours(self):
        _, sent = self._run()
        self.assertEqual(sent[0].count(_HOURS_ANSWER), 1,
                         "Hours answer appears more than once")

    def test_f3_06_no_duplicate_report(self):
        _, sent = self._run()
        self.assertEqual(sent[0].count(_REPORT_ANSWER), 1,
                         "Report answer appears more than once")

    def test_f3_06_no_duplicate_presence(self):
        _, sent = self._run()
        self.assertEqual(sent[0].count(_PRESENCE_ANSWER), 1,
                         "Presence answer appears more than once")

    def test_f3_06_single_outbound(self):
        _, sent = self._run()
        self.assertEqual(len(sent), 1, "Expected exactly one outbound message")

    def test_f3_06_faq_rule_reported(self):
        result, _ = self._run()
        self.assertIn("FAQ_RULE", result.contributing_sources or [])

    def test_f3_06_stage_advanced_to_scheduling(self):
        self._run()
        self.db.expire_all()
        state = self.db.get(WhatsAppThreadState, self.state.id)
        self.assertEqual(state.last_stage, "SCHEDULING")


# ═══════════════════════════════════════════════════════════════════════════════
# F3-R1 — Regression: F2 pricing+horarios path preserved via F3 mechanism
# ═══════════════════════════════════════════════════════════════════════════════
class TestF3R1PricingPlusHours(unittest.TestCase):
    """Berazategui SUV + horarios in same burst → $240k quote + hours answer.

    Verifies that removing the manual _build_faq_supplement() call sites does NOT
    regress the F2 behavior. The F3 _compose_secondary_answers mechanism (via
    _send_text_to_wa) now handles this path uniformly.
    """

    WA_ID = "5491153371011"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db, with_sur_pricing=True)
        self.thread, self.lead, self.state = _seed_qualifying_thread(
            self.db, self.WA_ID, with_sur_pricing=True
        )
        self.burst = ["El auto está en Berazategui.", "¿En qué horarios laburan?"]
        _add_messages(self.db, self.thread.id, "f3-r1", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-r1", self.burst,
            ai_reply=_ai_qualifying_reply(),
        )

    def test_r1_replied(self):
        result, sent = self._run()
        self.assertEqual(result.action, "replied")
        self.assertEqual(len(sent), 1)

    def test_r1_contains_price_240k(self):
        _, sent = self._run()
        self.assertIn("240", sent[0], "Expected $240k quote in reply")

    def test_r1_contains_hours_answer(self):
        _, sent = self._run()
        self.assertIn(_HOURS_ANSWER, sent[0], "Hours answer missing from pricing+horarios reply")

    def test_r1_hours_not_duplicated(self):
        _, sent = self._run()
        self.assertEqual(sent[0].count(_HOURS_ANSWER), 1,
                         "Hours answer duplicated in pricing+horarios reply")

    def test_r1_answer_source_pricing(self):
        result, _ = self._run()
        self.assertEqual(result.answer_source, "PRICING_SERVICE")

    def test_r1_faq_rule_in_contributing(self):
        result, _ = self._run()
        self.assertIn("FAQ_RULE", result.contributing_sources or [])


# ═══════════════════════════════════════════════════════════════════════════════
# F3-R2 — Regression: WILD-04 qualifying+FAQ burst — AI covers FAQs, no duplicate
# ═══════════════════════════════════════════════════════════════════════════════
class TestF3R2WildFourQualifyingFAQ(unittest.TestCase):
    """Original WILD-04 failure: AI answers report+presence FAQs in reply.

    F3 must NOT append duplicate answers when the AI already covered them.
    Probe detection: 'informe' in AI reply → report not re-appended.
    'presente' in AI reply → presence not re-appended.
    """

    WA_ID = "5491153371012"

    # AI explicitly answers report+presence FAQs
    _AI_REPLY = json.dumps({
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

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db, with_sur_pricing=False)

        _clean_all(self.db)
        contact = WhatsAppContact(wa_id=self.WA_ID, display_name="F3R2", phone=None)
        self.db.add(contact)
        self.db.flush()
        lead = Lead(flag=None, estado="CONSULTA_NUEVA", nombre="F3R2", necesita_humano=False)
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
        self.db.add(thread)
        self.db.flush()
        state = WhatsAppThreadState(
            thread_id=thread.id, needs_human=False,
            last_stage=None, last_intent=None,
            cycle_reset_pending=False, current_cycle_started_at=None,
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False,
            created_at=_NOW, updated_at=_NOW,
        )
        self.db.add(state)
        self.db.flush()
        self.db.commit()
        self.db.expire_all()

        self.thread_id = thread.id
        # Multi-FAQ burst without acceptance signal — routes through AI (Layer D won't
        # fire because this burst has a qualifying/inspection intent alongside the FAQ)
        self.burst = [
            "Hola, ¿cómo andan? Quería revisar un 2008 del 2014. Ustedes hacen eso, ¿no?",
            "¿Mandan informes también? ¿Tengo que estar presente?",
        ]
        _add_messages(self.db, thread.id, "f3-r2", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread_id, self.WA_ID, "f3-r2", self.burst,
            ai_reply=self._AI_REPLY,
        )

    def test_r2_replied(self):
        result, sent = self._run()
        self.assertEqual(result.action, "replied")

    def test_r2_report_not_duplicated(self):
        """AI mentions 'informe' → probe matches → F3 must NOT re-append report answer."""
        _, sent = self._run()
        count = sent[0].count(_REPORT_ANSWER)
        self.assertLessEqual(count, 1, f"Report answer duplicated (found {count}x): {sent[0]!r}")

    def test_r2_presence_not_duplicated(self):
        """AI mentions 'presente' → probe matches → F3 must NOT re-append presence answer."""
        _, sent = self._run()
        count = sent[0].count(_PRESENCE_ANSWER)
        self.assertLessEqual(count, 1, f"Presence answer duplicated (found {count}x): {sent[0]!r}")

    def test_r2_single_outbound(self):
        _, sent = self._run()
        self.assertEqual(len(sent), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# F3-R3 — Regression: SCHEDULING turn + horarios FAQ → scheduling + hours
# ═══════════════════════════════════════════════════════════════════════════════
class TestF3R3SchedulingPlusHours(unittest.TestCase):
    """Thread in SCHEDULING stage, burst = ['El martes a las 14', '¿Qué horarios tienen?'].

    _is_pure_scheduling_rafaga returns False (FAQ signal > 2 words, non-schedulable).
    Falls through to AI path. F3 armed → appends hours answer to AI reply.
    """

    WA_ID = "5491153371013"

    def setUp(self):
        self.db = _new_session()
        self.eng = _make_engine(self.db)
        self.thread, self.lead, self.state = _seed_scheduling_thread(self.db, self.WA_ID)
        self.burst = ["El martes a las 14", "¿Qué horarios tienen?"]
        _add_messages(self.db, self.thread.id, "f3-r3", self.burst)

    def tearDown(self):
        self.db.close()

    def _run(self):
        return _run_ce(
            self.db, self.eng, self.thread.id, self.WA_ID, "f3-r3", self.burst,
            ai_reply=_ai_scheduling_reply("Anotado. ¿Hay algún horario de preferencia para el martes?"),
        )

    def test_r3_replied(self):
        result, sent = self._run()
        self.assertEqual(result.action, "replied")

    def test_r3_hours_answer_present(self):
        _, sent = self._run()
        self.assertIn(_HOURS_ANSWER, sent[0],
                      "Hours answer missing from scheduling+horarios reply")

    def test_r3_hours_not_duplicated(self):
        _, sent = self._run()
        self.assertEqual(sent[0].count(_HOURS_ANSWER), 1)

    def test_r3_single_outbound(self):
        _, sent = self._run()
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
