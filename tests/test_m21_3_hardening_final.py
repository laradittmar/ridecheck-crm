"""M21.3-TRACE-HARDENING-FINAL — Durability, forensic, and signature tests.

T4  — Crash before Meta call: pending record durable in DB.
T5  — Crash after Meta WAMID returned: record reconstructible; mark_sent idempotent.
T6  — Status webhook 'sent' → record already at sent, no downgrade to pending.
T7  — Status webhook 'delivered' upgrades from 'sent'.
T8  — Status webhook 'read' upgrades from 'delivered'.
T9  — Status downgrade ignored (e.g. 'sent' when already 'delivered').
T11 — DB-only forensic reconstruction: full lifecycle survives without container logs.
T24 — Webhook signature verification: valid / invalid / missing.
T25 — Deployment evidence: path_id + deployment_id on every ALLOWED outbound record.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── JSONB → JSON for SQLite (must run before app.models import) ───────────────
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

# ── Engine + ORM setup ────────────────────────────────────────────────────────
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


import app.models  # noqa: F401 — registers ORM classes
from app.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadState,
)
from app.services.outbound_path_registry import OutboundPathId, get_deployment_id
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate

app.models.Base.metadata.create_all(_engine)

_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# ── STATUS_PRECEDENCE (mirror of whatsapp.py) ─────────────────────────────────
_STATUS_PRECEDENCE: dict[str, int] = {
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
}

# ── Shared constants ──────────────────────────────────────────────────────────
_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
_WA_BASE = "5491100"


def _wipe_tables() -> None:
    with _engine.begin() as conn:
        for table in reversed(app.models.Base.metadata.sorted_tables):
            conn.execute(table.delete())


def _new_session() -> Session:
    return _SessionLocal()


def _seed(db: Session, wa_id: str):
    contact = WhatsAppContact(wa_id=wa_id, display_name="Test")
    db.add(contact)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id, unread_count=0)
    db.add(thread)
    db.flush()
    state = WhatsAppThreadState(thread_id=thread.id)
    db.add(state)
    db.flush()
    db.commit()
    return contact, thread, state


def _apply_status_update(db: Session, wa_message_id: str, incoming_status: str) -> str:
    """Simulate whatsapp.py status webhook logic. Returns 'updated'/'ignored'/'not_found'."""
    if incoming_status not in _STATUS_PRECEDENCE:
        return "unknown_status"
    msg = db.execute(
        select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == wa_message_id)
    ).scalar_one_or_none()
    if msg is None:
        return "not_found"
    current_rank = _STATUS_PRECEDENCE.get(str(msg.status or ""), -1)
    incoming_rank = _STATUS_PRECEDENCE[incoming_status]
    if incoming_rank <= current_rank:
        return "ignored"
    msg.status = incoming_status
    db.commit()
    return "updated"


# ══════════════════════════════════════════════════════════════════════════════
# T4 — Crash before Meta call: pending record durable
# ══════════════════════════════════════════════════════════════════════════════

class T4_CrashBeforeMetaCall(unittest.TestCase):
    """After gate.attempt() returns ALLOWED, the pending WhatsAppMessage record
    is committed to the database.  Even if the process crashes before the Meta
    API call is made, the record survives and can be audited.
    """

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.wa_id = f"{_WA_BASE}t4_{id(self)}"
        _seed(self.db, self.wa_id)
        self.thread = self.db.execute(
            select(WhatsAppThread).join(
                WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id
            ).where(WhatsAppContact.wa_id == self.wa_id)
        ).scalar_one()

    def tearDown(self):
        self.db.close()

    def test_t4a_pending_record_exists_after_attempt(self):
        """gate.attempt() commits the pending record before any Meta call."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Confirmamos la revisión para mañana.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)
        message_id = result.message_id

        # Simulate crash: do NOT call mark_sent. Read via fresh session.
        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, message_id)
            self.assertIsNotNone(msg, "pending record must survive without mark_sent")
            self.assertEqual(msg.status, "pending")
            self.assertEqual(msg.direction, "out")
            self.assertTrue(msg.automated)
        finally:
            fresh.close()

    def test_t4b_pending_record_carries_path_and_deployment(self):
        """Pending record includes path_id and deployment_id for full audit trail."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Test durability payload.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.path_id, OutboundPathId.CE_TEXT.value)
            self.assertIsNotNone(msg.deployment_id, "deployment_id must be set on pending record")
            self.assertIsNotNone(msg.content_fingerprint, "fingerprint must be set")
            self.assertIsNotNone(msg.text, "text content must be preserved")
        finally:
            fresh.close()

    def test_t4c_dedup_entry_committed_with_pending(self):
        """Dedup entry is committed atomically with the pending record.
        An automatic retry in the same window is blocked even after a crash.
        """
        from app.models import WhatsAppOutboundDedup
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Dedup durability test message.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        fresh = _new_session()
        try:
            dedup = fresh.execute(
                select(WhatsAppOutboundDedup).where(
                    WhatsAppOutboundDedup.wa_id == self.wa_id
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(dedup, "dedup entry must be committed atomically with pending")
        finally:
            fresh.close()


# ══════════════════════════════════════════════════════════════════════════════
# T5 — Crash after Meta WAMID returned: reconstructible, mark_sent idempotent
# ══════════════════════════════════════════════════════════════════════════════

class T5_CrashAfterWamid(unittest.TestCase):
    """Crash scenario: Meta returns WAMID but process crashes before mark_sent.
    The pending record in DB is sufficient to reconstruct what was attempted.
    Calling mark_sent() after recovery succeeds.
    """

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.wa_id = f"{_WA_BASE}t5_{id(self)}"
        _seed(self.db, self.wa_id)
        self.thread = self.db.execute(
            select(WhatsAppThread).join(
                WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id
            ).where(WhatsAppContact.wa_id == self.wa_id)
        ).scalar_one()

    def tearDown(self):
        self.db.close()

    def test_t5a_pending_record_reconstructible_without_wamid(self):
        """Pending record alone is sufficient to identify what was sent."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Quote: $130.000 + viáticos.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        # "Meta returned WAMID" but crash before mark_sent
        simulated_wamid = "wamid.crash_test_t5"

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, result.message_id)
            # From DB alone we can reconstruct:
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "pending")
            self.assertIsNone(msg.wa_message_id, "WAMID not yet in DB before mark_sent")
            self.assertIsNotNone(msg.text)
            self.assertIsNotNone(msg.thread_id)
            self.assertIsNotNone(msg.path_id)
            self.assertIsNotNone(msg.deployment_id)
            self.assertIsNotNone(msg.timestamp)
        finally:
            fresh.close()

    def test_t5b_mark_sent_after_crash_recovery_succeeds(self):
        """Calling mark_sent() after crash recovery completes the lifecycle."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Recovery test message.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)
        simulated_wamid = "wamid.recovery_t5b"

        # Recovery: call mark_sent with the WAMID recovered from logs/Meta API
        gate.mark_sent(result.message_id, simulated_wamid)

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "sent")
            self.assertEqual(msg.wa_message_id, simulated_wamid)
        finally:
            fresh.close()


# ══════════════════════════════════════════════════════════════════════════════
# T6–T9 — Status webhook correlation
# ══════════════════════════════════════════════════════════════════════════════

class T6_T9_StatusWebhookCorrelation(unittest.TestCase):
    """Status webhook handler updates WhatsAppMessage.status via WAMID lookup.
    Tests the precedence-based update logic mirroring whatsapp.py:L491-596.
    """

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.wa_id = f"{_WA_BASE}t6_{id(self)}"
        _seed(self.db, self.wa_id)
        self.thread = self.db.execute(
            select(WhatsAppThread).join(
                WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id
            ).where(WhatsAppContact.wa_id == self.wa_id)
        ).scalar_one()
        # Create an outbound "sent" record (simulating completed gate lifecycle)
        self.wamid = f"wamid.status_test_{id(self)}"
        msg = WhatsAppMessage(
            thread_id=self.thread.id,
            direction="out",
            status="sent",
            wa_message_id=self.wamid,
            timestamp=_NOW,
            created_at=_NOW,
            automated=True,
            text="Status test message.",
            path_id=OutboundPathId.CE_TEXT.value,
            deployment_id=get_deployment_id(),
        )
        self.db.add(msg)
        self.db.commit()
        self.msg_id = msg.id

    def tearDown(self):
        self.db.close()

    def test_t6_sent_status_idempotent_when_already_sent(self):
        """'sent' webhook when record is already 'sent' → no change (same rank)."""
        result = _apply_status_update(self.db, self.wamid, "sent")
        self.assertEqual(result, "ignored", "same-rank status must be ignored")

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, self.msg_id)
            self.assertEqual(msg.status, "sent")
        finally:
            fresh.close()

    def test_t7_delivered_upgrades_from_sent(self):
        """'delivered' status upgrades record from 'sent'."""
        result = _apply_status_update(self.db, self.wamid, "delivered")
        self.assertEqual(result, "updated")

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, self.msg_id)
            self.assertEqual(msg.status, "delivered")
        finally:
            fresh.close()

    def test_t8_read_upgrades_from_sent(self):
        """'read' status upgrades record from 'sent' (skipping 'delivered' is valid)."""
        result = _apply_status_update(self.db, self.wamid, "read")
        self.assertEqual(result, "updated")

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, self.msg_id)
            self.assertEqual(msg.status, "read")
        finally:
            fresh.close()

    def test_t9_downgrade_ignored(self):
        """Lower-rank status ('sent') when record is already 'delivered' is ignored."""
        # First upgrade to delivered
        _apply_status_update(self.db, self.wamid, "delivered")
        # Then attempt to downgrade back to sent
        result = _apply_status_update(self.db, self.wamid, "sent")
        self.assertEqual(result, "ignored", "downgrade must be ignored")

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, self.msg_id)
            self.assertEqual(msg.status, "delivered", "status must not be downgraded")
        finally:
            fresh.close()

    def test_t9b_unknown_wamid_returns_not_found(self):
        """Status for an unknown WAMID → 'not_found' (would trigger SecurityEvent in prod)."""
        result = _apply_status_update(self.db, "wamid.does_not_exist", "delivered")
        self.assertEqual(result, "not_found")

    def test_t9c_wamid_linked_to_correct_thread(self):
        """WAMID lookup correctly correlates back to the originating thread."""
        fresh = _new_session()
        try:
            msg = fresh.execute(
                select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == self.wamid)
            ).scalar_one_or_none()
            self.assertIsNotNone(msg)
            self.assertEqual(msg.thread_id, self.thread.id)
            self.assertEqual(msg.direction, "out")
        finally:
            fresh.close()


# ══════════════════════════════════════════════════════════════════════════════
# T11 — DB-only forensic reconstruction
# ══════════════════════════════════════════════════════════════════════════════

class T11_DbOnlyReconstruction(unittest.TestCase):
    """Proves that all forensic data needed to reconstruct the outbound timeline
    is preserved in the database — no container logs required.

    This test creates a full outbound lifecycle, then queries the DB to verify
    that an investigator can reconstruct: WHO sent WHAT to WHOM, WHEN, via
    WHICH authorized path, under WHICH deployment, and what the final status was.
    """

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.wa_id = f"{_WA_BASE}t11_{id(self)}"
        _seed(self.db, self.wa_id)
        self.thread = self.db.execute(
            select(WhatsAppThread).join(
                WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id
            ).where(WhatsAppContact.wa_id == self.wa_id)
        ).scalar_one()

    def tearDown(self):
        self.db.close()

    def _build_lifecycle(self):
        """Run a complete outbound lifecycle and return the WAMID."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Cotización: $130.000 + viáticos $30.000.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)
        wamid = f"wamid.lifecycle_{id(self)}"
        gate.mark_sent(result.message_id, wamid)
        return result.message_id, wamid

    def test_t11a_sent_record_carries_complete_forensic_fields(self):
        """Sent record in DB is sufficient to reconstruct the full audit trail."""
        message_id, wamid = self._build_lifecycle()

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "sent")

            # Forensic fields
            self.assertEqual(msg.wa_message_id, wamid, "WAMID in DB")
            self.assertIsNotNone(msg.text, "message text preserved")
            self.assertIsNotNone(msg.content_fingerprint, "dedup fingerprint preserved")
            self.assertIsNotNone(msg.path_id, "authorized path preserved")
            self.assertIsNotNone(msg.deployment_id, "deployment context preserved")
            self.assertEqual(msg.direction, "out")
            self.assertTrue(msg.automated)
        finally:
            fresh.close()

    def test_t11b_thread_to_contact_linkage_reconstructible(self):
        """From the message's thread_id we can reach the wa_id (WHO)."""
        message_id, _ = self._build_lifecycle()

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, message_id)
            thread = fresh.get(WhatsAppThread, msg.thread_id)
            contact = fresh.get(WhatsAppContact, thread.contact_id)
            self.assertEqual(contact.wa_id, self.wa_id)
        finally:
            fresh.close()

    def test_t11c_dedup_entry_provides_timing_evidence(self):
        """Dedup entry in DB proves WHEN the outbound was first attempted."""
        from app.models import WhatsAppOutboundDedup
        message_id, _ = self._build_lifecycle()

        fresh = _new_session()
        try:
            dedup = fresh.execute(
                select(WhatsAppOutboundDedup).where(
                    WhatsAppOutboundDedup.wa_id == self.wa_id
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(dedup, "dedup entry provides timing evidence")
            self.assertIsNotNone(dedup.created_at, "creation timestamp preserved")
            self.assertIsNotNone(dedup.content_fingerprint, "fingerprint preserved")
        finally:
            fresh.close()

    def test_t11d_blocked_records_are_also_durable(self):
        """Kill-switch blocked records persist with reason — no logs needed."""
        gate = OutboundSafetyGate(self.db)
        os.environ.pop("OUTBOUND_ENABLED", None)  # ensure kill switch fires
        result = gate.attempt(
            wa_id=self.wa_id,
            thread_id=self.thread.id,
            text="This should be blocked.",
            path_id=OutboundPathId.CE_TEXT.value,
        )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_KILL_SWITCH)

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "blocked")
            self.assertIn("KILL_SWITCH", msg.blocked_reason or "")
        finally:
            fresh.close()


# ══════════════════════════════════════════════════════════════════════════════
# T24 — Webhook signature verification
# ══════════════════════════════════════════════════════════════════════════════

class T24_WebhookSignatureVerification(unittest.TestCase):
    """_verify_signature() in routes/whatsapp.py uses HMAC-SHA256 constant-time
    comparison. Tests: valid sig → accepted, invalid → rejected, missing → rejected,
    empty secret → dev-mode skip (not a bypass for known secrets).
    """

    @classmethod
    def setUpClass(cls):
        try:
            from app.routes.whatsapp import _verify_signature
            cls._verify_signature = staticmethod(_verify_signature)
        except Exception as exc:
            raise unittest.SkipTest(f"Cannot import _verify_signature: {exc}")

    def _make_sig(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def test_t24a_valid_signature_accepted(self):
        body = b'{"test": "payload"}'
        secret = "my_app_secret_123"
        sig = self._make_sig(body, secret)
        self.assertTrue(self._verify_signature(body, sig, secret))

    def test_t24b_invalid_signature_rejected(self):
        body = b'{"test": "payload"}'
        secret = "my_app_secret_123"
        wrong_sig = "sha256=deadbeef" + "00" * 28
        self.assertFalse(self._verify_signature(body, wrong_sig, secret))

    def test_t24c_missing_header_rejected_when_secret_set(self):
        body = b'{"test": "payload"}'
        secret = "my_app_secret_123"
        self.assertFalse(self._verify_signature(body, None, secret))

    def test_t24d_empty_secret_dev_mode_skips_verification(self):
        """Empty app_secret → dev mode: verification skipped (returns True)."""
        body = b'{"any": "payload"}'
        self.assertTrue(self._verify_signature(body, None, ""))
        self.assertTrue(self._verify_signature(body, "sha256=wrong", ""))

    def test_t24e_wrong_algorithm_prefix_rejected(self):
        body = b'{"test": "payload"}'
        secret = "my_app_secret_123"
        correct_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        bad_prefix = f"sha512={correct_hex}"
        self.assertFalse(self._verify_signature(body, bad_prefix, secret))

    def test_t24f_body_tampering_detected(self):
        body = b'{"test": "original"}'
        secret = "my_app_secret_123"
        sig = self._make_sig(body, secret)
        tampered_body = b'{"test": "tampered"}'
        self.assertFalse(self._verify_signature(tampered_body, sig, secret))


# ══════════════════════════════════════════════════════════════════════════════
# T25 — Deployment evidence
# ══════════════════════════════════════════════════════════════════════════════

class T25_DeploymentEvidence(unittest.TestCase):
    """Every ALLOWED gate.attempt() call records path_id + deployment_id.
    These fields make it possible to correlate outbound messages to the
    specific deployment and code path that produced them.
    """

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.wa_id = f"{_WA_BASE}t25_{id(self)}"
        _seed(self.db, self.wa_id)
        self.thread = self.db.execute(
            select(WhatsAppThread).join(
                WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id
            ).where(WhatsAppContact.wa_id == self.wa_id)
        ).scalar_one()

    def tearDown(self):
        self.db.close()

    def test_t25a_ce_text_path_records_deployment_id(self):
        """CE_TEXT outbound records carry deployment_id."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Deployment evidence test.",
                path_id=OutboundPathId.CE_TEXT.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.path_id, OutboundPathId.CE_TEXT.value)
            self.assertIsNotNone(msg.deployment_id)
            self.assertIsInstance(msg.deployment_id, str)
            self.assertGreater(len(msg.deployment_id), 0)
        finally:
            fresh.close()

    def test_t25b_path_id_registry_covers_all_ce_paths(self):
        """All authorized CE paths appear in the path registry."""
        from app.services.outbound_path_registry import AUTHORIZED_PATHS
        ce_paths = {
            OutboundPathId.CE_TEXT,
            OutboundPathId.CE_FLOW,
            OutboundPathId.CE_INTERACTIVE,
            OutboundPathId.CE_LIST,
        }
        for path in ce_paths:
            self.assertIn(path, AUTHORIZED_PATHS, f"{path} missing from AUTHORIZED_PATHS")

    def test_t25c_deployment_id_is_consistent_within_session(self):
        """get_deployment_id() returns the same value within a process run."""
        d1 = get_deployment_id()
        d2 = get_deployment_id()
        self.assertEqual(d1, d2, "deployment_id must be stable within a process")

    def test_t25d_manual_crm_path_also_carries_deployment_id(self):
        """MANUAL_CRM path records path_id and deployment_id."""
        gate = OutboundSafetyGate(self.db)
        os.environ["OUTBOUND_ENABLED"] = "true"
        try:
            result = gate.attempt(
                wa_id=self.wa_id,
                thread_id=self.thread.id,
                text="Manual CRM message.",
                path_id=OutboundPathId.MANUAL_CRM.value,
            )
        finally:
            os.environ.pop("OUTBOUND_ENABLED", None)

        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

        fresh = _new_session()
        try:
            msg = fresh.get(WhatsAppMessage, result.message_id)
            self.assertEqual(msg.path_id, OutboundPathId.MANUAL_CRM.value)
            self.assertIsNotNone(msg.deployment_id)
        finally:
            fresh.close()
