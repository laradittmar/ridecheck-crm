"""SB2A-R6 — exact-type admission by identity (F-01) and the capture capability (F-02).

F-01. `_revision_field_ok` admitted a Revision field when `type(value) in allowed`. Tuple
membership tests identity and then falls back to `==`, so a metaclass `__eq__` ran inside the
booking transaction's validation and could admit a str/int subclass, which was then written.
Admission is now `type(value) is T` for one of the column's declared types, and nothing else.

F-02. `_execute_snapshot` ran whatever it was handed; a direct call bypassed the R5 capture.
It now runs only a `_CapturedCall` minted by `_capture_call` (inside `execute`) and not yet
run. Anything else is refused before any lock, read or write.

Behavioural tests only; the one structural inventory (R6-08b) supplements, never replaces, a
behavioural check.
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import enum
import gc
import os
import pathlib
import pickle
import sys
import types
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (_ROOT / "backend", _ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import event  # noqa: E402

from app.models import Lead, Revision, ThreadRevision  # noqa: E402
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (AvailabilityRevalidation, BookingAttachmentPlan as Plan,  # noqa: E402
                                            BookingAuthority, BookingCommand, BookingOperation as Op,
                                            BookingOutcome as Out, BookingSource as Src, LeadIdentity,
                                            LeadUpdate, ThreadRevisionAttachment as TRA, ThreadStateUpdate)

import test_sb2a_r3_typed_plans as r3  # noqa: E402  (world fixture only)

D = date(2026, 11, 3)


def _cmd(lead, **kw):
    base = dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D,
                requested_time=time(10))
    base.update(kw)
    return BookingCommand(**base)


class _SQLCounter:
    """Counts every statement sent to the database while active."""

    def __init__(self, db):
        self.engine, self.n = db.get_bind(), 0

    def _hit(self, *a, **k):
        self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *a):
        event.remove(self.engine, "before_cursor_execute", self._hit)


# ── hostile types (F-01) ─────────────────────────────────────────────────────

EQ_CALLS: list = []


class _LyingMeta(type):
    """A metaclass whose classes claim equality with every type and record that they were asked."""

    def __eq__(cls, other):
        EQ_CALLS.append((cls.__name__, getattr(other, "__name__", other)))
        return True

    __hash__ = type.__hash__


class MStr(str, metaclass=_LyingMeta):
    pass


class MInt(int, metaclass=_LyingMeta):
    pass


class MDate(date, metaclass=_LyingMeta):
    pass


class SubStr(str):
    pass


class SubInt(int):
    pass


class SubDate(date):
    pass


class SubTime(time):
    pass


class SubDateTime(datetime):
    pass


class IntE(enum.IntEnum):
    ONE = 1


class StrE(str, enum.Enum):
    A = "a"


class _Truthy:
    def __bool__(self):
        return True

    def __eq__(self, other):
        EQ_CALLS.append(("Truthy", other))
        return True

    __hash__ = object.__hash__


def _columns_by_type():
    """One real Revision data column per SQL type name the admission table covers."""
    from app.models import Revision as R
    out = {}
    for name in sorted(ba._revision_data_columns()):
        tname = type(R.__table__.c[name].type).__name__
        if tname in ba._COLUMN_PY:
            out.setdefault(tname, name)
    return out


SAMPLE = {int: 7, str: "x", date: D, time: time(10), datetime: datetime(2026, 11, 3, 10), bool: True}


class R6_ExactAdmission(unittest.TestCase):

    def setUp(self):
        EQ_CALLS.clear()

    def test_r6_01_every_permitted_exact_type_is_accepted(self):
        cols = _columns_by_type()
        self.assertTrue({"Integer", "String"} <= set(cols), cols)
        for tname, col in cols.items():
            for t in ba._COLUMN_PY[tname]:
                self.assertTrue(ba._revision_field_ok(col, SAMPLE[t]), (tname, col, t))

    def test_r6_02_subclasses_and_impostors_are_rejected(self):
        cols = _columns_by_type()
        impostors = [SubStr("x"), SubInt(7), SubDate(2026, 11, 3), SubTime(10),
                     SubDateTime(2026, 11, 3, 10), IntE.ONE, StrE.A, Decimal(7), 7.0,
                     mock.MagicMock(), mock.MagicMock(spec=str), _Truthy(), b"x", ["x"], ("x",)]
        for tname, col in cols.items():
            for v in impostors:
                self.assertFalse(ba._revision_field_ok(col, v), (tname, col, type(v).__name__))
        if "Date" in cols:                        # a datetime is never a date
            self.assertFalse(ba._revision_field_ok(cols["Date"], datetime(2026, 11, 3, 10)))
        self.assertEqual(EQ_CALLS, [])

    def test_r6_03_bool_is_never_an_int_and_int_is_never_a_bool(self):
        cols = _columns_by_type()
        for v in (True, False):
            self.assertFalse(ba._revision_field_ok(cols["Integer"], v))
        if "Boolean" in cols:
            for v in (0, 1):
                self.assertFalse(ba._revision_field_ok(cols["Boolean"], v))

    def test_r6_04_metaclass_equality_never_runs_and_never_admits(self):
        cols = _columns_by_type()
        for col, v in ((cols["String"], MStr("x")), (cols["Integer"], MInt(7))):
            self.assertFalse(ba._revision_field_ok(col, v), col)
        if "Date" in cols:
            self.assertFalse(ba._revision_field_ok(cols["Date"], MDate(2026, 11, 3)))
        self.assertEqual(EQ_CALLS, [], "admission consulted a caller-defined __eq__")

    def test_r6_04b_end_to_end_hostile_value_writes_nothing(self):
        db, lead, tid = r3._world()
        cols = _columns_by_type()
        for col, v in ((cols["String"], MStr("x")), (cols["Integer"], MInt(7)), (cols["Integer"], True)):
            r = BookingAuthority(db).execute(_cmd(lead, revision_fields={col: v}))
            self.assertEqual((r.outcome, r.reason),
                             (Out.VALIDATION_FAILED, f"COMMAND_NOT_DATA:revision_fields.{col}"), col)
        self.assertEqual(r3._counts(db), (0, 0, 0))
        self.assertEqual(EQ_CALLS, [])


# ── the capture capability (F-02) ────────────────────────────────────────────

class R6_UncapturedExecution(unittest.TestCase):

    def test_r6_05_direct_call_with_a_raw_command_fails_closed_before_any_sql(self):
        db, lead, tid = r3._world()
        auth = BookingAuthority(db)
        raw = _cmd(lead, thread_id=tid, idempotency_key="raw-direct")
        with _SQLCounter(db) as sql:
            r = auth._execute_snapshot(raw)
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "UNCAPTURED_EXECUTION"))
        self.assertEqual(sql.n, 0)
        self.assertIsNone(ba.booking_transaction_active())
        with _SQLCounter(db) as sql, self.assertRaises(TypeError):
            auth._execute_snapshot(raw, None, Plan(lead=LeadUpdate(estado="X")))   # the R5 signature
        self.assertEqual(sql.n, 0)
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r6_05b_forged_copied_and_replayed_captures_are_refused(self):
        db, lead, tid = r3._world()
        with self.assertRaises(TypeError):
            ba._CapturedCall()
        minted = ba._capture_call(_cmd(lead, idempotency_key="minted"), None, None)
        with self.assertRaises(AttributeError):
            minted.command = _cmd(lead)
        for clone in (copy.copy, copy.deepcopy, lambda c: pickle.loads(pickle.dumps(c))):
            with self.assertRaises(TypeError):
                clone(minted)
        forged = object.__new__(ba._CapturedCall)
        for name in ("command", "availability", "attachments"):
            object.__setattr__(forged, name, getattr(minted, name))
        auth = BookingAuthority(db)
        with _SQLCounter(db) as sql:
            r = auth._execute_snapshot(forged)
        self.assertEqual((r.reason, sql.n), ("UNCAPTURED_EXECUTION", 0))
        first = auth._execute_snapshot(minted)
        self.assertEqual(first.outcome, Out.COMMITTED, first.reason)
        with _SQLCounter(db) as sql:
            again = auth._execute_snapshot(minted)
        self.assertEqual((again.reason, sql.n), ("UNCAPTURED_EXECUTION", 0))
        self.assertEqual(r3._counts(db), (1, 0, 1))

    def test_r6_05c_capture_happens_before_validation(self):
        db, lead, tid = r3._world()
        order = []
        real_cap, real_val = ba._capture_call, BookingAuthority._validate

        def cap(*a):
            order.append("capture")
            return real_cap(*a)

        def val(inner, cmd):
            order.append("validate")
            return real_val(inner, cmd)
        with mock.patch.object(ba, "_capture_call", cap), mock.patch.object(BookingAuthority, "_validate", val):
            BookingAuthority(db).execute(_cmd(lead, revision_fields={"marca": 5}))
        self.assertEqual(order, ["capture", "validate"])


class R6_GraphIsolation(unittest.TestCase):

    def _originals(self, tid, lead):
        rf = {"marca": "Fiat"}
        ident = LeadIdentity(nombre="Carlos")
        lu = LeadUpdate(identity=ident)
        tr = TRA(thread_id=tid, buyer_name="Carlos")
        st = ThreadStateUpdate(link_current_revision=True)
        plan = Plan(thread_revision=tr, lead=lu, thread_state=st)
        av = AvailabilityRevalidation()
        cmd = _cmd(lead, thread_id=tid, revision_fields=rf, idempotency_key="graph")
        return cmd, av, plan, dict(rf=rf, ident=ident, lu=lu, tr=tr, st=st, plan=plan, av=av, cmd=cmd)

    def test_r6_06_no_original_is_reachable_from_the_capture(self):
        db, lead, tid = r3._world()
        cmd, av, plan, originals = self._originals(tid, lead)
        call = ba._capture_call(cmd, av, plan)
        seen, stack = set(), [call]
        while stack:                              # everything the capture can reach
            obj = stack.pop()
            if id(obj) in seen or isinstance(obj, (type, types.ModuleType)):
                continue
            seen.add(id(obj))
            stack.extend(gc.get_referents(obj))
        for name, o in originals.items():
            self.assertNotIn(id(o), seen, f"capture still references the caller's {name}")
        self.assertIs(type(call.command.revision_fields), types.MappingProxyType)
        with self.assertRaises(TypeError):
            call.command.revision_fields["lead_id"] = 999
        with self.assertRaises(dataclasses.FrozenInstanceError):
            call.command.lead_id = 999

    def test_r6_07_mutating_or_poisoning_originals_after_capture_changes_nothing(self):
        db, lead, tid = r3._world()
        cmd, av, plan, o = self._originals(tid, lead)
        call = ba._capture_call(cmd, None, plan)
        # after capture, before execution: every original is changed or poisoned
        o["rf"]["marca"] = "MUTATED"; o["rf"]["lead_id"] = 999; o["rf"]["anio"] = MInt(1)
        object.__setattr__(cmd, "requested_time", time(15))
        object.__setattr__(cmd, "idempotency_key", "other-key")
        object.__setattr__(cmd, "lead_id", 424242)
        object.__setattr__(o["tr"], "buyer_name", "MUTATED")
        object.__setattr__(o["ident"], "nombre", MStr("Poison"))
        object.__setattr__(plan, "thread_state", "not a plan")
        r = BookingAuthority(db)._execute_snapshot(call)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        db.expire_all()
        rev = db.get(Revision, r.revision_id)
        self.assertEqual((rev.marca, rev.anio, rev.lead_id, rev.turno_hora), ("Fiat", None, lead.id, time(10)))
        self.assertEqual(db.get(ThreadRevision, r.thread_revision_id).buyer_name, "Carlos")
        self.assertEqual(db.get(Lead, lead.id).nombre, "Carlos")
        self.assertEqual(r.idempotency_key, "graph")
        self.assertEqual(EQ_CALLS, [])


class R6_Adapter(unittest.TestCase):

    def _spy(self):
        minted, ran = [], []
        real_cap, real_run = ba._capture_call, BookingAuthority._execute_snapshot

        def cap(*a):
            c = real_cap(*a)
            minted.append(c)
            return c

        def run(inner, call):
            ran.append(call)
            return real_run(inner, call)
        return minted, ran, mock.patch.object(ba, "_capture_call", cap), \
            mock.patch.object(BookingAuthority, "_execute_snapshot", run)

    def test_r6_08_both_adapter_entries_execute_only_a_minted_capture(self):
        db, lead, tid = r3._world()
        minted, ran, p1, p2 = self._spy()
        with p1, p2:
            r1 = ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
                thread_id=tid, lead_id=lead.id, inbound_event_id="w.r6", flow_token=None,
                requested_date=D, requested_time=time(10), revision_fields={"marca": "Fiat"},
                attachments=Plan(thread_revision=TRA(thread_id=tid))))
            r2 = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
                thread_id=tid, lead_id=lead.id, cycle_key=4))
        self.assertEqual((r1.outcome, r2.outcome), (Out.COMMITTED, Out.COMMITTED), (r1.reason, r2.reason))
        self.assertEqual(len(minted), 2)
        self.assertEqual([type(c) for c in ran], [ba._CapturedCall] * 2)
        self.assertTrue(all(a is b for a, b in zip(minted, ran)))

    def test_r6_08b_execute_is_the_only_production_caller(self):
        """Supplementary inventory: no production module calls `_execute_snapshot` except
        `BookingAuthority.execute` (the behavioural guarantee is R6-05/05b)."""
        callers = []
        for path in (_ROOT / "backend" / "app").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Attribute) and node.attr == "_execute_snapshot" \
                                and fn.name != "_execute_snapshot":
                            callers.append((path.name, fn.name))
        self.assertEqual(callers, [("booking_authority.py", "execute")])


class R6_KeyStability(unittest.TestCase):
    """Golden values computed on b10dbd5 (before R6). R6 must not move any key."""

    GOLDEN = {
        "flow_token": "ce-flow-response:3940e098fb78170e3134cd62aff43e7e4acdfd776da012fd",
        "flow_msg": "ce-flow-response:e2a88bf3802a0eea0e1aefc833d7f4f758c90f4d3fec3a81",
        "esc_cycle": "ce-escalation:e01a01f5c399e438fc2b3571345d5440578420160aa0a13c",
        "esc_id": "ce-escalation:8573d4db26b003397a91d434ee5cc84dde585cff244d7143",
        "esc_msg": "ce-escalation:e2a88bf3802a0eea0e1aefc833d7f4f758c90f4d3fec3a81",
        "flow_idem": "flow:1dc269d00d970f1852f83c9bf70131f39669cddeae2064b0",
        "fingerprint": "2804f630238393a43a2f2d04aba0bc690d04a4a1cdc9dad7fd19bb4aadac6594",
    }

    def test_r6_09_idempotency_keys_and_fingerprint_are_unchanged(self):
        tok = "41-1760000000-abcdef0123456789"
        got = {
            "flow_token": ce.ce_flow_booking_key(ce.CeFlowBookingRequest(
                thread_id=41, lead_id=7, inbound_event_id="wamid.GOLDEN", flow_token=tok,
                requested_date=date(2026, 11, 3), requested_time=time(10, 30))),
            "flow_msg": ce.ce_flow_booking_key(ce.CeFlowBookingRequest(
                thread_id=41, lead_id=7, inbound_event_id="wamid.GOLDEN", flow_token=None)),
            "esc_cycle": ce.ce_escalation_key(ce.CeSchedulingEscalationRequest(thread_id=41, lead_id=7, cycle_key=12)),
            "esc_id": ce.ce_escalation_key(ce.CeSchedulingEscalationRequest(thread_id=41, lead_id=7, escalation_id="41:12")),
            "esc_msg": ce.ce_escalation_key(ce.CeSchedulingEscalationRequest(
                thread_id=41, lead_id=7, inbound_event_id="wamid.GOLDEN")),
            "flow_idem": ba.flow_idempotency_key(tok),
        }
        cmd = BookingCommand(operation=Op.CREATE, source=Src.CONVERSATION_ENGINE, idempotency_key="k",
                             lead_id=7, thread_id=41, requested_date=date(2026, 11, 3), requested_time=time(10, 30),
                             revision_fields={"marca": "Fiat", "anio": 2020, "zone_group": "Sur"})
        got["fingerprint"] = ba.request_fingerprint(cmd)
        self.assertEqual(got, self.GOLDEN)
        # the captured (read-only) command fingerprints identically to the caller's
        self.assertEqual(ba.request_fingerprint(ba._capture_call(cmd, None, None).command), self.GOLDEN["fingerprint"])

    def test_r6_09b_ledger_records_the_captured_key(self):
        db, lead, tid = r3._world()
        req = ce.CeSchedulingEscalationRequest(thread_id=tid, lead_id=lead.id, cycle_key=12)
        r = ce.request_manual_scheduling(db, req)
        self.assertEqual(r.idempotency_key, ce.ce_escalation_key(req))
        replay = ce.request_manual_scheduling(db, req)
        self.assertEqual((replay.outcome, replay.revision_id), (Out.IDEMPOTENT_REPLAY, r.revision_id))

    def test_r6_10_lock_keys_are_derived_from_the_captured_slot(self):
        db, lead, tid = r3._world()
        prof = db.query(ba_models().Profesional).one().id
        seen = []
        real = BookingAuthority._acquire_locks

        def spy(inner, keys):
            seen.append(keys)
            return real(inner, keys)
        c = _cmd(lead, idempotency_key="locks")
        with mock.patch.object(BookingAuthority, "_acquire_locks", spy):
            call = ba._capture_call(c, None, None)
            object.__setattr__(c, "requested_date", date(2030, 1, 1))     # after capture
            r = BookingAuthority(db)._execute_snapshot(call)
            moved = BookingAuthority(db).execute(BookingCommand(
                operation=Op.RESCHEDULE, source=Src.OTHER, revision_id=r.revision_id,
                requested_date=date(2026, 11, 5), requested_time=time(11)))
        self.assertEqual((r.outcome, moved.outcome), (Out.COMMITTED, Out.COMMITTED), (r.reason, moved.reason))
        self.assertEqual(seen, [((prof, D),), ((prof, D), (prof, date(2026, 11, 5)))])
        self.assertEqual((ba._ymd(D), ba.DATE_LOCK_NAMESPACE), (20261103, -1))


def ba_models():
    import app.models as m
    return m


class R6_ExistingSafety(unittest.TestCase):

    def test_r6_11_identity_conflict_still_preserves_the_canonical_lead(self):
        db, lead, tid = r3._world()
        lead.nombre = "Carlos"; db.commit()
        r = BookingAuthority(db).execute(_cmd(lead, thread_id=tid),
                                         attachments=Plan(lead=LeadUpdate(identity=LeadIdentity(nombre="Ana"))))
        self.assertEqual((r.outcome, r.identity_outcome), (Out.COMMITTED, "CONFLICT"))
        db.expire_all()
        self.assertEqual(db.get(Lead, lead.id).nombre, "Carlos")

    def test_r6_12_candidate_of_another_thread_is_still_refused(self):
        db, lead, tid = r3._world()
        from app.models import WhatsAppThreadCandidate
        other = r3._thread(db, lead)
        cand = WhatsAppThreadCandidate(thread_id=other, marca="VW", status="current_focus")
        db.add(cand); db.commit()
        r = BookingAuthority(db).execute(_cmd(lead, thread_id=tid),
                                         attachments=Plan(thread_revision=TRA(thread_id=tid, candidate_id=cand.id)))
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "ATTACHMENT_CANDIDATE_NOT_IN_THREAD"))
        self.assertEqual(r3._counts(db), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
