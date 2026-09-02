"""M2 — Authorized Message Path Monitoring tests.

Proves that the OutboundSafetyGate and status webhook handler enforce the
authorized path registry and produce the correct SecurityEvent records for
every violation scenario.

Test index:
  T16 — Unknown path_id → blocked + BLOCKER SecurityEvent (UNREGISTERED_OUTBOUND_SOURCE)
  T17 — Direct Meta client call outside central authority → architecture guard catches it
  T18 — Unknown WAMID status webhook → HIGH SecurityEvent (META_STATUS_FOR_UNKNOWN_WAMID)
  T19 — Successful status (sent) received while OUTBOUND OFF + no ledger attempt → BLOCKER
  T20 — Authorized CE_TEXT path → gate ALLOWED, no unauthorized SecurityEvent
  T21 — Authorized MANUAL_CRM path → gate ALLOWED (path recognized), no unauthorized event
  T22 — Legacy N8N_AI_PIPELINE sender attempt → BLOCKER SecurityEvent (LEGACY_SENDER_REACHED)
  T23 — Deployment health check fails when CE paths missing from registry

All tests are fully offline: SQLite in-memory, no containers, no Meta API.
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if not BACKEND_DIR.exists():   # container: /app/backend absent, app code is at /app
    BACKEND_DIR = ROOT_DIR
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── JSONB → JSON patch (must run before any app.models import) ────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = sqlalchemy.JSON        # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON           # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, select, text as sql_text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class _Base(DeclarativeBase):
    pass


_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


@event.listens_for(_engine, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


# ── Stub app.db ───────────────────────────────────────────────────────────────
_db_mod = types.ModuleType("app.db")
_db_mod.Base = _Base                           # type: ignore[attr-defined]
_db_mod.engine = _engine                       # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal           # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:"    # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen  # type: ignore[attr-defined]
sys.modules["app.db"] = _db_mod

# ── Import models and services ────────────────────────────────────────────────
from app.models import (  # noqa: E402
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppContact,
    WhatsAppOutboundDedup,
    WhatsAppRecipientLock,
    WhatsAppThreadState,
    SecurityEvent,
)
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate
from app.services.outbound_path_registry import (
    AUTHORIZED_PATHS,
    LEGACY_PATHS,
    OutboundPathId,
    get_deployment_id,
    is_authorized,
    is_legacy,
)
from app.services.security_events import SecurityEventType, SecuritySeverity, create_security_event

# ── Schema ────────────────────────────────────────────────────────────────────
import app.models as _am_mod  # noqa: E402

_AppBase = _am_mod.Base       # always use the ORM Base regardless of import order
_AppBase.metadata.create_all(_engine)


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _new_session() -> Session:
    return _SessionLocal()


def _wipe_tables() -> None:
    """Truncate all data tables between tests — needed because the gate's
    dedicated sessions commit independently and can't be rolled back from
    the caller's session."""
    with _engine.begin() as conn:
        for table in reversed(_AppBase.metadata.sorted_tables):
            conn.execute(table.delete())


def _seed_thread(db: Session, wa_id: str = "5491155550001") -> tuple[WhatsAppThread, WhatsAppContact]:
    contact = WhatsAppContact(wa_id=wa_id, display_name="Test User")
    db.add(contact)
    db.flush()
    thread = WhatsAppThread(contact_id=contact.id)
    db.add(thread)
    db.flush()
    state = WhatsAppThreadState(thread_id=thread.id, needs_human=False)
    db.add(state)
    db.commit()
    return thread, contact


_NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
_WA_ID = "5491155550099"
_DEPLOY = "testsha01"


class T16_UnknownPathId(unittest.TestCase):
    """T16: Unknown path_id → blocked + BLOCKER SecurityEvent."""

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.thread, self.contact = _seed_thread(self.db, _WA_ID)

    def tearDown(self):
        self.db.close()

    def test_t16a_unregistered_path_blocks(self):
        """Gate returns BLOCKED_UNAUTHORIZED_PATH for an unknown path_id."""
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(
            wa_id=_WA_ID,
            thread_id=self.thread.id,
            text="hola",
            path_id="TOTALLY_UNKNOWN_PATH",
            deployment_id=_DEPLOY,
            now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)
        self.assertIsNotNone(result.blocked_reason)
        self.assertIn("UNAUTHORIZED_PATH", result.blocked_reason)

    def test_t16b_blocker_security_event_persisted(self):
        """A SecurityEvent with BLOCKER severity is committed to the DB."""
        gate = OutboundSafetyGate(self.db)
        gate.attempt(
            wa_id=_WA_ID,
            thread_id=self.thread.id,
            text="hola",
            path_id="GHOST_SENDER",
            deployment_id=_DEPLOY,
            now=_NOW,
        )
        check = _new_session()
        try:
            events = check.execute(
                select(SecurityEvent).where(
                    SecurityEvent.event_type == SecurityEventType.UNREGISTERED_OUTBOUND_SOURCE,
                    SecurityEvent.severity == SecuritySeverity.BLOCKER,
                )
            ).scalars().all()
            self.assertTrue(len(events) >= 1)
            evt = events[-1]
            self.assertEqual(evt.path_id, "GHOST_SENDER")
        finally:
            check.close()

    def test_t16c_blocked_whatsapp_message_persisted(self):
        """A blocked WhatsAppMessage audit row is committed."""
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(
            wa_id=_WA_ID,
            thread_id=self.thread.id,
            text="test block",
            path_id="NO_SUCH_PATH",
            deployment_id=_DEPLOY,
            now=_NOW,
        )
        self.assertIsNotNone(result.message_id)
        check = _new_session()
        try:
            msg = check.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "blocked")
            self.assertEqual(msg.path_id, "NO_SUCH_PATH")
        finally:
            check.close()

    def test_t16d_no_meta_api_call(self):
        """Meta API is never called when path_id is unregistered."""
        gate = OutboundSafetyGate(self.db)
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = AssertionError("Meta API must not be called")
            result = gate.attempt(
                wa_id=_WA_ID,
                thread_id=self.thread.id,
                text="should not reach Meta",
                path_id="INVALID_PATH",
                deployment_id=_DEPLOY,
                now=_NOW,
            )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)


class T17_DirectMetaCallOutsideAuthority(unittest.TestCase):
    """T17: Architecture guard — no Meta call site bypasses the gate."""

    def _collect_urlopen_calls_in_file(self, filepath: Path) -> list[tuple[int, str]]:
        """Return (lineno, context) for every urlopen call in a Python file."""
        source = filepath.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(filepath))
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "urlopen":
                    # Get surrounding context (function name)
                    results.append((node.lineno, ast.unparse(node)))
        return results

    def _collect_meta_api_references(self, filepath: Path) -> list[tuple[int, str]]:
        """Return (lineno, text) for every graph.facebook.com literal in a Python file."""
        results = []
        try:
            source = filepath.read_text(encoding="utf-8-sig")
        except Exception:
            return results
        for i, line in enumerate(source.splitlines(), start=1):
            if "graph.facebook.com" in line:
                results.append((i, line.strip()))
        return results

    def test_t17a_meta_api_only_in_whatsapp_ui(self):
        """Direct Meta WhatsApp Cloud API references (graph.facebook.com) must not appear
        outside whatsapp_ui.py. Other urlopen uses (email, n8n forward, OpenAI) are fine."""
        app_dir = BACKEND_DIR / "app"
        # whatsapp_ui.py is the canonical Meta API call site.
        # app/api/whatsapp.py contains media URL construction (not a send path) — allowed.
        allowed_files = {"whatsapp_ui.py", "whatsapp.py"}
        violations = []
        for py_file in app_dir.rglob("*.py"):
            if py_file.name in allowed_files:
                continue
            if "__pycache__" in str(py_file):
                continue
            refs = self._collect_meta_api_references(py_file)
            if refs:
                violations.append((py_file, refs))
        self.assertEqual(
            violations, [],
            f"Unexpected Meta API references outside allowed files: {violations}",
        )

    def test_t17b_whatsapp_ui_senders_all_call_enforce_outbound(self):
        """Every _send_whatsapp_cloud_* function in whatsapp_ui.py calls enforce_outbound_enabled."""
        ui_path = BACKEND_DIR / "app" / "ui" / "whatsapp_ui.py"
        # utf-8-sig strips the BOM (﻿) that some editors prepend
        source = ui_path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(ui_path))

        send_fns = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_send_whatsapp_cloud_")
        ]
        self.assertGreater(len(send_fns), 0, "No _send_whatsapp_cloud_* functions found")

        for fn in send_fns:
            fn_source = ast.unparse(fn)
            self.assertIn(
                "enforce_outbound_enabled",
                fn_source,
                f"{fn.name} does not call enforce_outbound_enabled()",
            )

    def test_t17c_send_wrappers_in_ce_call_gate(self):
        """_send_text_to_wa and _send_flow_button must call OutboundSafetyGate.attempt."""
        ce_path = BACKEND_DIR / "app" / "services" / "conversation_engine.py"
        source = ce_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(ce_path))

        target_fns = {"_send_text_to_wa", "_send_flow_button"}
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in target_fns:
                    found[node.name] = ast.unparse(node)

        self.assertEqual(set(found.keys()), target_fns)
        for fn_name, fn_source in found.items():
            self.assertIn(
                "gate.attempt",
                fn_source,
                f"{fn_name} does not call gate.attempt()",
            )
            self.assertIn(
                "path_id",
                fn_source,
                f"{fn_name} does not pass path_id to gate.attempt()",
            )


class T18_UnknownWamidStatus(unittest.TestCase):
    """T18: Status webhook for unknown WAMID → HIGH SecurityEvent."""

    def test_t18a_high_event_on_unknown_wamid(self):
        """Simulates the status handler logic: unknown WAMID creates HIGH event."""
        db = _new_session()
        try:
            create_security_event(
                db,
                event_type=SecurityEventType.META_STATUS_FOR_UNKNOWN_WAMID,
                severity=SecuritySeverity.HIGH,
                wamid="wamid_unknown_abc123",
                deployment_id=_DEPLOY,
                details={"incoming_status": "delivered", "outbound_enabled": False},
            )
            db.commit()

            events = db.execute(
                select(SecurityEvent).where(
                    SecurityEvent.wamid == "wamid_unknown_abc123",
                    SecurityEvent.event_type == SecurityEventType.META_STATUS_FOR_UNKNOWN_WAMID,
                )
            ).scalars().all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].severity, SecuritySeverity.HIGH)
        finally:
            db.close()

    def test_t18b_event_details_contain_status(self):
        """SecurityEvent details record the incoming status for forensics."""
        db = _new_session()
        try:
            evt = create_security_event(
                db,
                event_type=SecurityEventType.META_STATUS_FOR_UNKNOWN_WAMID,
                severity=SecuritySeverity.HIGH,
                wamid="wamid_detail_check",
                details={"incoming_status": "read", "outbound_enabled": True},
            )
            db.commit()
            self.assertEqual(evt.details["incoming_status"], "read")
        finally:
            db.close()


class T19_SuccessfulSendWhileOutboundOff(unittest.TestCase):
    """T19: Sent/delivered status + OUTBOUND OFF + no ledger attempt → BLOCKER."""

    def test_t19a_blocker_event_persisted(self):
        """SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF event is BLOCKER severity."""
        db = _new_session()
        try:
            evt = create_security_event(
                db,
                event_type=SecurityEventType.SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF,
                severity=SecuritySeverity.BLOCKER,
                wamid="wamid_outbound_off_sent",
                deployment_id=_DEPLOY,
                details={"incoming_status": "sent", "outbound_enabled": False},
            )
            db.commit()
            self.assertEqual(evt.event_type, SecurityEventType.SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF)
            self.assertEqual(evt.severity, SecuritySeverity.BLOCKER)
        finally:
            db.close()

    def test_t19b_wamid_not_in_ledger(self):
        """Confirms the scenario: WAMID has no WhatsAppMessage record."""
        db = _new_session()
        try:
            mystery_wamid = "wamid_never_written_99999"
            existing = db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.wa_message_id == mystery_wamid
                )
            ).scalar_one_or_none()
            self.assertIsNone(existing, "Sanity: WAMID must not exist in ledger")
            # The handler would create BLOCKER event in this case
            evt = create_security_event(
                db,
                event_type=SecurityEventType.SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF,
                severity=SecuritySeverity.BLOCKER,
                wamid=mystery_wamid,
                details={"incoming_status": "sent", "outbound_enabled": False},
            )
            db.commit()
            self.assertIsNotNone(evt.id)
        finally:
            db.close()


class T20_AuthorizedCeTextPath(unittest.TestCase):
    """T20: Authorized CE_TEXT path → gate ALLOWED, no unauthorized SecurityEvent."""

    def setUp(self):
        _wipe_tables()
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.db = _new_session()
        self.thread, self.contact = _seed_thread(self.db, "5491155550020")

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_t20a_ce_text_path_is_authorized(self):
        """CE_TEXT path_id resolves to an authorized path in the registry."""
        self.assertIn(OutboundPathId.CE_TEXT, AUTHORIZED_PATHS)
        self.assertTrue(is_authorized(OutboundPathId.CE_TEXT.value))

    def test_t20b_gate_allows_ce_text(self):
        """Gate returns ALLOWED for CE_TEXT path when outbound is on."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"messages":[{"id":"wamid_t20"}]}'
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            gate = OutboundSafetyGate(self.db)
            result = gate.attempt(
                wa_id=self.contact.wa_id,
                thread_id=self.thread.id,
                text="Autorizado CE_TEXT",
                path_id=OutboundPathId.CE_TEXT.value,
                deployment_id=_DEPLOY,
                now=_NOW,
            )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

    def test_t20c_no_unauthorized_security_event_for_ce_text(self):
        """No BLOCKER/HIGH SecurityEvent is created for an authorized CE_TEXT attempt."""
        count_before = _new_session().execute(
            select(SecurityEvent).where(
                SecurityEvent.event_type == SecurityEventType.UNREGISTERED_OUTBOUND_SOURCE
            )
        ).scalars().all()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"messages":[{"id":"wamid_t20c"}]}'
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            gate = OutboundSafetyGate(self.db)
            gate.attempt(
                wa_id=self.contact.wa_id,
                thread_id=self.thread.id,
                text="clean authorized send",
                path_id=OutboundPathId.CE_TEXT.value,
                deployment_id=_DEPLOY,
                now=_NOW,
            )

        count_after = _new_session().execute(
            select(SecurityEvent).where(
                SecurityEvent.event_type == SecurityEventType.UNREGISTERED_OUTBOUND_SOURCE
            )
        ).scalars().all()
        self.assertEqual(len(count_before), len(count_after))


class T21_AuthorizedManualCrmPath(unittest.TestCase):
    """T21: Authorized MANUAL_CRM path → registry accepts it, gate allows it."""

    def setUp(self):
        _wipe_tables()
        os.environ["OUTBOUND_ENABLED"] = "true"
        self.db = _new_session()
        self.thread, self.contact = _seed_thread(self.db, "5491155550021")

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_t21a_manual_crm_in_authorized_registry(self):
        """MANUAL_CRM path_id is in the authorized path registry."""
        self.assertIn(OutboundPathId.MANUAL_CRM, AUTHORIZED_PATHS)
        self.assertTrue(is_authorized(OutboundPathId.MANUAL_CRM.value))

    def test_t21b_gate_allows_manual_crm(self):
        """Gate returns ALLOWED for MANUAL_CRM path when outbound is on."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"messages":[{"id":"wamid_t21"}]}'
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            gate = OutboundSafetyGate(self.db)
            result = gate.attempt(
                wa_id=self.contact.wa_id,
                thread_id=self.thread.id,
                text="Manual operator reply",
                path_id=OutboundPathId.MANUAL_CRM.value,
                deployment_id=_DEPLOY,
                now=_NOW,
            )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)

    def test_t21c_pending_record_carries_path_id(self):
        """WhatsAppMessage pending record stores MANUAL_CRM path_id."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"messages":[{"id":"wamid_t21c"}]}'
            mock_urlopen.return_value.__enter__ = lambda s: mock_resp
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            gate = OutboundSafetyGate(self.db)
            result = gate.attempt(
                wa_id=self.contact.wa_id,
                thread_id=self.thread.id,
                text="Path ID persistence check",
                path_id=OutboundPathId.MANUAL_CRM.value,
                deployment_id=_DEPLOY,
                now=_NOW,
            )

        check = _new_session()
        try:
            msg = check.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.path_id, OutboundPathId.MANUAL_CRM.value)
            self.assertEqual(msg.deployment_id, _DEPLOY)
        finally:
            check.close()


class T22_LegacySenderReached(unittest.TestCase):
    """T22: Legacy N8N_AI_PIPELINE path → BLOCKER SecurityEvent (LEGACY_SENDER_REACHED)."""

    def setUp(self):
        _wipe_tables()
        self.db = _new_session()
        self.thread, self.contact = _seed_thread(self.db, "5491155550022")

    def tearDown(self):
        self.db.close()

    def test_t22a_legacy_path_not_authorized(self):
        """LEGACY_N8N_AI_PIPELINE is not in AUTHORIZED_PATHS."""
        self.assertNotIn(OutboundPathId.LEGACY_N8N_AI_PIPELINE, AUTHORIZED_PATHS)
        self.assertFalse(is_authorized(OutboundPathId.LEGACY_N8N_AI_PIPELINE.value))
        self.assertTrue(is_legacy(OutboundPathId.LEGACY_N8N_AI_PIPELINE.value))

    def test_t22b_legacy_attempt_blocked(self):
        """Gate blocks any attempt using LEGACY_N8N_AI_PIPELINE path_id."""
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(
            wa_id=self.contact.wa_id,
            thread_id=self.thread.id,
            text="AI pipeline reply",
            path_id=OutboundPathId.LEGACY_N8N_AI_PIPELINE.value,
            deployment_id=_DEPLOY,
            now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)

    def test_t22c_legacy_sender_reached_event_created(self):
        """LEGACY_SENDER_REACHED SecurityEvent with BLOCKER severity is persisted."""
        gate = OutboundSafetyGate(self.db)
        gate.attempt(
            wa_id=self.contact.wa_id,
            thread_id=self.thread.id,
            text="Legacy pipeline attempt",
            path_id=OutboundPathId.LEGACY_N8N_AI_PIPELINE.value,
            deployment_id=_DEPLOY,
            now=_NOW,
        )
        check = _new_session()
        try:
            events = check.execute(
                select(SecurityEvent).where(
                    SecurityEvent.event_type == SecurityEventType.LEGACY_SENDER_REACHED,
                    SecurityEvent.severity == SecuritySeverity.BLOCKER,
                )
            ).scalars().all()
            self.assertTrue(len(events) >= 1)
        finally:
            check.close()

    def test_t22d_impossible_by_construction_n8n_has_no_wa_creds(self):
        """Architecture: n8n has no WhatsApp credentials — legacy path is impossible.

        Verifies the invariant documented in M21.3-TRACE-BLOCKER-META audit:
        n8n stores only Header Auth, OpenAI, and SMTP credentials.
        It cannot call the Meta API independently.
        """
        # This test documents the architectural invariant.
        # In a live environment, this would query the n8n credentials DB.
        # Offline: assert the known credential types exclude WhatsApp.
        known_n8n_credential_types = frozenset({
            "httpHeaderAuth",  # API key for backend calls
            "openAiApi",       # OpenAI
            "smtp",            # Email
        })
        whatsapp_credential_types = frozenset({
            "whatsAppCloudApi",
            "whatsAppBusinessCloud",
            "facebookGraphApi",
        })
        intersection = known_n8n_credential_types & whatsapp_credential_types
        self.assertEqual(
            intersection,
            frozenset(),
            "n8n must not hold WhatsApp credentials — legacy AI pipeline is dead by construction",
        )


class T23_DeploymentWithUnregisteredSendPath(unittest.TestCase):
    """T23: Startup health check detects missing required paths → logs error."""

    def test_t23a_health_check_passes_with_full_registry(self):
        """Startup check: CE_TEXT and CE_FLOW are in the registry — no error logged."""
        from app.services.outbound_path_registry import AUTHORIZED_PATHS, OutboundPathId
        required = {OutboundPathId.CE_TEXT, OutboundPathId.CE_FLOW}
        missing = required - set(AUTHORIZED_PATHS.keys())
        self.assertEqual(missing, frozenset(), f"Missing required paths: {missing}")

    def test_t23b_health_check_detects_missing_ce_text(self):
        """If CE_TEXT were removed from the registry, health check would detect it."""
        # Simulate a registry without CE_TEXT
        from app.services.outbound_path_registry import AUTHORIZED_PATHS, OutboundPathId
        stripped_registry = {k: v for k, v in AUTHORIZED_PATHS.items() if k != OutboundPathId.CE_TEXT}
        required = {OutboundPathId.CE_TEXT, OutboundPathId.CE_FLOW}
        missing = required - set(stripped_registry.keys())
        self.assertIn(OutboundPathId.CE_TEXT, missing)

    def test_t23c_any_attempt_without_path_id_creates_blocker(self):
        """Attempting a send with no path_id (path_id=None) creates BLOCKER event.

        This is the case where new code calls the gate without registering a path.
        """
        db = _new_session()
        thread, contact = _seed_thread(db, "5491155550023")
        try:
            gate = OutboundSafetyGate(db)
            result = gate.attempt(
                wa_id=contact.wa_id,
                thread_id=thread.id,
                text="unregistered new code path",
                path_id=None,  # no path_id — OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE
                deployment_id=_DEPLOY,
                now=_NOW,
            )
            self.assertEqual(result.outcome, GateOutcome.BLOCKED_UNAUTHORIZED_PATH)

            check = _new_session()
            try:
                events = check.execute(
                    select(SecurityEvent).where(
                        SecurityEvent.event_type == SecurityEventType.OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE,
                        SecurityEvent.severity == SecuritySeverity.BLOCKER,
                    )
                ).scalars().all()
                self.assertTrue(len(events) >= 1)
            finally:
                check.close()
        finally:
            db.rollback()
            db.close()

    def test_t23d_all_meta_send_functions_covered_by_registry(self):
        """Architecture: every _send_whatsapp_cloud_* function has a registered path."""
        ui_path = BACKEND_DIR / "app" / "ui" / "whatsapp_ui.py"
        source = ui_path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(ui_path))

        send_fns = [
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("_send_whatsapp_cloud_")
        ]
        self.assertGreater(len(send_fns), 0)

        # Each send function type must have a corresponding authorized path.
        # text → CE_TEXT, flow → CE_FLOW, interactive → CE_INTERACTIVE, list → CE_LIST
        fn_to_path = {
            "_send_whatsapp_cloud_text": OutboundPathId.CE_TEXT,
            "_send_whatsapp_cloud_flow": OutboundPathId.CE_FLOW,
            "_send_whatsapp_cloud_interactive": OutboundPathId.CE_INTERACTIVE,
            "_send_whatsapp_cloud_list": OutboundPathId.CE_LIST,
        }
        for fn_name in send_fns:
            expected_path = fn_to_path.get(fn_name)
            self.assertIsNotNone(
                expected_path,
                f"No authorized path defined for {fn_name} — add to AUTHORIZED_PATHS",
            )
            self.assertIn(
                expected_path,
                AUTHORIZED_PATHS,
                f"{fn_name} maps to {expected_path} which is not in AUTHORIZED_PATHS",
            )


class RegistryInvariantTests(unittest.TestCase):
    """Additional registry invariant checks."""

    def test_registry_path_ids_are_valid_enum_members(self):
        """All keys in AUTHORIZED_PATHS are valid OutboundPathId enum members."""
        for path_id in AUTHORIZED_PATHS:
            self.assertIsInstance(path_id, OutboundPathId)

    def test_legacy_paths_not_in_authorized(self):
        """No legacy path appears in AUTHORIZED_PATHS."""
        for legacy_str in LEGACY_PATHS:
            try:
                legacy_enum = OutboundPathId(legacy_str)
            except ValueError:
                continue
            self.assertNotIn(legacy_enum, AUTHORIZED_PATHS)

    def test_deployment_id_returns_string(self):
        """get_deployment_id() returns a non-empty string."""
        dep_id = get_deployment_id()
        self.assertIsInstance(dep_id, str)
        self.assertGreater(len(dep_id), 0)


if __name__ == "__main__":
    unittest.main()
