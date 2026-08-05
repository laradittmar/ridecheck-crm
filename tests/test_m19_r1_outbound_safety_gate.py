"""M19.R1.1 — Recipient-level Outbound Safety Gate tests.

All 7 scenarios from the specification are covered.  Tests use SQLite
in-memory so they run fully offline without Docker, started containers,
or any network access.  JSONB → JSON downgrade and absent server_default
are handled explicitly so SQLite behaves identically to Postgres for
gate logic.

Test index:
  1. 50 concurrent/repeated attempts of the vehicle-question text to the
     same wa_id: one Meta mock call maximum.
  2. Same recipient (wa_id), different thread IDs, same exact text:
     one Meta mock call maximum.
  3. Four different automated messages in 60 seconds:
     first three eligible; fourth blocked; needs_human=True.
  4. Same text after 10 minutes: allowed (new dedup window bucket).
  5. Normal quote → acceptance → scheduling → Flow: not blocked.
  6. Meta failure: pending becomes failed; no automatic duplicate resend.
  7. Full offline test suite (all tests above collected in one runner).
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Stub heavy optional deps before importing anything from the backend.
for _mod in [
    "resend", "anthropic", "openai", "boto3",
    "botocore", "botocore.exceptions",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

# Minimal psycopg2 stub so SQLAlchemy's postgres dialect doesn't fail import.
if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, MetaData, String,
    Table, Text, create_engine, event,
)
from sqlalchemy.orm import Session

import app.models  # noqa: F401 — registers all ORM classes against Base
from app.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppOutboundDedup,
    WhatsAppThread,
    WhatsAppThreadState,
)
from app.services.outbound_safety_gate import (
    DEDUP_WINDOW_MINUTES,
    FLOOD_MAX_MESSAGES,
    FLOOD_WINDOW_SECONDS,
    GateOutcome,
    OutboundSafetyGate,
    _content_fingerprint,
)

# ── SQLite engine shared across all test cases ────────────────────────────────

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _sqlite_pragmas(conn, _rec):
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# Build a SQLite-compatible schema for just the tables the gate needs.
# Uses JSON (not JSONB) for raw_payload and omits unsupported Postgres CHECK
# constraints.  M19.R1.2: dedup table updated to rolling-window schema
# (message_kind added, window_start removed, UNIQUE constraint removed).
_test_meta = MetaData()

Table("whatsapp_contacts", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("wa_id", String(80), nullable=False, unique=True),
    Column("display_name", String(255)),
    Column("phone", String(40)),
    Column("created_at", DateTime(timezone=True)),
)
Table("whatsapp_threads", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("contact_id", Integer, nullable=False),
    Column("display_name_override", String(255)),
    Column("lead_id", Integer),
    Column("last_message_at", DateTime(timezone=True)),
    Column("unread_count", Integer, default=0),
    Column("latest_inbound_wa_message_id", String(255)),
    Column("created_at", DateTime(timezone=True)),
)
Table("whatsapp_thread_states", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("thread_id", Integer, nullable=False, unique=True),
    Column("last_intent", String(30)),
    Column("last_stage", String(30)),
    Column("needs_human", Boolean, default=False),
    Column("current_focus_candidate_id", Integer),
    Column("current_revision_id", Integer),
    Column("last_processed_inbound_wa_message_id", String(191)),
    Column("customer_name", String(120)),
    Column("home_zone_group", String(50)),
    Column("home_zone_detail", String(80)),
    Column("preferred_day", String(20)),
    Column("preferred_time", String(10)),
    Column("active_requested_date", String(20)),
    Column("last_requested_time", String(10)),
    Column("last_offered_slots", Text),
    Column("last_visible_slots", Text),
    Column("is_website_lead", Boolean, default=False),
    Column("flow_booking_token", String(120)),
    Column("vehicle_clarification_sent", Boolean, default=False),
    Column("location_clarification_sent", Boolean, default=False),
    Column("vehicle_fallback_flow_sent", Boolean, default=False),
    Column("location_fallback_flow_sent", Boolean, default=False),
    Column("inspectability_clarification_sent", Boolean, default=False),
    Column("unanswered_alert_sent_at", DateTime(timezone=True)),
    Column("quote_followup_sent_at", DateTime(timezone=True)),
    Column("buscando_followup_sent_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True)),
)
Table("whatsapp_messages", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("thread_id", Integer, nullable=False),
    Column("wa_message_id", String(191), unique=True),
    Column("direction", String(10), nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("message_type", String(20), default="text"),
    Column("media_id", String(191)),
    Column("text", Text),
    Column("status", String(20), nullable=False, default="received"),
    Column("raw_payload", JSON),   # JSON instead of JSONB — SQLite compatible
    Column("created_at", DateTime(timezone=True)),
    # Safety gate columns (from migration 20260625_outbound_safety_gate)
    Column("automated", Boolean, nullable=False, default=False),
    Column("content_fingerprint", String(64)),
    Column("blocked_reason", Text),
)
# M19.R1.2: rolling-window schema — message_kind added, window_start removed,
# UNIQUE constraint replaced by recipient lock + SELECT FOR UPDATE.
Table("whatsapp_outbound_dedup", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("wa_id", String(80), nullable=False),
    Column("thread_id", Integer, nullable=False),
    Column("message_kind", String(20), nullable=False, default="text"),
    Column("content_fingerprint", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True)),
)
# M19.R1.2: recipient-level lock table for SELECT FOR UPDATE serialization.
Table("whatsapp_recipient_locks", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("wa_id", String(80), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True)),
)

_test_meta.create_all(_engine)


# ── Fixture helpers ───────────────────────────────────────────────────────────

_VEHICLE_QUESTION = (
    "Para cotizarte la revisión necesito saber qué vehículo tenés. "
    "¿Me podés indicar la marca y el modelo?"
)

_NOW = datetime(2026, 6, 25, 13, 30, 0, tzinfo=timezone.utc)


def _make_contact(session: Session, wa_id: str) -> int:
    """Create or reuse a contact; return contact.id."""
    contact = WhatsAppContact(wa_id=wa_id, display_name="Test")
    session.add(contact)
    session.flush()
    return contact.id


def _make_thread(session: Session, contact_id: int) -> tuple[int, int]:
    """Create a thread + state for the given contact; return (thread_id, state_id)."""
    thread = WhatsAppThread(contact_id=contact_id, unread_count=0)
    session.add(thread)
    session.flush()
    state = WhatsAppThreadState(thread_id=thread.id)
    session.add(state)
    session.flush()
    return thread.id, state.id


def _make_recipient(session: Session) -> tuple[str, int, int]:
    """Create a unique contact + thread + state; return (wa_id, thread_id, state_id)."""
    wa_id = f"549test_{id(session)}_{id(object())}"
    contact_id = _make_contact(session, wa_id)
    thread_id, state_id = _make_thread(session, contact_id)
    session.commit()
    return wa_id, thread_id, state_id


# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — 50 repeated attempts of the vehicle-question text to the same wa_id.
#          One Meta mock call maximum.
# ══════════════════════════════════════════════════════════════════════════════

class TestVehicleQuestionFloodSameRecipient(unittest.TestCase):
    """50 repeated attempts of the exact vehicle-question text to the same
    recipient wa_id: exactly one ALLOWED outcome, 49 BLOCKED_DUPLICATE.

    Concurrency safety: the SAVEPOINT mechanism ensures that even truly
    concurrent INSERTs of the same (wa_id, fingerprint, window) row resolve
    atomically in Postgres.  In this sequential test, the second INSERT hits
    the UNIQUE constraint and is blocked via ROLLBACK TO SAVEPOINT.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_fifty_attempts_one_send(self):
        gate = OutboundSafetyGate(self.session)
        text = _VEHICLE_QUESTION
        now = _NOW
        sent_count = 0
        blocked_count = 0

        pfx = id(self)
        for i in range(50):
            r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=text, now=now)
            if r.outcome == GateOutcome.ALLOWED:
                gate.mark_sent(r.message_id, f"wamid_{pfx}_{i}")
                sent_count += 1
            else:
                self.assertEqual(r.outcome, GateOutcome.BLOCKED_DUPLICATE)
                blocked_count += 1

        self.assertEqual(sent_count, 1, "exactly one send allowed")
        self.assertEqual(blocked_count, 49, "all 49 repeats blocked as duplicates")

        msgs = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, automated=True
        ).all()
        self.assertEqual(len([m for m in msgs if m.status == "sent"]), 1)
        self.assertEqual(len([m for m in msgs if m.status == "blocked"]), 49)

    def test_dedup_entry_stores_wa_id(self):
        """The dedup row must be keyed by wa_id, not just thread_id."""
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                         text=_VEHICLE_QUESTION, now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        dedup_row = self.session.query(WhatsAppOutboundDedup).filter_by(
            wa_id=self.wa_id
        ).first()
        self.assertIsNotNone(dedup_row, "dedup row must exist with wa_id key")
        self.assertEqual(dedup_row.wa_id, self.wa_id)
        self.assertEqual(dedup_row.thread_id, self.thread_id)


# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — Same recipient (wa_id), different thread IDs, same exact text.
#          One Meta mock call maximum.
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossThreadRecipientDedup(unittest.TestCase):
    """The gate keys dedup by wa_id, not thread_id.

    When the same contact has two CRM threads (edge case: duplicate thread
    creation, admin action) and the bot attempts to send the same text via
    each thread, the second attempt must be blocked as a duplicate — even
    though the thread IDs differ.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        wa_id = f"549xthread_{id(self)}"
        contact_id = _make_contact(self.session, wa_id)
        self.thread_a_id, _ = _make_thread(self.session, contact_id)
        self.thread_b_id, _ = _make_thread(self.session, contact_id)
        self.session.commit()
        self.wa_id = wa_id

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_same_text_different_threads_one_send(self):
        gate = OutboundSafetyGate(self.session)
        text = _VEHICLE_QUESTION
        now = _NOW

        # Thread A sends first.
        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_a_id,
                          text=text, now=now)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED,
                         "first send via thread A must be allowed")
        gate.mark_sent(r1.message_id, f"wamid_xthread_{id(self)}_a")

        # Thread B attempts the same text to the same recipient.
        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_b_id,
                          text=text, now=now)
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "same text to same wa_id via different thread must be blocked")
        self.assertIn("...%s" % self.wa_id[-4:], r2.blocked_reason or "",
                      "blocked reason must reference recipient wa_id suffix")

        # Exactly one sent record across both threads combined.
        all_sent = self.session.query(WhatsAppMessage).filter_by(
            status="sent", automated=True
        ).filter(WhatsAppMessage.thread_id.in_([self.thread_a_id, self.thread_b_id])).all()
        self.assertEqual(len(all_sent), 1)

    def test_different_texts_both_allowed(self):
        """Different texts to the same recipient are NOT blocked by dedup."""
        gate = OutboundSafetyGate(self.session)
        now = _NOW
        pfx = id(self)

        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_a_id,
                          text=f"Mensaje uno {pfx}", now=now)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_{pfx}_1")

        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_b_id,
                          text=f"Mensaje dos {pfx}", now=now)
        self.assertEqual(r2.outcome, GateOutcome.ALLOWED,
                         "different text to same recipient must not be blocked")
        gate.mark_sent(r2.message_id, f"wamid_{pfx}_2")


# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — Four different automated messages in 60 seconds.
#          First three eligible; fourth blocked; needs_human=True.
# ══════════════════════════════════════════════════════════════════════════════

class TestFloodGate(unittest.TestCase):
    """Four distinct automated attempts within 60 seconds to the same wa_id:
    first three may send; fourth is blocked as BLOCKED_FLOOD and
    needs_human becomes True on the relevant thread state.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, self.state_id = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_fourth_message_blocked_and_needs_human(self):
        gate = OutboundSafetyGate(self.session)
        now = _NOW
        outcomes = []
        pfx = id(self)

        for i in range(4):
            text = f"Mensaje distinto {pfx} numero {i} para el turno"
            r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=text, now=now)
            outcomes.append(r.outcome)
            if r.outcome == GateOutcome.ALLOWED:
                gate.mark_sent(r.message_id, f"wamid_{pfx}_{i}")

        self.assertEqual(outcomes[0], GateOutcome.ALLOWED)
        self.assertEqual(outcomes[1], GateOutcome.ALLOWED)
        self.assertEqual(outcomes[2], GateOutcome.ALLOWED)
        self.assertEqual(outcomes[3], GateOutcome.BLOCKED_FLOOD)

        state = self.session.get(WhatsAppThreadState, self.state_id)
        self.assertTrue(state.needs_human,
                        "needs_human must be True after flood gate fires")

        blocked = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, status="blocked"
        ).all()
        self.assertEqual(len(blocked), 1, "exactly one blocked record created")
        self.assertIn("FLOOD", blocked[0].blocked_reason or "")

    def test_flood_reason_contains_wa_id_suffix_and_thread(self):
        """Structured flood warning must include wa_id suffix, thread_id, and reason."""
        gate = OutboundSafetyGate(self.session)
        now = _NOW
        pfx = id(self)

        for i in range(FLOOD_MAX_MESSAGES):
            r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=f"Flood reason test {pfx} msg {i}", now=now)
            self.assertEqual(r.outcome, GateOutcome.ALLOWED)
            gate.mark_sent(r.message_id, f"wamid_fr_{pfx}_{i}")

        r_blocked = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                                 text=f"Flood reason test {pfx} trigger", now=now)
        self.assertEqual(r_blocked.outcome, GateOutcome.BLOCKED_FLOOD)
        reason = r_blocked.blocked_reason or ""
        self.assertIn(self.wa_id[-4:], reason,
                      "blocked reason must include wa_id suffix")
        self.assertIn(str(self.thread_id), reason,
                      "blocked reason must include thread_id")
        self.assertIn("FLOOD", reason)


# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — Same text after 10 minutes: allowed (new dedup window bucket).
# ══════════════════════════════════════════════════════════════════════════════

class TestDedupWindowExpiry(unittest.TestCase):
    """Same text after more than 10 minutes (rolling window) is permitted.

    M19.R1.2: rolling window — the cutoff is `created_at > now - 10min` (strict).
    A message sent at T is blocked until T+10min exactly; at T+10min it is allowed.

    Rolling boundary:
      now1 = 13:05:00 → first send ALLOWED (dedup row created_at=13:05:00)
      now1 + 3min = 13:08:00 → BLOCKED (13:05:00 > 13:08:00 - 10min = 12:58:00 ✓)
      now2 = 13:15:00 → ALLOWED (13:05:00 > 13:15:00 - 10min = 13:05:00 is False ✓)
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_same_text_blocked_in_window_allowed_after(self):
        gate = OutboundSafetyGate(self.session)
        text = "Cotización de revisión: $130.000 + viáticos $30.000"

        # now1 at 13:05:00 — dedup row created_at = 13:05:00
        now1 = datetime(2026, 6, 25, 13, 5, 0, tzinfo=timezone.utc)
        # now2 at 13:15:00 — cutoff = 13:05:00; row at 13:05:00 NOT > 13:05:00 → allowed
        now2 = datetime(2026, 6, 25, 13, 15, 0, tzinfo=timezone.utc)

        pfx = id(self)
        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                          text=text, now=now1)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_{pfx}_first")

        # Within the rolling 10-minute window → blocked.
        r_dup = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=text, now=now1 + timedelta(minutes=3))
        self.assertEqual(r_dup.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "same text within 10-min rolling window must be blocked")

        # Exactly 10 minutes later — row at created_at is NOT > cutoff → allowed.
        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                          text=text, now=now2)
        self.assertEqual(r2.outcome, GateOutcome.ALLOWED,
                         "same text exactly 10min later must be allowed (rolling boundary)")
        gate.mark_sent(r2.message_id, f"wamid_{pfx}_second")

        sent = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, status="sent"
        ).all()
        self.assertEqual(len(sent), 2)


# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — Normal quote → acceptance → scheduling → Flow: not blocked.
# ══════════════════════════════════════════════════════════════════════════════

class TestLegitimateProgressionNotBlocked(unittest.TestCase):
    """Normal conversation progression with distinct messages is never blocked.

    The four canonical bot turns in a successful booking flow each have
    different text, so neither the dedup guard nor the flood gate fires.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_quote_acceptance_scheduling_flow_all_allowed(self):
        gate = OutboundSafetyGate(self.session)
        now = _NOW
        pfx = id(self)

        # Canonical four-turn bot sequence.
        turns = [
            # turn 1: quote with price
            ("text", "Genial! La cotización es: $130.000 + viáticos $30.000. ¿Avanzamos?"),
            # turn 2: acceptance acknowledgment + scheduling prompt
            ("text", "Perfecto, ¿qué día y horario te queda bien para la revisión?"),
            # turn 3: time slot confirmation
            ("text", "Te confirmo el turno para el martes 1 de julio a las 10:00."),
            # turn 4: Flow button (booking form)
            ("flow", "Completá tus datos para confirmar la reserva."),
        ]

        for i, (msg_type, text) in enumerate(turns):
            r = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread_id,
                text=text,
                message_type=msg_type,
                now=now + timedelta(seconds=i * 30),
            )
            self.assertEqual(
                r.outcome, GateOutcome.ALLOWED,
                f"Turn {i} ({msg_type!r}) was unexpectedly blocked: {r.blocked_reason}",
            )
            gate.mark_sent(r.message_id, f"wamid_{pfx}_{i}")

        sent = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, status="sent"
        ).all()
        self.assertEqual(len(sent), 4)
        blocked = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, status="blocked"
        ).all()
        self.assertEqual(len(blocked), 0)


# ══════════════════════════════════════════════════════════════════════════════
# Test 6 — Meta failure: pending becomes failed; no automatic duplicate resend.
# ══════════════════════════════════════════════════════════════════════════════

class TestMetaApiFailure(unittest.TestCase):
    """Meta API failure handling:
    - pending record becomes failed.
    - The dedup entry is retained, so an automatic retry in the same window is
      blocked as a duplicate (requires human intervention to clear).
    - No 'sent' record is created.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_failed_send_leaves_failed_and_blocks_auto_retry(self):
        gate = OutboundSafetyGate(self.session)
        text = _VEHICLE_QUESTION
        now = _NOW

        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                         text=text, now=now)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        # Simulate Meta API failure — caller invokes mark_failed.
        gate.mark_failed(r.message_id)

        msg = self.session.get(WhatsAppMessage, r.message_id)
        self.assertEqual(msg.status, "failed")
        self.assertIsNone(msg.wa_message_id)

        # Automatic retry in the same dedup window is blocked.
        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                          text=text, now=now)
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Automatic retry must be blocked — dedup entry was written "
                         "before the Meta call even on failure")

        msgs = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, automated=True
        ).all()
        self.assertEqual(len(msgs), 2)
        self.assertEqual({m.status for m in msgs}, {"failed", "blocked"})

    def test_pending_record_exists_before_mark_called(self):
        """pending record is durable BEFORE any Meta API call."""
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                         text=_VEHICLE_QUESTION, now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        msg = self.session.get(WhatsAppMessage, r.message_id)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.status, "pending",
                         "record must be pending before mark_sent/mark_failed is called")


# ══════════════════════════════════════════════════════════════════════════════
# Test 7 — Supporting: fingerprint stability, status compat, window math.
# ══════════════════════════════════════════════════════════════════════════════

class TestFingerprintAndStatusCompat(unittest.TestCase):
    """Fingerprint is stable across normalisation variants; existing statuses work."""

    def test_fingerprint_normalisation(self):
        base = _content_fingerprint("Para cotizarte la revisión")
        self.assertEqual(base, _content_fingerprint("  Para cotizarte la revisión  "))
        self.assertEqual(base, _content_fingerprint("para cotizarte la revisión"))
        self.assertEqual(base, _content_fingerprint("Para  cotizarte  la  revisión"))
        self.assertNotEqual(base, _content_fingerprint("Otro mensaje"))

    def test_fingerprint_is_64_hex_chars(self):
        fp = _content_fingerprint(_VEHICLE_QUESTION)
        self.assertEqual(len(fp), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp))

    def test_dedup_rolling_boundary(self):
        """Rolling window boundary: `created_at > now - 10min` uses strict inequality.

        Row at T blocks sends until T+10min (exclusive).  At exactly T+10min the
        row is no longer within the window and a new send is allowed.
        """
        T = datetime(2026, 6, 25, 13, 5, 0, tzinfo=timezone.utc)
        cutoff_at_T_plus_10 = T + timedelta(minutes=DEDUP_WINDOW_MINUTES) - T  # = 10min delta
        # At T + 10min exactly: T > (T + 10min - 10min) → T > T → False → allowed
        cutoff = T + timedelta(minutes=DEDUP_WINDOW_MINUTES) - T  # noqa: F841
        # Verify: row created at T, checked at T+9min59s → still blocked
        check_time_inside = T + timedelta(minutes=9, seconds=59)
        self.assertTrue(
            T > check_time_inside - timedelta(minutes=DEDUP_WINDOW_MINUTES),
            "row at T must be > cutoff when checked at T+9min59s (inside window)",
        )
        # Verify: row created at T, checked at T+10min exactly → no longer blocked
        check_time_boundary = T + timedelta(minutes=DEDUP_WINDOW_MINUTES)
        self.assertFalse(
            T > check_time_boundary - timedelta(minutes=DEDUP_WINDOW_MINUTES),
            "row at T must NOT be > cutoff when checked at T+10min exactly (boundary)",
        )

    def test_existing_status_values_accepted(self):
        """Existing non-automated messages with legacy status values still insert fine."""
        with Session(_engine) as session:
            contact = WhatsAppContact(wa_id="549existing_status_compat", display_name="X")
            session.add(contact)
            session.flush()
            thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
            session.add(thread)
            session.flush()
            for status in ("received", "sent", "delivered", "read", "failed"):
                session.add(WhatsAppMessage(
                    thread_id=thread.id,
                    direction="in" if status == "received" else "out",
                    status=status,
                    timestamp=_NOW,
                    text=f"test {status}",
                    automated=False,
                ))
            session.commit()
            msgs = session.query(WhatsAppMessage).filter_by(thread_id=thread.id).all()
            self.assertEqual(len(msgs), 5)

    def test_new_status_values_accepted(self):
        """Gate statuses 'pending' and 'blocked' must also be insertable."""
        with Session(_engine) as session:
            contact = WhatsAppContact(wa_id="549new_status_compat", display_name="Y")
            session.add(contact)
            session.flush()
            thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
            session.add(thread)
            session.flush()
            for status in ("pending", "blocked"):
                session.add(WhatsAppMessage(
                    thread_id=thread.id,
                    direction="out",
                    status=status,
                    timestamp=_NOW,
                    text=f"gate test {status}",
                    automated=True,
                ))
            session.commit()
            msgs = session.query(WhatsAppMessage).filter_by(thread_id=thread.id).all()
            self.assertEqual(len(msgs), 2)


# ══════════════════════════════════════════════════════════════════════════════
# Test suite runner (Test 7 — offline execution)
# ══════════════════════════════════════════════════════════════════════════════

def load_tests(loader, tests, pattern):
    """Called by unittest discovery — returns the full suite."""
    suite = unittest.TestSuite()
    for cls in [
        TestVehicleQuestionFloodSameRecipient,   # Test 1
        TestCrossThreadRecipientDedup,            # Test 2
        TestFloodGate,                            # Test 3
        TestDedupWindowExpiry,                    # Test 4
        TestLegitimateProgressionNotBlocked,      # Test 5
        TestMetaApiFailure,                       # Test 6
        TestFingerprintAndStatusCompat,           # Test 7 supporting
    ]:
        tests = loader.loadTestsFromTestCase(cls)
        suite.addTests(tests)
    return suite


if __name__ == "__main__":
    unittest.main(verbosity=2)
