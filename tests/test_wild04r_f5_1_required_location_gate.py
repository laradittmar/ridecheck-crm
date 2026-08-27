"""WILD-04R-F5.1 — Deterministic required-next-info gate tests.

Verifies that when vehicle is known and location is unknown, CE deterministically
appends the canonical location question to the final reply — regardless of whether
the AI (LLM) remembered to ask.

Key tests:
  A. AI already asks for location → gate is no-op, no duplicate question.
  B. AI intentionally omits location → gate appends canonical question.
  C-H. Negative gate conditions (zone known, SCHEDULING, QUOTED, no candidate,
       needs_human, tipo unknown).

Also verifies _reply_already_asks_location probe behaviour.

All tests use SQLite in-memory.  No containers, no Meta API, no live AI calls.
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
    _CANONICAL_LOCATION_ASK,
    _reply_already_asks_location,
    STAGE_QUALIFYING,
    STAGE_SCHEDULING,
    STAGE_QUOTED,
)
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

_WA_ID = "5491153369001"
_WA_MSG_PREFIX = "wamid.F51TEST"
_msg_counter = 0


def _next_wamid() -> str:
    global _msg_counter
    _msg_counter += 1
    return f"{_WA_MSG_PREFIX}{_msg_counter:04d}"


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
    eng._burst_message_count = 1
    eng._burst_earliest_inbound_db_id = None
    return eng


def _seed_live_burst_thread(db: Session) -> tuple[int, int]:
    """
    Reproduce the exact F4 live failure scenario:
    - Prior cycle: Peugeot 2008, San Miguel (status=current_focus)
    - cycle_reset_pending=True
    - Thread has 3-message burst (vehicle intro + 2 FAQ)
    Returns (thread_id, lead_id).
    """
    contact = WhatsAppContact(wa_id=_WA_ID, display_name="F5.1 User")
    db.add(contact)
    db.flush()

    lead = Lead(nombre="Test User", telefono=_WA_ID, flag="PRESUPUESTANDO",
                estado="CONSULTA_NUEVA")
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread)
    db.flush()

    # Explicitly set created_at in the past so it sits BEFORE the new-cycle
    # watermark (burst_msg.created_at = 2026-08-26T23:25:19Z).  Without this,
    # datetime.utcnow() (wall clock ≈ today) would place the candidate AFTER the
    # watermark, causing _reload_active_candidates to include it in the new cycle.
    prior_cand_ts = datetime(2026, 8, 25, 10, 0, 0, tzinfo=timezone.utc)
    prior_cand = WhatsAppThreadCandidate(
        thread_id=thread.id,
        marca="Peugeot", modelo="2008", anio=2014,
        tipo_vehiculo="SUV_4X4_DEPORTIVO",
        zone_group="Oeste",
        zone_detail="San Miguel",
        status="current_focus",
        created_at=prior_cand_ts,
    )
    db.add(prior_cand)
    db.flush()

    old_cycle_start = datetime(2026, 8, 26, 0, 51, 0, tzinfo=timezone.utc)
    state = WhatsAppThreadState(
        thread_id=thread.id,
        current_focus_candidate_id=prior_cand.id,
        home_zone_group="Oeste",
        home_zone_detail="San Miguel",
        last_stage=STAGE_QUALIFYING,
        cycle_reset_pending=True,
        current_cycle_start_message_db_id=None,
        current_cycle_started_at=old_cycle_start,
    )
    db.add(state)
    db.flush()

    msgs_text = [
        "Hola, ¿cómo va? Quiero hacer una revisión de un 2008 del 2014. ¿Ustedes hacen eso, no?",
        "¿Mandan informes? Tengo que estar presente.",
        "¿Aceptan débito? ¿Cómo se paga?",
    ]
    for i, txt in enumerate(msgs_text):
        ts = datetime(2026, 8, 26, 23, 25, 19 + i, tzinfo=timezone.utc)
        db.add(WhatsAppMessage(
            thread_id=thread.id,
            direction="in",
            text=txt,
            wa_message_id=_next_wamid(),
            timestamp=ts,
            status="received",
            created_at=ts,
        ))
    db.commit()
    return thread.id, lead.id


def _make_event(db: Session, thread_id: int) -> ConversationHandleIn:
    """Build a ConversationHandleIn for the last burst message."""
    msgs = db.execute(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.thread_id == thread_id, WhatsAppMessage.direction == "in")
        .order_by(WhatsAppMessage.id)
    ).scalars().all()
    last = msgs[-1]
    all_texts = [m.text for m in msgs if m.text]
    return ConversationHandleIn(
        thread_id=thread_id,
        wa_id=_WA_ID,
        wa_message_id=last.wa_message_id,
        message_type="text",
        text=last.text,
        unanswered_recent_user_messages=all_texts,
        recent_user_messages=all_texts,
    )


def _ai_json_with_location(vehicle: str = "Peugeot 2008 2014") -> str:
    """Simulated AI reply that ALREADY asks for location."""
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": (
            f"¡Hola! Sí, hacemos revisiones de vehículos como el {vehicle}. "
            "Al terminar recibís un informe detallado y no es necesario estar presente. "
            "Aceptamos transferencia, Mercado Pago y efectivo, no débito. "
            "¿En qué zona o barrio está el auto?"
        ),
        "lead_flag": None,
        "needs_human": False,
        "extracted": {},
        "candidate": {
            "action": "create",
            "marca": "Peugeot", "modelo": "2008", "anio": 2014,
            "tipo_vehiculo": "SUV_4X4_DEPORTIVO",
            "status": "current_focus",
        },
    })


def _ai_json_without_location(vehicle: str = "Peugeot 2008 2014") -> str:
    """Simulated AI reply that INTENTIONALLY omits the location question."""
    return json.dumps({
        "intent": "QUALIFYING",
        "reply": (
            f"¡Hola! Sí, hacemos revisiones de vehículos como el {vehicle}. "
            "Al terminar recibís un informe detallado y no es necesario estar presente. "
            "Aceptamos transferencia, Mercado Pago y efectivo, no débito ni tarjeta."
        ),
        "lead_flag": None,
        "needs_human": False,
        "extracted": {},
        "candidate": {
            "action": "create",
            "marca": "Peugeot", "modelo": "2008", "anio": 2014,
            "tipo_vehiculo": "SUV_4X4_DEPORTIVO",
            "status": "current_focus",
        },
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _reply_already_asks_location
# ═══════════════════════════════════════════════════════════════════════════════

class TestReplyAlreadyAsksLocation(unittest.TestCase):
    """Unit tests for the location-question probe function."""

    def test_detects_que_zona(self):
        self.assertTrue(_reply_already_asks_location("¿En qué zona está el auto?"))

    def test_detects_que_barrio(self):
        self.assertTrue(_reply_already_asks_location("¿En qué barrio se encuentra?"))

    def test_detects_que_localidad(self):
        self.assertTrue(_reply_already_asks_location("Decime en qué localidad está el vehículo."))

    def test_detects_que_ciudad(self):
        self.assertTrue(_reply_already_asks_location("¿En qué ciudad queda?"))

    def test_detects_donde_esta(self):
        self.assertTrue(_reply_already_asks_location("¿Dónde está el auto?"))

    def test_detects_localidad_o_barrio(self):
        self.assertTrue(_reply_already_asks_location(
            "Respuesta.\n\n" + _CANONICAL_LOCATION_ASK
        ))

    def test_faq_without_location_not_detected(self):
        reply = (
            "¡Hola! Sí, hacemos revisiones. "
            "No es necesario estar presente. "
            "Aceptamos transferencia y Mercado Pago."
        )
        self.assertFalse(_reply_already_asks_location(reply))

    def test_empty_reply_not_detected(self):
        self.assertFalse(_reply_already_asks_location(""))

    def test_zona_in_non_question_context_not_detected(self):
        reply = "El servicio está disponible en toda la zona metropolitana."
        self.assertFalse(_reply_already_asks_location(reply))

    def test_canonical_location_ask_detected(self):
        self.assertTrue(_reply_already_asks_location(_CANONICAL_LOCATION_ASK))


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _apply_required_next_question (unit, via ctx only)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyRequiredNextQuestion(unittest.TestCase):
    """Direct unit tests for the gate method — no full CE turn required."""

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def _make_ctx_qualifying_no_zone(self) -> _Context:
        """QUALIFYING stage, vehicle known, zone NULL."""
        contact = WhatsAppContact(wa_id=_WA_ID, display_name="F5.1 User")
        self.db.add(contact)
        self.db.flush()
        lead = Lead(nombre="Test", telefono=_WA_ID, flag="PRESUPUESTANDO")
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
        self.db.add(thread)
        self.db.flush()
        cand = WhatsAppThreadCandidate(
            thread_id=thread.id,
            marca="Peugeot", modelo="2008", anio=2014,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group=None, zone_detail=None,
            status="current_focus",
        )
        self.db.add(cand)
        self.db.flush()
        state = WhatsAppThreadState(
            thread_id=thread.id,
            last_stage=STAGE_QUALIFYING,
            current_focus_candidate_id=cand.id,
            home_zone_group=None, home_zone_detail=None,
        )
        self.db.add(state)
        self.db.commit()
        return _Context(
            thread=thread, contact=contact, lead=lead,
            state=state, candidates=[cand], db_messages=[],
        )

    # ── Gate fires ────────────────────────────────────────────────────────────

    def test_gate_appends_when_location_missing_and_not_in_reply(self):
        ctx = self._make_ctx_qualifying_no_zone()
        reply = "¡Hola! Sí, hacemos revisiones. No es necesario estar presente."
        result = self.eng._apply_required_next_question(reply, ctx)
        self.assertIn("localidad o barrio", result.lower())

    def test_gate_appends_canonical_phrase(self):
        ctx = self._make_ctx_qualifying_no_zone()
        result = self.eng._apply_required_next_question("Sí, hacemos revisiones.", ctx)
        self.assertIn(_CANONICAL_LOCATION_ASK.strip(), result)

    # ── Gate does not fire ────────────────────────────────────────────────────

    def test_no_duplicate_when_reply_already_has_location_question(self):
        """Case A: AI asked for location — gate must be a no-op."""
        ctx = self._make_ctx_qualifying_no_zone()
        reply = "¡Hola! ¿En qué zona o barrio está el auto?"
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_candidate_zone_known(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.candidates[0].zone_group = "CABA"
        ctx.candidates[0].zone_detail = "Palermo"
        reply = "Revisión en el lugar del auto."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_state_zone_known(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.state.home_zone_group = "CABA"
        ctx.state.home_zone_detail = "Palermo"
        reply = "Revisión en el lugar del auto."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_needs_human(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.state.needs_human = True
        reply = "Un asesor se va a comunicar con vos."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_stage_quoted(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.state.last_stage = STAGE_QUOTED
        reply = "El precio es $150.000."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_stage_scheduling(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.state.last_stage = STAGE_SCHEDULING
        reply = "¿Qué día te viene bien?"
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_no_candidates(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.candidates = []
        ctx.state.current_focus_candidate_id = None
        reply = "Revisión en el lugar del auto."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_tipo_unknown(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.candidates[0].tipo_vehiculo = None
        reply = "¿Qué tipo de vehículo tenés?"
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_append_when_state_is_none(self):
        ctx = self._make_ctx_qualifying_no_zone()
        ctx.state = None
        reply = "Revisión en el lugar del auto."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Exact live burst — end-to-end through handle() (mocked AI + outbound)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExactLiveBurst(unittest.TestCase):
    """
    Reproduce the exact F4 live failure scenario end-to-end.

    Prior cycle: Peugeot 2008, San Miguel (status=current_focus, zone known)
    cycle_reset_pending=True

    Burst (3 messages):
      "Hola, quiero hacer revisión de un 2008 del 2014."
      "¿Mandan informes? Tengo que estar presente."
      "¿Aceptan débito? ¿Cómo se paga?"

    Case A: AI naturally asks for location → final reply has one location question,
            gate is a no-op (no duplicate).
    Case B: AI intentionally omits location → gate appends canonical question,
            proving correctness does not depend on Rule 20 / LLM compliance.
    """

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.thread_id, self.lead_id = _seed_live_burst_thread(self.db)
        self.event = _make_event(self.db, self.thread_id)

    def tearDown(self):
        self.db.close()

    def _run_capture_text(self, ai_json: str) -> str:
        """
        Run CE handle() with mocked AI + intercepted _send_text_to_wa.
        Replays gate logic inside the intercept so captured text is post-gate.
        Returns the final text that would have been sent to WA.
        """
        eng = _make_engine(self.db)
        captured: list[str] = []

        def _intercepted_send(self_eng, ctx, text):
            _burst = getattr(self_eng, "_faq_reconciliation_burst", None)
            if _burst:
                self_eng._faq_reconciliation_burst = None
                text = self_eng._compose_secondary_answers(text, _burst)
            # Mirror CE's _send_text_to_wa: pass _burst as _turn_text so the
            # soft-close guard can inspect the original customer turn text.
            text = self_eng._apply_required_next_question(text, ctx, _turn_text=_burst)
            captured.append(text)
            return "wamid.FAKE001"

        with patch.object(eng, "_call_openai", return_value=ai_json), \
             patch.object(ConversationEngine, "_send_text_to_wa", _intercepted_send):
            eng.handle(self.event)

        return captured[-1] if captured else ""

    # ── Case A ────────────────────────────────────────────────────────────────

    def test_case_a_ai_asks_location_no_duplicate(self):
        """AI reply already contains zone question → gate is no-op, no extra question."""
        text = self._run_capture_text(_ai_json_with_location())

        self.assertGreater(len(text), 0, "Expected non-empty reply")
        self.assertTrue(
            _reply_already_asks_location(text),
            f"Expected location question in reply:\n{text}"
        )
        # Rough duplicate check: canonical append must NOT be present separately
        low = text.lower()
        # The canonical phrase appears only from AI, not doubled
        self.assertLessEqual(low.count("localidad o barrio"), 1,
            f"Duplicate 'localidad o barrio' detected:\n{text}")

    # ── Case B ────────────────────────────────────────────────────────────────

    def test_case_b_ai_omits_location_gate_appends(self):
        """
        AI intentionally omits location question.
        Gate MUST append canonical question, proving LLM compliance is not required.
        This is the essential B test per WILD-04R-F5.1 spec.
        """
        text = self._run_capture_text(_ai_json_without_location())

        self.assertGreater(len(text), 0, "Expected non-empty reply")
        self.assertTrue(
            _reply_already_asks_location(text),
            f"Gate did not append location question. Reply:\n{text}"
        )
        self.assertIn("localidad o barrio", text.lower(),
            f"Canonical phrase not in reply:\n{text}")

    def test_case_b_canonical_phrase_appended(self):
        """Canonical _CANONICAL_LOCATION_ASK text must appear verbatim in the reply."""
        text = self._run_capture_text(_ai_json_without_location())
        self.assertIn(_CANONICAL_LOCATION_ASK.strip(), text,
            f"Canonical location question not found in reply:\n{text}")

    # ── Historical zone must not leak ─────────────────────────────────────────

    def test_san_miguel_not_in_new_cycle_reply(self):
        """Prior-cycle zone (San Miguel) must NOT appear in the new-cycle reply."""
        text = self._run_capture_text(_ai_json_without_location())
        self.assertNotIn("San Miguel", text,
            f"Historical zone leaked into new-cycle reply:\n{text}")

    # ── Prior candidate archived on reset ─────────────────────────────────────

    def test_prior_cycle_candidate_archived_on_reset(self):
        """After cycle reset, all prior current_focus candidates must be archived."""
        eng = _make_engine(self.db)

        with patch.object(eng, "_call_openai", return_value=_ai_json_without_location()), \
             patch.object(ConversationEngine, "_send_text_to_wa",
                          lambda s, c, t: "wamid.FAKE001"):
            eng.handle(self.event)

        prior_cands = self.db.execute(
            select(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.thread_id == self.thread_id,
                WhatsAppThreadCandidate.zone_group == "Oeste",
                WhatsAppThreadCandidate.zone_detail == "San Miguel",
            )
        ).scalars().all()

        self.assertGreater(len(prior_cands), 0,
            "Prior-cycle San Miguel candidate not found at all")
        for c in prior_cands:
            self.assertEqual(c.status, "archived",
                f"Prior-cycle candidate not archived (id={c.id}, status={c.status})")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Next turn — zone provided → gate stays silent
# ═══════════════════════════════════════════════════════════════════════════════

class TestNextTurnZoneProvided(unittest.TestCase):
    """After zone is known, gate must not append a location question."""

    def setUp(self):
        self.db = _new_session()
        _clean_all(self.db)
        _seed_viaticos(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        self.db.close()

    def _make_ctx_with_zone(self, grp: str, det: str) -> _Context:
        contact = WhatsAppContact(wa_id=_WA_ID, display_name="F5.1 User")
        self.db.add(contact)
        self.db.flush()
        lead = Lead(nombre="T", telefono=_WA_ID, flag="PRESUPUESTANDO")
        self.db.add(lead)
        self.db.flush()
        thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
        self.db.add(thread)
        self.db.flush()
        cand = WhatsAppThreadCandidate(
            thread_id=thread.id,
            marca="Peugeot", modelo="2008", anio=2014,
            tipo_vehiculo="SUV_4X4_DEPORTIVO",
            zone_group=grp, zone_detail=det,
            status="current_focus",
        )
        self.db.add(cand)
        self.db.flush()
        state = WhatsAppThreadState(
            thread_id=thread.id,
            last_stage=STAGE_QUALIFYING,
            current_focus_candidate_id=cand.id,
            home_zone_group=grp, home_zone_detail=det,
        )
        self.db.add(state)
        self.db.commit()
        return _Context(
            thread=thread, contact=contact, lead=lead,
            state=state, candidates=[cand], db_messages=[],
        )

    def test_no_location_question_when_zone_known_caba(self):
        ctx = self._make_ctx_with_zone("CABA", "Palermo")
        reply = "Perfecto, Palermo. Te paso el precio ahora."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)

    def test_no_location_question_when_zone_known_gba(self):
        ctx = self._make_ctx_with_zone("Oeste", "San Miguel")
        reply = "Perfecto, San Miguel. Te paso el precio."
        self.assertEqual(self.eng._apply_required_next_question(reply, ctx), reply)


if __name__ == "__main__":
    unittest.main()
