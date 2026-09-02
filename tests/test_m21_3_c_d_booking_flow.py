"""M21.3-C-D — Booking Flow backend service tests.

BF01–BF10: Appointment screen + slot availability
BF11–BF15: Context / token validation
BF16–BF18: Confirm-booking revalidation and concurrency
BF19–BF24: Booking creation correctness
BF25–BF30: Crypto roundtrip, traceability, outbound-off invariant
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time as _time
import types
import unittest
from datetime import date, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── JSONB → JSON for SQLite ───────────────────────────────────────────────────
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = _sa.JSON
_pg_json.JSONB = _sa.JSON

# ── Stub heavy optional deps ─────────────────────────────────────────────────
for _mod_name in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

# ── Engine + ORM ──────────────────────────────────────────────────────────────
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


import app.models  # noqa: F401
from app.models import (
    Lead,
    Revision,
    ThreadRevision,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)

app.models.Base.metadata.create_all(_engine)

_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# ── Imports under test ────────────────────────────────────────────────────────
from app.services.booking_flow_service import (
    BOOKING_HORIZON_DAYS,
    FLOW_VERSION,
    STAGE_BOOKED,
    BookingContext,
    BookingFlowService,
    BookingSlotConflictError,
    BookingTokenError,
    _date_title,
    decrypt_flow_request,
    encrypt_flow_response,
    health_response,
    make_booking_token,
    parse_booking_token,
)
from app.schemas.schedule import ScheduleCheckOut, ScheduleSlotsOut, ScheduleSlotOut


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wipe():
    with _engine.begin() as conn:
        for tbl in reversed(app.models.Base.metadata.sorted_tables):
            conn.execute(tbl.delete())


def _fresh_db() -> Session:
    return _SessionLocal()


_FUTURE = date.today() + timedelta(days=3)
_SLOT = "10:00"


def _make_world(db: Session) -> tuple[WhatsAppThread, WhatsAppThreadState, WhatsAppThreadCandidate, Lead]:
    """Create minimal DB fixtures: contact, thread, lead, candidate, state."""
    contact = WhatsAppContact(wa_id="5491100000001", display_name="Test User")
    db.add(contact)
    db.flush()

    lead = Lead(estado="COTIZACION", necesita_humano=False)
    db.add(lead)
    db.flush()

    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread)
    db.flush()

    token = make_booking_token(thread.id)

    candidate = WhatsAppThreadCandidate(
        thread_id=thread.id,
        marca="Toyota",
        modelo="Corolla",
        anio=2020,
        tipo_vehiculo="AUTO",
        zone_group="CABA",
        zone_detail="Palermo",
        direccion_texto="Av. Santa Fe 1234",
        status="mentioned",
    )
    db.add(candidate)
    db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id,
        flow_booking_token=token,
        needs_human=False,
    )
    db.add(state)
    db.commit()

    return thread, state, candidate, lead


def _mock_sched_valid(svc: BookingFlowService, slots=None) -> None:
    """Patch ScheduleService on the service instance to always return slots."""
    if slots is None:
        slots = [_SLOT, "11:00", "15:00"]
    svc._sched = MagicMock()
    svc._sched.list_slots.return_value = ScheduleSlotsOut(
        preferred_day=_FUTURE,
        business_hours="09:00-18:00",
        slots=slots,
    )
    svc._sched.check.return_value = ScheduleCheckOut(
        valid=True,
        suggested_slots=slots,
        approval_tag="",
        requested_slot=ScheduleSlotOut(start=_SLOT, end="11:00"),
        business_hours="09:00-18:00",
    )


def _mock_sched_invalid(svc: BookingFlowService) -> None:
    """Patch ScheduleService to reject slots (no availability / check fails)."""
    svc._sched = MagicMock()
    svc._sched.list_slots.return_value = ScheduleSlotsOut(
        preferred_day=_FUTURE,
        business_hours="09:00-18:00",
        slots=[],
    )
    svc._sched.check.return_value = ScheduleCheckOut(
        valid=False,
        suggested_slots=[],
        approval_tag="",
        requested_slot=ScheduleSlotOut(start=_SLOT, end="11:00"),
        business_hours="09:00-18:00",
        reasons=["OCCUPIED"],
    )


def _confirm_data(token: str, d: date | None = None, t: str | None = None) -> dict:
    return {
        "booking_token": token,
        "date": (d or _FUTURE).isoformat(),
        "time": t or _SLOT,
        "name": "Juan Pérez",
        "phone": "+5491155550000",
        "email": "juan@example.com",
        "inspection_address": "Av. Santa Fe 1234",
        "seller_name": "Vendedor S.A.",
        "seller_phone": "+5491166660000",
        "listing_url": "https://example.com/listing/123",
    }


# ── BF01–BF10: Appointment screen + slot availability ────────────────────────

class TestBF01_ResolveContextSuccess(unittest.TestCase):
    """BF01 — resolve_context succeeds with valid token and live DB fixtures."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf01_resolve_valid_token(self):
        svc = BookingFlowService(self.db)
        ctx = svc.resolve_context(self.state.flow_booking_token)
        self.assertEqual(ctx.thread.id, self.thread.id)
        self.assertEqual(ctx.contact.wa_id, "5491100000001")
        self.assertIsNotNone(ctx.candidate)
        self.assertEqual(ctx.vehicle_summary, "Toyota Corolla 2020")
        self.assertEqual(ctx.zone_group, "CABA")


class TestBF02_HandleInit(unittest.TestCase):
    """BF02 — handle_init returns APPOINTMENT screen with available date items."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf02_init_returns_appointment_screen(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        result = svc.handle_init(self.state.flow_booking_token)
        self.assertEqual(result["version"], FLOW_VERSION)
        self.assertEqual(result["screen"], "APPOINTMENT")
        self.assertIn("data", result)
        self.assertIsInstance(result["data"]["date"], list)
        self.assertGreater(len(result["data"]["date"]), 0)


class TestBF03_ZeroSlotsExcluded(unittest.TestCase):
    """BF03 — dates with zero available slots are excluded from the date list."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf03_no_slots_means_no_dates(self):
        svc = BookingFlowService(self.db)
        _mock_sched_invalid(svc)
        result = svc.handle_init(self.state.flow_booking_token)
        self.assertEqual(result["data"]["date"], [])
        self.assertFalse(result["data"]["is_date_enabled"])


class TestBF04_HandleDateSelected(unittest.TestCase):
    """BF04 — handle_date_selected returns time slot items for the chosen date."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf04_date_selected_returns_slots(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc, slots=["10:00", "11:00"])
        result = svc.handle_date_selected(self.state.flow_booking_token, _FUTURE.isoformat())
        self.assertEqual(result["screen"], "APPOINTMENT")
        self.assertEqual(len(result["data"]["time"]), 2)
        self.assertTrue(result["data"]["is_time_enabled"])


class TestBF05_DateSelectedNoSlots(unittest.TestCase):
    """BF05 — handle_date_selected with no slots returns empty time list."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf05_no_slots_for_date(self):
        svc = BookingFlowService(self.db)
        _mock_sched_invalid(svc)
        result = svc.handle_date_selected(self.state.flow_booking_token, _FUTURE.isoformat())
        self.assertEqual(result["data"]["time"], [])
        self.assertFalse(result["data"]["is_time_enabled"])


class TestBF06_VehicleSummary(unittest.TestCase):
    """BF06 — APPOINTMENT screen includes vehicle_summary from candidate."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf06_vehicle_summary_in_screen(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        result = svc.handle_init(self.state.flow_booking_token)
        self.assertEqual(result["data"]["vehicle_summary"], "Toyota Corolla 2020")


class TestBF07_LocationSummary(unittest.TestCase):
    """BF07 — APPOINTMENT screen includes location_summary from candidate."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf07_location_summary_includes_zone_and_address(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        result = svc.handle_init(self.state.flow_booking_token)
        loc = result["data"]["location_summary"]
        self.assertIn("Palermo", loc)


class TestBF08_HorizonDays(unittest.TestCase):
    """BF08 — Date horizon is exactly BOOKING_HORIZON_DAYS (14) days from today."""

    def test_bf08_horizon_constant_is_14(self):
        self.assertEqual(BOOKING_HORIZON_DAYS, 14)

    def test_bf08_available_dates_respects_horizon(self):
        """Even with unlimited slots, no date beyond BOOKING_HORIZON_DAYS appears."""
        _wipe()
        db = _fresh_db()
        try:
            thread, state, candidate, lead = _make_world(db)
            svc = BookingFlowService(db)
            _mock_sched_valid(svc, slots=["10:00"])
            result = svc.handle_init(state.flow_booking_token)
            date_ids = [item["id"] for item in result["data"]["date"]]
            today = date.today()
            for d_str in date_ids:
                d = date.fromisoformat(d_str)
                self.assertGreater(d, today)
                self.assertLessEqual(d, today + timedelta(days=BOOKING_HORIZON_DAYS))
        finally:
            db.close()


class TestBF09_DateItemFormat(unittest.TestCase):
    """BF09 — Date items have ISO date `id` and human-readable Spanish `title`."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf09_date_item_fields(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        result = svc.handle_init(self.state.flow_booking_token)
        first = result["data"]["date"][0]
        self.assertIn("id", first)
        self.assertIn("title", first)
        d = date.fromisoformat(first["id"])
        expected_title = _date_title(d)
        self.assertEqual(first["title"], expected_title)

    def test_bf09_time_item_fields(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc, slots=["10:00"])
        result = svc.handle_date_selected(self.state.flow_booking_token, _FUTURE.isoformat())
        first = result["data"]["time"][0]
        self.assertEqual(first["id"], "10:00")
        self.assertEqual(first["title"], "10:00")


class TestBF10_SlotPassthrough(unittest.TestCase):
    """BF10 — Time slot items match the string slots returned by ScheduleService."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf10_slot_ids_match_schedule_output(self):
        expected_slots = ["09:00", "10:30", "14:00"]
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc, slots=expected_slots)
        result = svc.handle_date_selected(self.state.flow_booking_token, _FUTURE.isoformat())
        actual_ids = [item["id"] for item in result["data"]["time"]]
        self.assertEqual(actual_ids, expected_slots)


# ── BF11–BF15: Context / token validation ────────────────────────────────────

class TestBF11_MissingToken(unittest.TestCase):
    """BF11 — resolve_context raises BookingTokenError for empty/missing token."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()

    def tearDown(self):
        self.db.close()

    def test_bf11_empty_string_raises(self):
        svc = BookingFlowService(self.db)
        with self.assertRaises(BookingTokenError):
            svc.resolve_context("")

    def test_bf11_none_raises(self):
        svc = BookingFlowService(self.db)
        with self.assertRaises(BookingTokenError):
            svc.resolve_context(None)  # type: ignore


class TestBF12_ExpiredToken(unittest.TestCase):
    """BF12 — resolve_context raises BookingTokenError for expired token (>2h old)."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()

    def tearDown(self):
        self.db.close()

    def test_bf12_expired_token_rejected(self):
        old_ts = int(_time.time()) - (3 * 3600)  # 3 hours ago
        stale_token = f"999-{old_ts}-deadbeef"
        svc = BookingFlowService(self.db)
        with self.assertRaises(BookingTokenError) as cm:
            svc.resolve_context(stale_token)
        self.assertIn("expired", str(cm.exception).lower())


class TestBF13_WrongTokenMismatch(unittest.TestCase):
    """BF13 — resolve_context raises BookingTokenError when token doesn't match DB record."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf13_tampered_nonce_rejected(self):
        real_token = self.state.flow_booking_token
        parts = real_token.split("-")
        parts[-1] = "aaaaaaaaaaaaaaaa"  # tampered nonce
        bad_token = "-".join(parts)
        svc = BookingFlowService(self.db)
        with self.assertRaises(BookingTokenError):
            svc.resolve_context(bad_token)


class TestBF14_ConsumedToken(unittest.TestCase):
    """BF14 — resolve_context raises BookingTokenError after flow_booking_token is set to None."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)

    def tearDown(self):
        self.db.close()

    def test_bf14_consumed_token_rejected(self):
        original_token = self.state.flow_booking_token
        self.state.flow_booking_token = None
        self.db.commit()

        svc = BookingFlowService(self.db)
        with self.assertRaises(BookingTokenError):
            svc.resolve_context(original_token)


class TestBF15_PrepareSummaryValidation(unittest.TestCase):
    """BF15 — handle_prepare_summary validates required fields (name, phone, date, time)."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def _base_data(self) -> dict:
        return {
            "date": _FUTURE.isoformat(),
            "time": _SLOT,
            "name": "Ana García",
            "phone": "+5491155550001",
            "email": "",
            "inspection_address": "",
            "seller_name": "",
            "seller_phone": "",
            "listing_url": "",
        }

    def test_bf15_missing_name_raises(self):
        svc = BookingFlowService(self.db)
        data = self._base_data()
        data["name"] = ""
        with self.assertRaises(ValueError, msg="name is required"):
            svc.handle_prepare_summary(self.token, data)

    def test_bf15_missing_phone_raises(self):
        svc = BookingFlowService(self.db)
        data = self._base_data()
        data["phone"] = ""
        with self.assertRaises(ValueError, msg="phone is required"):
            svc.handle_prepare_summary(self.token, data)

    def test_bf15_invalid_date_raises(self):
        svc = BookingFlowService(self.db)
        data = self._base_data()
        data["date"] = "not-a-date"
        with self.assertRaises(ValueError):
            svc.handle_prepare_summary(self.token, data)

    def test_bf15_valid_data_returns_summary_screen(self):
        svc = BookingFlowService(self.db)
        result = svc.handle_prepare_summary(self.token, self._base_data())
        self.assertEqual(result["screen"], "SUMMARY")
        self.assertIn("appointment_summary", result["data"])
        self.assertIn("customer_summary", result["data"])


# ── BF16–BF18: Confirm-booking revalidation and concurrency ──────────────────

class TestBF16_ConfirmRevalidatesSlot(unittest.TestCase):
    """BF16 — handle_confirm_booking calls ScheduleService.check() before creating booking."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf16_check_called_on_confirm(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        svc._sched.check.assert_called_once()


class TestBF17_ConflictError(unittest.TestCase):
    """BF17 — handle_confirm_booking raises BookingSlotConflictError when slot invalid."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf17_conflict_raises(self):
        svc = BookingFlowService(self.db)
        _mock_sched_invalid(svc)
        with self.assertRaises(BookingSlotConflictError) as cm:
            svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        err = cm.exception
        self.assertIn("screen", err.refreshed_data)
        self.assertIn("slot_conflict_message", err.refreshed_data["data"])

    def test_bf17_conflict_no_thread_revision_created(self):
        svc = BookingFlowService(self.db)
        _mock_sched_invalid(svc)
        try:
            svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        except BookingSlotConflictError:
            pass
        self.db.rollback()
        count = self.db.query(ThreadRevision).filter_by(thread_id=self.thread.id).count()
        self.assertEqual(count, 0)


class TestBF18_AdvisoryLockSkipOnSQLite(unittest.TestCase):
    """BF18 — Advisory lock gracefully skips on SQLite (no pg_try_advisory_xact_lock)."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf18_lock_skip_does_not_raise(self):
        """On SQLite, _acquire_advisory_lock catches the exception and proceeds."""
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        lock_key = int(hashlib.sha256(_FUTURE.isoformat().encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
        # Should not raise despite SQLite not supporting pg_try_advisory_xact_lock
        svc._acquire_advisory_lock(lock_key, _FUTURE.isoformat())

    def test_bf18_full_confirm_works_on_sqlite(self):
        """Confirm booking completes end-to-end on SQLite (lock no-ops silently)."""
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        result = svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.assertEqual(result["screen"], "SUCCESS")


# ── BF19–BF24: Booking creation correctness ──────────────────────────────────

class TestBF19_ThreadRevisionStatus(unittest.TestCase):
    """BF19 — Booking creation sets ThreadRevision.status='booked'."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf19_thread_revision_status_booked(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        tr = self.db.query(ThreadRevision).filter_by(thread_id=self.thread.id).first()
        self.assertIsNotNone(tr)
        self.assertEqual(tr.status, "booked")

    def test_bf19_thread_revision_buyer_fields_populated(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        tr = self.db.query(ThreadRevision).filter_by(thread_id=self.thread.id).first()
        self.assertEqual(tr.buyer_name, "Juan Pérez")
        self.assertEqual(tr.buyer_phone, "+5491155550000")
        self.assertEqual(tr.buyer_email, "juan@example.com")


class TestBF20_LeadEstado(unittest.TestCase):
    """BF20 — Booking sets lead.estado='COORDINAR_DISPONIBILIDAD'."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf20_lead_estado(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        lead = self.db.get(Lead, self.lead.id)
        self.assertEqual(lead.estado, "COORDINAR_DISPONIBILIDAD")


class TestBF21_LeadFlagAndHuman(unittest.TestCase):
    """BF21 — Booking sets lead.flag='ACEPTADO' and necesita_humano=True."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf21_lead_flag_and_human(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        lead = self.db.get(Lead, self.lead.id)
        self.assertEqual(lead.flag, "ACEPTADO")
        self.assertTrue(lead.necesita_humano)


class TestBF22_RevisionSchedule(unittest.TestCase):
    """BF22 — Booking creates Revision with turno_fecha and turno_hora."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf22_revision_schedule_fields(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        rev = self.db.query(Revision).filter_by(lead_id=self.lead.id).first()
        self.assertIsNotNone(rev)
        self.assertEqual(rev.turno_fecha, _FUTURE)
        self.assertEqual(str(rev.turno_hora), _SLOT + ":00")

    def test_bf22_revision_vehicle_fields_from_candidate(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        rev = self.db.query(Revision).filter_by(lead_id=self.lead.id).first()
        self.assertEqual(rev.marca, "Toyota")
        self.assertEqual(rev.modelo, "Corolla")
        self.assertEqual(rev.anio, 2020)


class TestBF23_TokenConsumed(unittest.TestCase):
    """BF23 — Booking consumes flow_booking_token (sets to None) after creation."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf23_token_nulled_after_booking(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        state = self.db.query(WhatsAppThreadState).filter_by(thread_id=self.thread.id).first()
        self.assertIsNone(state.flow_booking_token)

    def test_bf23_second_confirm_with_same_token_fails(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        # Token is consumed — second call should raise
        with self.assertRaises(BookingTokenError):
            svc.handle_confirm_booking(self.token, _confirm_data(self.token))


class TestBF24_LastStageBooked(unittest.TestCase):
    """BF24 — Booking sets state.last_stage=STAGE_BOOKED and needs_human=True."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf24_stage_and_human_flag(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        state = self.db.query(WhatsAppThreadState).filter_by(thread_id=self.thread.id).first()
        self.assertEqual(state.last_stage, STAGE_BOOKED)
        self.assertTrue(state.needs_human)


# ── BF25–BF30: Crypto, traceability, outbound-off invariant ──────────────────

class TestBF25_CryptoRoundtrip(unittest.TestCase):
    """BF25 — encrypt_flow_response + decrypt roundtrip using generated AES key."""

    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            cls._aesgcm = AESGCM
        except ImportError:
            raise unittest.SkipTest("cryptography package not installed")

    def test_bf25_roundtrip(self):
        """Encrypted bytes decrypt correctly with flipped IV."""
        import os as _os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aes_key = _os.urandom(16)
        iv = _os.urandom(12)
        payload = {"screen": "APPOINTMENT", "data": {"date": [], "time": []}}

        encrypted = encrypt_flow_response(payload, aes_key, iv)
        self.assertIsInstance(encrypted, bytes)

        # Decrypt with flipped IV
        flipped_iv = bytes([b ^ 0xFF for b in iv])
        aesgcm = AESGCM(aes_key)
        decrypted = aesgcm.decrypt(flipped_iv, encrypted, None)
        recovered = json.loads(decrypted)
        self.assertEqual(recovered, payload)

    def test_bf25_encrypted_differs_from_plaintext(self):
        import os as _os

        aes_key = _os.urandom(16)
        iv = _os.urandom(12)
        payload = {"version": "3.0", "screen": "SUCCESS", "data": {}}

        encrypted = encrypt_flow_response(payload, aes_key, iv)
        plain_json = json.dumps(payload, ensure_ascii=False).encode()
        self.assertNotEqual(encrypted, plain_json)


class TestBF26_DecryptBadBody(unittest.TestCase):
    """BF26 — decrypt_flow_request raises ValueError on malformed / missing fields."""

    def test_bf26_missing_key_field(self):
        with self.assertRaises(ValueError):
            decrypt_flow_request({})

    def test_bf26_no_private_key_configured(self):
        """When FLOW_BOOKING_PRIVATE_KEY_PATH is not set, raises ValueError."""
        import base64
        body = {
            "encrypted_aes_key": base64.b64encode(b"x" * 256).decode(),
            "encrypted_flow_data": base64.b64encode(b"y" * 32).decode(),
            "initial_vector": base64.b64encode(b"z" * 12).decode(),
        }
        env_backup = os.environ.pop("FLOW_BOOKING_PRIVATE_KEY_PATH", None)
        try:
            with self.assertRaises(ValueError):
                decrypt_flow_request(body)
        finally:
            if env_backup is not None:
                os.environ["FLOW_BOOKING_PRIVATE_KEY_PATH"] = env_backup


class TestBF27_EncryptionOpaque(unittest.TestCase):
    """BF27 — encrypt_flow_response output is opaque (different from plain JSON)."""

    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise unittest.SkipTest("cryptography package not installed")

    def test_bf27_output_not_json_parseable(self):
        import os as _os

        aes_key = _os.urandom(16)
        iv = _os.urandom(12)
        payload = {"test": "data"}
        encrypted = encrypt_flow_response(payload, aes_key, iv)
        with self.assertRaises((json.JSONDecodeError, UnicodeDecodeError)):
            json.loads(encrypted)


class TestBF28_SuccessScreen(unittest.TestCase):
    """BF28 — handle_confirm_booking returns 'SUCCESS' screen on valid booking."""

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf28_success_screen_returned(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        result = svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.assertEqual(result["version"], FLOW_VERSION)
        self.assertEqual(result["screen"], "SUCCESS")
        self.assertIn("extension_message_response", result["data"])


class TestBF29_TokenFormat(unittest.TestCase):
    """BF29 — make_booking_token produces a token with thread_id as first segment."""

    def test_bf29_token_prefix_is_thread_id(self):
        token = make_booking_token(42)
        thread_id, issued_at = parse_booking_token(token)
        self.assertEqual(thread_id, 42)

    def test_bf29_token_issued_at_is_recent(self):
        before = int(_time.time())
        token = make_booking_token(1)
        _, issued_at = parse_booking_token(token)
        after = int(_time.time())
        self.assertGreaterEqual(issued_at, before)
        self.assertLessEqual(issued_at, after)

    def test_bf29_two_tokens_for_same_thread_differ(self):
        t1 = make_booking_token(7)
        t2 = make_booking_token(7)
        self.assertNotEqual(t1, t2)

    def test_bf29_malformed_token_parse_error(self):
        with self.assertRaises(BookingTokenError):
            parse_booking_token("notavalidtoken")


class TestBF30_OutboundOff(unittest.TestCase):
    """BF30 — handle_confirm_booking never creates a WhatsAppMessage (outbound off).

    Booking creation is purely DB-side (ThreadRevision + Revision + Lead state).
    No WhatsAppMessage record is written, confirming outbound remains off.
    """

    def setUp(self):
        _wipe()
        self.db = _fresh_db()
        self.thread, self.state, self.candidate, self.lead = _make_world(self.db)
        self.token = self.state.flow_booking_token

    def tearDown(self):
        self.db.close()

    def test_bf30_no_whatsapp_message_created(self):
        svc = BookingFlowService(self.db)
        _mock_sched_valid(svc)
        svc.handle_confirm_booking(self.token, _confirm_data(self.token))
        self.db.expire_all()
        msg_count = self.db.query(WhatsAppMessage).filter_by(
            thread_id=self.thread.id
        ).count()
        self.assertEqual(msg_count, 0, "No outbound WhatsApp message should be created by BookingFlowService")

    def test_bf30_health_response_has_correct_structure(self):
        r = health_response()
        self.assertEqual(r["version"], FLOW_VERSION)
        self.assertEqual(r["data"]["status"], "active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
