"""SB2A-R3 — typed booking plans, defence in depth and acknowledgement closure (SQLite / pure).

R3 removes arbitrary write-capable callbacks from the booking authority: callers describe
associated writes and availability revalidation as typed DATA, and the authority executes them
in its own transaction. This file proves the interface, the data-only validation, the typed
attachment semantics, the pre-commit invariants (by fault injection into the authority's OWN
steps — there is no caller interface to inject through), the acknowledgement exhaustion /
legacy-lease / ambiguity rules, the approved receipt copy and the SB2B manifest. PostgreSQL
evidence (transaction identity, database clock, real races, migration) is in
test_sb2a_r3_typed_plans_pg.py.
"""
from __future__ import annotations

import enum
import inspect
import json
import os
import sys
import unittest
from collections import UserDict
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import (BookingCommandRecord, CalendarException, Lead, Profesional,  # noqa: E402
                        Revision, ThreadRevision, WhatsAppContact, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadState)
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (AvailabilityRevalidation, BookingAttachmentPlan,  # noqa: E402
                                            BookingAuthority, BookingCommand, LeadUpdate,
                                            BookingOperation as Op, BookingOutcome as Out,
                                            BookingSource as Src, PriceRecalculation,
                                            ThreadRevisionAttachment, ThreadStateUpdate)

D = date(2026, 11, 3)
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    from app.models import Agencia, Vendedor
    tables = [Lead.__table__, Profesional.__table__, Revision.__table__,
              CalendarException.__table__, BookingCommandRecord.__table__,
              WhatsAppContact.__table__, WhatsAppThread.__table__, ThreadRevision.__table__,
              WhatsAppThreadCandidate.__table__, WhatsAppThreadState.__table__,
              Agencia.__table__, Vendedor.__table__]
    Lead.__table__.metadata.create_all(engine, tables=tables)
    return Session(engine)


def _world(with_state=True):
    db = _db()
    p = Profesional(nombre="Fixture", apellido="SB2A-R3-FIXTURE", email="fixture@example.invalid",
                    resource_key="fixture.r3", is_launch_default=True, is_active=True)
    lead = Lead()
    db.add_all([p, lead]); db.commit()
    tid = _thread(db, lead, with_state=with_state)
    return db, lead, tid


def _thread(db, lead, with_state=True):
    import secrets
    c = WhatsAppContact(wa_id="5491" + str(secrets.randbelow(10**9)).zfill(9), display_name="Fixture")
    db.add(c); db.flush()
    t = WhatsAppThread(contact_id=c.id, lead_id=lead.id)
    db.add(t); db.flush()
    if with_state:
        db.add(WhatsAppThreadState(thread_id=t.id, flow_booking_token="tok-1"))
    db.commit()
    return t.id


def _cmd(lead, *, op=Op.CREATE, day=D, at=time(10), key=None, **kw):
    return BookingCommand(operation=op, source=Src.OTHER, idempotency_key=key, lead_id=lead.id,
                          requested_date=day, requested_time=at, **kw)


def _counts(db):
    db.expire_all()
    return (db.query(Revision).count(), db.query(ThreadRevision).count(),
            db.query(BookingCommandRecord).count())


def _plan(tid, **kw):
    return BookingAttachmentPlan(thread_revision=ThreadRevisionAttachment(thread_id=tid, **kw))


# ══ 1. No arbitrary callback interface ═══════════════════════════════════════════════

class R3_NoCallbacks(unittest.TestCase):

    def test_r3_01_execute_accepts_only_typed_plans(self):
        sig = inspect.signature(BookingAuthority.execute)
        self.assertEqual(list(sig.parameters), ["self", "cmd", "availability", "attachments"])
        self.assertFalse(any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()))
        db, lead, _ = _world()
        for kw in ("attach", "revalidate", "callback"):
            with self.assertRaises(TypeError):
                BookingAuthority(db).execute(_cmd(lead), **{kw: lambda *a: None})

    def test_r3_02_adapter_accepts_no_callable(self):
        for fn in (ce.book_from_flow_response, ce.request_manual_scheduling):
            params = inspect.signature(fn).parameters
            self.assertEqual(set(params), {"db", "req", "lock_timeout_ms"}, fn.__name__)
        for req in (ce.CeFlowBookingRequest, ce.CeSchedulingEscalationRequest):
            ann = {k: str(v) for k, v in req.__annotations__.items()}
            self.assertFalse(any("Callable" in v for v in ann.values()), req)

    def test_r3_03_no_production_caller_passes_a_callable(self):
        import ast
        for rel in ("app/services/booking_flow_service.py", "app/api/revisions.py",
                    "app/ui/kanban_actions.py", "app/services/booking_ce_adapter.py"):
            tree = ast.parse(open(os.path.join(_BACKEND, rel)).read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute":
                    for k in node.keywords:
                        self.assertIn(k.arg, (None, "availability", "attachments"), (rel, k.arg))
                        self.assertNotIsInstance(k.value, ast.Lambda, rel)

    def test_r3_04_callback_guard_machinery_is_gone(self):
        src = inspect.getsource(ba)
        for gone in ("_run_callback", "CallbackViolation", "_FORBIDDEN_SQL_HEADS",
                     "before_cursor_execute", "engine_connect"):
            self.assertNotIn(gone, src)


# ══ 2. Plans and commands are data ═══════════════════════════════════════════════════

class _StrSub(str):
    pass


class _E(str, enum.Enum):
    A = "A"


class R3_DataOnly(unittest.TestCase):

    def test_r3_05_plans_carrying_code_or_objects_are_refused_before_any_write(self):
        db, lead, tid = _world()
        bad_plans = [
            BookingAttachmentPlan(thread_revision=ThreadRevisionAttachment(thread_id=lambda: tid)),
            BookingAttachmentPlan(lead=LeadUpdate(estado=print)),
            BookingAttachmentPlan(lead=LeadUpdate(estado=_StrSub("X"))),
            BookingAttachmentPlan(lead=LeadUpdate(estado=_E.A)),
            BookingAttachmentPlan(lead=LeadUpdate(estado={"a": 1})),
            BookingAttachmentPlan(thread_state=ThreadStateUpdate(last_stage=db)),
            BookingAttachmentPlan(price=PriceRecalculation(require_inputs=(lambda: 1,))),
            BookingAttachmentPlan(thread_revision=LeadUpdate()),
        ]
        for plan in bad_plans:
            r = BookingAuthority(db).execute(_cmd(lead, thread_id=tid), attachments=plan)
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "PLAN_NOT_DATA:attachments"))

        class Sub(BookingAttachmentPlan):
            pass
        r = BookingAuthority(db).execute(_cmd(lead), attachments=Sub())
        self.assertEqual(r.reason, "PLAN_NOT_DATA:attachments")
        r = BookingAuthority(db).execute(_cmd(lead), availability=AvailabilityRevalidation(address=print))
        self.assertEqual(r.reason, "PLAN_NOT_DATA:availability")
        r = BookingAuthority(db).execute(_cmd(lead), attachments={"thread_revision": tid})
        self.assertEqual(r.reason, "PLAN_NOT_DATA:attachments")
        self.assertEqual(_counts(db), (0, 0, 0))

    def test_r3_06_commands_carrying_code_or_objects_are_refused(self):
        db, lead, _ = _world()

        class MyDate(date):
            pass
        cases = [_cmd(lead, revision_fields=UserDict({"marca": "Fiat"})),
                 _cmd(lead, revision_fields={"marca": print}),
                 _cmd(lead, revision_fields={"marca": _StrSub("Fiat")}),
                 _cmd(lead, day=MyDate(2026, 11, 3)),
                 BookingCommand(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id,
                                requested_date=D, requested_time=time(10),
                                idempotency_key=_StrSub("k"))]
        for c in cases:
            r = BookingAuthority(db).execute(c)
            self.assertEqual(r.outcome, Out.VALIDATION_FAILED)
            self.assertTrue(r.reason.startswith("COMMAND_NOT_DATA"), r.reason)
        for key in ("lead", "__class__", "turno_hora", "occupied_during", "profesional_id"):
            r = BookingAuthority(db).execute(_cmd(lead, revision_fields={key: None}))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, f"FIELD_NOT_ALLOWED:{key}"))
        self.assertEqual(_counts(db), (0, 0, 0))


# ══ 3. Typed attachments ═════════════════════════════════════════════════════════════

class R3_Attachments(unittest.TestCase):

    def test_r3_07_thread_revision_status_and_slot_are_derived_from_the_booking(self):
        db, lead, tid = _world()
        r = BookingAuthority(db).execute(_cmd(lead, thread_id=tid), attachments=_plan(tid, buyer_name="J"))
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        tr = db.get(ThreadRevision, r.thread_revision_id)
        self.assertEqual((tr.status, tr.scheduled_date, tr.scheduled_time, tr.buyer_name),
                         ("booked", D, time(10), "J"))
        r2 = BookingAuthority(db).execute(_cmd(lead, at=time(14), target_status="COMPLETADO"),
                                          attachments=_plan(tid))
        self.assertEqual(db.get(ThreadRevision, r2.thread_revision_id).status, "completed")

    def test_r3_08_manual_scheduling_attachment_never_occupies(self):
        db, lead, tid = _world()
        r = BookingAuthority(db).execute(_cmd(lead, op=Op.REQUEST_MANUAL_SCHEDULING, at=None),
                                         attachments=_plan(tid))
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        tr = db.get(ThreadRevision, r.thread_revision_id)
        self.assertEqual((tr.status, tr.scheduled_date, tr.scheduled_time), ("provisional", D, None))
        for bad in ("booked", "completed", "cancelled"):
            r = BookingAuthority(db).execute(_cmd(lead, op=Op.REQUEST_MANUAL_SCHEDULING),
                                             attachments=_plan(tid, non_occupying_status=bad))
            # SB2A-R4 rewrite: the strict schema refuses a non-declared status at admission
            self.assertEqual(r.reason, "PLAN_NOT_DATA:attachments")

    def test_r3_09_attachment_scope_is_enforced_after_rollback_leaves_nothing(self):
        db, lead, tid = _world()
        other_lead = Lead(); db.add(other_lead); db.commit()
        foreign = _thread(db, other_lead)
        cand_elsewhere = WhatsAppThreadCandidate(thread_id=foreign); db.add(cand_elsewhere); db.commit()
        booked = BookingAuthority(db).execute(_cmd(lead))
        cases = [
            (_cmd(lead, at=time(12)), _plan(foreign), "ATTACHMENT_THREAD_OF_ANOTHER_LEAD"),
            (_cmd(lead, at=time(12)), _plan(999999), "ATTACHMENT_THREAD_NOT_FOUND"),
            (_cmd(lead, at=time(12)), _plan(tid, candidate_id=cand_elsewhere.id),
             "ATTACHMENT_CANDIDATE_NOT_IN_THREAD"),
            (_cmd(lead, at=time(12), thread_id=tid + 1), _plan(tid), "ATTACHMENT_THREAD_MISMATCH"),
            (BookingCommand(operation=Op.RESCHEDULE, source=Src.OTHER, revision_id=booked.revision_id,
                            requested_date=D, requested_time=time(15)), _plan(tid),
             "ATTACHMENT_ONLY_FOR_NEW_BOOKINGS"),
        ]
        before = _counts(db)
        for cmd, plan, reason in cases:
            r = BookingAuthority(db).execute(cmd, attachments=plan)
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason))
        self.assertEqual(_counts(db), before)

    def test_r3_10_lead_and_thread_state_updates(self):
        db, lead, tid = _world()
        lead.nombre = "Existing"; db.commit()
        plan = BookingAttachmentPlan(
            thread_revision=ThreadRevisionAttachment(thread_id=tid),
            # SB2A-R4 rewrite: identity is ONE bundle. Canonical "Existing" vs submitted "Nuevo"
            # conflicts, so nothing about the person is written (no "Existing Apellido").
            lead=LeadUpdate(estado="COORDINAR_DISPONIBILIDAD", flag="ACEPTADO", necesita_humano=True,
                            identity=ba.LeadIdentity(nombre="Nuevo", apellido="Apellido")),
            thread_state=ThreadStateUpdate(link_current_revision=True, last_stage="BOOKED",
                                           needs_human=True, consume_flow_token=True))
        r = BookingAuthority(db).execute(_cmd(lead, thread_id=tid), attachments=plan)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        db.expire_all()
        lead = db.get(Lead, lead.id)
        self.assertEqual((lead.estado, lead.flag, lead.necesita_humano, lead.nombre, lead.apellido),
                         ("COORDINAR_DISPONIBILIDAD", "ACEPTADO", True, "Existing", None))
        self.assertEqual(r.identity_outcome, "CONFLICT")
        st = db.query(WhatsAppThreadState).filter_by(thread_id=tid).one()
        self.assertEqual((st.current_revision_id, st.last_stage, st.needs_human, st.flow_booking_token),
                         (r.thread_revision_id, "BOOKED", True, None))

    def test_r3_11_missing_thread_state_rolls_back_everything(self):
        db, lead, tid = _world(with_state=False)
        r = BookingAuthority(db).execute(_cmd(lead, key="ts"), attachments=BookingAttachmentPlan(
            thread_revision=ThreadRevisionAttachment(thread_id=tid),
            thread_state=ThreadStateUpdate(link_current_revision=True)))
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "THREAD_STATE_NOT_FOUND"))
        self.assertEqual(_counts(db), (0, 0, 0))                # no booking, no TR, no ledger

    def test_r3_12_price_is_skipped_when_inputs_do_not_match(self):
        db, lead, _ = _world()
        with mock.patch("app.services.pricing.PricingService.recalculate_revision_if_possible") as m:
            r = BookingAuthority(db).execute(
                _cmd(lead, revision_fields={"tipo_vehiculo": "AUTO", "zone_group": "CABA",
                                            "zone_detail": "Palermo"}),
                attachments=BookingAttachmentPlan(price=PriceRecalculation(
                    require_inputs=("SUV/4x4", "CABA", "Palermo"))))
            self.assertEqual(r.outcome, Out.COMMITTED)
            m.assert_not_called()
            BookingAuthority(db).execute(
                _cmd(lead, at=time(14), revision_fields={"tipo_vehiculo": "AUTO", "zone_group": "CABA",
                                                         "zone_detail": "Palermo"}),
                attachments=BookingAttachmentPlan(price=PriceRecalculation(
                    require_inputs=("AUTO", "CABA", "Palermo"))))
            self.assertEqual(m.call_count, 1)


# ══ 4. Authority-owned revalidation ══════════════════════════════════════════════════

class R3_Revalidation(unittest.TestCase):

    def test_r3_13_revalidation_receives_data_and_refuses_typed(self):
        db, lead, _ = _world()
        seen = []
        fake = mock.MagicMock()
        fake.check.side_effect = lambda inp: (seen.append(inp), mock.MagicMock(valid=False,
                                                                              reasons=["x"]))[1]
        auth = BookingAuthority(db)
        auth._availability_service = lambda: fake
        r = auth.execute(_cmd(lead), availability=AvailabilityRevalidation(address="Calle 1",
                                                                          zone_group="CABA"))
        self.assertEqual((r.outcome, r.reason), (Out.SLOT_CONFLICT, "SCHEDULE_REVALIDATION"))
        self.assertEqual((seen[0].preferred_day, seen[0].preferred_time, seen[0].address,
                          seen[0].zone_group), (D, time(10), "Calle 1", "CABA"))
        self.assertEqual(_counts(db), (0, 0, 0))
        # not consulted for non-occupying operations
        seen.clear()
        r = auth.execute(_cmd(lead, op=Op.REQUEST_MANUAL_SCHEDULING),
                         availability=AvailabilityRevalidation())
        self.assertEqual((r.outcome, seen), (Out.COMMITTED, []))


# ══ 5. Defence in depth: fault injection into the authority's OWN steps ═══════════════

class _Inject(BookingAuthority):
    """Test-only: run `fault(session, revision)` inside the attachment step, simulating a
    defect in authority-owned code. Production callers have no such interface."""

    def __init__(self, db, fault):
        super().__init__(db)
        self._fault = fault

    def _apply_attachments(self, cmd, plan, revision, target):
        tr = super()._apply_attachments(cmd, plan, revision, target)
        self._fault(self.db, revision)
        return tr


class R3_DefenceInDepth(unittest.TestCase):

    def _run(self, fault, *, with_other=True):
        db, lead, tid = _world()
        other = BookingAuthority(db).execute(_cmd(lead, at=time(8))) if with_other else None
        other_tr = ThreadRevision(thread_id=tid, status="booked", scheduled_date=D,
                                  scheduled_time=time(16)); db.add(other_tr); db.commit()
        before = _counts(db)
        r = _Inject(db, lambda s, rev: fault(s, rev, other, other_tr)).execute(
            _cmd(lead, key="inj", thread_id=tid), attachments=_plan(tid))
        return db, r, before, other, other_tr

    def assertBreach(self, r, kind):
        self.assertEqual(r.outcome, Out.INTERNAL_FAILURE)
        self.assertEqual(r.reason, f"INVARIANT_BREACH:{kind}")

    def test_r3_14_deleting_another_booking_is_caught_and_rolled_back(self):
        db, r, before, other, _ = self._run(lambda s, rev, o, t: s.delete(s.get(Revision, o.revision_id)))
        self.assertBreach(r, "OCCUPANCY_SET_CHANGED")
        self.assertEqual(_counts(db), before)
        self.assertIsNotNone(db.get(Revision, other.revision_id))

    def test_r3_15_unbooking_another_thread_revision_is_caught(self):
        db, r, before, _, other_tr = self._run(lambda s, rev, o, t: setattr(s.get(ThreadRevision, t.id),
                                                                          "status", "draft"))
        self.assertBreach(r, "BOOKED_THREAD_REVISION_SET_CHANGED")
        self.assertEqual(db.get(ThreadRevision, other_tr.id).status, "booked")
        self.assertEqual(_counts(db), before)

    def test_r3_16_moving_another_booked_thread_revision_is_caught(self):
        db, r, before, _, other_tr = self._run(
            lambda s, rev, o, t: setattr(s.get(ThreadRevision, t.id), "scheduled_date", D + timedelta(days=1)))
        self.assertBreach(r, "BOOKED_THREAD_REVISION_SET_CHANGED")

    def test_r3_17_changing_the_booking_row_is_caught(self):
        for field, value in (("estado_revision", " cancelado "), ("turno_hora", time(15)),
                             ("profesional_id", None)):
            db, r, before, _, _ = self._run(lambda s, rev, o, t, f=field, v=value: setattr(rev, f, v))
            self.assertBreach(r, "BOOKING_FIELDS_CHANGED")
            self.assertEqual(_counts(db), before)

    def test_r3_18_another_occupying_revision_is_caught(self):
        db, r, before, _, _ = self._run(lambda s, rev, o, t: s.add(Revision(
            lead_id=rev.lead_id, turno_fecha=D, turno_hora=time(17), estado_revision="CONFIRMADO")))
        self.assertBreach(r, "OCCUPANCY_SET_CHANGED")
        self.assertEqual(_counts(db), before)

    def test_r3_19_no_external_io_before_the_commit(self):
        from app.services.outbound_safety_gate import OutboundSafetyGate
        db, r, before, _, _ = self._run(lambda s, rev, o, t: OutboundSafetyGate(s).attempt(
            wa_id="0", thread_id=1, text="x", path_id="BOOKING_FLOW"))
        self.assertEqual((r.outcome, r.reason), (Out.INTERNAL_FAILURE, "INTERNAL"))
        self.assertEqual(_counts(db), before)
        self.assertIsNone(ba.booking_transaction_active())
        ba.forbid_external_io("outbound_gate")                    # outside: no-op

    def test_r3_20_gate_guard_is_attempts_first_statement(self):
        from app.services.outbound_safety_gate import OutboundSafetyGate
        body = inspect.getsource(OutboundSafetyGate.attempt).split('"""', 2)[2]
        first = [ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")][:2]
        self.assertEqual(first, ["from .booking_authority import forbid_external_io",
                                 'forbid_external_io("outbound_gate")'])

    def test_r3_21_verification_runs_after_attachments_and_before_commit(self):
        src = inspect.getsource(BookingAuthority._execute_snapshot)  # SB2A-R5: execute() captures, then runs _execute_snapshot()
        i_att = src.index("self._apply_attachments(")
        i_rec = src.index("self._record(")
        i_ver = src.index("self._verify_before_commit(")
        i_com = src.index("self.db.commit()")
        self.assertTrue(i_att < i_rec < i_ver < i_com)
        self.assertLess(i_att, src.index("self._assert_continuous(txid)"))
        self.assertLess(src.index("self._assert_continuous(txid)"), i_rec)   # before the ledger
        cont = inspect.getsource(BookingAuthority._assert_continuous)
        self.assertIn("transaction_status", cont)
        self.assertIn("txid_current()", cont)
        ver = inspect.getsource(BookingAuthority._verify_before_commit)
        # SB2A-R4 rewrite: the set checks moved into _audit_transaction_writes (PostgreSQL) and
        # _window_diff (SQLite), both called from _verify_before_commit.
        ver += inspect.getsource(BookingAuthority._audit_transaction_writes)
        ver += inspect.getsource(BookingAuthority._window_diff)
        for check in ("self._assert_continuous(txid)", "verify_booking_schema(self.db)", "OCCUPANCY_SET_CHANGED",
                      "BOOKED_THREAD_REVISION_SET_CHANGED", "BOOKING_FIELDS_CHANGED"):
            self.assertIn(check, ver)


# ══ 6. Acknowledgement: exhaustion, legacy leases, ambiguity ══════════════════════════

class R3_Ack(unittest.TestCase):

    def setUp(self):
        self.db, lead, _ = _world()
        BookingAuthority(self.db).execute(_cmd(lead, key="ack", tracks_acknowledgement=True))
        self.auth = BookingAuthority(self.db)

    def claim(self, at):
        with mock.patch.object(ba, "_app_now", return_value=at):
            return self.auth.claim_acknowledgement("ack")

    def row(self):
        self.db.expire_all()
        r = self.db.query(BookingCommandRecord).filter_by(idempotency_key="ack").one()
        return (r.ack_status, r.ack_attempts, r.ack_duplicate_risk, r.ack_last_error, r.ack_transport_id)

    def test_r3_22_crash_at_each_attempt_1_to_5(self):
        t = T0
        for attempt in range(1, ba.ACK_MAX_ATTEMPTS + 1):
            c = self.claim(t)
            self.assertIsNotNone(c, attempt)
            self.assertEqual(c.attempt, attempt)
            self.assertEqual(c.duplicate_risk, attempt > 1)       # every reclaim of a dead lease
            t += timedelta(seconds=ba.ACK_LEASE_SECONDS + 1)      # …and this process dies
        self.assertIsNone(self.claim(t))
        status, attempts, risk, err, wamid = self.row()
        self.assertEqual((status, attempts, risk, err, wamid),
                         ("FAILED", 5, True, "ACK_EXHAUSTED_AFTER_CRASH", None))
        keys = [r["idempotency_key"] for r in self.auth.acknowledgements_needing_attention()]
        self.assertIn("ack", keys)

    def test_r3_23_operator_view_terminalizes_without_a_claim(self):
        t = T0
        for _ in range(ba.ACK_MAX_ATTEMPTS):
            self.claim(t); t += timedelta(seconds=ba.ACK_LEASE_SECONDS + 1)
        # SB2A-R4 rewrite (R3A-07): the view is READ-ONLY and flags the row; terminalization
        # is the explicit, separate operation.
        with mock.patch.object(ba, "_app_now", return_value=t):
            rows = self.auth.acknowledgements_needing_attention()
            self.assertEqual([(r["ack_status"], r["pending_terminalization"]) for r in rows],
                             [("SENDING", True)])
            self.assertEqual(self.auth.terminalize_exhausted_acknowledgements(), 1)
            self.assertEqual(self.auth.terminalize_exhausted_acknowledgements(), 0)
            rows = self.auth.acknowledgements_needing_attention()
        self.assertEqual([r["ack_status"] for r in rows], ["FAILED"])
        self.assertTrue(rows[0]["ack_last_error"].startswith("ACK_EXHAUSTED"))

    def test_r3_24_definitive_failure_on_last_attempt_is_marked_exhausted(self):
        t = T0
        for i in range(ba.ACK_MAX_ATTEMPTS):
            c = self.claim(t)
            self.auth.record_acknowledgement(c, sent=False, error="META_REJECTED_400")
            t += timedelta(seconds=1)
        self.assertEqual(self.row()[:4], ("FAILED", 5, False, "ACK_EXHAUSTED:META_REJECTED_400"))
        self.assertIsNone(self.claim(t))

    def test_r3_25_legacy_sending_without_lease_gets_one_recorded_recovery(self):
        self.db.query(BookingCommandRecord).update({"ack_status": "SENDING", "ack_attempts": 1,
                                                    "ack_lease_expires_at": None})
        self.db.commit()
        c = self.claim(T0)
        self.assertIsNotNone(c)
        self.assertTrue(c.duplicate_risk)
        self.assertEqual(self.row()[:4], ("SENDING", 2, True, "LEGACY_SENDING_NO_LEASE"))

    def test_r3_26_legacy_sending_without_lease_at_the_bound_is_terminal(self):
        self.db.query(BookingCommandRecord).update({"ack_status": "SENDING", "ack_attempts": 5,
                                                    "ack_lease_expires_at": None})
        self.db.commit()
        self.assertIsNone(self.claim(T0))
        self.assertEqual(self.row()[:4], ("FAILED", 5, True, "ACK_EXHAUSTED_AFTER_CRASH"))

    def test_r3_27_ambiguous_outcomes_record_duplicate_risk(self):
        c = self.claim(T0)
        self.assertTrue(self.auth.record_acknowledgement(c, sent=False, error="NETWORK_OR_TIMEOUT",
                                                         ambiguous=True))
        self.assertEqual(self.row()[:4], ("FAILED", 1, True, "AMBIGUOUS_NETWORK_OR_TIMEOUT"))
        c = self.claim(T0 + timedelta(seconds=1))
        self.assertTrue(self.auth.record_acknowledgement(c, sent=True, transport_id="  "))
        self.assertEqual(self.row()[:4], ("FAILED", 2, True, "AMBIGUOUS_SENT_WITHOUT_TRANSPORT_ID"))
        c = self.claim(T0 + timedelta(seconds=2))
        self.assertTrue(self.auth.record_acknowledgement(c, sent=True, transport_id="wamid.OK"))
        self.assertEqual(self.row(), ("SENT", 3, True, "AMBIGUOUS_SENT_WITHOUT_TRANSPORT_ID", "wamid.OK"))


# ══ 7. Receipt: approved copy and transport classification ═══════════════════════════

class R3_Receipt(unittest.TestCase):

    APPROVED = "Gracias por completarlo. Un representante de RideCheck se estará comunicando con vos."

    def _send(self, *, gate_ok=True, effect=None, ret=None):
        from app.services import booking_flow_service as bfs
        from app.services.outbound_safety_gate import GateOutcome
        svc = bfs.BookingFlowService.__new__(bfs.BookingFlowService)
        svc.db = mock.MagicMock()
        ctx = mock.MagicMock(); ctx.contact.wa_id = "549"; ctx.thread.id = 1
        sent_texts = []
        with mock.patch("app.services.outbound_safety_gate.OutboundSafetyGate") as G, \
                mock.patch("app.ui.whatsapp_ui._send_whatsapp_cloud_text") as T:
            G.return_value.attempt.side_effect = lambda **kw: (sent_texts.append(kw["text"]), mock.MagicMock(
                outcome=GateOutcome.ALLOWED if gate_ok else GateOutcome.BLOCKED_KILL_SWITCH,
                message_id=1))[1]
            if effect is not None:
                T.side_effect = effect
            else:
                T.return_value = (ret, 200)
            out = svc._send_booking_receipt(ctx, name="Julián", selected_date=D, selected_time=time(10))
        return out, sent_texts

    def test_r3_28_receipt_is_exactly_the_approved_copy(self):
        from app.services import booking_flow_service as bfs
        self.assertEqual(bfs.BOOKING_FLOW_RECEIPT_TEXT, self.APPROVED)
        out, texts = self._send(ret="wamid.1")
        self.assertEqual(texts, [self.APPROVED])                  # name/date never appended
        self.assertEqual((out.sent, out.wamid, out.ambiguous), (True, "wamid.1", False))

    def test_r3_29_transport_outcomes_are_classified(self):
        from app.ui.whatsapp_ui import MetaSendError
        cases = [
            (dict(effect=MetaSendError(None, "timed out")), (False, True, "AMBIGUOUS_NETWORK_OR_TIMEOUT")),
            (dict(effect=MetaSendError(200, "{}", "no id")), (False, True, "AMBIGUOUS_2XX_WITHOUT_MESSAGE_ID")),
            (dict(effect=MetaSendError(503, "{}")), (False, True, "AMBIGUOUS_META_503")),
            (dict(effect=ValueError("malformed json")), (False, True, "AMBIGUOUS_TRANSPORT_EXCEPTION")),
            (dict(effect=MetaSendError(400, "{}")), (False, False, "META_REJECTED_400")),
            (dict(ret=""), (False, True, "AMBIGUOUS_SUCCESS_WITHOUT_WAMID")),
            (dict(gate_ok=False, ret="w"), (False, False, "GATE_blocked_kill_switch")),
        ]
        for kw, expected in cases:
            out, _ = self._send(**kw)
            self.assertEqual((out.sent, out.ambiguous, out.error), expected, kw)

    def test_r3_30_flow_records_ambiguity_into_the_ledger(self):
        from app.services import booking_flow_service as bfs
        svc = bfs.BookingFlowService.__new__(bfs.BookingFlowService)
        svc._authority = mock.MagicMock()
        claim = ba.AckClaim("k", "t", 1, False)
        svc._record_receipt(claim, bfs.ReceiptOutcome(False, None, "AMBIGUOUS_NETWORK_OR_TIMEOUT", True))
        svc._authority.record_acknowledgement.assert_called_once_with(
            claim, sent=False, transport_id=None, error="AMBIGUOUS_NETWORK_OR_TIMEOUT", ambiguous=True)

    def test_r3_31_track_a_builder_is_unchanged(self):
        from app.services.booking_flow_service import build_booking_receipt_message
        self.assertIn("Recibimos tu solicitud", build_booking_receipt_message("Lara", None, None))


# ══ 8. Preflight column set and SB2B manifest ════════════════════════════════════════

class R3_Contract(unittest.TestCase):

    def test_r3_32_required_columns_cover_the_whole_ledger_and_lease(self):
        cols = set(ba._required_columns())
        ledger = {c.name for c in BookingCommandRecord.__table__.columns}
        self.assertTrue({("booking_commands", c) for c in ledger} <= cols)
        for c in ("ack_claim_token", "ack_claimed_at", "ack_lease_expires_at", "ack_transport_id",
                  "ack_last_error", "ack_duplicate_risk", "request_fingerprint", "idempotency_key"):
            self.assertIn(("booking_commands", c), cols)
        for t, c in (("revisions", "booking_version"), ("revisions", "occupied_during"),
                     ("revisions", "profesional_id"), ("revisions", "estado_revision"),
                     ("thread_revisions", "status"), ("whatsapp_thread_states", "flow_booking_token")):
            self.assertIn((t, c), cols)
        self.assertGreater(len(cols), 50)

    def test_r3_33_manifest_states_the_nine_sb2b_rules(self):
        m = json.load(open(os.path.join(_ROOT, "docs", "architecture", "SB2B_CE_INTEGRATION_MANIFEST.json")))
        rules = {r["id"]: r["rule"] for r in m["sb2b_mandatory_rules"]}
        self.assertEqual(sorted(rules), [f"SB2B-{i}" for i in range(1, 10)])
        phrases = {"SB2B-1": "uncommitted row locks", "SB2B-2": "COMMITTED and visible",
                   "SB2B-3": "expire or refresh", "SB2B-4": "IDEMPOTENCY_KEY_REUSED",
                   "SB2B-5": "RETRYABLE_FAILURE", "SB2B-6": "INTERNAL_FAILURE",
                   "SB2B-7": "OUTSIDE the failed adapter transaction",
                   "SB2B-8": "REQUEST_MANUAL_SCHEDULING", "SB2B-9": "No CE integration may reintroduce"}
        for rid, phrase in phrases.items():
            self.assertIn(phrase, rules[rid], rid)
        w2 = next(s for s in m["sites"] if s["id"] == "CE-W2")
        self.assertTrue(any("IDEMPOTENCY_KEY_REUSED" in r for r in w2["expected_results"]))
        self.assertIn("NO callback", m["session_contract"])


if __name__ == "__main__":
    unittest.main()
