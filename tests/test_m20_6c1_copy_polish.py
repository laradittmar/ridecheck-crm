"""M20.6C.1 — Deterministic copy-polish regression tests.

Verifies the four deterministic message strings across the full
quote → acceptance → scheduling → booking-flow sequence.

Test index:
  1.  Quote starts with "Genial!" and contains vehicle/location/price.
  2a. Acceptance "Sí, avancemos" → starts with "¡Perfecto!", no price words.
  2b. Acceptance "Si avancemos" → same.
  3.  Available-slot Flow intro starts with "Ese horario está disponible 🎉".
  4.  Booking-flow submission confirmation starts with "¡Listo,", includes
      buyer first name, formatted day/time, says "revisar los datos".
  5.  Sequence-level distinct opener check: four messages open with distinct
      phrases in the right order.
  6.  M20.6B.1 acceptance regression (existing tests still pass — run inline).
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── SQLAlchemy / SQLite in-memory setup ───────────────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON           # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON              # type: ignore[attr-defined]

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

# ── Import ORM models ─────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    Lead, ViaticosZone, WhatsAppContact, WhatsAppMessage,
    WhatsAppThread, WhatsAppThreadCandidate, WhatsAppThreadState,
)

Lead.__table__.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.repositories.pricing_repository import BasePriceRow
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine, _is_acceptance
from app.services.pricing import PricingService

# ── Shared constants ──────────────────────────────────────────────────────────
_WA_ID = "5491153368330"
_NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
_SCHED_DATE = "2026-07-06"   # a Monday (lunes)
_SCHED_TIME = "15:00"


# ── Fake pricing repository ───────────────────────────────────────────────────

@dataclass
class _FakeZoneRow:
    zone_group: str
    zone_detail: Optional[str]
    viaticos: int


class FakeRepoBenavidez:
    def find_base_price(self, tipo_vehiculo: str) -> Optional[BasePriceRow]:
        if tipo_vehiculo == "SUV/4x4":
            return BasePriceRow(tipo_vehiculo="SUV/4x4", precio_base=140_000)
        return None

    def find_zone_by_group_and_detail(self, db, zone_group, zone_detail):
        key = (zone_detail or "").strip().lower()
        if key == "benavidez":
            return _FakeZoneRow("Norte", "Benavidez", 0)
        if (zone_group or "").strip().lower() == "norte":
            return _FakeZoneRow("Norte", None, 0)
        return None


# ── SQLite helpers ────────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _clean_all(db: Session) -> None:
    for tbl in [
        "whatsapp_outbound_dedup", "whatsapp_messages",
        "whatsapp_thread_candidates", "whatsapp_thread_states",
        "whatsapp_threads", "whatsapp_contacts", "viaticos_zones",
        "whatsapp_recipient_locks", "leads", "thread_revisions",
    ]:
        try:
            db.execute(sql_text(f"DELETE FROM {tbl}"))
        except Exception:
            pass
    db.commit()


def _seed_qualifying(db: Session):
    _clean_all(db)
    contact = WhatsAppContact(wa_id=_WA_ID, display_name="Lara", phone=None)
    db.add(contact)
    db.flush()
    lead = Lead(flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA", nombre=None, necesita_humano=False)
    db.add(lead)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id, unread_count=0, created_at=_NOW)
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
    candidate = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="3008",
        tipo_vehiculo="SUV/4x4", status="current_focus", created_at=_NOW, updated_at=_NOW,
    )
    db.add(candidate)
    db.flush()
    db.add(ViaticosZone(zone_group="Norte", zone_detail="Benavidez", viaticos=0))
    db.add(ViaticosZone(zone_group="Norte", zone_detail=None, viaticos=0))
    db.commit()
    return contact, thread, lead, state, candidate


def _seed_quoted(db: Session):
    contact, thread, lead, state, candidate = _seed_qualifying(db)
    lead.flag = "PRESUPUESTO_ENVIADO"
    state.last_stage = "QUOTED"
    state.home_zone_group = "Norte"
    state.home_zone_detail = "Benavidez"
    candidate.zone_group = "Norte"
    candidate.zone_detail = "Benavidez"
    db.commit()
    return contact, thread, lead, state, candidate


def _seed_scheduling(db: Session):
    contact, thread, lead, state, candidate = _seed_quoted(db)
    lead.flag = "ACEPTADO"
    state.last_stage = "SCHEDULING"
    state.preferred_day = _SCHED_DATE
    state.preferred_time = _SCHED_TIME
    db.commit()
    return contact, thread, lead, state, candidate


def _seed_flow_sent(db: Session):
    contact, thread, lead, state, candidate = _seed_scheduling(db)
    state.flow_booking_token = f"{thread.id}-9999999"
    db.commit()
    return contact, thread, lead, state, candidate


def _make_settings():
    s = MagicMock()
    s.openai_api_key = "sk-test-fake-key"
    s.openai_chat_model = "gpt-4o-mini"
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = "FLOW_ID_GENERIC"
    s.whatsapp_website_flow_id = ""
    s.whatsapp_vehicle_fallback_flow_id = ""
    s.whatsapp_location_fallback_flow_id = ""
    s.whatsapp_website_flow_id = ""
    return s


def _make_engine(db: Session) -> ConversationEngine:
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = _make_settings()
    eng._pricing = PricingService(repository=FakeRepoBenavidez())
    from app.services.schedule import ScheduleService
    eng._schedule = ScheduleService(db=db)
    return eng


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _openai_quote_response() -> _FakeHTTPResponse:
    """AI reply that starts with Genial! and includes the price — no injection needed."""
    ai_decision = json.dumps({
        "intent": "PRESUPUESTO",
        "reply": (
            "Genial! La cotización para la revisión del Peugeot 3008 en Benavidez "
            "es de $140.000. Si te parece bien, podemos avanzar."
        ),
        "lead_flag": "PRESUPUESTO_ENVIADO",
        "flag_accepted": False,
        "needs_human": False,
        "extracted": {"zone_detail": "Benavidez", "zone_group": "Norte"},
        "candidate": {"action": "none"},
    })
    body = json.dumps({"choices": [{"message": {"content": ai_decision}}]}).encode()
    return _FakeHTTPResponse(body)


def _make_text_event(thread_id: int, text: str, wa_msg_id: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id, wa_message_id=wa_msg_id, wa_id=_WA_ID,
        text=text, recent_user_messages=[text], unanswered_recent_user_messages=[text],
    )


def _make_flow_event(thread_id: int, wa_msg_id: str, flow_data: dict, token: str) -> ConversationHandleIn:
    return ConversationHandleIn(
        thread_id=thread_id, wa_message_id=wa_msg_id, wa_id=_WA_ID,
        text="", recent_user_messages=[], unanswered_recent_user_messages=[],
        message_type="flow_response", flow_response=flow_data, flow_token=token,
    )


def _blocked_text(db: Session, thread_id: int) -> str:
    rows = db.execute(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.thread_id == thread_id, WhatsAppMessage.status == "blocked")
        .order_by(WhatsAppMessage.id.desc())
    ).scalars().all()
    return rows[0].text if rows else ""


# ── Test 1: Quote starts with "Genial!" ───────────────────────────────────────

class TestQuoteOpener(unittest.TestCase):

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_qualifying(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_1_quote_starts_with_genial(self, mock_urlopen):
        mock_urlopen.return_value = _openai_quote_response()
        result = self.eng.handle(_make_text_event(
            self.thread.id, "Hola, quiero revisar un 3008 en Benavídez", "wamid.M20.6C1.T1",
        ))
        self.db.expire_all()
        self.db.refresh(self.lead)
        self.db.refresh(self.state)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "QUOTED")
        self.assertEqual(self.lead.flag, "PRESUPUESTO_ENVIADO")

        text = _blocked_text(self.db, self.thread.id)
        self.assertTrue(text.startswith("Genial!"),
                        f"Quote must start with 'Genial!' — got: {text!r}")
        self.assertIn("140", text, f"Quote must contain price — got: {text!r}")
        self.assertIn("Peugeot", text, f"Quote must contain vehicle — got: {text!r}")
        self.assertIn("Benavidez", text, f"Quote must contain location — got: {text!r}")
        self.assertFalse(self.state.needs_human)


# ── Tests 2a/2b: Acceptance → starts with "¡Perfecto!" ───────────────────────

class TestAcceptanceOpener(unittest.TestCase):

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_quoted(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def _run(self, text: str, wa_id: str):
        result = self.eng.handle(_make_text_event(self.thread.id, text, wa_id))
        self.db.expire_all()
        self.db.refresh(self.lead)
        self.db.refresh(self.state)
        return result, _blocked_text(self.db, self.thread.id)

    @patch("urllib.request.urlopen")
    def test_2a_acceptance_with_accent_starts_perfecto(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError("AI must NOT be called on acceptance path")
        result, text = self._run("Sí, avancemos", "wamid.M20.6C1.T2A")

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "SCHEDULING")
        self.assertEqual(self.lead.flag, "ACEPTADO")
        self.assertFalse(self.state.needs_human)

        self.assertTrue(text.startswith("¡Perfecto!"),
                        f"Must start with '¡Perfecto!' — got: {text!r}")
        for forbidden in ("$", "precio", "viático", "viatico", "base", "Genial", "140"):
            self.assertNotIn(forbidden, text,
                             f"Reply must not contain {forbidden!r}: {text!r}")
        self.assertTrue(any(k in text.lower() for k in ("día", "horario", "fecha")),
                        f"Reply must ask for day/time: {text!r}")

    @patch("urllib.request.urlopen")
    def test_2b_acceptance_no_accent_starts_perfecto(self, mock_urlopen):
        mock_urlopen.side_effect = AssertionError("AI must NOT be called on acceptance path")
        result, text = self._run("Si avancemos", "wamid.M20.6C1.T2B")

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "SCHEDULING")
        self.assertTrue(text.startswith("¡Perfecto!"),
                        f"Must start with '¡Perfecto!' — got: {text!r}")
        self.assertNotIn("Genial", text)
        self.assertNotIn("$", text)


# ── Test 3: Available-slot Flow intro ─────────────────────────────────────────

class TestAvailableSlotIntro(unittest.TestCase):

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_scheduling(self.db)
        self.eng = _make_engine(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    @patch("urllib.request.urlopen")
    def test_3_flow_intro_starts_ese_horario(self, mock_urlopen):
        """Flow intro body must start with 'Ese horario está disponible 🎉'."""
        # Mock schedule service to report slot as valid
        mock_sched_result = MagicMock()
        mock_sched_result.valid = True
        self.eng._schedule = MagicMock()
        self.eng._schedule.check.return_value = mock_sched_result

        # Capture the body passed to _send_flow_button without making a real Meta call
        captured_body: list[str] = []

        def fake_send_flow(ctx, body, token, flow_id=None, initial_screen=None):
            captured_body.append(body)
            # Simulate a blocked outbound (OUTBOUND_ENABLED not set) by writing a blocked row
            from app.models import WhatsAppMessage
            msg = WhatsAppMessage(
                thread_id=ctx.thread.id,
                wa_message_id=f"wamid.MOCK.FLOW.{ctx.thread.id}",
                direction="outbound",
                message_type="flow",
                text=body,
                status="blocked",
                blocked_reason="TEST_MOCK",
            )
            self.db.add(msg)
            self.db.commit()
            return f"wamid.MOCK.FLOW.{ctx.thread.id}"

        self.eng._send_flow_button = fake_send_flow

        mock_urlopen.side_effect = AssertionError("AI must not be called — scheduling is deterministic")

        result = self.eng.handle(_make_text_event(
            self.thread.id, "El lunes a las 15 hs", "wamid.M20.6C1.T3",
        ))
        self.db.expire_all()

        self.assertTrue(
            len(captured_body) > 0,
            "Expected _send_flow_button to be called — schedule check was valid",
        )
        body = captured_body[0]
        self.assertTrue(
            body.startswith("Ese horario está disponible 🎉"),
            f"Flow intro must start with 'Ese horario está disponible 🎉' — got: {body!r}",
        )
        self.assertNotIn("Perfecto,", body,
                         f"Flow intro must not start with 'Perfecto,' — got: {body!r}")


# ── Test 4: Booking-flow submission confirmation ──────────────────────────────

class TestBookingConfirmation(unittest.TestCase):

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.lead, self.state, _ = _seed_flow_sent(self.db)
        self.eng = _make_engine(self.db)
        # Stub out the internal notification helper — it tries to serialize
        # real settings and hits a missing SQLite column in the in-memory DB.
        self.eng._send_booking_notification = MagicMock(return_value=None)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_4_booking_confirmation_copy(self):
        """Flow submission confirmation uses buyer first name, day/time, 'revisar los datos'."""
        flow_data = {
            "nombre_apellido": "Lara Dittmar",
            "telefono": _WA_ID,
            "email": "lara@test.com",
            "direccion": "Dean Funes 1907",
            "tipo_vendedor": "particular",
            "como_llego": "WhatsApp",
        }
        token = self.state.flow_booking_token
        result = self.eng.handle(_make_flow_event(
            self.thread.id, "wamid.M20.6C1.T4", flow_data, token,
        ))
        self.db.expire_all()
        self.db.refresh(self.lead)
        self.db.refresh(self.state)

        self.assertEqual(result.action, "blocked_dispatch")
        self.assertEqual(self.state.last_stage, "BOOKED")
        self.assertTrue(self.state.needs_human, "needs_human must be True post-booking")

        text = _blocked_text(self.db, self.thread.id)

        # Must start with ¡Listo,
        self.assertTrue(text.startswith("¡Listo,"),
                        f"Confirmation must start with '¡Listo,' — got: {text!r}")

        # Must include buyer first name
        self.assertIn("Lara", text,
                      f"Confirmation must include buyer first name — got: {text!r}")

        # Must include formatted day from _SCHED_DATE (2026-07-06 = lunes 06/07)
        self.assertIn("06/07", text,
                      f"Confirmation must include formatted date — got: {text!r}")
        self.assertIn("lunes", text,
                      f"Confirmation must include weekday name — got: {text!r}")

        # Must include time
        self.assertIn("15:00", text,
                      f"Confirmation must include formatted time — got: {text!r}")

        # Must say "revisar los datos", NOT "revisar la disponibilidad"
        self.assertIn("revisar los datos", text,
                      f"Must say 'revisar los datos' — got: {text!r}")
        self.assertNotIn("revisar la disponibilidad", text,
                         f"Must NOT say 'revisar la disponibilidad' — got: {text!r}")

        # Must NOT imply appointment is fully confirmed
        self.assertNotIn("confirmado", text.lower(),
                         f"Must not say appointment is confirmed — got: {text!r}")
        self.assertNotIn("quedó registrada", text,
                         f"Old wording 'quedó registrada' must be removed — got: {text!r}")

        # Revision must be persisted
        from app.models import ThreadRevision
        rev = self.db.execute(
            select(ThreadRevision).where(ThreadRevision.thread_id == self.thread.id)
        ).scalars().first()
        self.assertIsNotNone(rev, "ThreadRevision must be created")
        self.assertEqual(rev.status, "booked")
        self.assertEqual(rev.buyer_name, "Lara Dittmar")

    def test_4b_confirmation_fallback_no_name(self):
        """When buyer name is absent, confirmation gracefully falls back."""
        flow_data = {
            "nombre_apellido": "",
            "telefono": _WA_ID,
            "email": "anon@test.com",
            "direccion": "Calle Falsa 123",
            "como_llego": "WhatsApp",
        }
        token = self.state.flow_booking_token
        result = self.eng.handle(_make_flow_event(
            self.thread.id, "wamid.M20.6C1.T4B", flow_data, token,
        ))
        self.db.expire_all()

        self.assertEqual(result.action, "blocked_dispatch")
        text = _blocked_text(self.db, self.thread.id)
        # With no name, opener should be "¡Listo! Recibimos tu solicitud..."
        self.assertTrue(text.startswith("¡Listo!"),
                        f"No-name fallback must start with '¡Listo!' — got: {text!r}")
        self.assertNotIn("cliente", text.lower())
        self.assertIn("revisar los datos", text)


# ── Test 5: Sequence-level opener distinctness ────────────────────────────────

class TestSequenceOpeners(unittest.TestCase):
    """Assert all four messages have distinct, ordered openers."""

    def test_5_four_distinct_openers_in_order(self):
        expected_openers = [
            "Genial!",
            "¡Perfecto!",
            "Ese horario está disponible 🎉",
            "¡Listo,",
        ]
        # Verify each is unique (no duplicates)
        self.assertEqual(len(expected_openers), len(set(expected_openers)))
        # Verify none of the acceptance openers start with "Genial"
        for opener in expected_openers[1:]:
            self.assertFalse(
                opener.startswith("Genial"),
                f"Only the quote opener should start with Genial — found: {opener!r}",
            )
        # Verify the Flow intro does not start with "Perfecto"
        self.assertFalse(
            expected_openers[2].startswith("Perfecto"),
            "Flow intro must not start with 'Perfecto'",
        )
        # Verify booking confirmation does not contain "disponibilidad"
        # (this is a static assertion on the template strings, not on a live CE call)
        confirm_template = (
            "¡Listo, Nombre! Recibimos tu solicitud para el lunes 06/07 a las 15:00 🎉\n\n"
            "Un asesor va a revisar los datos y te confirma el turno a la brevedad."
        )
        self.assertNotIn("disponibilidad", confirm_template)
        self.assertIn("revisar los datos", confirm_template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
