"""SB2A — booking authority: contract, writer cutover and CE adapter (SQLite / pure).

Proves the typed contract, the canonical status set, professional assignment, calendar
exceptions, idempotency, acknowledgement state, stale versions, the CRM/API conflict
responses and the SB2B manifest. Nothing here is concurrency evidence: SQLite has no
advisory locks and no exclusion constraint. Races live in test_sb2a_booking_authority_pg.py.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import unittest
from datetime import date, time, timedelta

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import (BookingCommandRecord, CalendarException, Lead, Profesional,  # noqa: E402
                        Revision, ThreadRevision, WhatsAppContact, WhatsAppThread)
from app.services import booking_authority as ba  # noqa: E402
from app.services import booking_ce_adapter as ce  # noqa: E402
from app.services.booking_authority import (BookingAuthority, BookingCommand,  # noqa: E402
                                            BookingOperation as Op, BookingOutcome as Out,
                                            BookingSource as Src)

D = date(2026, 11, 3)


def _db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    tables = [Lead.__table__, Profesional.__table__, Revision.__table__,
              CalendarException.__table__, BookingCommandRecord.__table__,
              WhatsAppContact.__table__, WhatsAppThread.__table__, ThreadRevision.__table__]
    from app.models import Agencia, Vendedor, WhatsAppThreadCandidate
    tables += [Agencia.__table__, Vendedor.__table__, WhatsAppThreadCandidate.__table__]
    Lead.__table__.metadata.create_all(engine, tables=tables)
    return Session(engine)


def _prof(db, *, default=False, active=True, key=None):
    p = Profesional(nombre="Fixture", apellido="SB2A-FIXTURE", email="fixture@example.invalid",
                    resource_key=key or (f"fixture.{id(db)}.{default}" if default else None),
                    is_launch_default=default, is_active=active)
    db.add(p); db.commit()
    return p


def _lead(db):
    lead = Lead(); db.add(lead); db.commit()
    return lead


def _create(db, lead, at=time(10, 0), day=D, key=None, prof=None, src=Src.OTHER, **kw):
    return BookingAuthority(db).execute(BookingCommand(
        operation=Op.CREATE, source=src, idempotency_key=key, lead_id=lead.id,
        profesional_id=prof, requested_date=day, requested_time=at, **kw))


class TypedContract(unittest.TestCase):

    def test_sb2a_01_outcomes_and_operations_are_complete(self):
        self.assertEqual({o.value for o in Out}, {
            "COMMITTED", "IDEMPOTENT_REPLAY", "SLOT_CONFLICT", "CALENDAR_EXCEPTION",
            "MISSING_PROFESSIONAL", "STALE_VERSION", "INVALID_TRANSITION", "VALIDATION_FAILED",
            "RETRYABLE_FAILURE", "INTERNAL_FAILURE"})
        self.assertTrue({"CREATE", "RESCHEDULE", "CANCEL", "MARK_FOR_RESCHEDULE"}
                        <= {o.value for o in Op})
        self.assertTrue({"WHATSAPP_FLOW", "CRM", "API", "CONVERSATION_ENGINE", "OTHER"}
                        == {s.value for s in Src})
        for o in Out:
            self.assertTrue(ba._MESSAGES[o])                      # every outcome has a message
            for leak in ("SQL", "psycopg", "Traceback", "constraint", "violat"):
                self.assertNotIn(leak.lower(), ba._MESSAGES[o].lower())

    def test_sb2a_02_commit_carries_interval_professional_and_version(self):
        db = _db(); p = _prof(db, default=True); lead = _lead(db)
        r = _create(db, lead)
        self.assertEqual(r.outcome, Out.COMMITTED, r.reason)
        self.assertEqual(r.profesional_id, p.id)
        self.assertEqual(r.slot_end - r.slot_start, timedelta(minutes=45))
        self.assertEqual(r.lock_mode, "sqlite-serialized")
        rev = db.get(Revision, r.revision_id)
        self.assertEqual((rev.service_minutes, rev.buffer_minutes, rev.estado_revision,
                          rev.booking_version), (30, 15, "PENDIENTE", 0))

    def test_sb2a_03_validation_failures_are_typed(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        cases = [
            (dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id), "SLOT_REQUIRED"),
            (dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D),
             "SLOT_REQUIRED"),
            (dict(operation=Op.RESCHEDULE, source=Src.OTHER, requested_date=D,
                  requested_time=time(9)), "REVISION_REQUIRED"),
            (dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D,
                  requested_time=time(9), target_status="cancelada"), "UNKNOWN_STATUS"),
            (dict(operation=Op.CREATE, source=Src.OTHER, lead_id=lead.id, requested_date=D,
                  requested_time=time(9), service_minutes=0), "INVALID_SERVICE"),
        ]
        for kw, reason in cases:
            r = BookingAuthority(db).execute(BookingCommand(**kw))
            self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, reason), kw)
        self.assertEqual(db.query(Revision).count(), 0)


class StatusCanon(unittest.TestCase):

    def test_sb2a_04_status_canon_matches_sb1(self):
        from app.services import booking_representation as br
        from app.services import schedule
        self.assertEqual(frozenset(ba.NON_OCCUPYING_STATUSES), br.NON_OCCUPYING_ESTADOS)
        self.assertEqual(frozenset(ba.NON_OCCUPYING_STATUSES), schedule._NON_OCCUPYING_ESTADOS)
        for raw, want in [(" cancelado ", "CANCELADO"), ("Reprogramar", "REPROGRAMAR"),
                          ("confirmado", "CONFIRMADO"), ("cancelada", None), ("", None),
                          ("CANCELADO\t", "CANCELADO"), ("anything", None)]:
            self.assertEqual(ba.normalize_status(raw), want, raw)

    def test_sb2a_05_aliases_cannot_bypass_or_acquire_occupancy(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        r = _create(db, lead, target_status=" confirmado ")
        self.assertEqual(r.outcome, Out.COMMITTED)
        self.assertEqual(db.get(Revision, r.revision_id).estado_revision, "CONFIRMADO")
        cancel = BookingAuthority(db).execute(BookingCommand(
            operation=Op.CANCEL, source=Src.OTHER, revision_id=r.revision_id))
        self.assertEqual(db.get(Revision, cancel.revision_id).estado_revision, "CANCELADO")
        # the slot is free again: the same time books
        self.assertEqual(_create(db, lead).outcome, Out.COMMITTED)
        bad = _create(db, lead, at=time(14), target_status="cancelado-ish")
        self.assertEqual(bad.outcome, Out.VALIDATION_FAILED)


class Professional(unittest.TestCase):

    def test_sb2a_06_no_launch_default_fails_closed(self):
        db = _db(); lead = _lead(db)
        r = _create(db, lead)
        self.assertEqual((r.outcome, r.reason), (Out.MISSING_PROFESSIONAL, "NO_LAUNCH_DEFAULT"))
        self.assertEqual(db.query(Revision).count(), 0)

    def test_sb2a_07_inactive_professionals_fail_closed(self):
        db = _db(); lead = _lead(db)
        p = _prof(db, default=True, active=False)
        r = _create(db, lead)
        self.assertEqual((r.outcome, r.reason), (Out.MISSING_PROFESSIONAL, "LAUNCH_DEFAULT_INACTIVE"))
        q = _prof(db, active=False)
        r = _create(db, lead, prof=q.id)
        self.assertEqual((r.outcome, r.reason), (Out.MISSING_PROFESSIONAL, "PROFESSIONAL_INACTIVE"))
        r = _create(db, lead, prof=999_999)
        self.assertEqual(r.reason, "PROFESSIONAL_NOT_FOUND")
        self.assertEqual(db.query(Revision).count(), 0)

    def test_sb2a_08_explicit_eligible_professional_is_used(self):
        db = _db(); _prof(db, default=True); other = _prof(db); lead = _lead(db)
        r = _create(db, lead, prof=other.id)
        self.assertEqual(r.profesional_id, other.id)

    def test_sb2a_09_no_arbitrary_first_professional_lookup(self):
        src = ast.unparse(ast.parse(open(ba.__file__).read()))
        self.assertNotIn(".first()", src)
        self.assertIn("AMBIGUOUS_LAUNCH_DEFAULT", src)


class Calendar(unittest.TestCase):

    def test_sb2a_10_closed_date_and_partial_interval(self):
        db = _db(); p = _prof(db, default=True); lead = _lead(db)
        ba.create_calendar_exception(db, kind="CLOSED_DATE", exception_date=D)
        r = _create(db, lead)
        self.assertEqual((r.outcome, r.reason), (Out.CALENDAR_EXCEPTION, "CLOSED_DATE"))
        nxt = D + timedelta(days=1)
        ba.create_calendar_exception(db, kind="UNAVAILABLE_INTERVAL", exception_date=nxt,
                                     start_time=time(12), end_time=time(13))
        self.assertEqual(_create(db, lead, day=nxt, at=time(11, 30)).outcome,
                         Out.CALENDAR_EXCEPTION)                  # 11:30–12:15 overlaps
        self.assertEqual(_create(db, lead, day=nxt, at=time(11, 15)).outcome,
                         Out.COMMITTED)                           # 11:15–12:00 adjacent
        self.assertEqual(_create(db, lead, day=nxt, at=time(13)).outcome, Out.COMMITTED)
        # an exception for another professional does not block this one
        other = _prof(db)
        ba.create_calendar_exception(db, kind="CLOSED_DATE", exception_date=nxt + timedelta(1),
                                     profesional_id=other.id)
        self.assertEqual(_create(db, lead, day=nxt + timedelta(1)).outcome, Out.COMMITTED)

    def test_sb2a_11_exceptions_never_mutate_existing_bookings(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        r = _create(db, lead)
        before = db.get(Revision, r.revision_id).estado_revision
        ba.create_calendar_exception(db, kind="CLOSED_DATE", exception_date=D)
        db.expire_all()
        self.assertEqual(db.get(Revision, r.revision_id).estado_revision, before)


class Idempotency(unittest.TestCase):

    def test_sb2a_12_duplicate_returns_original(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        a = _create(db, lead, key="k-1")
        b = _create(db, lead, key="k-1")
        self.assertEqual(a.outcome, Out.COMMITTED)
        self.assertEqual((b.outcome, b.revision_id), (Out.IDEMPOTENT_REPLAY, a.revision_id))
        self.assertEqual(db.query(Revision).count(), 1)
        self.assertEqual(db.query(BookingCommandRecord).one().replay_count, 1)

    def test_sb2a_13_key_reuse_with_different_request_is_refused(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        _create(db, lead, key="k-2")
        r = _create(db, lead, key="k-2", at=time(15))
        self.assertEqual((r.outcome, r.reason), (Out.VALIDATION_FAILED, "IDEMPOTENCY_KEY_REUSED"))
        self.assertEqual(db.query(Revision).count(), 1)

    def test_sb2a_14_distinct_keys_are_distinct_bookings(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        self.assertEqual(_create(db, lead, key="a").outcome, Out.COMMITTED)
        self.assertEqual(_create(db, lead, key="b", at=time(11)).outcome, Out.COMMITTED)
        self.assertEqual(db.query(Revision).count(), 2)

    def test_sb2a_15_acknowledgement_state_is_separate_and_recoverable(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        auth = BookingAuthority(db)
        r = _create(db, lead, key="ack-1", tracks_acknowledgement=True)
        self.assertEqual(r.ack_status, "PENDING")
        # SB2A-R2 rewrite: claims are leases (AckClaim | None) and outcomes are recorded by
        # the claim, not the key. The behaviour asserted is unchanged.
        first = auth.claim_acknowledgement("ack-1")
        self.assertIsNotNone(first)
        self.assertIsNone(auth.claim_acknowledgement("ack-1"))     # active lease: nobody else
        auth.record_acknowledgement(first, sent=False)             # transport failure
        self.assertEqual(auth.lookup("ack-1").ack_status, "FAILED")
        replay = _create(db, lead, key="ack-1", tracks_acknowledgement=True)
        self.assertEqual(replay.outcome, Out.IDEMPOTENT_REPLAY)     # committed, not re-booked
        second = auth.claim_acknowledgement("ack-1")               # recovery claims once
        self.assertIsNotNone(second)
        auth.record_acknowledgement(second, sent=True)
        self.assertIsNone(auth.claim_acknowledgement("ack-1"))     # SENT: silent forever
        self.assertEqual(db.query(Revision).count(), 1)
        rec = db.query(BookingCommandRecord).one()
        self.assertEqual((rec.ack_status, rec.ack_attempts), ("SENT", 2))


class Transitions(unittest.TestCase):

    def _booked(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        r = _create(db, lead)
        return db, lead, r

    def test_sb2a_16_stale_version_cannot_overwrite(self):
        db, lead, r = self._booked()
        move = lambda at, ver: BookingAuthority(db).execute(BookingCommand(
            operation=Op.RESCHEDULE, source=Src.CRM, revision_id=r.revision_id,
            requested_date=D, requested_time=at, expected_version=ver))
        first = move(time(11), 0)
        self.assertEqual((first.outcome, first.booking_version), (Out.COMMITTED, 1))
        stale = move(time(12), 0)
        self.assertEqual((stale.outcome, stale.booking_version), (Out.STALE_VERSION, 1))
        self.assertEqual(db.get(Revision, r.revision_id).turno_hora, time(11))

    def test_sb2a_17_reschedule_keeps_one_occupying_version(self):
        db, lead, r = self._booked()
        BookingAuthority(db).execute(BookingCommand(
            operation=Op.RESCHEDULE, source=Src.CRM, revision_id=r.revision_id,
            requested_date=D, requested_time=time(16)))
        occupying = [x for x in db.query(Revision).all()
                     if ba.br.is_occupying(x.turno_fecha, x.turno_hora, x.estado_revision)]
        self.assertEqual([(x.id, x.turno_hora) for x in occupying], [(r.revision_id, time(16))])

    def test_sb2a_18_invalid_transitions_are_typed(self):
        db, lead, r = self._booked()
        again = BookingAuthority(db).execute(BookingCommand(
            operation=Op.CREATE, source=Src.CRM, revision_id=r.revision_id,
            requested_date=D, requested_time=time(12)))
        self.assertEqual((again.outcome, again.reason), (Out.INVALID_TRANSITION,
                                                         "ALREADY_BOOKED_USE_RESCHEDULE"))
        moving_status = BookingAuthority(db).execute(BookingCommand(
            operation=Op.SET_STATUS, source=Src.CRM, revision_id=r.revision_id,
            requested_date=D, requested_time=time(12), target_status="CONFIRMADO"))
        self.assertEqual(moving_status.outcome, Out.INVALID_TRANSITION)

    def test_sb2a_19_rollback_leaves_nothing_partial(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)

        def attach(db_, rev):
            db_.add(ThreadRevision(thread_id=1, status="booked"))
            db_.flush()
            raise RuntimeError("attach failed after the Revision was written")

        r = BookingAuthority(db).execute(BookingCommand(
            operation=Op.CREATE, source=Src.OTHER, idempotency_key="rb", lead_id=lead.id,
            requested_date=D, requested_time=time(10)), attach=attach)
        self.assertEqual(r.outcome, Out.INTERNAL_FAILURE)
        self.assertEqual((db.query(Revision).count(), db.query(ThreadRevision).count(),
                          db.query(BookingCommandRecord).count()), (0, 0, 0))

    def test_sb2a_20_unattributed_legacy_rows_count_against_launch_resource(self):
        db = _db(); lead = _lead(db)
        db.add(Revision(lead_id=lead.id, turno_fecha=D, turno_hora=time(10),
                        estado_revision="CONFIRMADO"))          # legacy: no professional
        db.commit()
        p = _prof(db, default=True)
        r = _create(db, lead, at=time(10, 30))
        self.assertEqual((r.outcome, r.reason), (Out.SLOT_CONFLICT, "OVERLAP"))
        other = _prof(db)
        self.assertEqual(_create(db, lead, at=time(10, 30), prof=other.id).outcome, Out.COMMITTED)


class EditMapping(unittest.TestCase):

    def test_sb2a_21_command_for_edit(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        r = _create(db, lead)
        rev = db.get(Revision, r.revision_id)
        f = lambda **kw: ba.command_for_edit(rev, source=Src.CRM, expected_version=0, **{
            "new_date": rev.turno_fecha, "new_time": rev.turno_hora, "new_status": None,
            "new_profesional_id": None, **kw})
        self.assertIsNone(f())
        self.assertEqual(f(new_time=time(12)).operation, Op.RESCHEDULE)
        self.assertEqual(f(new_status="CANCELADO").operation, Op.CANCEL)
        self.assertEqual(f(new_status="REPROGRAMAR").operation, Op.MARK_FOR_RESCHEDULE)
        self.assertEqual(f(new_status="COMPLETADO").operation, Op.SET_STATUS)
        self.assertEqual(f(new_date=None, new_time=None).operation, Op.UNSCHEDULE)
        self.assertTrue(f(new_time=time(12)).idempotency_key.startswith("crm:rev"))
        self.assertIsNone(ba.command_for_edit(rev, source=Src.API, new_date=rev.turno_fecha,
                                              new_time=time(12), new_status=None,
                                              new_profesional_id=None,
                                              expected_version=None).idempotency_key)


class CrmAndApi(unittest.TestCase):
    """The migrated writers: typed conflicts become controlled responses, never a 500."""

    def setUp(self):
        self.db = _db(); _prof(self.db, default=True); self.lead = _lead(self.db)
        self.first = _create(self.db, self.lead)                  # 10:00–10:45
        other = _lead(self.db)
        self.other_rev = _create(self.db, other, at=time(14)).revision_id
        self.other_lead = other

    def _crm(self, **form):
        from app.ui import kanban_actions as ka
        base = {k: None for k in ka.ui_revision_latest_update.__code__.co_varnames[
            :ka.ui_revision_latest_update.__code__.co_argcount] if k not in ("db",)}
        base.update(recalcular_presupuesto="false", lead_id=self.other_lead.id,
                    estado_revision=None)
        rev = self.db.get(Revision, self.other_rev)
        base.update(turno_fecha=rev.turno_fecha.isoformat(), turno_hora="14:00",
                    booking_version=str(rev.booking_version))
        base.update(form)
        return ka.ui_revision_latest_update(db=self.db, **base)

    def test_sb2a_22_crm_overlap_is_a_controlled_409(self):
        resp = self._crm(turno_hora="10:15")
        self.assertEqual(resp.status_code, 409)
        body = resp.body.decode()
        self.assertIn("ya no está disponible", body)
        self.assertIn("El turno original quedó sin cambios", body)
        self.assertIn("/kanban#lead-", body)
        for leak in ("IntegrityError", "psycopg", "Traceback", "INSERT", "UPDATE revisions"):
            self.assertNotIn(leak, body)
        self.db.expire_all()
        self.assertEqual(self.db.get(Revision, self.other_rev).turno_hora, time(14))

    def test_sb2a_23_crm_success_and_stale_form(self):
        ok = self._crm(turno_hora="15:00")
        self.assertEqual(ok.status_code, 303)
        stale = self._crm(turno_hora="16:00", booking_version="0")
        self.assertEqual(stale.status_code, 409)
        self.assertIn("cambió mientras lo editabas", stale.body.decode())
        self.db.expire_all()
        self.assertEqual(self.db.get(Revision, self.other_rev).turno_hora, time(15))

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import revisions as api
        from app.db import get_db
        app = FastAPI(); app.include_router(api.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[api.get_db] = lambda: self.db
        return TestClient(app)

    def test_sb2a_24_api_conflict_is_structured_409(self):
        c = self._client()
        r = c.post(f"/leads/{self.lead.id}/revisions",
                   json={"turno_fecha": D.isoformat(), "turno_hora": "10:30:00"})
        self.assertEqual(r.status_code, 409, r.text)
        detail = r.json()["detail"]
        self.assertEqual((detail["code"], detail["reason"]), ("SLOT_CONFLICT", "OVERLAP"))
        self.assertEqual(detail["conflicts"][0]["kind"], "booking")
        ok = c.post(f"/leads/{self.lead.id}/revisions",
                    json={"turno_fecha": D.isoformat(), "turno_hora": "10:45:00"},
                    headers={"Idempotency-Key": "client-req-1"})
        self.assertEqual(ok.status_code, 200, ok.text)
        again = c.post(f"/leads/{self.lead.id}/revisions",
                       json={"turno_fecha": D.isoformat(), "turno_hora": "10:45:00"},
                       headers={"Idempotency-Key": "client-req-1"})
        self.assertEqual(again.json()["id"], ok.json()["id"])         # replay, no duplicate

    def test_sb2a_25_api_unknown_or_padded_status(self):
        c = self._client()
        r = c.post(f"/leads/{self.lead.id}/revisions", json={"estado_revision": "cancelada"})
        self.assertEqual(r.status_code, 422)
        r = c.post(f"/leads/{self.lead.id}/revisions",
                   json={"turno_fecha": D.isoformat(), "turno_hora": "10:00:00",
                         "estado_revision": " cancelado "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["estado_revision"], "CANCELADO")  # canonical, non-occupying

    def test_sb2a_26_api_patch_conflict_and_if_match(self):
        c = self._client()
        r = c.patch(f"/leads/{self.other_lead.id}/revisions/latest",
                    json={"turno_hora": "10:15:00"})
        self.assertEqual(r.status_code, 409)
        r = c.patch(f"/leads/{self.other_lead.id}/revisions/latest",
                    json={"turno_hora": "16:00:00"}, headers={"If-Match": '"7"'})
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (409, "STALE_VERSION"))
        r = c.patch(f"/leads/{self.other_lead.id}/revisions/latest",
                    json={"turno_hora": "16:00:00"}, headers={"If-Match": "nope"})
        self.assertEqual(r.status_code, 422)


class FlowCutover(unittest.TestCase):

    def test_sb2a_27_writers_route_through_the_authority(self):
        def fn_src(path, name):
            tree = ast.parse(open(os.path.join(_BACKEND, path)).read())
            return next(ast.unparse(n) for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == name)
        confirm = fn_src("app/services/booking_flow_service.py", "handle_confirm_booking")
        self.assertIn("self._authority.execute(", confirm)
        self.assertNotIn("Revision(", confirm.replace("ThreadRevision(", ""))
        self.assertNotIn("self.db.commit()", confirm)
        crm = fn_src("app/ui/kanban_actions.py", "ui_revision_latest_update")
        self.assertIn("BookingAuthority(db).execute(command)", crm)
        for field in ("latest.turno_fecha =", "latest.turno_hora =", "latest.estado_revision =",
                      "latest.profesional_id ="):
            self.assertNotIn(field, crm)
        for name in ("create_revision", "update_latest_revision"):
            src = fn_src("app/api/revisions.py", name)
            self.assertIn("BookingAuthority(db).execute(", src)
        self.assertNotIn("pg_try_advisory", open(os.path.join(
            _BACKEND, "app/services/booking_flow_service.py")).read())


class CeContract(unittest.TestCase):
    MANIFEST = os.path.join(_ROOT, "docs", "architecture", "SB2B_CE_INTEGRATION_MANIFEST.json")

    def test_sb2a_28_manifest_maps_every_ce_writer(self):
        m = json.load(open(self.MANIFEST))
        ce_src = open(os.path.join(_ROOT, m["source_file"])).read()
        tree = ast.parse(ce_src)
        writers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and "Revision(" in ast.unparse(n).replace("ThreadRevision(", "")
                   .replace("FeedbackPostRevision(", "")}
        sites = {s["function"]: s for s in m["sites"]}
        self.assertEqual(writers, set(sites))                    # no unmapped CE writer
        for fname, site in sites.items():
            body = next(ast.get_source_segment(ce_src, n) for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == fname)
            for anchor in site["anchors"]:
                self.assertIn(anchor, body, (site["id"], anchor))
            self.assertTrue(callable(getattr(ce, site["adapter"])), site["adapter"])
            for res in site["expected_results"]:
                self.assertIn(res.split(" ")[0], {o.value for o in Out})

    def test_sb2a_29_flow_response_adapter(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        made = []

        def tr(db_, rev):
            t = ThreadRevision(thread_id=7, status="booked", scheduled_date=rev.turno_fecha,
                               scheduled_time=rev.turno_hora)
            db_.add(t); db_.flush(); made.append(t.id)
            return t

        req = ce.CeFlowBookingRequest(thread_id=7, lead_id=lead.id, inbound_event_id="wamid.X",
                                      flow_token="7-1-abc", requested_date=D,
                                      requested_time=time(10))
        r = ce.book_from_flow_response(db, req, create_thread_revision=tr)
        self.assertEqual(r.outcome, Out.COMMITTED)
        self.assertEqual(r.thread_revision_id, made[0])            # flow-txn key source
        again = ce.book_from_flow_response(db, req, create_thread_revision=tr)
        self.assertEqual((again.outcome, again.revision_id), (Out.IDEMPOTENT_REPLAY, r.revision_id))
        clash = ce.book_from_flow_response(db, ce.CeFlowBookingRequest(
            thread_id=8, lead_id=lead.id, inbound_event_id="wamid.Y", flow_token="8-1-x",
            requested_date=D, requested_time=time(10, 30)), create_thread_revision=tr)
        self.assertIn(clash.outcome, ce.CE_W1_FALLBACK_TO_MANUAL)

    def test_sb2a_30_manual_scheduling_preserves_escalation_on_unavailable_slot(self):
        db = _db(); _prof(db, default=True); lead = _lead(db)
        booked = _create(db, lead)                                 # 10:00 is taken
        req = ce.CeSchedulingEscalationRequest(
            thread_id=9, lead_id=lead.id, escalation_id="9:0", requested_date=D,
            requested_time=time(10, 0), revision_fields={"turno_notas": "coordinación manual"})
        r = ce.request_manual_scheduling(db, req)
        self.assertEqual(r.outcome, Out.COMMITTED)
        self.assertFalse(r.occupying)
        rev = db.get(Revision, r.revision_id)
        self.assertEqual((rev.estado_revision, rev.turno_hora), ("REPROGRAMAR", time(10)))
        self.assertEqual(ce.request_manual_scheduling(db, req).outcome, Out.IDEMPOTENT_REPLAY)
        self.assertNotEqual(r.revision_id, booked.revision_id)


class Scope(unittest.TestCase):

    def test_sb2a_31_conversation_engine_unchanged(self):
        import hashlib
        data = open(os.path.join(_BACKEND, "app/services/conversation_engine.py"), "rb").read()
        blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        self.assertEqual(blob, "ec0571bd740bec678fd7dea63d80a0ac998973c2")

    def test_sb2a_32_sqlite_is_not_concurrency_evidence(self):
        db = _db()
        self.assertEqual(db.get_bind().dialect.name, "sqlite")
        self.assertNotIn("occupied_during",
                         {c["name"] for c in inspect(db.get_bind()).get_columns("revisions")})


if __name__ == "__main__":
    unittest.main()
