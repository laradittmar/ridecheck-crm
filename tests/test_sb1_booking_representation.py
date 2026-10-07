"""SB1 — booking representation: policy, anchors, ORM compatibility, scope.

Runs anywhere (SQLite / pure Python). It proves the REPRESENTATION — defaults, separation of
service and buffer, half-open semantics of the mirror, the anchor calendar, the exception
calendar's shape — and that nothing outside SB1's scope moved.

It proves NOTHING about overlap prevention, attribution or concurrency. Those are database
guarantees and live in `test_sb1_booking_representation_pg.py`, against real PostgreSQL.
`test_sb1_20_sqlite_is_not_concurrency_proof` keeps that line from blurring.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import CalendarException, Lead, Profesional, Revision  # noqa: E402
from app.services import booking_representation as br  # noqa: E402
from app.services import route_anchors as ra  # noqa: E402
from app.services import schedule as sched  # noqa: E402


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    # Other suites swap sys.modules["app.db"] for a stub, so `app.db.Base` may not be the
    # metadata these classes are mapped on; take it from the classes themselves.
    Lead.__table__.metadata.create_all(engine)
    return Session(engine)


# ── interval policy ──────────────────────────────────────────────────────────

class IntervalPolicy(unittest.TestCase):

    def test_sb1_01_thirty_plus_fifteen_is_forty_five(self):
        self.assertEqual(br.SERVICE_MINUTES_DEFAULT, 30)
        self.assertEqual(br.BUFFER_MINUTES_DEFAULT, 15)
        self.assertEqual(br.OCCUPIED_MINUTES_DEFAULT, 45)
        iv = br.occupied_interval(date(2026, 11, 3), time(10, 0))
        self.assertEqual(iv.end - iv.start, timedelta(minutes=45))
        self.assertEqual((iv.service_minutes, iv.buffer_minutes), (30, 15))

    def test_sb1_02_service_and_buffer_persist_separately(self):
        s = _session()
        lead = Lead(); s.add(lead); s.flush()
        rev = Revision(lead_id=lead.id, turno_fecha=date(2026, 11, 3), turno_hora=time(10, 0))
        s.add(rev); s.flush(); s.expire_all()
        rev = s.get(Revision, rev.id)
        self.assertEqual((rev.service_minutes, rev.buffer_minutes), (30, 15))
        self.assertEqual(rev.turno_timezone, br.APPOINTMENT_TIMEZONE)
        rev.buffer_minutes = 0
        s.flush(); s.expire_all()
        rev = s.get(Revision, rev.id)
        self.assertEqual((rev.service_minutes, rev.buffer_minutes), (30, 0))
        self.assertEqual(br.revision_interval(rev).end, datetime(2026, 11, 3, 10, 30))
        cols = {c["name"] for c in inspect(s.get_bind()).get_columns("revisions")}
        self.assertTrue({"service_minutes", "buffer_minutes", "turno_timezone"} <= cols)

    def test_sb1_03_half_open_mirror(self):
        a = br.occupied_interval(date(2026, 11, 3), time(10, 0))
        adjacent = br.occupied_interval(date(2026, 11, 3), time(10, 45))
        overlapping = br.occupied_interval(date(2026, 11, 3), time(10, 30))
        self.assertFalse(a.overlaps(adjacent))
        self.assertFalse(adjacent.overlaps(a))
        self.assertTrue(a.overlaps(overlapping))

    def test_sb1_06_unscheduled_has_no_interval_and_does_not_occupy(self):
        self.assertIsNone(br.occupied_interval(None, None))
        self.assertIsNone(br.occupied_interval(date(2026, 11, 3), None))
        self.assertFalse(br.is_occupying(None, None, "PENDIENTE"))
        s = _session()
        lead = Lead(); s.add(lead); s.flush()
        rev = Revision(lead_id=lead.id)
        s.add(rev); s.flush()
        self.assertIsNone(rev.profesional_id)

    def test_sb1_08_non_occupying_estados_mirror_schedule_exactly(self):
        self.assertEqual(br.NON_OCCUPYING_ESTADOS, sched._NON_OCCUPYING_ESTADOS)
        d, t = date(2026, 11, 3), time(10, 0)
        for estado in ("CANCELADO", "REPROGRAMAR", "cancelado"):
            self.assertFalse(br.is_occupying(d, t, estado))
        for estado in ("PENDIENTE", "CONFIRMADO", "EN_PROCESO", "COMPLETADO", None):
            self.assertTrue(br.is_occupying(d, t, estado))

    def test_sb1_buenos_aires_interpretation(self):
        self.assertEqual(br.APPOINTMENT_TIMEZONE, sched.BUSINESS_TIMEZONE)
        start, end = br.occupied_interval(date(2026, 11, 3), time(10, 0)).aware()
        self.assertEqual(start.astimezone(timezone.utc), datetime(2026, 11, 3, 13, 0,
                                                                  tzinfo=timezone.utc))
        self.assertEqual(end - start, timedelta(minutes=45))
        # a late-evening instant in UTC is still the previous business date
        late = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(sched.business_date_at(late), date(2026, 9, 7))


# ── legacy classification ───────────────────────────────────────────────────

class LegacyClassification(unittest.TestCase):

    def test_sb1_11_classification_is_total_and_deterministic(self):
        C = br.LegacyClass
        d, t = date(2026, 9, 1), time(10, 0)
        cases = {
            (None, None, "PENDIENTE", None): C.UNSCHEDULED,
            (d, None, "PENDIENTE", None): C.INCOMPLETE,
            (None, t, "PENDIENTE", None): C.INCOMPLETE,
            (d, t, "CANCELADO", None): C.NON_OCCUPYING,
            (d, t, "reprogramar", 1): C.NON_OCCUPYING,
            (d, t, "CONFIRMADO", None): C.OCCUPYING_UNATTRIBUTED,
            (d, t, "COMPLETADO", 7): C.OCCUPYING_ATTRIBUTED,
        }
        for args, expected in cases.items():
            self.assertIs(br.classify(*args), expected, args)
            self.assertIs(br.classify(*args), br.classify(*args))
        # classification and occupancy agree
        for (fd, ft, estado, _p), cls in cases.items():
            occupying = cls in (C.OCCUPYING_ATTRIBUTED, C.OCCUPYING_UNATTRIBUTED)
            self.assertEqual(br.is_occupying(fd, ft, estado), occupying)

    def test_sb1_11_classification_sql_reads_only_pre_sb1_columns(self):
        """So the same aggregate runs read-only against the un-migrated database."""
        for sql in (br.LEGACY_CLASSIFICATION_SQL, br.LEGACY_OVERLAP_SQL):
            for sb1_only in ("agenda_occupying", "occupied_during", "service_minutes",
                             "buffer_minutes", "is_launch_default", "resource_key"):
                self.assertNotIn(sb1_only, sql)
            for forbidden in ("INSERT", "UPDATE", "DELETE", "direccion", "nombre", "phone",
                              "email", "link_maps"):
                self.assertNotIn(forbidden, sql.upper() if forbidden.isupper() else sql)


# ── professional resource ───────────────────────────────────────────────────

class ProfessionalResource(unittest.TestCase):

    def test_sb1_professional_resource_columns_and_key(self):
        self.assertEqual(br.LAUNCH_PROFESSIONAL_KEY, "inspector.julian")
        s = _session()
        cols = {c["name"] for c in inspect(s.get_bind()).get_columns("profesionales")}
        self.assertTrue({"resource_key", "is_launch_default"} <= cols)
        p = Profesional(nombre="Fixture", apellido="SB1-FIXTURE", email="fixture@example.invalid")
        s.add(p); s.flush()
        self.assertFalse(p.is_launch_default)
        br.designate_launch_professional(s, p.id)
        self.assertEqual((p.resource_key, p.is_launch_default), (br.LAUNCH_PROFESSIONAL_KEY, True))
        q = Profesional(nombre="Other", apellido="SB1-FIXTURE", email="fixture@example.invalid")
        s.add(q); s.flush()
        with self.assertRaises(br.LaunchProfessionalError):
            br.designate_launch_professional(s, q.id)
        with self.assertRaises(br.LaunchProfessionalError):
            br.designate_launch_professional(s, 999_999)

    def test_sb1_no_professional_identity_is_invented_in_source(self):
        """Only the approved operational identity appears; no surname, email or phone."""
        email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
        for module in (br, ra):
            src = open(module.__file__, encoding="utf-8").read()
            self.assertEqual(email.findall(src), [])
            self.assertNotIn("apellido=", src)
        migration = os.path.join(_BACKEND, "migrations", "versions",
                                 "20261007_sb1_booking_representation.py")
        src = open(migration, encoding="utf-8").read()
        self.assertNotIn("INSERT INTO profesionales", src)
        self.assertNotIn("UPDATE revisions SET profesional_id", src)


# ── anchors ─────────────────────────────────────────────────────────────────

class RouteAnchors(unittest.TestCase):

    def test_sb1_12_home_and_school_are_distinct(self):
        self.assertNotEqual(ra.HOME, ra.SCHOOL)
        self.assertNotEqual(ra.HOME.key, ra.SCHOOL.key)
        self.assertEqual((ra.HOME.key, ra.SCHOOL.key), ("anchor.home", "anchor.school"))
        self.assertEqual(ra.HOME.anchor_type, ra.AnchorType.HOME)
        self.assertEqual(ra.SCHOOL.anchor_type, ra.AnchorType.SCHOOL)
        self.assertEqual(set(ra.ANCHORS), {ra.HOME.key, ra.SCHOOL.key})
        # SB1-R3 privacy: the tracked registry carries structure only. Real labels, location
        # references and coordinates are deployment-provided, never committed.
        self.assertEqual((ra.HOME.label, ra.SCHOOL.label), ("Home", "School"))
        for anchor in (ra.HOME, ra.SCHOOL):
            self.assertIsNone(anchor.location_ref)
            self.assertIsNone(anchor.coordinates)
        # Distinct even when both are configured, and even with identical placeholders
        # for everything but identity (synthetic values only).
        configured = [replace(a, location_ref=f"https://example.invalid/{a.key}")
                      for a in (ra.HOME, ra.SCHOOL)]
        self.assertNotEqual(configured[0], configured[1])
        same_ref = [replace(a, location_ref="https://example.invalid/same")
                    for a in (ra.HOME, ra.SCHOOL)]
        self.assertNotEqual(same_ref[0], same_ref[1])

    def test_sb1_12b_no_maps_short_links_in_tracked_sb1_files(self):
        """SB1-R3: a private anchor location must never be committed. The pattern is
        escaped so this file does not match itself, and no private value is named here."""
        host = re.compile(r"maps\.app\.goo\.gl")
        sb1_files = [
            "backend/app/services/route_anchors.py",
            "backend/app/services/booking_representation.py",
            "backend/app/models.py",
            "backend/migrations/versions/20261007_sb1_booking_representation.py",
            "tests/test_sb1_booking_representation.py",
            "tests/test_sb1_booking_representation_pg.py",
        ]
        swept = 0
        for rel in sb1_files:
            path = os.path.join(_ROOT, rel)
            self.assertTrue(os.path.isfile(path), rel)        # never vacuous
            self.assertEqual(host.findall(open(path, encoding="utf-8").read()), [], rel)
        for top in ("backend/app", "backend/migrations", "tests"):
            for root, _dirs, files in os.walk(os.path.join(_ROOT, top)):
                for name in files:
                    if name.endswith((".py", ".json", ".md", ".html", ".csv", ".txt")):
                        swept += 1
                        text_ = open(os.path.join(root, name), encoding="utf-8",
                                     errors="replace").read()
                        self.assertEqual(host.findall(text_), [], os.path.join(root, name))
        self.assertGreater(swept, 100)

    def test_sb1_13_four_monday_reference_dates(self):
        expected = {date(2026, 9, 7): ra.SCHOOL, date(2026, 9, 14): ra.HOME,
                    date(2026, 9, 21): ra.SCHOOL, date(2026, 9, 28): ra.HOME}
        for day, anchor in expected.items():
            self.assertEqual(day.weekday(), 0)
            self.assertIs(ra.start_anchor(day).target, anchor, day)
            self.assertIs(ra.end_anchor(day).target, ra.LAST_INSPECTION, day)
        # continues alternating in both directions
        self.assertIs(ra.start_anchor(date(2026, 8, 31)).target, ra.HOME)
        self.assertIs(ra.start_anchor(date(2026, 10, 5)).target, ra.SCHOOL)

    def test_sb1_14_two_friday_reference_dates(self):
        self.assertEqual(date(2026, 9, 11).weekday(), 4)
        self.assertIs(ra.end_anchor(date(2026, 9, 11)).target, ra.LAST_INSPECTION)
        self.assertIs(ra.end_anchor(date(2026, 9, 18)).target, ra.SCHOOL)
        self.assertIs(ra.end_anchor(date(2026, 9, 25)).target, ra.LAST_INSPECTION)
        self.assertIs(ra.end_anchor(date(2026, 9, 4)).target, ra.SCHOOL)
        for friday in (date(2026, 9, 11), date(2026, 9, 18)):
            self.assertIs(ra.start_anchor(friday).target, ra.SCHOOL)

    def test_sb1_15_tuesday_thursday_end_at_school_with_julian_marker(self):
        for day in (date(2026, 11, 3), date(2026, 11, 5)):      # Tue, Thu
            res = ra.end_anchor(day)
            self.assertIs(res.target, ra.SCHOOL)
            self.assertEqual(res.override_authority, ra.OVERRIDE_AUTHORITY_JULIAN)
            self.assertIs(ra.start_anchor(day).target, ra.HOME)
        # nobody else carries an override marker
        for rule in ra.RULES:
            if not (rule.role is ra.AnchorRole.END and rule.weekday in (1, 3)):
                self.assertIsNone(rule.override_authority, rule)

    def test_sb1_weekly_start_end_policy(self):
        week = {  # Mon 2026-11-02 … Sun 2026-11-08
            date(2026, 11, 3): (ra.HOME, ra.SCHOOL),            # Tue
            date(2026, 11, 4): (ra.SCHOOL, ra.LAST_INSPECTION), # Wed
            date(2026, 11, 5): (ra.HOME, ra.SCHOOL),            # Thu
            date(2026, 11, 7): (ra.HOME, ra.LAST_INSPECTION),   # Sat
        }
        for day, (start, end) in week.items():
            self.assertIs(ra.start_anchor(day).target, start, day)
            self.assertIs(ra.end_anchor(day).target, end, day)
        self.assertIsNone(ra.start_anchor(date(2026, 11, 8)))   # Sunday closed
        self.assertIsNone(ra.end_anchor(date(2026, 11, 8)))

    def test_sb1_registry_agrees_with_legacy_start_parity(self):
        """`schedule._zero_zone_detail` (untouched) and the registry name the same anchor
        type on every day, so SB1 changes no start origin — only its label authority."""
        svc = sched.ScheduleService(db=None)
        legacy_type = {sched.ZERO_SANTA: ra.AnchorType.HOME, sched.ZERO_MELO: ra.AnchorType.SCHOOL}
        day = date(2026, 1, 5)
        for _ in range(730):
            if day.weekday() != 6:
                self.assertIs(ra.start_anchor(day).anchor.anchor_type,
                              legacy_type[svc._zero_zone_detail(day)], day)
            day += timedelta(days=1)


# ── calendar exceptions ─────────────────────────────────────────────────────

class CalendarExceptions(unittest.TestCase):
    """Representation shape on SQLite. The CHECKs are re-proven on PostgreSQL (pg16)."""

    def test_sb1_16_closed_date(self):
        s = _session()
        row = CalendarException(kind="CLOSED_DATE", exception_date=date(2026, 12, 25),
                                reason="cierre", created_by="operator")
        s.add(row); s.flush(); s.refresh(row)
        self.assertIsNotNone(row.created_at)
        self.assertEqual(row.timezone, br.APPOINTMENT_TIMEZONE)
        bad = CalendarException(kind="CLOSED_DATE", exception_date=date(2026, 12, 26),
                                start_time=time(9, 0), end_time=time(10, 0))
        s.add(bad)
        with self.assertRaises(IntegrityError):
            s.flush()

    def test_sb1_17_unavailable_interval(self):
        s = _session()
        s.add(CalendarException(kind="UNAVAILABLE_INTERVAL", exception_date=date(2026, 11, 3),
                                start_time=time(12, 0), end_time=time(13, 0)))
        s.flush()
        s.add(CalendarException(kind="UNAVAILABLE_INTERVAL", exception_date=date(2026, 11, 3),
                                start_time=time(13, 0), end_time=time(12, 0)))
        with self.assertRaises(IntegrityError):
            s.flush()


# ── unchanged contracts and scope ───────────────────────────────────────────

def _git_blob_sha1(path: str) -> str:
    data = open(path, "rb").read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


#: Blob ids at the SB1 parent `ab95706`. SB1 may change none of these files.
_FROZEN_AT_PARENT = {
    "backend/app/services/conversation_engine.py": "ec0571bd740bec678fd7dea63d80a0ac998973c2",
    "backend/app/services/semantic_interpreter.py": "0de2635795da8e408da12f1264c12f840a87d82c",
    "backend/app/services/semantic_turn_evidence.py": "50118bf6cbf21108bc1356057ba95c9a12e816c7",
    "backend/app/services/scheduling_reconciler.py": "cb102122e1dab93ff1d182b32d35f031dc7d0040",
    "backend/app/services/field_reconciler.py": "5af92de42901e130c8f9e9b59d63082a14eda594",
    "backend/app/services/shadow_reconciler.py": "2024d335a0383054f3a0d3796ac1109101de3289",
    "backend/app/services/claim_projection.py": "e61cd7ef60a422017e56e91a4028b7e7a7af9317",
    "backend/app/services/response_composition.py": "aa60cbc06578efcbc69aa8d046374690674c0789",
    "backend/app/services/response_validator.py": "7085211b96771b802c177789e8c4460e4525dfad",
    "backend/app/services/pricing.py": "9aed0358cfba7b08a4082bd7b1ecebcc778e5b5a",
    "backend/app/services/booking_flow_service.py": "58a227e065fdc1afc19c0b3cc893c25c1bc3da98",
    "backend/app/services/schedule.py": "00487c9a46ee168ae12dc76667863f0fbb5f71ad",
    "backend/app/services/travel.py": "fc95b0550e486b55ddf2cbb2af19e6c6d104d11d",
    "backend/app/services/outbound_safety_gate.py": "73481ce2d8473edfc7ba04c123f5e12d6f1d5395",
    "backend/app/services/outbound_guard.py": "db2e3766d540e1b858ddb31cdd68c031ed2c830b",
    "backend/app/services/scheduling_lexicon.py": "0df23923081484e221441a36ac36afde4a6bcfb3",
    "backend/app/services/acceptance_lexicon.py": "153e39d3975ad2bcdd29c06c43ff5fde9d3ddb7a",
    "backend/app/routes/flow_data_exchange.py": "559871881a5ee5090fce8387a31bbb134baa941d",
    "backend/app/routes/whatsapp.py": "b2913d5584db84b6e0f1c8613d3ff968e15449be",
    "meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json":
        "6981647ac8f14b32de85401191ae0b12a7a6f991",
}


class UnchangedContracts(unittest.TestCase):

    def test_sb1_18_availability_and_flow_contract_unchanged(self):
        # availability still occupies 45 minutes from the start, which is exactly the SB1
        # default interval — so the database and the offer path agree on every row today
        self.assertEqual(sched.SERVICE_MINUTES, br.OCCUPIED_MINUTES_DEFAULT)
        for name in ("schedule.py", "booking_flow_service.py"):
            rel = f"backend/app/services/{name}"
            self.assertEqual(_git_blob_sha1(os.path.join(_ROOT, rel)), _FROZEN_AT_PARENT[rel])
        rel = "meta/flows/booking/PUBLISHED_28104222025943520_v7.3.json"
        self.assertEqual(_git_blob_sha1(os.path.join(_ROOT, rel)), _FROZEN_AT_PARENT[rel])

    def test_sb1_20_frozen_files_unchanged(self):
        changed = [rel for rel, blob in _FROZEN_AT_PARENT.items()
                   if _git_blob_sha1(os.path.join(_ROOT, rel)) != blob]
        self.assertEqual(changed, [])

    def test_sb1_20_new_modules_make_no_external_call(self):
        for module in (br, ra):
            src = open(module.__file__, encoding="utf-8").read()
            for marker in ("requests", "httpx", "urllib", "openai", "socket", "googlemaps"):
                self.assertNotIn(f"import {marker}", src)
                self.assertNotIn(f"from {marker}", src)

    def test_sb1_20_sqlite_is_not_concurrency_proof(self):
        """SQLite has no exclusion constraint, so it accepts a true overlap. Recorded here
        so no SQLite pass is ever quoted as overlap or concurrency evidence (SB0-05)."""
        s = _session()
        self.assertEqual(s.get_bind().dialect.name, "sqlite")
        lead = Lead(); s.add(lead); s.flush()
        p = Profesional(nombre="Fixture", apellido="SB1-FIXTURE", email="fixture@example.invalid")
        s.add(p); s.flush()
        for at in (time(10, 0), time(10, 30)):
            s.add(Revision(lead_id=lead.id, profesional_id=p.id,
                           turno_fecha=date(2026, 11, 3), turno_hora=at))
        s.flush()   # accepted: SQLite proves nothing about overlap
        n = s.execute(text("SELECT count(*) FROM revisions")).scalar()
        self.assertEqual(n, 2)
        self.assertNotIn("occupied_during",
                         {c["name"] for c in inspect(s.get_bind()).get_columns("revisions")})


if __name__ == "__main__":
    unittest.main()
