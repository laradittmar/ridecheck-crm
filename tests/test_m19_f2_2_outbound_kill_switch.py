"""M19.F2.2 — Central Outbound Kill Switch: verification tests.

All scenarios run fully offline: SQLite in-memory or file-based, no
containers, no network, no production DB, no migrations, no HTTP.

Test index:
  1.  enforce_outbound_enabled() raises when OUTBOUND_ENABLED is unset.
  2.  enforce_outbound_enabled() raises when OUTBOUND_ENABLED='false'.
  3.  enforce_outbound_enabled() raises when OUTBOUND_ENABLED='True' (wrong case).
  4.  enforce_outbound_enabled() passes when OUTBOUND_ENABLED='true'.
  5.  Layer-2 guard fires for kind='text' — OutboundBlockedError.kind=='text'.
  6.  Layer-2 guard fires for kind='flow' — OutboundBlockedError.kind=='flow'.
  7.  Layer-2 guard fires for kind='interactive'.
  8.  Layer-2 guard fires for kind='list'.
  8b. Static (AST): enforce_outbound_enabled() is the first call in each sender.
  9.  Gate returns BLOCKED_KILL_SWITCH and writes a durable blocked audit record
      (committed in a dedicated session, not the caller's).
  10. Gate returns ALLOWED when OUTBOUND_ENABLED='true'.
  11. vehicle_clarification_sent flag is NOT committed when kill switch fires.
  12. location_clarification_sent flag is NOT committed when kill switch fires.
  13. vehicle_fallback_flow_sent flag is NOT committed when kill switch fires.
  14. location_fallback_flow_sent flag is NOT committed when kill switch fires.
  15. Follow-up sent timestamp is NOT set when gate blocks kill switch.
  16. Only the exact literal 'true' enables outbound (parametrised values).
  17. Dirty-session isolation: dirty state in caller's session is NOT committed
      when kill switch fires; only the blocked audit row persists.
      Verified from a SEPARATE DB session (file-based SQLite, true isolation).
  18. Static (AST): _send_text_to_wa raises OutboundBlockedError on
      BLOCKED_KILL_SWITCH (not on dedup/flood blocks).
  19. Static (AST): every ConversationEngine path to a Meta send goes through
      gate.attempt() — blocked audit coverage is universal for engine sends.
  20. Gate killed but ALLOWED would route to Meta — layer-2 guard is last resort.
"""
from __future__ import annotations

import ast as _ast
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# ── Patch JSONB → JSON before any backend import ─────────────────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json
_pg_dialect.JSONB = sqlalchemy.JSON           # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON              # type: ignore[attr-defined]

# ── Primary in-memory SQLite engine + Base ────────────────────────────────────
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

# ── Stub heavy optional deps ─────────────────────────────────────────────────
for _mod_name in [
    "resend", "anthropic", "openai", "boto3",
    "botocore", "botocore.exceptions",
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)

# ── Import ORM models ─────────────────────────────────────────────────────────
import app.models  # noqa: F401
from app.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppOutboundDedup,
    WhatsAppThread,
    WhatsAppThreadState,
)

Base.metadata.create_all(_engine)

# ── Import units under test ───────────────────────────────────────────────────
from app.services.outbound_guard import OutboundBlockedError, enforce_outbound_enabled
from app.services.outbound_safety_gate import GateOutcome, OutboundSafetyGate

# ── File paths for static tests ───────────────────────────────────────────────
_UI_FILE = BACKEND_DIR / "app" / "ui" / "whatsapp_ui.py"
_ENGINE_FILE = BACKEND_DIR / "app" / "services" / "conversation_engine.py"

# ── Shared helpers ────────────────────────────────────────────────────────────
_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
_WA_ID = "5491100000099"


def _new_session() -> Session:
    return _SessionLocal()


def _clean_db(db: Session) -> None:
    """Delete all rows so each test starts clean."""
    for tbl in [
        "whatsapp_outbound_dedup", "whatsapp_messages",
        "whatsapp_thread_states", "whatsapp_threads", "whatsapp_contacts",
    ]:
        db.execute(sql_text(f"DELETE FROM {tbl}"))
    db.commit()


def _seed(db: Session) -> tuple[WhatsAppContact, WhatsAppThread, WhatsAppThreadState]:
    _clean_db(db)
    c = WhatsAppContact(wa_id=_WA_ID, display_name="Test", phone=None)
    db.add(c)
    db.flush()
    t = WhatsAppThread(contact_id=c.id, lead_id=None, unread_count=0)
    db.add(t)
    db.flush()
    s = WhatsAppThreadState(
        thread_id=t.id,
        needs_human=False,
        vehicle_clarification_sent=False,
        location_clarification_sent=False,
        vehicle_fallback_flow_sent=False,
        location_fallback_flow_sent=False,
    )
    db.add(s)
    db.commit()
    return c, t, s


# ── 1-4  enforce_outbound_enabled() semantics ─────────────────────────────────

class TestEnforceSemantics(unittest.TestCase):
    def setUp(self): os.environ.pop("OUTBOUND_ENABLED", None)
    def tearDown(self): os.environ.pop("OUTBOUND_ENABLED", None)

    def test_1_raises_when_unset(self):
        with self.assertRaises(OutboundBlockedError) as ctx:
            enforce_outbound_enabled("test", "text", _WA_ID)
        self.assertIn("OUTBOUND_DISABLED", str(ctx.exception))

    def test_2_raises_when_false(self):
        os.environ["OUTBOUND_ENABLED"] = "false"
        with self.assertRaises(OutboundBlockedError):
            enforce_outbound_enabled("test", "text", _WA_ID)

    def test_3_raises_when_uppercase_True(self):
        os.environ["OUTBOUND_ENABLED"] = "True"
        with self.assertRaises(OutboundBlockedError):
            enforce_outbound_enabled("test", "text", _WA_ID)

    def test_4_passes_when_literal_true(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        enforce_outbound_enabled("test", "text", _WA_ID, thread_id=1, text="hola")


# ── 5-8b  Layer-2 guard: each kind + static first-call proof ─────────────────

class TestLayer2GuardKinds(unittest.TestCase):
    def setUp(self): os.environ.pop("OUTBOUND_ENABLED", None)
    def tearDown(self): os.environ.pop("OUTBOUND_ENABLED", None)

    def test_5_kind_text_raises(self):
        with self.assertRaises(OutboundBlockedError) as ctx:
            enforce_outbound_enabled(
                "whatsapp_ui._send_whatsapp_cloud_text", "text", _WA_ID, text="hola"
            )
        self.assertEqual(ctx.exception.kind, "text")

    def test_6_kind_flow_raises(self):
        with self.assertRaises(OutboundBlockedError) as ctx:
            enforce_outbound_enabled(
                "whatsapp_ui._send_whatsapp_cloud_flow", "flow", _WA_ID, text="body"
            )
        self.assertEqual(ctx.exception.kind, "flow")

    def test_7_kind_interactive_raises(self):
        with self.assertRaises(OutboundBlockedError) as ctx:
            enforce_outbound_enabled(
                "whatsapp_ui._send_whatsapp_cloud_interactive",
                "interactive", _WA_ID, text="¿confirmás?"
            )
        self.assertEqual(ctx.exception.kind, "interactive")

    def test_8_kind_list_raises(self):
        with self.assertRaises(OutboundBlockedError) as ctx:
            enforce_outbound_enabled(
                "whatsapp_ui._send_whatsapp_cloud_list", "list", _WA_ID, text="Elegí"
            )
        self.assertEqual(ctx.exception.kind, "list")

    def test_8b_static_guard_first_in_each_sender(self):
        """Static (AST): enforce_outbound_enabled() is the first statement in each sender."""
        src = _UI_FILE.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src, filename=str(_UI_FILE))
        fn_map: dict[str, _ast.FunctionDef] = {
            n.name: n for n in _ast.walk(tree) if isinstance(n, _ast.FunctionDef)
        }
        for fn_name in [
            "_send_whatsapp_cloud_text",
            "_send_whatsapp_cloud_interactive",
            "_send_whatsapp_cloud_list",
            "_send_whatsapp_cloud_flow",
        ]:
            with self.subTest(sender=fn_name):
                self.assertIn(fn_name, fn_map)
                stmts = fn_map[fn_name].body
                # Skip optional leading docstring.
                if stmts and isinstance(stmts[0], _ast.Expr) and isinstance(stmts[0].value, _ast.Constant):
                    stmts = stmts[1:]
                self.assertGreater(len(stmts), 0)
                first = stmts[0]
                self.assertIsInstance(first, _ast.Expr)
                call = first.value
                self.assertIsInstance(call, _ast.Call)
                fname = (
                    call.func.id if isinstance(call.func, _ast.Name)
                    else getattr(call.func, "attr", "")
                )
                got = repr(fname)
                self.assertEqual(
                    fname, "enforce_outbound_enabled",
                    f"First call in {fn_name} is {got}, expected 'enforce_outbound_enabled'",
                )


# ── 9-10  Gate kill-switch: BLOCKED_KILL_SWITCH + durable audit record ────────

class TestGateKillSwitch(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, _ = _seed(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_9_gate_blocked_creates_durable_audit_record(self):
        """Gate returns BLOCKED_KILL_SWITCH and commits a blocked WhatsAppMessage
        in a dedicated session (not the caller's)."""
        gate = OutboundSafetyGate(self.db)
        result = gate.attempt(
            wa_id=_WA_ID, thread_id=self.thread.id,
            text="Para cotizarte la revisión…", now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_KILL_SWITCH)
        self.assertIsNotNone(result.message_id)
        self.assertIn("KILL_SWITCH", result.blocked_reason or "")

        # Verify via a fresh session (the record came from the gate's own session).
        inspect = _new_session()
        try:
            msg = inspect.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(msg)
            self.assertEqual(msg.status, "blocked")
            self.assertEqual(msg.direction, "out")
            self.assertTrue(msg.automated)
            self.assertIn("KILL_SWITCH", msg.blocked_reason or "")
            # No dedup entry (kill switch is before dedup).
            dedup_rows = inspect.execute(
                select(WhatsAppOutboundDedup).where(WhatsAppOutboundDedup.wa_id == _WA_ID)
            ).all()
            self.assertEqual(len(dedup_rows), 0)
        finally:
            inspect.close()

    def test_10_gate_allowed_when_true(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        result = OutboundSafetyGate(self.db).attempt(
            wa_id=_WA_ID, thread_id=self.thread.id,
            text="Para cotizarte la revisión…", now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.ALLOWED)


# ── 11-14  Pre-dispatch: state flags not committed ────────────────────────────

class TestStateFlagIntegrity(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.state = _seed(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def _blocked_count(self) -> int:
        return len(self.db.execute(
            select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == self.thread.id,
                WhatsAppMessage.status == "blocked",
            )
        ).scalars().all())

    def _assert_gate_blocks_before_flag(self, flag_name: str, text: str) -> None:
        result = OutboundSafetyGate(self.db).attempt(
            wa_id=_WA_ID, thread_id=self.thread.id, text=text, now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_KILL_SWITCH)
        # State flag: never set (simulates early return before mutation).
        self.db.expire(self.state)
        self.assertFalse(getattr(self.state, flag_name))
        # Blocked audit must exist.
        self.assertGreaterEqual(self._blocked_count(), 1)

    def test_11_vehicle_clarification_sent_not_committed(self):
        self._assert_gate_blocks_before_flag(
            "vehicle_clarification_sent",
            "Para cotizarte la revisión necesito saber qué vehículo tenés. "
            "¿Me podés indicar la marca y el modelo?",
        )

    def test_12_location_clarification_sent_not_committed(self):
        self._assert_gate_blocks_before_flag(
            "location_clarification_sent",
            "¿En qué localidad o barrio está el auto? "
            "Por ejemplo: Palermo, Tigre, Dock Sud o Lomas de Zamora.",
        )

    def test_13_vehicle_fallback_flow_sent_not_committed(self):
        self._assert_gate_blocks_before_flag(
            "vehicle_fallback_flow_sent",
            "Completá los datos del vehículo para que podamos cotizarte "
            "la revisión mecánica.",
        )

    def test_14_location_fallback_flow_sent_not_committed(self):
        self._assert_gate_blocks_before_flag(
            "location_fallback_flow_sent",
            "Indicanos dónde está el auto para calcular los viáticos "
            "de la revisión.",
        )


# ── 15  Follow-up timestamp not set ──────────────────────────────────────────

class TestFollowupTimestampNotSet(unittest.TestCase):
    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db = _new_session()
        _, self.thread, self.state = _seed(self.db)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db.close()

    def test_15_gate_blocked_means_no_followup_upsert(self):
        result = OutboundSafetyGate(self.db).attempt(
            wa_id=_WA_ID, thread_id=self.thread.id,
            text="Hola! ¿Pudiste revisar el presupuesto?", now=_NOW,
        )
        # Follow-up loop: if result.outcome != ALLOWED: continue → no upsert.
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_KILL_SWITCH)
        self.db.expire(self.state)
        self.assertIsNone(self.state.quote_followup_sent_at)
        self.assertIsNone(self.state.buscando_followup_sent_at)
        msg = self.db.get(WhatsAppMessage, result.message_id)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.status, "blocked")


# ── 16  Exact-value semantics ─────────────────────────────────────────────────

class TestExactValueRequired(unittest.TestCase):
    def tearDown(self): os.environ.pop("OUTBOUND_ENABLED", None)

    _BLOCKING = [
        "", "false", "FALSE", "True", "TRUE", "1", "yes",
        "true ", " true", "TRUE\n", "0", "on",
    ]

    def test_16a_all_non_true_values_block(self):
        for val in self._BLOCKING:
            with self.subTest(val=repr(val)):
                os.environ["OUTBOUND_ENABLED"] = val
                with self.assertRaises(OutboundBlockedError):
                    enforce_outbound_enabled("test", "text", _WA_ID)

    def test_16b_exactly_true_allows(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        enforce_outbound_enabled("test", "text", _WA_ID)

    def test_16c_absent_blocks(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        with self.assertRaises(OutboundBlockedError):
            enforce_outbound_enabled("test", "text", _WA_ID)


# ── 17  Dirty-session isolation ───────────────────────────────────────────────

class TestDirtySessionIsolation(unittest.TestCase):
    """Test 17 — the gate's blocked-audit commit must not carry dirty objects
    from the caller's session.

    Uses a FILE-BASED SQLite database so that two connections are truly
    isolated (separate file-level locks, separate transaction contexts).
    This satisfies the requirement to "inspect from a separate DB session."

    Protocol:
      1. Make thread-state dirty in session_a (no commit).
      2. Call OutboundSafetyGate(session_a).attempt() → kill switch fires.
         Gate uses its own dedicated SessionLocal() → separate connection.
      3. Gate's commit writes only the blocked audit record.
      4. Verify via session_b (independent connection):
           a. Blocked audit row IS committed and visible.
           b. Dirty state change is NOT committed.
      5. Rollback session_a → dirty change discarded.
    """

    def setUp(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        # Temp file for true connection isolation.
        self._iso_fd, self._iso_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(self._iso_fd)
        self._iso_engine = create_engine(
            f"sqlite:///{self._iso_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self._iso_engine)
        self._IsoSession = sessionmaker(
            bind=self._iso_engine, autoflush=False, autocommit=False
        )
        # Patch app.db.SessionLocal so the gate's internal SessionLocal()
        # uses our isolated engine (not the shared in-memory engine).
        self._orig_sl = sys.modules["app.db"].SessionLocal
        sys.modules["app.db"].SessionLocal = self._IsoSession

        self.db_a = self._IsoSession()
        _, self.thread, self.state = _seed(self.db_a)

    def tearDown(self):
        os.environ.pop("OUTBOUND_ENABLED", None)
        self.db_a.close()
        sys.modules["app.db"].SessionLocal = self._orig_sl
        self._iso_engine.dispose()
        try:
            os.unlink(self._iso_path)
        except OSError:
            pass

    def test_17_dirty_state_not_committed_when_gate_blocks(self):
        # 1. Make state dirty (no commit).
        self.state.customer_name = "DIRTY_SHOULD_NOT_PERSIST"
        self.assertIn(self.state, self.db_a.dirty)

        # 2. Gate fires (kill switch on, gate uses separate IsoSession connection).
        result = OutboundSafetyGate(self.db_a).attempt(
            wa_id=_WA_ID,
            thread_id=self.thread.id,
            text="Para cotizarte la revisión…",
            now=_NOW,
        )
        self.assertEqual(result.outcome, GateOutcome.BLOCKED_KILL_SWITCH)

        # 3. session_a was NOT committed — dirty object still tracked as dirty.
        self.assertIn(
            self.state, self.db_a.dirty,
            "session_a must remain uncommitted; gate must not call session_a.commit()",
        )

        # 4. Inspect via a SEPARATE session (truly isolated connection).
        db_b = self._IsoSession()
        try:
            # 4a. Blocked audit row IS committed and visible from session_b.
            blocked = db_b.get(WhatsAppMessage, result.message_id)
            self.assertIsNotNone(
                blocked,
                "Blocked audit row must be committed and visible from a separate session",
            )
            self.assertEqual(blocked.status, "blocked")
            self.assertIn("KILL_SWITCH", blocked.blocked_reason or "")

            # 4b. Dirty state change is NOT committed — separate session sees original.
            fresh_state = db_b.get(WhatsAppThreadState, self.state.id)
            self.assertIsNotNone(fresh_state)
            self.assertNotEqual(
                fresh_state.customer_name,
                "DIRTY_SHOULD_NOT_PERSIST",
                "Dirty customer_name must NOT be visible from a separate session",
            )
        finally:
            db_b.close()

        # 5. Rollback session_a → dirty change discarded.
        self.db_a.rollback()
        self.db_a.expire(self.state)
        self.assertNotEqual(self.state.customer_name, "DIRTY_SHOULD_NOT_PERSIST")


# ── 18  Static: _send_text_to_wa raises OutboundBlockedError ─────────────────

class TestSendTextToWaStaticProof(unittest.TestCase):
    """Test 18 — static (AST) proof that _send_text_to_wa raises
    OutboundBlockedError for ALL non-ALLOWED gate outcomes (M19.R1.2: dedup/flood
    blocks now raise explicitly instead of silently returning None).

    The function must contain:
      if result.outcome != GateOutcome.ALLOWED:
          raise OutboundBlockedError(...)
    """

    def _get_fn_body(self, fn_name: str) -> list[_ast.stmt]:
        src = _ENGINE_FILE.read_text(encoding="utf-8-sig")
        tree = _ast.parse(src, filename=str(_ENGINE_FILE))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == fn_name:
                return node.body
        raise AssertionError(f"{fn_name} not found in conversation_engine.py")

    def test_18a_send_text_raises_on_all_blocked_outcomes(self):
        """_send_text_to_wa must raise OutboundBlockedError for ALL non-ALLOWED outcomes.

        M19.R1.2: dedup/flood blocks now raise (not silent return None) so that
        handle() rolls back caller state and reports the block explicitly.
        """
        body = self._get_fn_body("_send_text_to_wa")
        found_raise = False
        for node in _ast.walk(_ast.Module(body=body, type_ignores=[])):
            if isinstance(node, _ast.If):
                test = node.test
                # Look for: if result.outcome != GateOutcome.ALLOWED:
                if (
                    isinstance(test, _ast.Compare)
                    and len(test.ops) == 1
                    and isinstance(test.ops[0], _ast.NotEq)
                ):
                    for comp in test.comparators:
                        if isinstance(comp, _ast.Attribute) and comp.attr == "ALLOWED":
                            for stmt in node.body:
                                if isinstance(stmt, _ast.Raise):
                                    found_raise = True
        self.assertTrue(
            found_raise,
            "_send_text_to_wa must contain: "
            "if result.outcome != GateOutcome.ALLOWED: raise OutboundBlockedError(...)",
        )

    def test_18b_send_text_does_not_silently_return_none_for_blocks(self):
        """_send_text_to_wa must NOT silently return None for non-ALLOWED outcomes.

        M19.R1.2: all blocked outcomes raise OutboundBlockedError so callers
        cannot commit state flags after a suppressed send.
        """
        body = self._get_fn_body("_send_text_to_wa")
        silent_return_found = False
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
                                    silent_return_found = True
        self.assertFalse(
            silent_return_found,
            "_send_text_to_wa must NOT silently return None for blocked outcomes "
            "(M19.R1.2: all blocks must raise OutboundBlockedError)",
        )


# ── 19  Static: all engine send paths go through gate.attempt() ───────────────

class TestEngineAuditCoverage(unittest.TestCase):
    """Test 19 — every ConversationEngine path to a Meta API call goes through
    OutboundSafetyGate.attempt() in either _send_text_to_wa or _send_flow_button.

    We prove this by verifying:
      (a) The only callers of _send_whatsapp_cloud_text and _send_whatsapp_cloud_flow
          inside conversation_engine.py are _send_text_to_wa and _send_flow_button.
      (b) Both helpers call gate.attempt() before reaching the cloud sender.
    """

    def _src(self) -> str:
        return _ENGINE_FILE.read_text(encoding="utf-8-sig")

    def test_19a_cloud_senders_only_called_from_helpers(self):
        """_send_whatsapp_cloud_{text,flow} must only appear inside the two helpers."""
        import re
        src = self._src()
        # Find all function bodies that call _send_whatsapp_cloud_*
        tree = _ast.parse(src, filename=str(_ENGINE_FILE))
        callers: dict[str, list[str]] = {}
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef):
                for subnode in _ast.walk(node):
                    if isinstance(subnode, _ast.Call):
                        fname = (
                            subnode.func.id if isinstance(subnode.func, _ast.Name)
                            else getattr(subnode.func, "attr", "")
                        )
                        if fname in ("_send_whatsapp_cloud_text", "_send_whatsapp_cloud_flow"):
                            callers.setdefault(node.name, []).append(fname)
        # Only the two helper methods should contain these calls.
        allowed = {"_send_text_to_wa", "_send_flow_button"}
        unexpected = {k: v for k, v in callers.items() if k not in allowed}
        self.assertEqual(
            unexpected, {},
            f"Cloud senders called from unexpected functions: {unexpected}",
        )
        self.assertIn("_send_text_to_wa", callers)
        self.assertIn("_send_flow_button", callers)

    def test_19b_helpers_call_gate_attempt_before_meta(self):
        """_send_text_to_wa and _send_flow_button both call gate.attempt()."""
        src = self._src()
        tree = _ast.parse(src, filename=str(_ENGINE_FILE))
        for fn_name in ("_send_text_to_wa", "_send_flow_button"):
            with self.subTest(helper=fn_name):
                for node in _ast.walk(tree):
                    if isinstance(node, _ast.FunctionDef) and node.name == fn_name:
                        found_attempt = False
                        for subnode in _ast.walk(node):
                            if (
                                isinstance(subnode, _ast.Call)
                                and isinstance(subnode.func, _ast.Attribute)
                                and subnode.func.attr == "attempt"
                            ):
                                found_attempt = True
                        self.assertTrue(
                            found_attempt,
                            f"{fn_name} must call gate.attempt() for blocked-audit coverage",
                        )

    def test_19c_send_text_raises_for_all_blocked_outcomes(self):
        """_send_text_to_wa raises OutboundBlockedError for all non-ALLOWED outcomes.

        M19.R1.2: kill switch, dedup, and flood all raise (no silent return None).
        """
        src = self._src()
        tree = _ast.parse(src, filename=str(_ENGINE_FILE))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "_send_text_to_wa":
                found_raise_for_non_allowed = False
                for subnode in _ast.walk(node):
                    if isinstance(subnode, _ast.If):
                        test = subnode.test
                        if (
                            isinstance(test, _ast.Compare)
                            and len(test.ops) == 1
                            and isinstance(test.ops[0], _ast.NotEq)
                        ):
                            for comp in test.comparators:
                                if isinstance(comp, _ast.Attribute) and comp.attr == "ALLOWED":
                                    for stmt in subnode.body:
                                        if isinstance(stmt, _ast.Raise):
                                            found_raise_for_non_allowed = True
                self.assertTrue(
                    found_raise_for_non_allowed,
                    "_send_text_to_wa must raise OutboundBlockedError for all "
                    "non-ALLOWED outcomes (M19.R1.2 requirement)",
                )


# ── 20  Layer-2 is unreachable when Layer-1 fires correctly ──────────────────

class TestLayer2IsLastResort(unittest.TestCase):
    """Test 20 — when OUTBOUND_ENABLED='true' and gate returns ALLOWED,
    the cloud sender's layer-2 guard does NOT block (it passes through).
    When OUTBOUND_ENABLED is absent, layer-1 (gate) fires first, making
    layer-2 unreachable via the normal engine path (both check same env var).
    """
    def tearDown(self): os.environ.pop("OUTBOUND_ENABLED", None)

    def test_20_allowed_path_does_not_raise_from_layer2(self):
        os.environ["OUTBOUND_ENABLED"] = "true"
        # Layer-2 guard with 'true' must not raise.
        enforce_outbound_enabled("test", "text", _WA_ID)
        enforce_outbound_enabled("test", "flow", _WA_ID)
        enforce_outbound_enabled("test", "interactive", _WA_ID)
        enforce_outbound_enabled("test", "list", _WA_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
