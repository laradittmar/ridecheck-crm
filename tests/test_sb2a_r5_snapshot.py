"""SB2A-R5 — capture-first validated snapshot (SQLite / pure).

R3A-T1: the authority must never validate a caller-owned object and later re-read it. These
tests prove ownership of the snapshot, the capture-THEN-validate ordering (deterministically,
with a trace hook that mutates the caller object at the instant it is captured), resistance
to hostile `__dict__` contents, post-return mutation, the CE adapter boundary and a bounded
entry-time race. Real lock-wait evidence is in test_sb2a_r5_lockwait_pg.py.
"""
from __future__ import annotations

import os
import sys
import threading
import time as _time
import unittest
from datetime import date, time
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
                                            LeadIdentity, LeadUpdate, ThreadRevisionAttachment as TRA,
                                            ThreadStateUpdate, AvailabilityRevalidation)

import test_sb2a_r3_typed_plans as r3  # noqa: E402

D = date(2026, 11, 3)


def _cmd(lead, **kw):
    base = dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D,
                requested_time=time(10))
    base.update(kw)
    return BookingCommand(**base)


class _Spy:
    """Records the objects `_execute_snapshot` actually receives."""

    def __init__(self):
        self.args = None

    def __enter__(self):
        real = BookingAuthority._execute_snapshot
        spy = self

        def wrapped(inner, cmd, availability, attachments):
            spy.args = (cmd, availability, attachments)
            return real(inner, cmd, availability, attachments)
        self._p = mock.patch.object(BookingAuthority, "_execute_snapshot", wrapped)
        self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()


class R5_Ownership(unittest.TestCase):

    def test_r5_01_execution_receives_new_authority_owned_objects(self):
        db, lead, tid = r3._world()
        rf = {"marca": "Fiat"}
        c = _cmd(lead, thread_id=tid, revision_fields=rf)
        tr = TRA(thread_id=tid)
        ident = LeadIdentity(nombre="Carlos")
        lu = LeadUpdate(identity=ident)
        st = ThreadStateUpdate(link_current_revision=True)
        plan = Plan(thread_revision=tr, lead=lu, thread_state=st)
        av = AvailabilityRevalidation()
        with _Spy() as spy, mock.patch.object(BookingAuthority, "_revalidate_availability"):
            r = BookingAuthority(db).execute(c, availability=av, attachments=plan)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        cmd_s, av_s, plan_s = spy.args
        for original, captured in ((c, cmd_s), (rf, cmd_s.revision_fields), (av, av_s), (plan, plan_s),
                                   (tr, plan_s.thread_revision), (lu, plan_s.lead),
                                   (ident, plan_s.lead.identity), (st, plan_s.thread_state)):
            self.assertIsNot(original, captured)
            self.assertIs(type(original), type(captured))
        self.assertEqual(cmd_s.revision_fields, rf)
        self.assertIs(type(cmd_s.revision_fields), dict)

    def test_r5_02_mutation_after_return_changes_nothing(self):
        db, lead, tid = r3._world()
        rf = {"marca": "Fiat"}
        plan = Plan(thread_revision=TRA(thread_id=tid, buyer_name="Carlos"))
        c = _cmd(lead, thread_id=tid, revision_fields=rf, idempotency_key="after")
        r = BookingAuthority(db).execute(c, attachments=plan)
        rf["marca"] = "MUTATED"; rf["lead_id"] = 999
        object.__setattr__(plan.thread_revision, "buyer_name", "MUTATED")
        object.__setattr__(c, "requested_time", time(15))
        db.expire_all()
        rev = db.get(Revision, r.revision_id)
        self.assertEqual((rev.marca, rev.lead_id, rev.turno_hora), ("Fiat", lead.id, time(10)))
        self.assertEqual(db.get(ThreadRevision, r.thread_revision_id).buyer_name, "Carlos")


class R5_CaptureThenValidate(unittest.TestCase):
    """Deterministic ordering proof: a trace hook mutates the caller object at the very moment
    the authority starts capturing it. Capture-first sees the mutated (invalid) value and
    refuses it; a validate-first/copy-later implementation would have validated the old value
    and executed the new one."""

    def _run_with_hook(self, db, call, target_type, mutate):
        fired = []

        def tracer(frame, event, arg):
            if event == "call" and frame.f_code is ba._owned_fields.__code__ \
                    and type(frame.f_locals.get("obj")) is target_type and not fired:
                fired.append(1)
                mutate()
            return None
        sys.settrace(tracer)
        try:
            return call(), fired
        finally:
            sys.settrace(None)

    def test_r5_03_command_mutated_at_capture_is_validated_as_captured(self):
        db, lead, tid = r3._world()
        c = _cmd(lead, tracks_acknowledgement=True, idempotency_key="cap")
        r, fired = self._run_with_hook(db, lambda: BookingAuthority(db).execute(c), BookingCommand,
                                       lambda: object.__setattr__(c, "tracks_acknowledgement", "yes"))
        self.assertEqual(fired, [1])
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "COMMAND_NOT_DATA:tracks_acknowledgement"))
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r5_04_nested_plan_mutated_at_capture_is_validated_as_captured(self):
        db, lead, tid = r3._world()
        tr = TRA(thread_id=tid, request_approval=False)
        r, fired = self._run_with_hook(
            db, lambda: BookingAuthority(db).execute(_cmd(lead, thread_id=tid), attachments=Plan(thread_revision=tr)),
            TRA, lambda: object.__setattr__(tr, "request_approval", "no"))
        self.assertEqual(fired, [1])
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "PLAN_NOT_DATA:attachments"))
        self.assertEqual(r3._counts(db), (0, 0, 0))

    def test_r5_05_revision_fields_mutated_at_capture(self):
        db, lead, _ = r3._world()
        rf = {"marca": "Fiat"}
        c = _cmd(lead, revision_fields=rf)
        r, fired = self._run_with_hook(db, lambda: BookingAuthority(db).execute(c), BookingCommand,
                                       lambda: rf.__setitem__("lead_id", 999))
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "FIELD_NOT_ALLOWED:lead_id"))
        self.assertEqual(r3._counts(db), (0, 0, 0))


class R5_HostileContainers(unittest.TestCase):

    def test_r5_06_hostile_dict_contents_run_no_code(self):
        calls = []

        class K(str):
            def __hash__(self):
                calls.append("__hash__"); return 1

            def __eq__(self, o):
                calls.append("__eq__"); return False

        class DictSub(dict):
            def copy(self):
                calls.append("copy"); return dict(self)

            def __getitem__(self, k):
                calls.append("__getitem__"); return dict.__getitem__(self, k)
        db, lead, tid = r3._world()
        c = _cmd(lead)
        d = dict(object.__getattribute__(c, "__dict__"))
        evil = BookingCommand(**{k: v for k, v in d.items()})
        object.__setattr__(evil, "__dict__", {**d, K("x"): 1})               # non-str key
        calls.clear()                        # building the dict above hashed K — not the authority
        r1 = BookingAuthority(db).execute(evil)
        evil2 = BookingCommand(**d)
        object.__setattr__(evil2, "__dict__", DictSub(d))                    # __dict__ not exact dict
        r2 = BookingAuthority(db).execute(evil2)
        r3_ = BookingAuthority(db).execute(_cmd(lead, revision_fields=DictSub({"marca": "x"})))
        plan = Plan(thread_revision=TRA(thread_id=tid))
        object.__setattr__(plan, "__dict__", DictSub(object.__getattribute__(plan, "__dict__")))
        r4 = BookingAuthority(db).execute(_cmd(lead), attachments=plan)
        self.assertEqual([r.reason for r in (r1, r2, r3_, r4)],
                         ["COMMAND_NOT_DATA", "COMMAND_NOT_DATA", "COMMAND_NOT_DATA",   # dict subclass: not admitted
                          "PLAN_NOT_DATA:attachments"])
        self.assertEqual(calls, [])
        self.assertEqual(r3._counts(db), (0, 0, 0))


class R5_Adapter(unittest.TestCase):

    def test_r5_07_adapter_captures_and_passes_only_owned_objects(self):
        db, lead, tid = r3._world()
        rf = {"marca": "Fiat"}
        plan = Plan(thread_revision=TRA(thread_id=tid))
        req = ce.CeFlowBookingRequest(thread_id=tid, lead_id=lead.id, inbound_event_id="w.r5", flow_token=None,
                                      requested_date=D, requested_time=time(10), revision_fields=rf, attachments=plan)
        seen = {}
        real = BookingAuthority.execute

        def spy(inner, cmd, *, availability=None, attachments=None):
            seen["rf"], seen["plan"] = object.__getattribute__(cmd, "__dict__")["revision_fields"], attachments
            seen["key"] = object.__getattribute__(cmd, "__dict__")["idempotency_key"]
            # the caller mutates its request after the adapter captured it
            rf["lead_id"] = 999; rf["marca"] = "MUTATED"
            object.__setattr__(req, "thread_id", 424242)
            object.__setattr__(req, "inbound_event_id", "w.OTHER")
            object.__setattr__(plan.thread_revision, "request_approval", "no")
            return real(inner, cmd, availability=availability, attachments=attachments)
        with mock.patch.object(BookingAuthority, "execute", spy):
            r = ce.book_from_flow_response(db, req)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        self.assertIsNot(seen["rf"], rf)
        self.assertIsNot(seen["plan"], plan)
        self.assertIsNot(seen["plan"].thread_revision, plan.thread_revision)
        db.expire_all()
        rev = db.get(Revision, r.revision_id)
        tr = db.get(ThreadRevision, r.thread_revision_id)
        self.assertEqual((rev.marca, rev.lead_id, tr.thread_id, tr.appointment_approval_status),
                         ("Fiat", lead.id, tid, None))
        rec = db.query(BookingCommandRecord).one()
        self.assertEqual(rec.idempotency_key, seen["key"])
        object.__setattr__(req, "thread_id", tid); object.__setattr__(req, "inbound_event_id", "w.r5")
        self.assertEqual(ce.ce_flow_booking_key(req), seen["key"])

    def test_r5_08_escalation_adapter_also_captures(self):
        db, lead, tid = r3._world()
        rf = {"turno_notas": "manual"}
        req = ce.CeSchedulingEscalationRequest(thread_id=tid, lead_id=lead.id, cycle_key=3, revision_fields=rf)
        real = BookingAuthority.execute

        def spy(inner, cmd, *, availability=None, attachments=None):
            rf["turno_notas"] = "MUTATED"; object.__setattr__(req, "cycle_key", 99)
            return real(inner, cmd, availability=availability, attachments=attachments)
        with mock.patch.object(BookingAuthority, "execute", spy):
            r = ce.request_manual_scheduling(db, req)
        db.expire_all()
        self.assertEqual(db.get(Revision, r.revision_id).turno_notas, "manual")
        object.__setattr__(req, "cycle_key", 3)
        self.assertEqual(db.query(BookingCommandRecord).one().idempotency_key, ce.ce_escalation_key(req))


class R5_EntryRace(unittest.TestCase):
    """Bounded stress: a thread flips caller fields between a VALID and an INVALID version
    while bookings are submitted. Every outcome must be a typed refusal or a booking built
    only from validated values — never a string Boolean, a foreign thread or a merged identity."""

    def test_r5_09_entry_time_race_never_executes_unvalidated_data(self):
        db, lead, tid = r3._world()
        stop = threading.Event()
        c = _cmd(lead, thread_id=tid, tracks_acknowledgement=False)
        tr = TRA(thread_id=tid, request_approval=False)
        ident = LeadIdentity(nombre="Carlos")
        plan = Plan(thread_revision=tr, lead=LeadUpdate(identity=ident))

        def flip():
            i = 0
            while not stop.is_set():
                bad = i % 2
                object.__setattr__(tr, "request_approval", "no" if bad else False)
                object.__setattr__(c, "tracks_acknowledgement", "yes" if bad else False)
                object.__setattr__(ident, "nombre", 5 if bad else "Carlos")
                i += 1
                _time.sleep(0.0005)              # yield so both versions are actually captured
        th = threading.Thread(target=flip); th.start()
        outcomes = {}
        try:
            for k in range(200):
                day = date(2027, 1, 1).toordinal() + k
                object.__setattr__(c, "requested_date", date.fromordinal(day))
                r = BookingAuthority(db).execute(c, attachments=plan)
                outcomes[(r.outcome.value, r.reason)] = outcomes.get((r.outcome.value, r.reason), 0) + 1
        finally:
            stop.set(); th.join()
        allowed = {("COMMITTED", "COMMITTED"), ("VALIDATION_FAILED", "COMMAND_NOT_DATA:tracks_acknowledgement"),
                   ("VALIDATION_FAILED", "PLAN_NOT_DATA:attachments")}
        self.assertTrue(set(outcomes) <= allowed, outcomes)
        db.expire_all()
        self.assertEqual(db.query(ThreadRevision).filter(ThreadRevision.appointment_approval_status.isnot(None)).count(), 0)
        self.assertEqual(db.query(BookingCommandRecord).count(), 0)          # no key → no ledger
        names = {l.nombre for l in db.query(Lead).filter(Lead.id == lead.id)}
        self.assertTrue(names <= {"Carlos", None}, names)        # never a merged/unvalidated name
        print("R5 entry race outcomes:", outcomes)


if __name__ == "__main__":
    unittest.main()
