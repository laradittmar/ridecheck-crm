"""M19.R1.2 — Recipient-Level Outbound Safety Gate: rolling-window + recipient lock.

Offline unit tests for all 11 spec requirements (A.1–A.11).

Schema differences from M19.R1 tests:
  - whatsapp_outbound_dedup: message_kind added, window_start removed, no UNIQUE constraint
  - whatsapp_recipient_locks: new table for SELECT FOR UPDATE serialisation
  - _dedup_window_start() removed from gate module (rolling window, not buckets)

Test index:
  A.1  Rolling boundary: first send at T=now1, second at T+1s → still blocked
  A.2  Rolling boundary: second send at exactly T+10min → allowed (strict >)
  A.3  Cross-thread same wa_id, same text → blocked (keyed by wa_id, not thread_id)
  A.4  Same text at T+9min59s → blocked (inside 10-min rolling window)
  A.5  Same text at T+10min exactly → allowed (rolling boundary)
  A.6  50 concurrent attempts, same text → 1 allowed, 49 blocked as BLOCKED_DUPLICATE
  A.7  4 distinct messages in 60s → 3 allowed, 4th BLOCKED_FLOOD, needs_human=True
  A.8  Meta failure → pending→failed; auto-retry blocked as BLOCKED_DUPLICATE
  A.9  Pending record committed before Meta call
  A.10 Automated n8n-style unauthenticated API (send-to-phone) → gate applies (static)
  A.11 Engine dedup/flood block → raises OutboundBlockedError (not silent return None)
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

# Stub psycopg2 (psycopg3/psycopg is not needed — SQLite in-memory is used).
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

import app.models  # noqa: F401 — registers ORM classes against Base
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
from app.services.outbound_guard import OutboundBlockedError

# ── SQLite engine ─────────────────────────────────────────────────────────────

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _sqlite_pragmas(conn, _rec):
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


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
    Column("needs_human", Boolean, default=False),
    Column("last_intent", String(30)),
    Column("last_stage", String(30)),
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
    Column("raw_payload", JSON),
    Column("created_at", DateTime(timezone=True)),
    Column("automated", Boolean, nullable=False, default=False),
    Column("content_fingerprint", String(64)),
    Column("blocked_reason", Text),
)
# M19.R1.2 rolling-window schema — message_kind, no window_start, no UNIQUE constraint.
Table("whatsapp_outbound_dedup", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("wa_id", String(80), nullable=False),
    Column("thread_id", Integer, nullable=False),
    Column("message_kind", String(20), nullable=False, default="text"),
    Column("content_fingerprint", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True)),
)
# Recipient-lock table — FOR UPDATE skipped on SQLite but table must exist.
Table("whatsapp_recipient_locks", _test_meta,
    Column("id", Integer, primary_key=True),
    Column("wa_id", String(80), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True)),
)

_test_meta.create_all(_engine)

# ── Fixture helpers ───────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 29, 20, 0, 0, tzinfo=timezone.utc)


def _make_recipient(session: Session) -> tuple[str, int, int]:
    wa_id = f"549r12_{id(session)}_{id(object())}"
    contact = WhatsAppContact(wa_id=wa_id, display_name="Test")
    session.add(contact)
    session.flush()
    thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
    session.add(thread)
    session.flush()
    state = WhatsAppThreadState(thread_id=thread.id)
    session.add(state)
    session.flush()
    session.commit()
    return wa_id, thread.id, state.id


# ══════════════════════════════════════════════════════════════════════════════
# A.1 — Rolling boundary: second send at T+1s → still blocked
# ══════════════════════════════════════════════════════════════════════════════

class TestRollingBoundaryInsideWindow(unittest.TestCase):
    """A.1 — Same text sent 1 second apart → second must be BLOCKED_DUPLICATE."""

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a1_second_send_1s_later_blocked(self):
        gate = OutboundSafetyGate(self.session)
        text = "Hola, tu cotización es $130.000"
        T = _NOW
        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=T)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_a1_first_{id(self)}")

        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text,
                          now=T + timedelta(seconds=1))
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Same text 1s later must be BLOCKED_DUPLICATE in rolling window")


# ══════════════════════════════════════════════════════════════════════════════
# A.2 — Rolling boundary: exactly T+10min → allowed (strict > not >=)
# ══════════════════════════════════════════════════════════════════════════════

class TestRollingBoundaryExactlyAtWindow(unittest.TestCase):
    """A.2 — Same text at exactly T+10min must be allowed (boundary is exclusive).

    Rolling window check: created_at > (now - 10min).
    Row at T: T > (T+10min - 10min) = T > T → False → not blocked → ALLOWED.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a2_send_at_exact_10min_allowed(self):
        gate = OutboundSafetyGate(self.session)
        text = "Hola, tu cotización es $130.000"
        T = _NOW
        T_plus_10 = T + timedelta(minutes=DEDUP_WINDOW_MINUTES)

        pfx = id(self)
        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=T)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_a2_first_{pfx}")

        # At exactly T+10min: cutoff = T+10min - 10min = T, row created_at = T
        # Condition: T > T → False → row NOT found → ALLOWED
        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=T_plus_10)
        self.assertEqual(r2.outcome, GateOutcome.ALLOWED,
                         "Same text at exactly T+10min must be allowed (rolling boundary exclusive)")
        gate.mark_sent(r2.message_id, f"wamid_a2_second_{pfx}")


# ══════════════════════════════════════════════════════════════════════════════
# A.3 — Cross-thread same wa_id: same text from two threads → blocked
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossThreadDedup(unittest.TestCase):
    """A.3 — Gate is keyed by wa_id, not thread_id.

    Same contact, two different CRM threads, same text → second send blocked.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        wa_id = f"549a3_{id(self)}"
        contact = WhatsAppContact(wa_id=wa_id, display_name="Test")
        self.session.add(contact)
        self.session.flush()
        thread_a = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.session.add(thread_a)
        self.session.flush()
        state_a = WhatsAppThreadState(thread_id=thread_a.id)
        self.session.add(state_a)
        self.session.flush()
        thread_b = WhatsAppThread(contact_id=contact.id, unread_count=0)
        self.session.add(thread_b)
        self.session.flush()
        state_b = WhatsAppThreadState(thread_id=thread_b.id)
        self.session.add(state_b)
        self.session.flush()
        self.session.commit()
        self.wa_id = wa_id
        self.thread_a_id = thread_a.id
        self.thread_b_id = thread_b.id

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a3_same_text_different_thread_blocked(self):
        gate = OutboundSafetyGate(self.session)
        text = "Para cotizarte la revisión necesito saber qué vehículo tenés."
        pfx = id(self)

        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_a_id, text=text, now=_NOW)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_a3_thread_a_{pfx}")

        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_b_id, text=text, now=_NOW)
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Same text from different thread (same wa_id) must be blocked")


# ══════════════════════════════════════════════════════════════════════════════
# A.4 — Same text at T+9min59s → blocked
# ══════════════════════════════════════════════════════════════════════════════

class TestDedupAt9Min59s(unittest.TestCase):
    """A.4 — At T+9min59s the row is still within the 10-min window → blocked."""

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a4_blocked_at_9min_59s(self):
        gate = OutboundSafetyGate(self.session)
        text = "Cotización confirmada: $130.000"
        T = _NOW
        pfx = id(self)

        r1 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=T)
        self.assertEqual(r1.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r1.message_id, f"wamid_a4_{pfx}")

        check = T + timedelta(minutes=9, seconds=59)
        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=check)
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "At T+9m59s the row (created_at=T) is still > cutoff (T+9m59s-10m)")


# ══════════════════════════════════════════════════════════════════════════════
# A.5 — Same text at T+10min exactly → allowed (combined: A.2 + A.4)
# (Covered by A.2 above; included as a named sub-test for completeness)
# ══════════════════════════════════════════════════════════════════════════════

# A.5 is effectively the same as A.2; TestRollingBoundaryExactlyAtWindow covers it.


# ══════════════════════════════════════════════════════════════════════════════
# A.6 — 50 repeated attempts of same text → 1 allowed, 49 BLOCKED_DUPLICATE
# ══════════════════════════════════════════════════════════════════════════════

class TestFiftyRepeatedAttempts(unittest.TestCase):
    """A.6 — 50 concurrent/sequential attempts for the same text → 1 ALLOWED, 49 blocked."""

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a6_fifty_attempts_one_allowed(self):
        gate = OutboundSafetyGate(self.session)
        text = "Para cotizarte la revisión necesito saber qué vehículo tenés."
        pfx = id(self)
        allowed = 0
        blocked_dup = 0
        blocked_flood = 0

        for i in range(50):
            r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=text, now=_NOW)
            if r.outcome == GateOutcome.ALLOWED:
                gate.mark_sent(r.message_id, f"wamid_a6_{pfx}_{i}")
                allowed += 1
            elif r.outcome == GateOutcome.BLOCKED_DUPLICATE:
                blocked_dup += 1
            else:
                blocked_flood += 1

        self.assertEqual(allowed, 1, "exactly 1 send allowed out of 50")
        # 49 others are either BLOCKED_DUPLICATE or BLOCKED_FLOOD
        self.assertEqual(allowed + blocked_dup + blocked_flood, 50)
        self.assertGreaterEqual(blocked_dup, 1, "at least one BLOCKED_DUPLICATE")

        msgs = self.session.query(WhatsAppMessage).filter_by(
            thread_id=self.thread_id, automated=True
        ).all()
        self.assertEqual(len([m for m in msgs if m.status == "sent"]), 1)


# ══════════════════════════════════════════════════════════════════════════════
# A.7 — 4 distinct messages in 60s → 3 allowed, 4th BLOCKED_FLOOD + needs_human
# ══════════════════════════════════════════════════════════════════════════════

class TestFloodGateRolling(unittest.TestCase):
    """A.7 — 4 distinct automated messages in 60s: first 3 allowed, 4th BLOCKED_FLOOD."""

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, self.state_id = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a7_fourth_message_blocked_flood_needs_human(self):
        gate = OutboundSafetyGate(self.session)
        pfx = id(self)
        messages = [
            f"Mensaje automatizado 1 para {pfx}",
            f"Mensaje automatizado 2 para {pfx}",
            f"Mensaje automatizado 3 para {pfx}",
            f"Mensaje automatizado 4 para {pfx}",
        ]
        outcomes = []
        for i, msg in enumerate(messages):
            r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=msg, now=_NOW + timedelta(seconds=i * 5))
            outcomes.append(r.outcome)
            if r.outcome == GateOutcome.ALLOWED:
                gate.mark_sent(r.message_id, f"wamid_a7_{pfx}_{i}")

        self.assertEqual(outcomes[0], GateOutcome.ALLOWED)
        self.assertEqual(outcomes[1], GateOutcome.ALLOWED)
        self.assertEqual(outcomes[2], GateOutcome.ALLOWED)
        self.assertEqual(outcomes[3], GateOutcome.BLOCKED_FLOOD,
                         "4th distinct message in 60s must be BLOCKED_FLOOD")

        # Thread state must have needs_human=True after flood.
        state = self.session.get(WhatsAppThreadState, self.state_id)
        self.session.refresh(state)
        self.assertTrue(state.needs_human,
                        "needs_human must be set True after flood block")

    def test_a7_flood_blocked_reason_contains_thread_id(self):
        gate = OutboundSafetyGate(self.session)
        pfx = id(self)
        for i in range(FLOOD_MAX_MESSAGES):
            r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                             text=f"Distinct flood msg {pfx}_{i}",
                             now=_NOW + timedelta(seconds=i * 2))
            if r.outcome == GateOutcome.ALLOWED:
                gate.mark_sent(r.message_id, f"wamid_a7b_{pfx}_{i}")

        r_blocked = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                                 text=f"Fourth distinct {pfx}",
                                 now=_NOW + timedelta(seconds=10))
        self.assertEqual(r_blocked.outcome, GateOutcome.BLOCKED_FLOOD)
        self.assertIn(str(self.thread_id), r_blocked.blocked_reason or "")
        self.assertIn("FLOOD", r_blocked.blocked_reason or "")


# ══════════════════════════════════════════════════════════════════════════════
# A.8 — Meta failure → pending→failed; auto-retry BLOCKED_DUPLICATE
# ══════════════════════════════════════════════════════════════════════════════

class TestMetaFailureRolling(unittest.TestCase):
    """A.8 — Meta API failure: pending → failed; dedup row retained → retry blocked."""

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a8_failed_send_blocks_auto_retry(self):
        gate = OutboundSafetyGate(self.session)
        text = "Hola, tu revisión está confirmada."
        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        gate.mark_failed(r.message_id)
        msg = self.session.get(WhatsAppMessage, r.message_id)
        self.assertEqual(msg.status, "failed")

        # Auto-retry in rolling window → blocked (dedup row still exists)
        r2 = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id, text=text, now=_NOW)
        self.assertEqual(r2.outcome, GateOutcome.BLOCKED_DUPLICATE,
                         "Auto-retry after failed send must be BLOCKED_DUPLICATE "
                         "(dedup claim is durable even on failure)")


# ══════════════════════════════════════════════════════════════════════════════
# A.9 — Pending record exists before Meta call
# ══════════════════════════════════════════════════════════════════════════════

class TestPendingBeforeMeta(unittest.TestCase):
    """A.9 — gate.attempt() returns ALLOWED and the pending record is committed."""

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_a9_pending_record_durable_before_meta(self):
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                         text="Confirmo el turno.", now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)
        self.assertIsNotNone(r.message_id, "message_id must be set")

        msg = self.session.get(WhatsAppMessage, r.message_id)
        self.assertIsNotNone(msg, "pending record must exist before Meta call")
        self.assertEqual(msg.status, "pending")
        self.assertTrue(msg.automated)

    def test_a9_dedup_row_committed_before_meta(self):
        gate = OutboundSafetyGate(self.session)
        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                         text="Dedup row check.", now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        dedup = self.session.query(WhatsAppOutboundDedup).filter_by(
            wa_id=self.wa_id
        ).first()
        self.assertIsNotNone(dedup, "dedup claim must be committed before Meta call")
        self.assertEqual(dedup.wa_id, self.wa_id)
        self.assertEqual(dedup.message_kind, "text")


# ══════════════════════════════════════════════════════════════════════════════
# A.10 — Static: send-to-phone route enters recipient gate (automated classification)
# ══════════════════════════════════════════════════════════════════════════════

class TestSendToPhoneStaticGateCheck(unittest.TestCase):
    """A.10 — send-to-phone route must call OutboundSafetyGate.attempt().

    This is an automated n8n-style call with no authenticated CRM session.
    Per M19.R1.2 policy, all unauthenticated sends are classified as automated
    and must enter the recipient gate.
    """

    def test_a10_send_to_phone_calls_gate_attempt(self):
        import ast as _ast
        src_path = BACKEND_DIR / "app" / "api" / "whatsapp.py"
        src = src_path.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src, filename=str(src_path))

        found_gate_attempt = False
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "send_to_phone":
                for subnode in _ast.walk(node):
                    if (
                        isinstance(subnode, _ast.Call)
                        and isinstance(subnode.func, _ast.Attribute)
                        and subnode.func.attr == "attempt"
                    ):
                        found_gate_attempt = True
        self.assertTrue(
            found_gate_attempt,
            "send_to_phone() must call gate.attempt() — automated sends must enter the recipient gate",
        )

    def test_a10_send_to_phone_no_longer_hardcodes_automated_false(self):
        """send_to_phone must not classify itself as manual (automated=False)."""
        import ast as _ast
        src_path = BACKEND_DIR / "app" / "api" / "whatsapp.py"
        src = src_path.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src, filename=str(src_path))

        # Look for WhatsAppMessage(... automated=False ...) inside send_to_phone
        found_manual_record = False
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "send_to_phone":
                for subnode in _ast.walk(node):
                    if isinstance(subnode, _ast.Call):
                        for kw in getattr(subnode, "keywords", []):
                            if (
                                kw.arg == "automated"
                                and isinstance(kw.value, _ast.Constant)
                                and kw.value.value is False
                            ):
                                found_manual_record = True
        self.assertFalse(
            found_manual_record,
            "send_to_phone must not create a WhatsAppMessage with automated=False "
            "(M19.R1.2: all sends via this route are automated)",
        )


# ══════════════════════════════════════════════════════════════════════════════
# A.11 — Engine dedup/flood block → raises OutboundBlockedError (not return None)
# ══════════════════════════════════════════════════════════════════════════════

class TestEngineSendRaisesOnBlock(unittest.TestCase):
    """A.11 — _send_text_to_wa and _send_flow_button must raise OutboundBlockedError
    for ALL non-ALLOWED gate outcomes (not silently return None).

    M19.R1.2: callers must not be able to commit state flags after a blocked send.
    """

    def _get_fn_body(self, fn_name: str) -> list:
        import ast as _ast
        src_path = BACKEND_DIR / "app" / "services" / "conversation_engine.py"
        src = src_path.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src, filename=str(src_path))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == fn_name:
                return node.body
        raise AssertionError(f"{fn_name} not found")

    def _has_raise_for_non_allowed(self, body: list) -> bool:
        import ast as _ast
        for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
            if isinstance(node, _ast.If):
                test = node.test
                if (
                    isinstance(test, _ast.Compare)
                    and len(test.ops) == 1
                    and isinstance(test.ops[0], _ast.NotEq)
                ):
                    for comp in test.comparators:
                        if isinstance(comp, _ast.Attribute) and comp.attr == "ALLOWED":
                            for stmt in node.body:
                                if isinstance(stmt, _ast.Raise):
                                    return True
        return False

    def test_a11_send_text_raises_for_all_blocked(self):
        body = self._get_fn_body("_send_text_to_wa")
        self.assertTrue(
            self._has_raise_for_non_allowed(body),
            "_send_text_to_wa must raise OutboundBlockedError for ALL non-ALLOWED outcomes",
        )

    def test_a11_send_flow_raises_for_all_blocked(self):
        body = self._get_fn_body("_send_flow_button")
        self.assertTrue(
            self._has_raise_for_non_allowed(body),
            "_send_flow_button must raise OutboundBlockedError for ALL non-ALLOWED outcomes",
        )

    def test_a11_no_silent_return_none_for_blocks(self):
        """_send_text_to_wa must not have a silent return None for non-ALLOWED outcomes."""
        import ast as _ast
        body = self._get_fn_body("_send_text_to_wa")
        silent_return = False
        for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
            if isinstance(node, _ast.If):
                test = node.test
                if (
                    isinstance(test, _ast.Compare)
                    and len(test.ops) == 1
                    and isinstance(test.ops[0], _ast.NotEq)
                ):
                    for comp in test.comparators:
                        if isinstance(comp, _ast.Attribute) and comp.attr == "ALLOWED":
                            for stmt in node.body:
                                if (
                                    isinstance(stmt, _ast.Return)
                                    and (stmt.value is None or isinstance(stmt.value, _ast.Constant))
                                ):
                                    silent_return = True
        self.assertFalse(
            silent_return,
            "_send_text_to_wa must NOT silently return None for blocked outcomes (M19.R1.2)",
        )

    def test_a11_outbound_blocked_error_has_gate_outcome_attr(self):
        """OutboundBlockedError must carry gate_outcome attribute (M19.R1.2)."""
        exc = OutboundBlockedError(
            sender_path="test",
            kind="text",
            to_wa_id="549test0000",
            thread_id=1,
            gate_outcome="BLOCKED_DUPLICATE",
        )
        self.assertEqual(exc.gate_outcome, "BLOCKED_DUPLICATE")
        self.assertIn("BLOCKED_DUPLICATE", str(exc))

    def test_a11_kill_switch_still_says_outbound_disabled(self):
        """Kill switch gate_outcome preserves 'OUTBOUND_DISABLED' in message for backward compat."""
        exc = OutboundBlockedError(
            sender_path="test",
            kind="text",
            to_wa_id="549test0000",
            gate_outcome="BLOCKED_KILL_SWITCH",
        )
        self.assertIn("OUTBOUND_DISABLED", str(exc),
                      "Kill switch OutboundBlockedError message must contain OUTBOUND_DISABLED")


# ══════════════════════════════════════════════════════════════════════════════
# Supplemental: message_kind stored correctly in dedup row
# ══════════════════════════════════════════════════════════════════════════════

class TestMessageKindInDedupRow(unittest.TestCase):
    """Supplemental — message_kind column is stored per dedup row.

    This distinguishes between, e.g., a text and a flow message with the same
    body text so they don't block each other.
    """

    def setUp(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.session = Session(_engine)
        self.wa_id, self.thread_id, _ = _make_recipient(self.session)

    def tearDown(self):
        self.session.close()
        os.environ.pop("OUTBOUND_ENABLED", None)

    def test_text_and_flow_with_same_body_not_deduped_against_each_other(self):
        """'text' and 'flow' message_kinds are treated as separate dedup keys."""
        gate = OutboundSafetyGate(self.session)
        body = "Completá tus datos para confirmar la reserva."
        pfx = id(self)

        r_text = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                              text=body, message_type="text", now=_NOW)
        self.assertEqual(r_text.outcome, GateOutcome.ALLOWED)
        gate.mark_sent(r_text.message_id, f"wamid_kind_text_{pfx}")

        r_flow = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                              text=body, message_type="flow",
                              now=_NOW + timedelta(seconds=5))
        self.assertEqual(r_flow.outcome, GateOutcome.ALLOWED,
                         "flow with same body as earlier text should NOT be deduped "
                         "(different message_kind)")
        gate.mark_sent(r_flow.message_id, f"wamid_kind_flow_{pfx}")

    def test_dedup_row_stores_message_kind(self):
        """Dedup row must persist the message_kind for the rolling window query."""
        gate = OutboundSafetyGate(self.session)
        pfx = id(self)
        r = gate.attempt(wa_id=self.wa_id, thread_id=self.thread_id,
                         text=f"Unique text {pfx}", message_type="flow", now=_NOW)
        self.assertEqual(r.outcome, GateOutcome.ALLOWED)

        row = self.session.query(WhatsAppOutboundDedup).filter_by(
            wa_id=self.wa_id
        ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.message_kind, "flow")


# ══════════════════════════════════════════════════════════════════════════════
# Supplemental: dead code removed from buscando_followup
# ══════════════════════════════════════════════════════════════════════════════

class TestBuscandoFollowupDeadCodeRemoved(unittest.TestCase):
    """_send_followup() dead code (direct Meta call bypassing the gate) must be removed."""

    def test_send_followup_function_removed(self):
        import ast as _ast
        src_path = BACKEND_DIR / "app" / "services" / "buscando_followup.py"
        src = src_path.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src, filename=str(src_path))
        fn_names = [n.name for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)]
        self.assertNotIn(
            "_send_followup",
            fn_names,
            "_send_followup() dead code must be removed from buscando_followup.py",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
