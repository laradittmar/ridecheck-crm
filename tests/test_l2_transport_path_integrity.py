"""L2-TRANSPORT-OPS — Outbound path_id integrity tests.

Verifies that every authorized gate.attempt() call site passes a valid OutboundPathId.

L2-PATH-01  MANUAL_CRM is an authorized path (enum + registry)
L2-PATH-02  SYSTEM_NOTIFICATION is an authorized path (enum + registry)
L2-PATH-03  CE_TEXT is an authorized path (enum + registry)
L2-PATH-04  CE_FLOW is an authorized path (enum + registry)
L2-PATH-05  None path_id → gate blocks with OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE
L2-PATH-06  LEGACY_N8N_AI_PIPELINE → gate blocks with LEGACY_SENDER_REACHED
L2-PATH-07  send_thread_text passes MANUAL_CRM to gate.attempt()
L2-PATH-08  buscando_followup passes SYSTEM_NOTIFICATION to gate.attempt()
L2-PATH-09  quote_followup passes SYSTEM_NOTIFICATION to gate.attempt()

All tests offline: SQLite in-memory or pure mock, no containers, no Meta API.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if not BACKEND_DIR.exists():
    BACKEND_DIR = ROOT_DIR
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── JSONB → JSON patch (before any app.models import) ─────────────────────────
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = _sa.JSON  # type: ignore[attr-defined]
_pg_json.JSONB = _sa.JSON     # type: ignore[attr-defined]

for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if "psycopg2" not in sys.modules:
    _pg = types.ModuleType("psycopg2")
    _pg.extensions = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2"] = _pg
    sys.modules["psycopg2.extensions"] = _pg.extensions

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models as _app_models
_Base = _app_models.Base

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
_Base.metadata.create_all(_engine)

from app.services.outbound_path_registry import AUTHORIZED_PATHS, LEGACY_PATHS, OutboundPathId
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate


def _fresh_db() -> Session:
    _Base.metadata.drop_all(_engine)
    _Base.metadata.create_all(_engine)
    return _SessionLocal()


def _seed_contact_thread(db: Session) -> tuple[_app_models.WhatsAppContact, _app_models.WhatsAppThread]:
    contact = _app_models.WhatsAppContact(wa_id="54911TEST0001", display_name="Test User")
    db.add(contact)
    db.flush()
    thread = _app_models.WhatsAppThread(contact_id=contact.id)
    db.add(thread)
    db.flush()
    return contact, thread


# ──────────────────────────────────────────────────────────────────────────────
# PATH-01 to PATH-04: Authorized path registry membership
# ──────────────────────────────────────────────────────────────────────────────

class TestAuthorizedPathRegistry(unittest.TestCase):

    def test_l2_path_01_manual_crm_authorized(self):
        """L2-PATH-01: MANUAL_CRM is in the authorized path registry."""
        self.assertIn(OutboundPathId.MANUAL_CRM, AUTHORIZED_PATHS,
                      "MANUAL_CRM must be in AUTHORIZED_PATHS")

    def test_l2_path_02_system_notification_authorized(self):
        """L2-PATH-02: SYSTEM_NOTIFICATION is in the authorized path registry."""
        self.assertIn(OutboundPathId.SYSTEM_NOTIFICATION, AUTHORIZED_PATHS,
                      "SYSTEM_NOTIFICATION must be in AUTHORIZED_PATHS")

    def test_l2_path_03_ce_text_authorized(self):
        """L2-PATH-03: CE_TEXT is in the authorized path registry."""
        self.assertIn(OutboundPathId.CE_TEXT, AUTHORIZED_PATHS,
                      "CE_TEXT must be in AUTHORIZED_PATHS")

    def test_l2_path_04_ce_flow_authorized(self):
        """L2-PATH-04: CE_FLOW is in the authorized path registry."""
        self.assertIn(OutboundPathId.CE_FLOW, AUTHORIZED_PATHS,
                      "CE_FLOW must be in AUTHORIZED_PATHS")


# ──────────────────────────────────────────────────────────────────────────────
# PATH-05, PATH-06: Gate blocks unauthorized paths
# ──────────────────────────────────────────────────────────────────────────────

class TestGateBlocksUnauthorizedPaths(unittest.TestCase):

    def _gate(self) -> tuple[OutboundSafetyGate, Session]:
        db = _fresh_db()
        _, thread = _seed_contact_thread(db)
        db.commit()
        gate = OutboundSafetyGate(db)
        return gate, db, thread.id

    def _run_path_check(self, path_id) -> object:
        gate, db, thread_id = self._gate()
        now = datetime.now(timezone.utc)
        from app.services.outbound_safety_gate import _content_fingerprint
        fp = _content_fingerprint("test message")
        result = gate._check_authorized_path(
            wa_id="54911TEST0001",
            thread_id=thread_id,
            text="test message",
            fp=fp,
            message_type="text",
            now=now,
            path_id=path_id,
            deployment_id=None,
        )
        db.close()
        return result

    def test_l2_path_05_none_path_id_blocked(self):
        """L2-PATH-05: None path_id → gate returns BLOCKED_UNAUTHORIZED_PATH."""
        result = self._run_path_check(None)
        self.assertIsNotNone(result, "Gate must block None path_id")
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)

    def test_l2_path_05b_none_path_creates_security_event(self):
        """L2-PATH-05b: None path_id → SecurityEvent with OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE."""
        db = _fresh_db()
        _, thread = _seed_contact_thread(db)
        db.commit()
        gate = OutboundSafetyGate(db)
        now = datetime.now(timezone.utc)
        from app.services.outbound_safety_gate import _content_fingerprint
        fp = _content_fingerprint("test message")
        gate._check_authorized_path(
            wa_id="54911TEST0001",
            thread_id=thread.id,
            text="test message",
            fp=fp,
            message_type="text",
            now=now,
            path_id=None,
            deployment_id=None,
        )
        events = db.execute(
            select(_app_models.SecurityEvent)
            .where(_app_models.SecurityEvent.event_type == "OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE")
        ).scalars().all()
        self.assertEqual(len(events), 1, "Exactly one SecurityEvent expected for None path_id")
        self.assertEqual(events[0].severity, "BLOCKER")
        db.close()

    def test_l2_path_06_legacy_path_blocked(self):
        """L2-PATH-06: LEGACY_N8N_AI_PIPELINE → gate returns BLOCKED_UNAUTHORIZED_PATH."""
        result = self._run_path_check(OutboundPathId.LEGACY_N8N_AI_PIPELINE.value)
        self.assertIsNotNone(result, "Gate must block legacy path_id")
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)

    def test_l2_path_06b_legacy_path_is_in_legacy_set(self):
        """L2-PATH-06b: LEGACY_N8N_AI_PIPELINE is in LEGACY_PATHS (not AUTHORIZED_PATHS)."""
        self.assertIn(OutboundPathId.LEGACY_N8N_AI_PIPELINE.value, LEGACY_PATHS)
        self.assertNotIn(OutboundPathId.LEGACY_N8N_AI_PIPELINE, AUTHORIZED_PATHS)


# ──────────────────────────────────────────────────────────────────────────────
# PATH-07: send_thread_text passes MANUAL_CRM to gate.attempt()
# ──────────────────────────────────────────────────────────────────────────────

class TestSendThreadTextPathId(unittest.TestCase):

    def test_l2_path_07_send_thread_text_uses_manual_crm(self):
        """L2-PATH-07: whatsapp.py send_thread_text passes MANUAL_CRM to gate.attempt()."""
        db = _fresh_db()
        contact, thread = _seed_contact_thread(db)
        db.commit()

        from app.api.whatsapp import send_thread_text
        from app.schemas.whatsapp_api import WhatsAppSendTextIn

        captured_path_id = []

        mock_result = MagicMock()
        mock_result.outcome = GateOutcome.BLOCKED_KILL_SWITCH
        mock_result.message_id = 999

        def _fake_attempt(**kwargs):
            captured_path_id.append(kwargs.get("path_id"))
            return mock_result

        with patch("app.services.outbound_safety_gate.OutboundSafetyGate.attempt",
                   side_effect=_fake_attempt):
            from fastapi import HTTPException
            try:
                send_thread_text(
                    thread_id=thread.id,
                    payload=WhatsAppSendTextIn(text="Hola test"),
                    db=db,
                )
            except HTTPException:
                pass  # expected — gate blocked

        self.assertEqual(len(captured_path_id), 1,
                         "gate.attempt() must have been called exactly once")
        self.assertEqual(captured_path_id[0], OutboundPathId.MANUAL_CRM.value,
                         f"Expected MANUAL_CRM, got {captured_path_id[0]!r}")
        db.close()


# ──────────────────────────────────────────────────────────────────────────────
# PATH-08: buscando_followup passes SYSTEM_NOTIFICATION to gate.attempt()
# ──────────────────────────────────────────────────────────────────────────────

class TestBuscandoFollowupPathId(unittest.TestCase):

    def test_l2_path_08_buscando_followup_uses_system_notification(self):
        """L2-PATH-08: buscando_followup _run_check passes SYSTEM_NOTIFICATION to gate.attempt()."""
        import inspect
        import app.services.buscando_followup as _bf
        source = inspect.getsource(_bf)
        self.assertIn("OutboundPathId.SYSTEM_NOTIFICATION.value", source,
                      "buscando_followup must pass SYSTEM_NOTIFICATION to gate.attempt()")

    def test_l2_path_08b_buscando_followup_path_id_adjacent_to_gate_call(self):
        """L2-PATH-08b: SYSTEM_NOTIFICATION appears in the same gate.attempt() block."""
        src = (ROOT_DIR / "backend" / "app" / "services" / "buscando_followup.py").read_text()
        import re
        # Find gate.attempt( call and look within 3 lines for path_id
        for m in re.finditer(r"gate\.attempt\(", src):
            snippet = src[m.start():m.start() + 300]
            self.assertIn("path_id=", snippet,
                          f"gate.attempt() in buscando_followup must include path_id= kwarg\n{snippet[:150]!r}")
            self.assertIn("SYSTEM_NOTIFICATION", snippet,
                          f"buscando_followup gate.attempt() must use SYSTEM_NOTIFICATION\n{snippet[:150]!r}")


# ──────────────────────────────────────────────────────────────────────────────
# PATH-09: quote_followup passes SYSTEM_NOTIFICATION to gate.attempt()
# ──────────────────────────────────────────────────────────────────────────────

class TestQuoteFollowupPathId(unittest.TestCase):

    def test_l2_path_09_quote_followup_uses_system_notification(self):
        """L2-PATH-09: quote_followup passes SYSTEM_NOTIFICATION to gate.attempt()."""
        import inspect
        import app.services.quote_followup as _qf
        source = inspect.getsource(_qf)
        self.assertIn("OutboundPathId.SYSTEM_NOTIFICATION.value", source,
                      "quote_followup must pass SYSTEM_NOTIFICATION to gate.attempt()")


# ──────────────────────────────────────────────────────────────────────────────
# PATH-07b, 08b: Source inspection for additional call sites
# ──────────────────────────────────────────────────────────────────────────────

class TestSourceInspectionAllCallSites(unittest.TestCase):
    """Static source inspection confirms every gate.attempt() call site has path_id."""

    def _load_source(self, relative_path: str) -> str:
        path = ROOT_DIR / "backend" / "app" / relative_path
        return path.read_text()

    def test_l2_path_07b_whatsapp_api_text_has_manual_crm(self):
        """whatsapp.py send_thread_text: path_id=OutboundPathId.MANUAL_CRM.value present."""
        src = self._load_source("api/whatsapp.py")
        self.assertIn("OutboundPathId.MANUAL_CRM.value", src)

    def test_l2_path_07c_whatsapp_api_system_notif_has_system_notification(self):
        """whatsapp.py send_to_phone: path_id=OutboundPathId.SYSTEM_NOTIFICATION.value present."""
        src = self._load_source("api/whatsapp.py")
        self.assertIn("OutboundPathId.SYSTEM_NOTIFICATION.value", src)

    def test_l2_path_07d_store_outbound_and_send_accepts_path_id(self):
        """_store_outbound_and_send helper accepts path_id and passes it to gate."""
        src = self._load_source("api/whatsapp.py")
        self.assertIn("path_id: str = \"\"", src,
                      "_store_outbound_and_send must declare path_id param")
        # Callers must pass path_id=OutboundPathId.MANUAL_CRM.value
        # (at least 3 occurrences: interactive, list, flow)
        # L4.7W1-F4: send-text is now attributed by CALLER identity — a
        # machine-authenticated n8n send is CE_FLOW, an operator's is MANUAL_CRM — so the
        # literal count dropped by one. What matters is that every send declares a path.
        count = (src.count("path_id=OutboundPathId.MANUAL_CRM.value")
                 + src.count("path_id=_caller_path_id(request)"))
        self.assertGreaterEqual(count, 4,
                                f"Expected ≥4 attributed sends in whatsapp.py, found {count}")
        self.assertIn("def _caller_path_id", src,
                      "caller-derived attribution must exist")

    def test_l2_path_08b_buscando_followup_has_system_notification(self):
        """buscando_followup.py: path_id=OutboundPathId.SYSTEM_NOTIFICATION.value present."""
        src = self._load_source("services/buscando_followup.py")
        self.assertIn("OutboundPathId.SYSTEM_NOTIFICATION.value", src)

    def test_l2_path_09b_quote_followup_has_system_notification(self):
        """quote_followup.py: path_id=OutboundPathId.SYSTEM_NOTIFICATION.value present."""
        src = self._load_source("services/quote_followup.py")
        self.assertIn("OutboundPathId.SYSTEM_NOTIFICATION.value", src)

    def test_l2_path_10_ce_text_has_ce_text(self):
        """conversation_engine.py _send_text_to_wa: path_id=CE_TEXT present."""
        src = self._load_source("services/conversation_engine.py")
        self.assertIn("OutboundPathId.CE_TEXT.value", src)

    def test_l2_path_11_ce_flow_has_ce_flow(self):
        """conversation_engine.py _send_flow_button: path_id=CE_FLOW present."""
        src = self._load_source("services/conversation_engine.py")
        self.assertIn("OutboundPathId.CE_FLOW.value", src)

    def test_l2_path_12_no_bare_gate_attempt_without_path_id(self):
        """No gate.attempt() call in whatsapp.py uses positional args only (no path_id)."""
        src = self._load_source("api/whatsapp.py")
        import re
        # Find all gate.attempt( calls and verify each has path_id= in its vicinity
        calls = [(m.start(), m.group()) for m in re.finditer(r"gate\.attempt\(", src)]
        # Also check gate_result = gate.attempt( style
        attempt_calls = [(m.start(), m.group()) for m in re.finditer(r"gate(?:_result\s*=\s*gate|)\s*\.attempt\s*\(", src)]
        for pos, match in attempt_calls:
            snippet = src[pos:pos + 300]
            self.assertIn("path_id=", snippet,
                          f"gate.attempt() at position {pos} is missing path_id=\nSnippet: {snippet[:120]!r}")


if __name__ == "__main__":
    unittest.main()
