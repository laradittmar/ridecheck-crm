"""SB2A-R7 — temporal values are rebuilt at capture, never shared (R6RV-01 … R6RV-04).

R6RV-01. An exact `time`/`datetime` passed exact-type admission while carrying a `tzinfo` the
caller chose. Capture kept it by reference, so caller code (`utcoffset`, …) ran inside the
booking transaction and a later offset change moved a committed `timestamptz`. Now every
booking date and time is a LOCAL CIVIL value: capture rebuilds each temporal value from its
components, an aware value is refused (`VALIDATION_FAILED / TEMPORAL_NOT_NAIVE:<field>`) before
the transaction scope and before any SQL, and no `tzinfo` method is ever called.

R6RV-02. The API refuses an offset `turno_hora` as a validation error, never a 500.
R6RV-03. `_CapturedCall` refuses deletion as well as assignment.
R6RV-04. A deterministic concurrent-redemption test (fails reliably without `_MINTED_LOCK`).

Real-PostgreSQL behaviour (driver adaptation, round trip): test_sb2a_r7_temporal_pg.py.
"""
from __future__ import annotations

import copy
import dataclasses
import gc
import os
import pathlib
import pickle
import sys
import threading
import types
import typing
import unittest
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (_ROOT / "backend", _ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import event  # noqa: E402

from app.models import Revision  # noqa: E402
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingCommand, BookingOperation as Op,  # noqa: E402
                                            BookingOutcome as Out, BookingSource as Src)

import test_sb2a_r3_typed_plans as r3  # noqa: E402  (world fixture only)

D = date(2026, 11, 3)


def _cmd(lead, **kw):
    base = dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D,
                requested_time=time(10))
    base.update(kw)
    return BookingCommand(**base)


class _SQLCounter:
    def __init__(self, db):
        self.engine, self.n = db.get_bind(), 0

    def _hit(self, *a, **k):
        self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *a):
        event.remove(self.engine, "before_cursor_execute", self._hit)


# ── hostile time zones ───────────────────────────────────────────────────────

TZ_CALLS: list = []


class RecordingTZ(tzinfo):
    """Every protocol method records that it ran; the offset can be changed at any time."""

    def __init__(self, hours=-3):
        self.offset = timedelta(hours=hours)

    def utcoffset(self, dt):
        TZ_CALLS.append("utcoffset")
        return self.offset

    def dst(self, dt):
        TZ_CALLS.append("dst")
        return timedelta(0)

    def tzname(self, dt):
        TZ_CALLS.append("tzname")
        return "HOSTILE"


class RaisingTZ(RecordingTZ):
    def utcoffset(self, dt):
        TZ_CALLS.append("utcoffset")
        raise RuntimeError("caller code ran")


class NoneOffsetTZ(RecordingTZ):
    """Python would call a time with this tzinfo 'naive'; the authority must not ask."""

    def utcoffset(self, dt):
        TZ_CALLS.append("utcoffset")
        return None


def _aware_times():
    return [time(10, tzinfo=RecordingTZ()), time(10, tzinfo=RaisingTZ()), time(10, tzinfo=NoneOffsetTZ()),
            time(10, tzinfo=timezone(timedelta(hours=-3))), time(10, tzinfo=timezone.utc)]


def _aware_datetimes():
    return [datetime(2026, 11, 1, 12, tzinfo=RecordingTZ()), datetime(2026, 11, 1, 12, tzinfo=RaisingTZ()),
            datetime(2026, 11, 1, 12, tzinfo=NoneOffsetTZ()),
            datetime(2026, 11, 1, 12, tzinfo=timezone(timedelta(hours=-3))),
            datetime(2026, 11, 1, 12, tzinfo=timezone.utc)]


def _temporal_columns():
    """Every Date/Time/DateTime column admissible through `revision_fields`, from metadata."""
    out = {}
    for name in sorted(ba._revision_data_columns()):
        col = Revision.__table__.c[name]
        tname = type(col.type).__name__
        if tname in ("Date", "Time", "DateTime"):
            out[name] = (tname, bool(getattr(col.type, "timezone", False)))
    return out


# ── 1. inventory ─────────────────────────────────────────────────────────────

class R7_Inventory(unittest.TestCase):

    def test_r7_01_temporal_inventory_and_policy(self):
        """The admissible temporal surface is exactly what the closeout inventories. A new
        temporal column must be classified (local civil / instant / metadata) before use."""
        self.assertEqual(_temporal_columns(), {
            "fecha_cobro": ("Date", False),
            "created_at": ("DateTime", False),
            "appointment_approval_sent_at": ("DateTime", True),
            "appointment_approved_at": ("DateTime", True),
        })
        self.assertEqual(ba._COMMAND_TEMPORALS, ("requested_date", "requested_time"))
        hints = typing.get_type_hints(BookingCommand)
        temporal = {f.name for f in dataclasses.fields(BookingCommand)
                    if {date, time, datetime} & set(typing.get_args(hints[f.name]) or (hints[f.name],))}
        self.assertEqual(temporal, set(ba._COMMAND_TEMPORALS))
        for cls in ba._PLAN_CLASSES.values():           # plans carry no temporal values
            for name, (kind, _n, _e) in ba._PLAN_SCHEMA[cls.__name__].items():
                self.assertIn(kind, ("int", "bool", "str", "choice", "plan", "str3"), (cls.__name__, name))


# ── 2. naive controls ────────────────────────────────────────────────────────

class R7_NaivePreserved(unittest.TestCase):

    def test_r7_02_naive_values_are_accepted_and_rebuilt(self):
        db, lead, tid = r3._world()
        d, t, dt, fc = date(2026, 11, 3), time(10, 30, 15, 250), datetime(2026, 10, 1, 9, 5), date(2026, 10, 2)
        cmd = _cmd(lead, requested_date=d, requested_time=t, idempotency_key="r7-naive",
                   revision_fields={"created_at": dt, "fecha_cobro": fc, "marca": "Fiat"})
        call = ba._capture_call(cmd, None, None)
        got = call.command
        for orig, cap in ((d, got.requested_date), (t, got.requested_time),
                          (dt, got.revision_fields["created_at"]), (fc, got.revision_fields["fecha_cobro"])):
            self.assertEqual((type(cap), cap), (type(orig), orig))
            self.assertIsNot(cap, orig, "temporal value was shared, not rebuilt")
        self.assertIsNone(got.requested_time.tzinfo)
        self.assertIsNone(got.revision_fields["created_at"].tzinfo)
        r = BookingAuthority(db)._execute_snapshot(call)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        db.expire_all()
        rev = db.get(Revision, r.revision_id)
        self.assertEqual((rev.turno_fecha, rev.turno_hora, rev.created_at, rev.fecha_cobro), (d, t, dt, fc))

    def test_r7_03_fold_and_microseconds_survive_the_rebuild(self):
        t = time(1, 30, 0, 999999, fold=1)
        dt = datetime(2026, 10, 4, 1, 30, 0, 1, fold=1)
        self.assertEqual(ba._capture_temporal(t).fold, 1)
        self.assertEqual(ba._capture_temporal(t), t)
        self.assertEqual((ba._capture_temporal(dt).fold, ba._capture_temporal(dt)), (1, dt))


# ── 3. aware values are refused before the transaction, without asking the tzinfo ──

class R7_AwareRefused(unittest.TestCase):

    def setUp(self):
        TZ_CALLS.clear()

    def _refused_before_scope(self, cmd, reason):
        db, lead, tid = r3._world()
        entered = []
        real = BookingAuthority._validate

        def spy(inner, c):
            entered.append(ba.booking_transaction_active())
            return real(inner, c)
        command = cmd(lead)                         # built outside the counter (lazy lead load)
        with mock.patch.object(BookingAuthority, "_validate", spy), _SQLCounter(db) as sql:
            r = BookingAuthority(db).execute(command)
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason))
        self.assertEqual((sql.n, entered), (0, []), "refusal reached validation or SQL")
        self.assertIsNone(ba.booking_transaction_active())
        self.assertEqual(r3._counts(db), (0, 0, 0))
        return r

    def test_r7_04_every_aware_time_is_refused(self):
        for t in _aware_times():
            self._refused_before_scope(lambda lead, t=t: _cmd(lead, requested_time=t, idempotency_key="k"),
                                       "TEMPORAL_NOT_NAIVE:requested_time")
        self.assertEqual(TZ_CALLS, [], "a tzinfo method was called to reject it")

    def test_r7_05_every_aware_datetime_is_refused_on_every_datetime_column(self):
        for col, (tname, _tz) in _temporal_columns().items():
            if tname != "DateTime":
                continue
            for v in _aware_datetimes():
                r = self._refused_before_scope(lambda lead, v=v: _cmd(lead, revision_fields={col: v}, idempotency_key="k2"),
                                               f"TEMPORAL_NOT_NAIVE:revision_fields.{col}")
                self.assertEqual(r.idempotency_key, "k2")
        self.assertEqual(TZ_CALLS, [])

    def test_r7_06_aware_datetime_as_requested_date_is_refused(self):
        self._refused_before_scope(
            lambda lead: _cmd(lead, requested_date=datetime(2026, 11, 3, tzinfo=RecordingTZ())),
            "TEMPORAL_NOT_NAIVE:requested_date")
        self.assertEqual(TZ_CALLS, [])

    def test_r7_07_timestamptz_columns_refuse_naive_values(self):
        """An absolute instant cannot come from a naive value without assuming the database's
        session timezone, so a booking command can never set one."""
        db, lead, tid = r3._world()
        for col, (tname, tz) in _temporal_columns().items():
            if tname == "DateTime" and tz:
                r = BookingAuthority(db).execute(_cmd(lead, revision_fields={col: datetime(2026, 11, 1, 12)}))
                self.assertEqual((r.outcome, r.reason),
                                 (Out.VALIDATION_FAILED, f"COMMAND_NOT_DATA:revision_fields.{col}"), col)
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r7_08_admission_itself_never_admits_an_aware_value(self):
        """Defence in depth: even an uncaptured aware value fails the column check."""
        for col, (tname, _tz) in _temporal_columns().items():
            for v in (_aware_times() if tname == "Time" else _aware_datetimes() if tname == "DateTime" else []):
                self.assertFalse(ba._revision_field_ok(col, v), col)
        self.assertEqual(TZ_CALLS, [])

    def test_r7_09_raw_caller_command_is_never_fingerprinted(self):
        """The fingerprint is computed only from the canonical capture: a caller value with a
        hostile tzinfo is never formatted (isoformat would call utcoffset)."""
        db, lead, tid = r3._world()
        seen = []
        real = ba.request_fingerprint

        def spy(c):
            seen.append(c)
            return real(c)
        tzv = datetime(2026, 11, 1, 12, tzinfo=RecordingTZ())
        with mock.patch.object(ba, "request_fingerprint", spy):
            r = BookingAuthority(db).execute(_cmd(lead, revision_fields={"created_at": tzv}))
        self.assertEqual(r.reason, "TEMPORAL_NOT_NAIVE:revision_fields.created_at")
        self.assertEqual((seen, TZ_CALLS), ([], []))


# ── 4. isolation: the capture never holds the caller's tzinfo ────────────────

class R7_Isolation(unittest.TestCase):

    def test_r7_10_capture_holds_no_tzinfo_and_no_caller_temporal(self):
        db, lead, tid = r3._world()
        tz = RecordingTZ()
        t, dt = time(10, tzinfo=tz), datetime(2026, 11, 1, 12, tzinfo=tz)
        call = ba._capture_call(_cmd(lead, requested_time=t, revision_fields={"created_at": dt}), None, None)
        got = call.command
        self.assertIs(got.requested_time, ba._AWARE_TEMPORAL)
        self.assertIs(got.revision_fields["created_at"], ba._AWARE_TEMPORAL)
        # explicit check: gc.get_referents cannot see into datetime/time (R6RV-04)
        for v in (got.requested_date, *got.revision_fields.values()):
            self.assertIsNot(v, t)
            self.assertIsNot(v, dt)
            if type(v) in (time, datetime):
                self.assertIsNone(v.tzinfo)
        seen, stack = set(), [call]
        while stack:
            o = stack.pop()
            if id(o) in seen or isinstance(o, (type, types.ModuleType)):
                continue
            seen.add(id(o))
            stack.extend(gc.get_referents(o))
        self.assertNotIn(id(tz), seen)
        r = BookingAuthority(db)._execute_snapshot(call)
        self.assertEqual(r.reason, "TEMPORAL_NOT_NAIVE:requested_time")

    def test_r7_11_persisted_value_is_the_canonical_capture(self):
        """What is flushed to the database is the rebuilt value, not the caller's object, and
        the fingerprint recorded in the ledger is that of the canonical capture."""
        db, lead, tid = r3._world()
        t, dt = time(11, 15), datetime(2026, 10, 1, 9, 5)
        rf = {"created_at": dt, "marca": "Fiat"}
        cmd = _cmd(lead, requested_time=t, idempotency_key="r7-canon", revision_fields=rf)
        flushed = []

        def before_flush(session, ctx, inst):
            for obj in session.new | session.dirty:
                if isinstance(obj, Revision):
                    flushed.append((obj.turno_hora, obj.created_at))
        event.listen(db, "before_flush", before_flush)
        try:
            r = BookingAuthority(db).execute(cmd)
        finally:
            event.remove(db, "before_flush", before_flush)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        self.assertTrue(flushed)
        for hora, created in flushed:
            self.assertIsNot(hora, t)
            self.assertIsNot(created, dt)
            self.assertEqual((hora, created), (t, dt))
        rf["created_at"] = datetime(1999, 1, 1)            # caller mutation after the call
        from app.models import BookingCommandRecord
        rec = db.query(BookingCommandRecord).filter_by(idempotency_key="r7-canon").one()
        canonical = _cmd(lead, requested_time=time(11, 15), idempotency_key="r7-canon",
                         revision_fields={"created_at": datetime(2026, 10, 1, 9, 5), "marca": "Fiat"})
        self.assertEqual(rec.request_fingerprint, ba.request_fingerprint(canonical))
        db.expire_all()
        self.assertEqual(db.get(Revision, r.revision_id).created_at, dt)


# ── 5. callers: API, Kanban, CE adapter ──────────────────────────────────────

class R7_Callers(unittest.TestCase):

    def test_r7_12_api_offset_time_is_a_422_on_every_path(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import revisions as api
        from app.db import get_db
        db, lead, tid = r3._world()
        app = FastAPI(); app.include_router(api.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[api.get_db] = lambda: db
        c = TestClient(app, raise_server_exceptions=False)
        for body in ({"turno_fecha": D.isoformat(), "turno_hora": "11:00:00-03:00"},           # booking path
                     {"turno_hora": "11:00:00Z"},                                             # partial: direct path
                     {"turno_fecha": D.isoformat(), "turno_hora": "11:00:00+00:00",
                      "estado_revision": "CANCELADO"}):                                       # non-occupying
            r = c.post(f"/leads/{lead.id}/revisions", json=body)
            self.assertEqual(r.status_code, 422, (body, r.text))
            self.assertIn("turno_hora", r.text)
        ok = c.post(f"/leads/{lead.id}/revisions", json={"turno_fecha": D.isoformat(), "turno_hora": "11:00:00"})
        self.assertEqual(ok.status_code, 200, ok.text)
        r = c.patch(f"/leads/{lead.id}/revisions/latest", json={"turno_hora": "12:00:00-03:00"})
        self.assertEqual(r.status_code, 422, r.text)
        db.expire_all()
        self.assertEqual(db.query(Revision).one().turno_hora, time(11))

    def test_r7_13_kanban_and_flow_parsed_offsets_are_refused_not_shifted(self):
        """Kanban and Flow parse with `time.fromisoformat`, which yields an aware time for
        '10:00-03:00'. The authority refuses it as a validation failure."""
        from app.ui import kanban_actions as ka
        db, lead, tid = r3._world()
        base = BookingAuthority(db).execute(_cmd(lead))
        self.assertEqual(base.outcome, Out.COMMITTED, base.reason)
        rev = db.get(Revision, base.revision_id)
        cmd = ba.command_for_edit(rev, source=Src.CRM, new_date=D, new_time=ka._to_time("12:00-03:00"),
                                  new_status=None, new_profesional_id=None, expected_version=None)
        r = BookingAuthority(db).execute(cmd)
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "TEMPORAL_NOT_NAIVE:requested_time"))
        db.expire_all()
        self.assertEqual(db.get(Revision, base.revision_id).turno_hora, time(10))

    def test_r7_14_ce_adapter_naive_unchanged_and_aware_refused(self):
        db, lead, tid = r3._world()
        TZ_CALLS.clear()
        bad = ce.request_manual_scheduling(db, ce.CeSchedulingEscalationRequest(
            thread_id=tid, lead_id=lead.id, cycle_key=9, requested_date=D,
            requested_time=time(10, tzinfo=RecordingTZ())))
        self.assertEqual((bad.outcome, bad.reason), (Out.VALIDATION_FAILED, "TEMPORAL_NOT_NAIVE:requested_time"))
        self.assertEqual(TZ_CALLS, [])
        req = ce.CeFlowBookingRequest(thread_id=tid, lead_id=lead.id, inbound_event_id="w.r7",
                                      flow_token="tok-1", requested_date=D, requested_time=time(10))
        ok = ce.book_from_flow_response(db, req)
        self.assertEqual(ok.outcome, Out.COMMITTED, ok.reason)
        self.assertEqual(ok.idempotency_key, ce.ce_flow_booking_key(req))
        self.assertEqual(r3._counts(db), (1, 0, 1))


# ── 6. the capability contract ───────────────────────────────────────────────

class R7_Capability(unittest.TestCase):

    def test_r7_15_capture_refuses_assignment_deletion_copy_and_forgery(self):
        db, lead, tid = r3._world()
        call = ba._capture_call(_cmd(lead, idempotency_key="cap"), None, None)
        for name in ("command", "availability", "attachments"):
            with self.assertRaises(AttributeError):
                setattr(call, name, None)
            with self.assertRaises(AttributeError):
                delattr(call, name)
        for clone in (copy.copy, copy.deepcopy, lambda c: pickle.loads(pickle.dumps(c))):
            with self.assertRaises(TypeError):
                clone(call)

        class Forged(ba._CapturedCall):
            __slots__ = ()
        forged = object.__new__(Forged)
        for name in ("command", "availability", "attachments"):
            object.__setattr__(forged, name, getattr(call, name))
        with ba._MINTED_LOCK:
            ba._MINTED.add(forged)                 # even smuggled into the registry
        auth = BookingAuthority(db)
        with _SQLCounter(db) as sql:
            self.assertEqual(auth._execute_snapshot(forged).reason, "UNCAPTURED_EXECUTION")
        self.assertEqual(sql.n, 0)
        self.assertEqual(auth._execute_snapshot(call).outcome, Out.COMMITTED)
        with _SQLCounter(db) as sql:
            self.assertEqual(auth._execute_snapshot(call).reason, "UNCAPTURED_EXECUTION")
        self.assertEqual(sql.n, 0)


class _GatedRegistry:
    """Wraps the real registry. Every membership test waits at a barrier until all contenders
    have asked, or until the barrier times out. Under `_MINTED_LOCK` only one contender can be
    inside, so the barrier times out and the others then see the capture already discarded.
    Without the lock all contenders are inside together, all see the capture, and all win:
    the race is forced, not hoped for."""

    def __init__(self, inner, parties, timeout):
        self.inner, self.barrier, self.timeout = inner, threading.Barrier(parties), timeout

    def __contains__(self, item):
        present = item in self.inner
        try:
            self.barrier.wait(self.timeout)
        except threading.BrokenBarrierError:
            pass
        return present

    def add(self, item):
        self.inner.add(item)

    def discard(self, item):
        self.inner.discard(item)


class R7_ConcurrentRedemption(unittest.TestCase):

    def test_r7_16_simultaneous_redemption_executes_exactly_once(self):
        n = 4
        worlds = [r3._world() for _ in range(n)]
        db0, lead0, _ = worlds[0]
        call = ba._capture_call(_cmd(lead0, idempotency_key="race"), None, None)
        validated = []
        real_val = BookingAuthority._validate

        def spy(inner, c):
            validated.append(threading.get_ident())
            return real_val(inner, c)
        start = threading.Barrier(n)
        results = [None] * n

        def contender(i):
            db = worlds[i][0]
            with _SQLCounter(db) as sql:
                start.wait()
                r = BookingAuthority(db)._execute_snapshot(call)
            results[i] = (r.outcome, r.reason, sql.n)
        gated = _GatedRegistry(ba._MINTED, n, timeout=1.0)
        with mock.patch.object(ba, "_MINTED", gated), mock.patch.object(BookingAuthority, "_validate", spy):
            threads = [threading.Thread(target=contender, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        winners = [r for r in results if r[0] is not Out.VALIDATION_FAILED]
        losers = [r for r in results if r[0] is Out.VALIDATION_FAILED]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(len(validated), 1, "more than one contender entered validation")
        self.assertEqual(len(losers), n - 1)
        for outcome, reason, sql in losers:
            self.assertEqual((reason, sql), ("UNCAPTURED_EXECUTION", 0))


if __name__ == "__main__":
    unittest.main()
