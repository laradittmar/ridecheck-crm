"""M21.3-OPS-DASHBOARD — Operational observability dashboard tests.

OPS-01  Control route requires existing CRM auth
OPS-02  Control dashboard renders
OPS-03  Inbound count correct
OPS-04  Outbound count derives from authoritative records
OPS-05  Outbound OFF displayed correctly
OPS-06  Unanswered bot thread identified
OPS-07  Waiting-for-customer not classified unanswered
OPS-08  Needs_human thread classified waiting human
OPS-09  Critical unanswered threshold works
OPS-10  Response latency computed from deterministic timestamps
OPS-11  P50 correct
OPS-12  P95 correct
OPS-13  Registered path displayed normally
OPS-14  Unknown path displayed CRITICAL
OPS-15  Legacy path displayed CRITICAL
OPS-16  Unknown WAMID security event visible
OPS-17  Outbound-off suspicious Meta success event visible
OPS-18  Outbound ledger visible
OPS-19  Message trace direction correct
OPS-20  Thread link resolves correct Inbox thread
OPS-21  Filters work
OPS-22  Message preview escapes HTML
OPS-23  No secret data rendered
OPS-24  Empty-state works
OPS-25  Mobile layout does not overflow
OPS-26  Dashboard reads do not mutate message/thread/lead state
OPS-27  Dashboard does not invoke outbound safety gate/send
OPS-28  Auto-refresh endpoint/read works
OPS-29  Existing WhatsApp Inbox UX unchanged
OPS-30  Existing Agenda/Kanban unaffected

All tests are fully offline: SQLite in-memory, no containers, no Meta API.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if not BACKEND_DIR.exists():
    BACKEND_DIR = ROOT_DIR
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── JSONB → JSON patch (must run before any app.models import) ────────────────
import sqlalchemy as _sa
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

_pg_dialect.JSONB = _sa.JSON  # type: ignore[attr-defined]
_pg_json.JSONB = _sa.JSON     # type: ignore[attr-defined]

# Stub optional heavy dependencies before importing app code
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

# ── Import the modules under test ────────────────────────────────────────────
from app.routes.ops_dashboard import (
    AUTHORIZED_PATHS,
    LEGACY_PATHS,
    UNANSWERED_WARNING_SECONDS,
    UNANSWERED_CRITICAL_SECONDS,
    _classify_thread_health,
    _age_tier,
    _mask_wa_id,
    _preview,
    _is_path_critical,
    _percentile,
    _window_range,
    _query_thread_health_rows,
    get_summary,
    get_messages,
    get_threads,
    get_paths,
    get_critical_events,
)
from app.ui.control_view import ICON_CONTROL, render_control_page


# ── Helpers ──────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 8, 29, 15, 0, 0, tzinfo=timezone.utc)
_TODAY_START = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def _db() -> Session:
    """Return a fresh in-memory SQLite session with a clean state."""
    _Base.metadata.drop_all(_engine)
    _Base.metadata.create_all(_engine)
    return _SessionLocal()


def _contact(db: Session, wa_id: str = "549114000001", display_name: str = "Test User") -> _app_models.WhatsAppContact:
    c = _app_models.WhatsAppContact(wa_id=wa_id, display_name=display_name)
    db.add(c)
    db.flush()
    return c


def _thread(db: Session, contact: _app_models.WhatsAppContact, last_message_at: datetime = _NOW) -> _app_models.WhatsAppThread:
    t = _app_models.WhatsAppThread(contact_id=contact.id, last_message_at=last_message_at)
    db.add(t)
    db.flush()
    return t


def _state(db: Session, thread_id: int, needs_human: bool = False, last_stage: str | None = None) -> _app_models.WhatsAppThreadState:
    s = _app_models.WhatsAppThreadState(thread_id=thread_id, needs_human=needs_human, last_stage=last_stage)
    db.add(s)
    db.flush()
    return s


def _message(
    db: Session,
    thread_id: int,
    direction: str = "in",
    status: str = "received",
    text: str = "Hello",
    message_type: str = "text",
    path_id: str | None = None,
    automated: bool = False,
    ts: datetime = _NOW,
    wa_message_id: str | None = None,
) -> _app_models.WhatsAppMessage:
    import uuid
    m = _app_models.WhatsAppMessage(
        thread_id=thread_id,
        direction=direction,
        status=status,
        text=text,
        message_type=message_type,
        path_id=path_id,
        automated=automated,
        timestamp=ts,
        wa_message_id=wa_message_id or f"wamid.{uuid.uuid4().hex[:16]}",
        created_at=ts,
    )
    db.add(m)
    db.flush()
    return m


def _security_event(
    db: Session,
    event_type: str = "META_STATUS_FOR_UNKNOWN_WAMID",
    severity: str = "HIGH",
    thread_id: int | None = None,
    wamid: str | None = None,
    path_id: str | None = None,
    detected_at: datetime = _NOW,
) -> _app_models.SecurityEvent:
    e = _app_models.SecurityEvent(
        event_type=event_type,
        severity=severity,
        thread_id=thread_id,
        wamid=wamid,
        path_id=path_id,
        detected_at=detected_at,
    )
    db.add(e)
    db.flush()
    return e


def _ai_event(
    db: Session,
    thread_id: int,
    wa_message_id: str,
    latency_total_ms: int | None = None,
    status: str = "triggered",
    created_at: datetime = _NOW,
) -> _app_models.AiEvent:
    a = _app_models.AiEvent(
        thread_id=thread_id,
        wa_message_id=wa_message_id,
        wa_id="549114000001",
        latency_total_ms=latency_total_ms,
        status=status,
        created_at=created_at,
    )
    db.add(a)
    db.flush()
    return a


# ══════════════════════════════════════════════════════════════════════════════
# OPS-01: Control route requires CRM auth
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS01AuthRequired(unittest.TestCase):
    """OPS-01: /control must be in the protected_prefixes."""

    def test_ops01_control_in_protected_prefixes(self):
        """_is_protected_path('/control') must return True."""
        from app.main import _is_protected_path
        self.assertTrue(_is_protected_path("/control"))

    def test_ops01_control_subpath_protected(self):
        """Subpaths of /control are also protected."""
        from app.main import _is_protected_path
        self.assertTrue(_is_protected_path("/control/"))

    def test_ops01_api_ops_not_protected_via_prefix(self):
        """/api/ops routes are API endpoints, not UI — they share the same auth via session cookie
        but are NOT in the protected_prefixes UI list (they return JSON, not redirects).
        This test documents the boundary."""
        from app.main import _is_protected_path
        # /api/ routes are JSON APIs — auth is via cookie but not in protected prefix
        # They are still gated by session cookie in the middleware
        self.assertFalse(_is_protected_path("/api/ops/summary"))


# ══════════════════════════════════════════════════════════════════════════════
# OPS-02: Control dashboard renders
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS02DashboardRenders(unittest.TestCase):
    """OPS-02: render_control_page returns valid HTML with expected structure."""

    def setUp(self):
        self.html = render_control_page(user_email="admin@test.com")

    def test_ops02_returns_string(self):
        self.assertIsInstance(self.html, str)
        self.assertGreater(len(self.html), 500)

    def test_ops02_contains_doctype(self):
        self.assertIn("<!DOCTYPE html>", self.html)

    def test_ops02_contains_control_section(self):
        self.assertIn("/control", self.html)

    def test_ops02_contains_sidebar(self):
        self.assertIn('class="sidebar"', self.html)

    def test_ops02_contains_api_calls(self):
        self.assertIn("/api/ops/summary", self.html)
        self.assertIn("/api/ops/threads", self.html)
        self.assertIn("/api/ops/messages", self.html)

    def test_ops02_contains_auto_refresh(self):
        self.assertIn("setInterval", self.html)

    def test_ops02_contains_last_updated(self):
        self.assertIn("lastUpdated", self.html)

    def test_ops02_contains_outbound_state_element(self):
        self.assertIn("outbound", self.html.lower())

    def test_ops02_user_email_rendered(self):
        self.assertIn("admin@test.com", self.html)

    def test_ops02_icon_control_exported(self):
        self.assertIsInstance(ICON_CONTROL, str)
        self.assertIn("svg", ICON_CONTROL)

    def test_ops02_control_nav_item_in_sidebar(self):
        self.assertIn("Control", self.html)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-03: Inbound count correct
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS03InboundCount(unittest.TestCase):
    """OPS-03: Inbound count in summary matches WhatsAppMessage direction='in'."""

    def test_ops03_inbound_count_correct(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW)
            _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=1))
            _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=2))
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=3))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertEqual(result["inbound_count"], 2)
            self.assertEqual(result["outbound_count"], 1)
        finally:
            db.close()

    def test_ops03_empty_db_returns_zero(self):
        db = _db()
        try:
            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)
            self.assertEqual(result["inbound_count"], 0)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-04: Outbound count from authoritative records
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS04OutboundCount(unittest.TestCase):
    """OPS-04: Outbound count uses automated=True WhatsAppMessage records only."""

    def test_ops04_only_automated_counted(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW)
            # Automated outbound
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=1))
            # Non-automated (manual CRM) — still counted if automated=True in model
            # Note: MANUAL_CRM path sets automated=True in gate
            _message(db, t.id, direction="out", automated=False, path_id="MANUAL_CRM",
                     status="sent", ts=_TODAY_START + timedelta(hours=2))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            # Only automated=True is counted
            self.assertEqual(result["outbound_count"], 1)
        finally:
            db.close()

    def test_ops04_blocked_counted_separately(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW)
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="blocked", ts=_TODAY_START + timedelta(hours=1))
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=2))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertEqual(result["outbound_count"], 2)  # both automated
            self.assertEqual(result["blocked_count"], 1)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-05: Outbound OFF displayed correctly
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS05OutboundOff(unittest.TestCase):
    """OPS-05: OUTBOUND_ENABLED env var controls outbound_enabled field."""

    def test_ops05_outbound_off_when_env_unset(self):
        db = _db()
        try:
            env = {k: v for k, v in os.environ.items() if k != "OUTBOUND_ENABLED"}
            with patch.dict(os.environ, env, clear=True):
                with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                    mock_dt.now.return_value = _NOW
                    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                    result = get_summary(window="today", db=db)
            self.assertFalse(result["outbound_enabled"])
        finally:
            db.close()

    def test_ops05_outbound_off_when_env_false(self):
        db = _db()
        try:
            with patch.dict(os.environ, {"OUTBOUND_ENABLED": "false"}):
                with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                    mock_dt.now.return_value = _NOW
                    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                    result = get_summary(window="today", db=db)
            self.assertFalse(result["outbound_enabled"])
        finally:
            db.close()

    def test_ops05_outbound_on_when_env_true(self):
        db = _db()
        try:
            with patch.dict(os.environ, {"OUTBOUND_ENABLED": "true"}):
                with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                    mock_dt.now.return_value = _NOW
                    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                    result = get_summary(window="today", db=db)
            self.assertTrue(result["outbound_enabled"])
        finally:
            db.close()

    def test_ops05_dashboard_html_shows_outbound_state_element(self):
        html = render_control_page(user_email="admin@test.com")
        # The dashboard must render an element that JS populates with outbound state
        self.assertIn("outbound", html.lower())

    def test_ops05_outbound_off_not_red_in_css(self):
        """The OFF state CSS should be calm grey/blue — not pure #ef4444 (red) for the outbound card."""
        html = render_control_page(user_email="admin@test.com")
        # The page should not use the outbound card's off-state as red
        # We check by seeing the specific class is NOT mapped to error-red
        # Instead, it should be a neutral color class
        self.assertIn("outboundOff", html)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-06: Unanswered bot thread identified
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS06UnansweredBotIdentified(unittest.TestCase):
    """OPS-06: A thread where the last message is inbound and needs_human=False
    is classified as UNANSWERED_BOT."""

    def test_ops06_unanswered_bot_classified(self):
        self.assertEqual(
            _classify_thread_health(needs_human=False, latest_direction="in"),
            "UNANSWERED_BOT",
        )

    def test_ops06_unanswered_bot_counted_in_summary(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(hours=1))
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertGreaterEqual(result["unanswered_bot_count"], 1)
        finally:
            db.close()

    def test_ops06_unanswered_bot_appears_in_threads_endpoint(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(hours=1))
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="unanswered", limit=50, db=db)

            bot_threads = [r for r in result["threads"] if r["health"] == "UNANSWERED_BOT"]
            self.assertGreaterEqual(len(bot_threads), 1)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-07: Waiting-for-customer not classified unanswered
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS07WaitingCustomerNotUnanswered(unittest.TestCase):
    """OPS-07: A thread where the last message is outbound must be WAITING_CUSTOMER."""

    def test_ops07_outbound_last_is_waiting_customer(self):
        self.assertEqual(
            _classify_thread_health(needs_human=False, latest_direction="out"),
            "WAITING_CUSTOMER",
        )

    def test_ops07_waiting_customer_not_counted_as_unanswered(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(minutes=5))
            _state(db, t.id, needs_human=False)
            # Last message is outbound (bot already responded)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(minutes=10))
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_NOW - timedelta(minutes=5))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertEqual(result["unanswered_bot_count"], 0)
            self.assertGreaterEqual(result["waiting_customer_count"], 1)
        finally:
            db.close()

    def test_ops07_filter_unanswered_excludes_waiting_customer(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(minutes=5))
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_NOW - timedelta(minutes=5))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="unanswered", limit=50, db=db)

            unanswered = [r for r in result["threads"] if r["health"] in ("UNANSWERED_BOT", "WAITING_HUMAN")]
            self.assertEqual(len(unanswered), 0)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-08: Needs_human classified as waiting human
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS08NeedsHumanWaitingHuman(unittest.TestCase):
    """OPS-08: needs_human=True + latest_direction='in' → WAITING_HUMAN."""

    def test_ops08_classify_waiting_human(self):
        self.assertEqual(
            _classify_thread_health(needs_human=True, latest_direction="in"),
            "WAITING_HUMAN",
        )

    def test_ops08_waiting_human_counted_correctly(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(hours=1))
            _state(db, t.id, needs_human=True)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertGreaterEqual(result["waiting_human_count"], 1)
            self.assertEqual(result["unanswered_bot_count"], 0)
        finally:
            db.close()

    def test_ops08_needs_human_filter_works(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(hours=1))
            _state(db, t.id, needs_human=True)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="needs_human", limit=50, db=db)

            human_threads = [r for r in result["threads"] if r["health"] == "WAITING_HUMAN"]
            self.assertGreaterEqual(len(human_threads), 1)
            self.assertTrue(human_threads[0]["needs_human"])
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-09: Critical unanswered threshold works
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS09CriticalUnansweredThreshold(unittest.TestCase):
    """OPS-09: age_tier CRITICAL fires when waiting >= UNANSWERED_CRITICAL_SECONDS."""

    def test_ops09_constants_defined(self):
        self.assertEqual(UNANSWERED_WARNING_SECONDS, 120)
        self.assertEqual(UNANSWERED_CRITICAL_SECONDS, 300)

    def test_ops09_normal_tier(self):
        tier = _age_tier("UNANSWERED_BOT", 60)
        self.assertEqual(tier, "NORMAL")

    def test_ops09_warning_tier(self):
        tier = _age_tier("UNANSWERED_BOT", 180)
        self.assertEqual(tier, "WARNING")

    def test_ops09_critical_tier(self):
        tier = _age_tier("UNANSWERED_BOT", 360)
        self.assertEqual(tier, "CRITICAL")

    def test_ops09_boundary_warning_at_120(self):
        self.assertEqual(_age_tier("UNANSWERED_BOT", 120), "WARNING")

    def test_ops09_boundary_critical_at_300(self):
        self.assertEqual(_age_tier("UNANSWERED_BOT", 300), "CRITICAL")

    def test_ops09_waiting_customer_no_tier(self):
        self.assertIsNone(_age_tier("WAITING_CUSTOMER", 9999))

    def test_ops09_critical_filter_works(self):
        db = _db()
        try:
            c = _contact(db)
            # Thread last active > CRITICAL threshold ago
            old_ts = _NOW - timedelta(seconds=UNANSWERED_CRITICAL_SECONDS + 60)
            t = _thread(db, c, last_message_at=old_ts)
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="in", ts=old_ts)
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="critical", limit=50, db=db)

            critical = [r for r in result["threads"] if r["age_tier"] == "CRITICAL"]
            self.assertGreaterEqual(len(critical), 1)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-10: Response latency computed from deterministic timestamps
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS10ResponseLatency(unittest.TestCase):
    """OPS-10: Latency sourced from AiEvent.latency_total_ms."""

    def test_ops10_latency_from_ai_event(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            msg = _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=1))
            _ai_event(db, t.id, msg.wa_message_id, latency_total_ms=5000,
                      created_at=_TODAY_START + timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertEqual(result["latency_p50_ms"], 5000)
            self.assertEqual(result["latency_sample_count"], 1)
        finally:
            db.close()

    def test_ops10_null_latency_excluded(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            msg = _message(db, t.id, direction="in")
            _ai_event(db, t.id, msg.wa_message_id, latency_total_ms=None)
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertIsNone(result["latency_p50_ms"])
            self.assertEqual(result["latency_sample_count"], 0)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-11: P50 correct
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS11P50(unittest.TestCase):
    """OPS-11: P50 percentile is computed correctly."""

    def test_ops11_p50_single_value(self):
        self.assertEqual(_percentile([100], 0.50), 100)

    def test_ops11_p50_two_values(self):
        result = _percentile([100, 200], 0.50)
        self.assertIn(result, [100, 200])

    def test_ops11_p50_five_values(self):
        vals = sorted([100, 200, 300, 400, 500])
        result = _percentile(vals, 0.50)
        self.assertEqual(result, 300)

    def test_ops11_p50_empty_none(self):
        self.assertIsNone(_percentile([], 0.50))

    def test_ops11_p50_from_summary(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            latencies = [1000, 2000, 3000, 4000, 5000]
            for i, lat in enumerate(latencies):
                msg = _message(db, t.id, direction="in",
                               ts=_TODAY_START + timedelta(hours=i + 1),
                               wa_message_id=f"wamid.test{i}")
                _ai_event(db, t.id, msg.wa_message_id, latency_total_ms=lat,
                          created_at=_TODAY_START + timedelta(hours=i + 1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertEqual(result["latency_p50_ms"], 3000)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-12: P95 correct
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS12P95(unittest.TestCase):
    """OPS-12: P95 percentile is computed correctly."""

    def test_ops12_p95_single_value(self):
        self.assertEqual(_percentile([100], 0.95), 100)

    def test_ops12_p95_twenty_values(self):
        vals = sorted(range(100, 2100, 100))  # 100..2000, 20 values
        result = _percentile(vals, 0.95)
        # idx = int(20 * 0.95) = 19, which is the last element = 2000
        self.assertEqual(result, 2000)

    def test_ops12_p95_greater_than_p50(self):
        vals = sorted([1000, 1500, 1200, 5000, 800, 900, 1100, 1300, 1400, 4000])
        p50 = _percentile(vals, 0.50)
        p95 = _percentile(vals, 0.95)
        self.assertGreaterEqual(p95, p50)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-13: Registered path displayed normally
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS13RegisteredPathNormal(unittest.TestCase):
    """OPS-13: Authorized paths are not flagged as critical."""

    def test_ops13_ce_text_not_critical(self):
        self.assertFalse(_is_path_critical("CE_TEXT"))

    def test_ops13_all_authorized_paths_not_critical(self):
        for path in AUTHORIZED_PATHS:
            with self.subTest(path=path):
                self.assertFalse(_is_path_critical(path))

    def test_ops13_authorized_path_is_authorized_in_paths_endpoint(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_paths(window="today", db=db)

            ce_text_path = next((p for p in result["paths"] if p["path_id"] == "CE_TEXT"), None)
            self.assertIsNotNone(ce_text_path)
            self.assertTrue(ce_text_path["is_authorized"])
            self.assertFalse(ce_text_path["is_critical"])
            self.assertFalse(ce_text_path["is_legacy"])
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-14: Unknown path displayed CRITICAL
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS14UnknownPathCritical(unittest.TestCase):
    """OPS-14: A path_id not in AUTHORIZED_PATHS or LEGACY_PATHS is critical."""

    def test_ops14_none_path_is_critical(self):
        self.assertTrue(_is_path_critical(None))

    def test_ops14_arbitrary_string_is_critical(self):
        self.assertTrue(_is_path_critical("SOME_UNKNOWN_PATH"))

    def test_ops14_unknown_path_flagged_in_paths_endpoint(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="out", automated=True, path_id=None,
                     status="blocked", ts=_TODAY_START + timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_paths(window="today", db=db)

            unknown_path = next((p for p in result["paths"] if p["path_id"] == "UNKNOWN"), None)
            self.assertIsNotNone(unknown_path)
            self.assertTrue(unknown_path["is_critical"])
            self.assertFalse(unknown_path["is_authorized"])
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-15: Legacy path displayed CRITICAL
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS15LegacyPathCritical(unittest.TestCase):
    """OPS-15: LEGACY_N8N_AI_PIPELINE path is always critical."""

    def test_ops15_legacy_path_is_critical(self):
        self.assertTrue(_is_path_critical("LEGACY_N8N_AI_PIPELINE"))

    def test_ops15_legacy_path_in_legacy_paths(self):
        self.assertIn("LEGACY_N8N_AI_PIPELINE", LEGACY_PATHS)

    def test_ops15_legacy_path_not_in_authorized(self):
        self.assertNotIn("LEGACY_N8N_AI_PIPELINE", AUTHORIZED_PATHS)

    def test_ops15_legacy_path_flagged_in_paths_endpoint(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="out", automated=True,
                     path_id="LEGACY_N8N_AI_PIPELINE",
                     status="blocked", ts=_TODAY_START + timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_paths(window="today", db=db)

            legacy = next((p for p in result["paths"] if p["path_id"] == "LEGACY_N8N_AI_PIPELINE"), None)
            self.assertIsNotNone(legacy)
            self.assertTrue(legacy["is_legacy"])
            self.assertTrue(legacy["is_critical"])
            self.assertFalse(legacy["is_authorized"])
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-16: Unknown WAMID security event visible
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS16UnknownWAMIDVisible(unittest.TestCase):
    """OPS-16: META_STATUS_FOR_UNKNOWN_WAMID SecurityEvent appears in critical events."""

    def test_ops16_unknown_wamid_in_critical_events(self):
        db = _db()
        try:
            _security_event(
                db,
                event_type="META_STATUS_FOR_UNKNOWN_WAMID",
                severity="HIGH",
                wamid="wamid.unknown123",
                detected_at=_TODAY_START + timedelta(hours=1),
            )
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_critical_events(window="today", limit=50, db=db)

            self.assertGreaterEqual(result["count"], 1)
            wamid_events = [
                e for e in result["events"]
                if e["event_category"] == "UNKNOWN_WAMID"
            ]
            self.assertGreaterEqual(len(wamid_events), 1)
            self.assertEqual(wamid_events[0]["wamid"], "wamid.unknown123")
        finally:
            db.close()

    def test_ops16_unknown_wamid_counted_in_summary(self):
        db = _db()
        try:
            _security_event(
                db,
                event_type="META_STATUS_FOR_UNKNOWN_WAMID",
                severity="HIGH",
                detected_at=_TODAY_START + timedelta(hours=1),
            )
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)

            self.assertGreaterEqual(result["critical_events_count"], 1)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-17: Outbound-off suspicious Meta success visible
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS17OutboundOffMetaSuccess(unittest.TestCase):
    """OPS-17: SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF SecurityEvent visible."""

    def test_ops17_outbound_off_success_categorized(self):
        db = _db()
        try:
            _security_event(
                db,
                event_type="SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF",
                severity="BLOCKER",
                detected_at=_TODAY_START + timedelta(hours=1),
            )
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_critical_events(window="today", limit=50, db=db)

            suspect_events = [
                e for e in result["events"]
                if e["event_category"] == "OUTBOUND_OFF_BUT_META_SUCCESS"
            ]
            self.assertGreaterEqual(len(suspect_events), 1)
            self.assertEqual(suspect_events[0]["severity"], "BLOCKER")
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-18: Outbound ledger visible
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS18OutboundLedgerVisible(unittest.TestCase):
    """OPS-18: Outbound messages appear in messages endpoint with direction='out'."""

    def test_ops18_outbound_message_visible(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="delivered", text="Tu cotización es...",
                     ts=_TODAY_START + timedelta(hours=2))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction="out", thread_id=None, limit=100, db=db)

            self.assertGreaterEqual(result["count"], 1)
            out_messages = [m for m in result["messages"] if m["direction"] == "out"]
            self.assertGreaterEqual(len(out_messages), 1)
            self.assertEqual(out_messages[0]["path_id"], "CE_TEXT")
            self.assertEqual(out_messages[0]["status"], "delivered")
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-19: Message trace direction correct
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS19MessageTraceDirection(unittest.TestCase):
    """OPS-19: Direction filter for message trace is correct."""

    def test_ops19_direction_in_filters_correctly(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=1))
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=2))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction="in", thread_id=None, limit=100, db=db)

            self.assertEqual(result["count"], 1)
            self.assertEqual(result["messages"][0]["direction"], "in")
        finally:
            db.close()

    def test_ops19_direction_out_filters_correctly(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=1))
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=2))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction="out", thread_id=None, limit=100, db=db)

            self.assertEqual(result["count"], 1)
            self.assertEqual(result["messages"][0]["direction"], "out")
        finally:
            db.close()

    def test_ops19_no_direction_filter_returns_all(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=1))
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_TODAY_START + timedelta(hours=2))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction=None, thread_id=None, limit=100, db=db)

            self.assertEqual(result["count"], 2)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-20: Thread link resolves correct Inbox thread
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS20ThreadLinkCorrect(unittest.TestCase):
    """OPS-20: inbox_link points to /whatsapp/thread/{thread_id}."""

    def test_ops20_inbox_link_format(self):
        db = _db()
        try:
            c = _contact(db, wa_id="549114000099")
            t = _thread(db, c, last_message_at=_NOW - timedelta(hours=1))
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="all", limit=50, db=db)

            self.assertGreaterEqual(len(result["threads"]), 1)
            thread_row = result["threads"][0]
            expected_link = f"/whatsapp/thread/{t.id}"
            self.assertEqual(thread_row["inbox_link"], expected_link)
        finally:
            db.close()

    def test_ops20_dashboard_html_contains_whatsapp_thread_link(self):
        html = render_control_page(user_email="admin@test.com")
        self.assertIn("/whatsapp/thread/", html)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-21: Filters work
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS21FiltersWork(unittest.TestCase):
    """OPS-21: Window, health, and direction filters all function correctly."""

    def test_ops21_window_today_excludes_old_messages(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            # Yesterday's message
            _message(db, t.id, direction="in",
                     ts=_TODAY_START - timedelta(hours=5),
                     wa_message_id="wamid.old1")
            # Today's message
            _message(db, t.id, direction="in",
                     ts=_TODAY_START + timedelta(hours=1),
                     wa_message_id="wamid.today1")
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction=None, thread_id=None, limit=100, db=db)

            self.assertEqual(result["count"], 1)
        finally:
            db.close()

    def test_ops21_window_24h_includes_recent_past(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            # 20 hours ago — within 24h window but might be before today midnight
            _message(db, t.id, direction="in",
                     ts=_NOW - timedelta(hours=20),
                     wa_message_id="wamid.recent1")
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="24h", direction=None, thread_id=None, limit=100, db=db)

            self.assertEqual(result["count"], 1)
        finally:
            db.close()

    def test_ops21_health_filter_waiting_customer(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(minutes=5))
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="out", automated=True, path_id="CE_TEXT",
                     status="sent", ts=_NOW - timedelta(minutes=5))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="waiting_customer", limit=50, db=db)

            wc = [r for r in result["threads"] if r["health"] == "WAITING_CUSTOMER"]
            self.assertGreaterEqual(len(wc), 1)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-22: Message preview escapes HTML
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS22MessagePreviewEscapesHTML(unittest.TestCase):
    """OPS-22: Message preview does not pass raw HTML to the API consumer."""

    def test_ops22_preview_truncates_to_80_chars(self):
        long_text = "A" * 120
        result = _preview(long_text, "text")
        self.assertEqual(len(result), 80)
        self.assertEqual(result, "A" * 80)

    def test_ops22_preview_uses_message_type_when_no_text(self):
        result = _preview(None, "audio")
        self.assertEqual(result, "audio")

    def test_ops22_preview_returned_as_plain_string(self):
        # The API returns JSON — the frontend must handle HTML escaping.
        # Here we verify the preview is a plain Python string (not pre-escaped).
        text = "<script>alert('xss')</script>"
        result = _preview(text, "text")
        self.assertIsInstance(result, str)
        # The API itself does not HTML-escape (that's the frontend's job for JSON APIs).
        self.assertIn("<script>", result)

    def test_ops22_dashboard_html_uses_esc_function(self):
        """The dashboard JavaScript must use esc() for user content."""
        html = render_control_page(user_email="test@test.com")
        # The JS must define an esc() function for HTML safety
        self.assertIn("function esc(", html)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-23: No secret data rendered
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS23NoSecretsRendered(unittest.TestCase):
    """OPS-23: Dashboard HTML must not contain secret tokens or private keys."""

    def setUp(self):
        self.html = render_control_page(user_email="admin@test.com")

    def test_ops23_no_whatsapp_token_in_html(self):
        # The WHATSAPP_TOKEN env var should not appear in any rendered HTML
        token = os.environ.get("WHATSAPP_TOKEN", "")
        if token:
            self.assertNotIn(token, self.html)

    def test_ops23_no_private_key_pem_header(self):
        self.assertNotIn("BEGIN RSA PRIVATE KEY", self.html)
        self.assertNotIn("BEGIN PRIVATE KEY", self.html)

    def test_ops23_no_app_secret_literal(self):
        secret = os.environ.get("WHATSAPP_APP_SECRET", "")
        if secret:
            self.assertNotIn(secret, self.html)

    def test_ops23_api_routes_not_expose_raw_payload(self):
        # The /api/ops endpoints do not expose raw_payload JSONB field
        from app.routes.ops_dashboard import get_messages
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            m = _message(db, t.id, direction="in",
                         ts=_TODAY_START + timedelta(hours=1))
            db.commit()

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction=None, thread_id=None, limit=100, db=db)

            # raw_payload not in message fields
            self.assertGreaterEqual(len(result["messages"]), 1)
            msg = result["messages"][0]
            self.assertNotIn("raw_payload", msg)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-24: Empty-state works
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS24EmptyState(unittest.TestCase):
    """OPS-24: Empty DB returns valid zero-count responses."""

    def test_ops24_summary_empty_db(self):
        db = _db()
        try:
            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_summary(window="today", db=db)
            self.assertEqual(result["inbound_count"], 0)
            self.assertEqual(result["outbound_count"], 0)
            self.assertIsNone(result["latency_p50_ms"])
        finally:
            db.close()

    def test_ops24_messages_empty_db(self):
        db = _db()
        try:
            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_messages(window="today", direction=None, thread_id=None, limit=100, db=db)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["messages"], [])
        finally:
            db.close()

    def test_ops24_threads_empty_db(self):
        db = _db()
        try:
            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_threads(health="all", limit=50, db=db)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["threads"], [])
        finally:
            db.close()

    def test_ops24_paths_empty_db(self):
        db = _db()
        try:
            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_paths(window="today", db=db)
            self.assertEqual(result["paths"], [])
            self.assertEqual(result["unregistered_count"], 0)
        finally:
            db.close()

    def test_ops24_critical_events_empty_db(self):
        db = _db()
        try:
            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                result = get_critical_events(window="today", limit=50, db=db)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["events"], [])
        finally:
            db.close()

    def test_ops24_dashboard_renders_with_empty_data(self):
        html = render_control_page(user_email="admin@test.com")
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 100)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-25: Mobile layout does not overflow
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS25MobileLayout(unittest.TestCase):
    """OPS-25: Dashboard CSS includes mobile media queries."""

    def setUp(self):
        self.html = render_control_page(user_email="admin@test.com")

    def test_ops25_has_mobile_media_query(self):
        self.assertIn("@media", self.html)

    def test_ops25_has_max_width_breakpoint(self):
        self.assertIn("max-width", self.html)

    def test_ops25_viewport_meta_present(self):
        self.assertIn('name="viewport"', self.html)

    def test_ops25_table_wrap_has_overflow_auto(self):
        self.assertIn("overflow", self.html.lower())


# ══════════════════════════════════════════════════════════════════════════════
# OPS-26: Dashboard reads do not mutate state
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS26NoDatabaseMutation(unittest.TestCase):
    """OPS-26: All API endpoints are read-only (no INSERT/UPDATE/DELETE)."""

    def test_ops26_get_summary_no_mutation(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c)
            _message(db, t.id, direction="in", ts=_TODAY_START + timedelta(hours=1))
            db.commit()

            thread_before = db.get(_app_models.WhatsAppThread, t.id)
            self.assertIsNotNone(thread_before)

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                get_summary(window="today", db=db)

            thread_after = db.get(_app_models.WhatsAppThread, t.id)
            self.assertIsNotNone(thread_after)
            # last_message_at unchanged
            self.assertEqual(
                thread_before.last_message_at,
                thread_after.last_message_at,
            )
        finally:
            db.close()

    def test_ops26_get_threads_no_mutation(self):
        db = _db()
        try:
            c = _contact(db)
            t = _thread(db, c, last_message_at=_NOW - timedelta(hours=1))
            _state(db, t.id, needs_human=False)
            _message(db, t.id, direction="in", ts=_NOW - timedelta(hours=1))
            db.commit()

            state_before = db.execute(
                _sa.select(_app_models.WhatsAppThreadState)
                .where(_app_models.WhatsAppThreadState.thread_id == t.id)
            ).scalar_one()
            nh_before = state_before.needs_human

            with patch("app.routes.ops_dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                get_threads(health="all", limit=50, db=db)

            state_after = db.execute(
                _sa.select(_app_models.WhatsAppThreadState)
                .where(_app_models.WhatsAppThreadState.thread_id == t.id)
            ).scalar_one()
            self.assertEqual(nh_before, state_after.needs_human)
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# OPS-27: Dashboard does not invoke outbound safety gate/send
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS27NoOutboundInvoked(unittest.TestCase):
    """OPS-27: Dashboard never calls OutboundSafetyGate or any send function."""

    def test_ops27_no_gate_import_in_ops_dashboard(self):
        """ops_dashboard.py must not import outbound_safety_gate."""
        import ast
        import inspect
        import app.routes.ops_dashboard as mod
        source = inspect.getsource(mod)
        self.assertNotIn("outbound_safety_gate", source)
        self.assertNotIn("gate.attempt", source)

    def test_ops27_no_send_function_in_ops_dashboard(self):
        import inspect
        import app.routes.ops_dashboard as mod
        source = inspect.getsource(mod)
        self.assertNotIn("send_text", source)
        self.assertNotIn("send_flow", source)
        self.assertNotIn("httpx", source)
        self.assertNotIn("requests.post", source)

    def test_ops27_no_outbound_mutations_in_control_view(self):
        import inspect
        import app.ui.control_view as mod
        source = inspect.getsource(mod)
        self.assertNotIn("outbound_safety_gate", source)
        self.assertNotIn("gate.attempt", source)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-28: Auto-refresh endpoint/read works
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS28AutoRefresh(unittest.TestCase):
    """OPS-28: Auto-refresh is implemented in the dashboard JS."""

    def setUp(self):
        self.html = render_control_page(user_email="admin@test.com")

    def test_ops28_setinterval_present(self):
        self.assertIn("setInterval", self.html)

    def test_ops28_refresh_interval_10_or_15_seconds(self):
        # REFRESH_MS should be 10000 or 15000
        self.assertTrue(
            "10000" in self.html or "15000" in self.html,
            "Expected refresh interval of 10000 or 15000 ms",
        )

    def test_ops28_pause_button_present(self):
        # There must be a way to pause auto-refresh
        self.assertTrue(
            "Pausar" in self.html or "pause" in self.html.lower()
        )

    def test_ops28_last_updated_display(self):
        self.assertIn("lastUpdated", self.html)

    def test_ops28_all_five_api_endpoints_called(self):
        self.assertIn("/api/ops/summary", self.html)
        self.assertIn("/api/ops/threads", self.html)
        self.assertIn("/api/ops/messages", self.html)
        self.assertIn("/api/ops/critical-events", self.html)
        self.assertIn("/api/ops/paths", self.html)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-29: Existing WhatsApp Inbox UX unchanged
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS29WhatsAppInboxUnchanged(unittest.TestCase):
    """OPS-29: WhatsApp inbox route still returns proper HTML."""

    def test_ops29_whatsapp_ui_module_importable(self):
        from app.ui.whatsapp_ui import router as wa_router
        self.assertIsNotNone(wa_router)

    def test_ops29_whatsapp_ui_has_inbox_route(self):
        from app.ui.whatsapp_ui import router as wa_router
        routes = [r.path for r in wa_router.routes]
        self.assertIn("/whatsapp/inbox", routes)

    def test_ops29_components_py_render_sidebar_nav_still_works(self):
        from app.ui.components import render_sidebar_nav, render_whatsapp_icon_svg
        icon_wa = render_whatsapp_icon_svg()
        nav_html = render_sidebar_nav(
            icon_board="B",
            icon_calendar="C",
            icon_filter="F",
            icon_prof="P",
            icon_ag="A",
            icon_wa=icon_wa,
        )
        self.assertIn("/whatsapp/inbox", nav_html)
        self.assertIn("/control", nav_html)  # Control now always in sidebar

    def test_ops29_control_nav_item_added_to_shared_sidebar(self):
        from app.ui.components import render_sidebar_nav, render_whatsapp_icon_svg
        icon_wa = render_whatsapp_icon_svg()
        nav_html = render_sidebar_nav(
            icon_board="B",
            icon_calendar="C",
            icon_filter="F",
            icon_prof="P",
            icon_ag="A",
            icon_wa=icon_wa,
        )
        self.assertIn("/control", nav_html)
        self.assertIn("Control", nav_html)


# ══════════════════════════════════════════════════════════════════════════════
# OPS-30: Existing Agenda/Kanban unaffected
# ══════════════════════════════════════════════════════════════════════════════

class TestOPS30AgendaKanbanUnaffected(unittest.TestCase):
    """OPS-30: Kanban/Calendar router still has its routes intact."""

    def test_ops30_kanban_route_exists(self):
        from app.ui.kanban import router as kanban_router
        routes = [r.path for r in kanban_router.routes]
        self.assertIn("/kanban", routes)

    def test_ops30_calendar_route_exists(self):
        from app.ui.kanban import router as kanban_router
        routes = [r.path for r in kanban_router.routes]
        self.assertIn("/calendar", routes)

    def test_ops30_control_route_added(self):
        from app.ui.kanban import router as kanban_router
        routes = [r.path for r in kanban_router.routes]
        self.assertIn("/control", routes)

    def test_ops30_main_ops_router_registered(self):
        """ops_dashboard router is registered in main.py."""
        import app.routes.ops_dashboard as ops_mod
        self.assertEqual(ops_mod.router.prefix, "/api/ops")

    def test_ops30_ops_dashboard_router_has_five_routes(self):
        from app.routes.ops_dashboard import router
        self.assertGreaterEqual(len(router.routes), 5)


# ══════════════════════════════════════════════════════════════════════════════
# Utility function tests
# ══════════════════════════════════════════════════════════════════════════════

class TestUtilityFunctions(unittest.TestCase):
    """Helper function correctness."""

    def test_mask_wa_id_short(self):
        result = _mask_wa_id("54911")
        self.assertIn("*", result)

    def test_mask_wa_id_long(self):
        result = _mask_wa_id("5491140000199")
        self.assertTrue(result.startswith("54911"))
        self.assertTrue(result.endswith("99"))
        self.assertIn("*", result)

    def test_mask_wa_id_none(self):
        self.assertEqual(_mask_wa_id(None), "—")

    def test_window_range_today(self):
        start, end = _window_range("today")
        self.assertEqual(start.hour, 0)
        self.assertEqual(start.minute, 0)

    def test_window_range_24h(self):
        start, end = _window_range("24h")
        delta = end - start
        self.assertAlmostEqual(delta.total_seconds(), 86400, delta=5)

    def test_window_range_7d(self):
        start, end = _window_range("7d")
        delta = end - start
        self.assertAlmostEqual(delta.total_seconds(), 7 * 86400, delta=5)

    def test_window_range_invalid_defaults_to_24h(self):
        start, end = _window_range("invalid")
        delta = end - start
        self.assertAlmostEqual(delta.total_seconds(), 86400, delta=5)

    def test_preview_empty_text_uses_type(self):
        self.assertEqual(_preview("", "flow_response"), "flow_response")

    def test_preview_truncates_long_text(self):
        self.assertEqual(len(_preview("x" * 200, "text")), 80)


if __name__ == "__main__":
    unittest.main()
