"""SB2A-R4 — strict plan typing, hostile-object safety, coherent lead identity, pre-dispatch
classification and the read-only attention query (SQLite / pure).

PostgreSQL evidence for the narrowed locked window, lock order, concurrent identities and the
write audit is in test_sb2a_r4_window_scope_pg.py.
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.models import BookingCommandRecord, Lead, Revision, ThreadRevision, WhatsAppThreadState  # noqa: E402
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (BookingAttachmentPlan as Plan, BookingAuthority,  # noqa: E402
                                            BookingCommand, BookingOperation as Op,
                                            BookingOutcome as Out, BookingSource as Src,
                                            LeadIdentity, LeadUpdate, PriceRecalculation,
                                            ThreadRevisionAttachment as TRA, ThreadStateUpdate,
                                            AvailabilityRevalidation)

import test_sb2a_r3_typed_plans as r3  # noqa: E402  (fixtures: _world, _thread, _counts)

D = date(2026, 11, 3)


def _cmd(lead, **kw):
    base = dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D,
                requested_time=time(10))
    base.update(kw)
    return BookingCommand(**base)


# ══ 1. Exact declared types ═════════════════════════════════════════════════

class R4_ExactTypes(unittest.TestCase):

    def assertRefused(self, r, prefix):
        self.assertEqual(r.outcome, Out.VALIDATION_FAILED, r.reason)
        self.assertTrue(r.reason.startswith(prefix), r.reason)

    def test_r4_01_boolean_fields_accept_only_literal_bool(self):
        db, lead, tid = r3._world()
        for bad in ("true", "false", "yes", "no", 1, 0, None.__class__, [], [1]):
            for plan in (Plan(thread_revision=TRA(thread_id=tid, request_approval=bad)),
                         Plan(thread_state=ThreadStateUpdate(consume_flow_token=bad)),
                         Plan(thread_state=ThreadStateUpdate(link_current_revision=bad)),
                         Plan(lead=LeadUpdate(necesita_humano=bad))):
                r = BookingAuthority(db).execute(_cmd(lead, thread_id=tid), attachments=plan)
                self.assertRefused(r, "PLAN_NOT_DATA")
            r = BookingAuthority(db).execute(_cmd(lead), availability=AvailabilityRevalidation(is_holiday=bad))
            self.assertRefused(r, "PLAN_NOT_DATA:availability")
            r = BookingAuthority(db).execute(_cmd(lead, tracks_acknowledgement=bad))
            self.assertRefused(r, "COMMAND_NOT_DATA:tracks_acknowledgement")
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r4_02_identifiers_accept_only_exact_int_never_bool(self):
        db, lead, tid = r3._world()

        class MyInt(int):
            pass
        for bad in (True, False, "1", 1.0, MyInt(tid), 0, -1):
            r = BookingAuthority(db).execute(_cmd(lead), attachments=Plan(thread_revision=TRA(thread_id=bad)))
            self.assertRefused(r, "PLAN_NOT_DATA")
            r = BookingAuthority(db).execute(_cmd(lead, thread_id=bad))
            self.assertRefused(r, "COMMAND_NOT_DATA:thread_id")
        for field in ("lead_id", "revision_id", "profesional_id"):
            r = BookingAuthority(db).execute(_cmd(lead, **{field: True}))
            self.assertRefused(r, f"COMMAND_NOT_DATA:{field}")
        r = BookingAuthority(db).execute(_cmd(lead, expected_version=False))
        self.assertRefused(r, "COMMAND_NOT_DATA:expected_version")
        r = BookingAuthority(db).execute(_cmd(lead), attachments=Plan(thread_revision=TRA(thread_id=tid, anio="2020")))
        self.assertRefused(r, "PLAN_NOT_DATA")
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r4_03_strings_are_exact_bounded_and_nul_free(self):
        db, lead, tid = r3._world()

        class S(str):
            pass
        for bad in (S("x"), b"x", 5, "x" * 121, "a\x00b"):
            r = BookingAuthority(db).execute(_cmd(lead), attachments=Plan(thread_revision=TRA(thread_id=tid, buyer_name=bad)))
            self.assertRefused(r, "PLAN_NOT_DATA")
        r = BookingAuthority(db).execute(_cmd(lead), attachments=Plan(lead=LeadUpdate(flag="x" * 41)))
        self.assertRefused(r, "PLAN_NOT_DATA")
        ok = BookingAuthority(db).execute(_cmd(lead), attachments=Plan(thread_revision=TRA(thread_id=tid, buyer_name="x" * 120)))
        self.assertEqual(ok.outcome, Out.COMMITTED, ok.reason)

    def test_r4_04_dates_times_collections_and_revision_fields(self):
        db, lead, _ = r3._world()
        cases = [
            (_cmd(lead, requested_date=datetime(2026, 11, 3, 10)), "COMMAND_NOT_DATA:requested_date"),
            (_cmd(lead, requested_time="10:00"), "COMMAND_NOT_DATA:requested_time"),
            (_cmd(lead, revision_fields={"marca": 5}), "COMMAND_NOT_DATA:revision_fields.marca"),
            (_cmd(lead, revision_fields={"anio": "2020"}), "COMMAND_NOT_DATA:revision_fields.anio"),
            (_cmd(lead, revision_fields={"anio": True}), "COMMAND_NOT_DATA:revision_fields.anio"),
            (_cmd(lead, revision_fields={"marca": "x" * 51}), "COMMAND_NOT_DATA:revision_fields.marca"),
            (_cmd(lead, revision_fields={1: "x"}), "COMMAND_NOT_DATA:revision_fields"),
            (_cmd(lead, revision_fields={"nope": "x"}), "FIELD_NOT_ALLOWED:nope"),
        ]
        for c, reason in cases:
            r = BookingAuthority(db).execute(c)
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason))
        for bad in (["AUTO", "CABA", "Palermo"], ("AUTO", "CABA"), ("AUTO", "CABA", 5), ("AUTO", "CABA", "a\x00")):
            r = BookingAuthority(db).execute(_cmd(lead), attachments=Plan(price=PriceRecalculation(require_inputs=bad)))
            self.assertRefused(r, "PLAN_NOT_DATA")
        ok = BookingAuthority(db).execute(_cmd(lead, revision_fields={"marca": "Fiat", "anio": 2020}))
        self.assertEqual(ok.outcome, Out.COMMITTED, ok.reason)

    def test_r4_05_unknown_and_missing_fields_cannot_enter(self):
        with self.assertRaises(TypeError):
            TRA(thread_id=1, surprise="x")              # construction rejects unknown fields
        with self.assertRaises(TypeError):
            TRA()                                       # missing required field
        with self.assertRaises(TypeError):
            LeadIdentity()
        db, lead, tid = r3._world()
        plan = Plan(thread_revision=TRA(thread_id=tid))
        object.__setattr__(plan.thread_revision, "surprise", "x")   # injected after construction
        r = BookingAuthority(db).execute(_cmd(lead), attachments=plan)
        self.assertRefused(r, "PLAN_NOT_DATA")
        self.assertEqual(r3._counts(db), (0, 0, 0))


# ══ 2. Validation executes no caller behaviour ═══════════════════════════════

class R4_HostileObjects(unittest.TestCase):

    def test_r4_06_validation_rejects_without_executing_anything(self):
        calls = []

        class Meta(type):
            def __instancecheck__(cls, inst):
                calls.append("__instancecheck__"); return True

        class Evil(metaclass=Meta):
            @property
            def __class__(self):
                calls.append("__class__"); return str

            def __bool__(self):
                calls.append("__bool__"); return False

            def __len__(self):
                calls.append("__len__"); return 0

            def __iter__(self):
                calls.append("__iter__"); return iter(())

            def __getattr__(self, name):
                calls.append(f"__getattr__:{name}"); return None

            def __eq__(self, other):
                calls.append("__eq__"); return True

            def __hash__(self):
                calls.append("__hash__"); return 0

            def __str__(self):
                calls.append("__str__"); return "x"

            @property
            def value(self):
                calls.append("property:value"); return "CREATE"

        db, lead, tid = r3._world()
        e = Evil()
        cases = {
            "plan.bool": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=Plan(thread_revision=TRA(thread_id=tid, request_approval=e))),
            "plan.str": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=Plan(lead=LeadUpdate(estado=e))),
            "plan.int": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=Plan(thread_revision=TRA(thread_id=e))),
            "plan.nested": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=Plan(lead=e)),
            "plan.identity": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=Plan(lead=LeadUpdate(identity=e))),
            "plan.tuple": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=Plan(price=PriceRecalculation(require_inputs=e))),
            "plan.whole": lambda: BookingAuthority(db).execute(_cmd(lead), attachments=e),
            "availability": lambda: BookingAuthority(db).execute(_cmd(lead), availability=e),
            "cmd.whole": lambda: BookingAuthority(db).execute(e),
            "cmd.operation": lambda: BookingAuthority(db).execute(_cmd(lead, operation=e)),
            "cmd.key": lambda: BookingAuthority(db).execute(_cmd(lead, idempotency_key=e)),
            "cmd.revision_fields": lambda: BookingAuthority(db).execute(_cmd(lead, revision_fields=e)),
            "cmd.revision_field_value": lambda: BookingAuthority(db).execute(_cmd(lead, revision_fields={"marca": e})),
            "adapter.flow": lambda: ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
                thread_id=tid, lead_id=lead.id, inbound_event_id=e, flow_token=e)),
            "adapter.escalation": lambda: ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
                thread_id=tid, lead_id=lead.id, escalation_id=e, cycle_key=e)),
            "adapter.whole": lambda: ce.request_manual_scheduling(db, e),
        }
        for name, fn in cases.items():
            calls.clear()
            r = fn()
            self.assertEqual(r.outcome, Out.VALIDATION_FAILED, (name, r.reason))
            self.assertEqual(calls, [], name)
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r4_07_validation_uses_type_identity_not_isinstance(self):
        for fn in (ba._plan_is_data, ba._field_ok, ba._ok_str, ba._revision_field_ok,
                   BookingAuthority._validate, ce._capture_request,   # SB2A-R5: capture replaces _request_is_data
                   ba._owned_fields, ba._capture_plan, ba._capture_command):
            self.assertNotIn("isinstance(", inspect.getsource(fn), fn.__name__)
        self.assertNotIn("getattr(cmd", inspect.getsource(BookingAuthority.execute))


# ══ 3. Coherent lead identity ════════════════════════════════════════════════

class R4_Identity(unittest.TestCase):

    def book(self, canonical, submitted, at=time(10)):
        db, lead, _ = r3._world()
        for k, v in canonical.items():
            setattr(lead, k, v)
        db.commit()
        r = BookingAuthority(db).execute(_cmd(lead, requested_time=at),
                                         attachments=Plan(lead=LeadUpdate(identity=LeadIdentity(**submitted))))
        db.expire_all()
        lead = db.get(Lead, lead.id)
        return r, (lead.nombre, lead.apellido, lead.email)

    def test_r4_08_policy_matrix(self):
        carlos = dict(nombre="Carlos", apellido="Pérez", email="carlos@example.invalid")
        cases = [
            ("empty → admitted", {}, carlos, "ADMITTED", ("Carlos", "Pérez", "carlos@example.invalid")),
            ("exact repeat", carlos, carlos, "UNCHANGED", ("Carlos", "Pérez", "carlos@example.invalid")),
            ("normalized repeat", carlos, dict(nombre=" carlos ", apellido="PÉREZ", email="Carlos@Example.invalid"),
             "UNCHANGED", ("Carlos", "Pérez", "carlos@example.invalid")),
            ("missing surname, consistent", dict(nombre="Carlos", email="carlos@example.invalid"), carlos,
             "FILLED", ("Carlos", "Pérez", "carlos@example.invalid")),
            ("missing email, consistent", dict(nombre="Carlos", apellido="Pérez"), carlos,
             "FILLED", ("Carlos", "Pérez", "carlos@example.invalid")),
            ("conflicting name", dict(nombre="Ana"), carlos, "CONFLICT", ("Ana", None, None)),
            ("conflicting surname", dict(nombre="Carlos", apellido="Gómez"), carlos, "CONFLICT", ("Carlos", "Gómez", None)),
            ("conflicting email", dict(nombre="Carlos", email="otro@example.invalid"), carlos,
             "CONFLICT", ("Carlos", None, "otro@example.invalid")),
            ("canonical email, submission without email", dict(email="carlos@example.invalid"),
             dict(nombre="Carlos"), "CONFLICT", (None, None, "carlos@example.invalid")),
        ]
        for name, canonical, submitted, outcome, after in cases:
            r, got = self.book(canonical, submitted)
            self.assertEqual(r.outcome, Out.COMMITTED, name)          # booking always stands
            self.assertEqual((r.identity_outcome, got), (outcome, after), name)

    def test_r4_09_the_audit_combination_can_no_longer_happen(self):
        r, got = self.book(dict(nombre="Ana"), dict(nombre="Carlos", apellido="Pérez", email="c@example.invalid"))
        self.assertEqual((r.identity_outcome, got), ("CONFLICT", ("Ana", None, None)))

    def test_r4_10_retry_of_the_same_bundle_is_idempotent(self):
        db, lead, _ = r3._world()
        plan = Plan(lead=LeadUpdate(identity=LeadIdentity(nombre="Carlos", apellido="Pérez")))
        c = _cmd(lead, idempotency_key="id-retry")
        first = BookingAuthority(db).execute(c, attachments=plan)
        again = BookingAuthority(db).execute(c, attachments=plan)
        self.assertEqual((first.identity_outcome, again.outcome), ("ADMITTED", Out.IDEMPOTENT_REPLAY))
        self.assertEqual(db.get(Lead, lead.id).apellido, "Pérez")


# ══ 4. Pre-dispatch classification ═══════════════════════════════════════════

class R4_PreDispatch(unittest.TestCase):

    def _send(self, *, token="real-token", phone="", wa_id="5491100000000", transport_effect=None):
        from app.services import booking_flow_service as bfs
        from app.services.outbound_safety_gate import GateOutcome
        # Convergence Phase 3 (resolves the Phase 1 deferral of r4_11): the receipt sender is
        # `booking_acknowledgement._send_ledger_acknowledgement`, `870b4e3`'s
        # `_send_booking_receipt` carried verbatim — the only success sender now.
        from app.services import booking_acknowledgement as ack
        db = mock.MagicMock()
        ctx = mock.MagicMock(); ctx.contact.wa_id = wa_id; ctx.thread.id = 1
        settings = mock.MagicMock(whatsapp_token=token, whatsapp_phone_number_id=phone)
        with mock.patch("app.settings.get_settings", return_value=settings), \
                mock.patch("app.services.outbound_safety_gate.OutboundSafetyGate") as G, \
                mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text") as T:
            G.return_value.attempt.return_value = mock.MagicMock(outcome=GateOutcome.ALLOWED, message_id=1)
            if transport_effect is not None:
                T.side_effect = transport_effect
            else:
                T.return_value = ("wamid.1", 200)
            out = ack._send_ledger_acknowledgement(db, ctx, name="", selected_date=D,
                                                   selected_time=time(10))
            return out, G.return_value.attempt.called, T.called

    def test_r4_11_configuration_known_before_dispatch_is_definitive(self):
        out, gate, net = self._send(phone="")
        self.assertEqual((out.sent, out.ambiguous, out.error, gate, net),
                         (False, False, "PRE_DISPATCH_CONFIG_PHONE_NUMBER_ID_MISSING", False, False))
        out, gate, net = self._send(phone="123", wa_id="  ")
        self.assertEqual((out.ambiguous, out.error, gate, net),
                         (False, "PRE_DISPATCH_CONFIG_DESTINATION_MISSING", False, False))
        from app.services.outbound_guard import OutboundBlockedError
        out, gate, net = self._send(phone="123", transport_effect=OutboundBlockedError(sender_path="x", kind="text", to_wa_id="0"))
        self.assertEqual((out.ambiguous, out.error), (False, "PRE_DISPATCH_OUTBOUND_BLOCKED"))
        out, _, _ = self._send(token="dummy", phone="")            # local dummy mode still sends
        self.assertTrue(out.sent)
        out, _, _ = self._send(phone="123", transport_effect=RuntimeError("after dispatch"))
        self.assertEqual((out.ambiguous, out.error), (True, "AMBIGUOUS_TRANSPORT_EXCEPTION"))

    def test_r4_12_definitive_pre_dispatch_failure_stays_retryable_without_risk(self):
        db, lead, _ = r3._world()
        BookingAuthority(db).execute(_cmd(lead, idempotency_key="pd", tracks_acknowledgement=True))
        auth = BookingAuthority(db)
        c = auth.claim_acknowledgement("pd")
        auth.record_acknowledgement(c, sent=False, error="PRE_DISPATCH_CONFIG_PHONE_NUMBER_ID_MISSING")
        rec = db.query(BookingCommandRecord).one(); db.refresh(rec)
        self.assertEqual((rec.ack_status, rec.ack_duplicate_risk), ("FAILED", False))
        self.assertIsNotNone(auth.claim_acknowledgement("pd"))      # retry after config fix


# ══ 5. Attention query is read-only ══════════════════════════════════════════

class R4_Attention(unittest.TestCase):

    def test_r4_13_read_is_pure_and_terminalization_is_explicit(self):
        db, lead, _ = r3._world()
        BookingAuthority(db).execute(_cmd(lead, idempotency_key="att", tracks_acknowledgement=True))
        auth = BookingAuthority(db)
        t = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for _ in range(ba.ACK_MAX_ATTEMPTS):
            with mock.patch.object(ba, "_app_now", return_value=t):
                auth.claim_acknowledgement("att")
            t += timedelta(seconds=ba.ACK_LEASE_SECONDS + 1)
        flushes, commits = [], []
        with mock.patch.object(ba, "_app_now", return_value=t), \
                mock.patch.object(db, "commit", side_effect=lambda: commits.append(1)), \
                mock.patch.object(db, "flush", side_effect=lambda *a, **k: flushes.append(1)):
            rows = auth.acknowledgements_needing_attention()
        self.assertEqual((commits, flushes), ([], []))
        self.assertEqual([(r["ack_status"], r["pending_terminalization"]) for r in rows], [("SENDING", True)])
        self.assertNotIn("_terminalize_exhausted", inspect.getsource(BookingAuthority.acknowledgements_needing_attention))
        with mock.patch.object(ba, "_app_now", return_value=t):
            self.assertEqual(auth.terminalize_exhausted_acknowledgements(), 1)
            self.assertEqual(auth.terminalize_exhausted_acknowledgements(), 0)


# ══ 6. Lock order is documented and implemented in that order ════════════════

class R4_LockOrderDoc(unittest.TestCase):

    def test_r4_14_lock_order_documented_and_followed(self):
        doc = ba.__doc__
        for step in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"):
            self.assertIn(step, doc)
        src = inspect.getsource(BookingAuthority._execute_snapshot)  # SB2A-R5: execute() captures, then runs _execute_snapshot()
        order = [src.index(m) for m in ("self._acquire_locks(", "self._lock_revision(",
                                         "self._lock_overlapping(", "self._apply_attachments(",
                                         "self._sync_thread_revision(", "self._record(")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("with_for_update", inspect.getsource(BookingAuthority._load_revision))
        self.assertNotIn("with_for_update", inspect.getsource(BookingAuthority._window_state))
        acq = inspect.getsource(BookingAuthority._acquire_locks)
        self.assertIn("sorted(", acq)


if __name__ == "__main__":
    unittest.main()
