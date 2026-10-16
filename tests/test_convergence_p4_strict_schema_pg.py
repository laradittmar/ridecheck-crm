"""Convergence Phase 4 STRICT-SCHEMA-ADOPTION — proven on real PostgreSQL 16 through Alembic
itself (a subprocess, exactly as a deployment runs it), the start-up preflight, a real
`uvicorn` start, and the production booking / notification / refusal paths.

  CONV_SSA_PARENT_URL   a disposable PostgreSQL 16 database at the Phase 4 PARENT schema
                        (`20261014_ledger_detached_state`), built the project's way. It is used
                        only as a TEMPLATE: every case clones it into its own database
                        (`ssa_*`, created and dropped here), so the role needs CREATEDB.
  CONV_P3_PG_URL        a disposable database at the head, for the runtime classes (they
                        truncate it per test).

Every refusal asserts: a non-zero exit with the typed message, the Alembic version unchanged,
and a whole-database fingerprint — tables, columns with defaults and comments, constraints,
indexes, triggers, functions, rules, policies, sequences, event triggers, inheritance, the
version and the data of both touched tables — byte-equal before and after. That fingerprint
is this file's own catalogue query, independent of `app.services.schema_fingerprint`.
"""
from __future__ import annotations

import hashlib
import itertools
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

_BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
PARENT_URL = os.environ.get("CONV_SSA_PARENT_URL", "")
P4_PARENT = "20261014_ledger_detached_state"
P4_REV = "20261015_booking_attempt_events"
HEAD = "20261016_notification_sent_at"
_SEQ = itertools.count(1)

# ── the whole-database fingerprint (this file's own oracle) ──────────────────────────────────

_NS = "relnamespace = current_schema()::regnamespace"
_WHOLE = [
    ("tables", f"SELECT concat_ws(':', c.relname, c.relkind, c.relpersistence, c.relrowsecurity,"
               f" pg_get_userbyid(c.relowner), obj_description(c.oid, 'pg_class'), "
               f"c.reloptions::text) FROM pg_class c WHERE c.{_NS} ORDER BY 1"),
    ("columns", f"SELECT concat_ws(':', c.relname || '.' || a.attname, "
                f"format_type(a.atttypid, a.atttypmod), a.attnotnull, a.attidentity, "
                f"a.attgenerated, a.attcollation, pg_get_expr(d.adbin, d.adrelid), "
                f"col_description(c.oid, a.attnum)) FROM pg_attribute a "
                f"JOIN pg_class c ON c.oid = a.attrelid LEFT JOIN pg_attrdef d ON "
                f"d.adrelid = a.attrelid AND d.adnum = a.attnum WHERE c.{_NS} AND "
                f"c.relkind IN ('r','p') AND a.attnum > 0 AND NOT a.attisdropped "
                f"ORDER BY c.relname, a.attnum"),
    ("constraints", "SELECT concat_ws(':', conrelid::regclass::text || '.' || conname, contype, "
                    "condeferrable, convalidated, pg_get_constraintdef(oid)) FROM pg_constraint "
                    "WHERE connamespace = current_schema()::regnamespace ORDER BY 1"),
    ("indexes", f"SELECT concat_ws(':', i.indexrelid::regclass::text, "
                f"pg_get_indexdef(i.indexrelid), i.indisvalid) FROM pg_index i "
                f"JOIN pg_class c ON c.oid = i.indrelid WHERE c.{_NS} ORDER BY 1"),
    ("triggers", "SELECT concat_ws(':', tgrelid::regclass::text, pg_get_triggerdef(oid), "
                 "tgenabled) FROM pg_trigger WHERE NOT tgisinternal ORDER BY 1"),
    ("functions", "SELECT concat_ws(':', p.proname || '(' || "
                  "pg_get_function_identity_arguments(p.oid) || ')', md5(p.prosrc), "
                  "p.prosecdef) FROM pg_proc p WHERE p.pronamespace = "
                  "current_schema()::regnamespace AND NOT EXISTS (SELECT 1 FROM pg_depend d "
                  "WHERE d.objid = p.oid AND d.deptype = 'e') ORDER BY 1"),
    ("rules", f"SELECT concat_ws(':', r.ev_class::regclass::text, r.rulename, "
              f"pg_get_ruledef(r.oid)) FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class "
              f"WHERE c.{_NS} AND c.relkind <> 'v' ORDER BY 1"),
    ("policies", "SELECT concat_ws(':', polrelid::regclass::text, polname) FROM pg_policy "
                 "ORDER BY 1"),
    ("sequences", f"SELECT concat_ws(':', c.relname, format_type(s.seqtypid, NULL), "
                  f"s.seqincrement) FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid "
                  f"WHERE c.{_NS} ORDER BY 1"),
    ("event_triggers", "SELECT concat_ws(':', evtname, evtenabled) FROM pg_event_trigger "
                       "ORDER BY 1"),
    ("inherits", "SELECT inhrelid::regclass::text || '<' || inhparent::regclass::text "
                 "FROM pg_inherits ORDER BY 1"),
    ("version", "SELECT version_num FROM alembic_version ORDER BY 1"),
]
_REAL_TABLE = "SELECT relkind = 'r' FROM pg_class WHERE oid = to_regclass(:t)"
_DATA = {
    "thread_revisions": "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) "
                        "FROM thread_revisions t",
    "booking_attempt_events": "SELECT md5(coalesce(string_agg(e::text, '|' ORDER BY e.id), "
                              "'')) FROM booking_attempt_events e",
}


def _whole(url) -> dict:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            versioned = c.execute(text(_REAL_TABLE), {"t": "alembic_version"}).scalar()
            out = {k: c.execute(text(q)).scalars().all() for k, q in _WHOLE
                   if k != "version" or versioned}
            for table, q in _DATA.items():
                real = c.execute(text(_REAL_TABLE), {"t": table}).scalar()
                out["data:" + table] = c.execute(text(q)).scalar() if real else None
            return out
    finally:
        eng.dispose()


def _alembic(url, *args):
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], env=env, cwd=str(_BACKEND),
                          capture_output=True, text=True, timeout=600)


def _sql(url, *statements):
    eng = create_engine(url)
    try:
        with eng.begin() as c:
            for s in statements:
                c.execute(text(s))
    finally:
        eng.dispose()


def _scalars(url, sql):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).scalars().all()
    finally:
        eng.dispose()


def _admin():
    return create_engine(make_url(PARENT_URL).set(database="postgres"),
                         isolation_level="AUTOCOMMIT")


def _clone(template_url, name) -> str:
    template = make_url(template_url).database
    eng = _admin()
    try:
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
    finally:
        eng.dispose()
    return make_url(PARENT_URL).set(database=name).render_as_string(hide_password=False)


def _drop(name):
    eng = _admin()
    try:
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        eng.dispose()


def _create_all(url):
    from app.models import Lead
    eng = create_engine(url)
    try:
        Lead.__table__.metadata.create_all(eng)        # what an image started first does
    finally:
        eng.dispose()


def _migration(name):
    import importlib.util
    path = _BACKEND / "migrations" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_ssa_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# What the OLD application start-up ran, verbatim (the legacy state under test).
_LEGACY_DDL = ("ALTER TABLE thread_revisions ADD COLUMN IF NOT EXISTS notification_sent_at "
               "TIMESTAMPTZ")
_STAGES = "'PRE_EXECUTE', 'AUTHORITY', 'FINALISE', 'NOTIFICATION'"
_OUTCOMES = "'REFUSED', 'RETRYABLE', 'CONTENDED', 'INTERNAL', 'FAILED', 'HELD'"
#: A hand-written description of the create_all shape — the oracle for the frozen literals.
_HAND_DDL = [f"""
    CREATE TABLE booking_attempt_events (
      id BIGSERIAL PRIMARY KEY,
      event_uid VARCHAR(40) NOT NULL UNIQUE,
      occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      source VARCHAR(32) NOT NULL,
      stage VARCHAR(16) NOT NULL,
      outcome VARCHAR(16) NOT NULL,
      reason VARCHAR(64) NOT NULL,
      command_key_hash VARCHAR(16),
      attempt_ref_hash VARCHAR(16),
      hist_thread_id INTEGER,
      hist_offer_id INTEGER,
      hist_revision_id INTEGER,
      hist_thread_revision_id INTEGER,
      CONSTRAINT ck_booking_attempt_events_stage CHECK (stage IN ({_STAGES})),
      CONSTRAINT ck_booking_attempt_events_outcome CHECK (outcome IN ({_OUTCOMES})))""",
    "CREATE INDEX ix_booking_attempt_events_occurred ON booking_attempt_events (occurred_at)",
    "CREATE INDEX ix_booking_attempt_events_thread ON booking_attempt_events (hist_thread_id)",
    "CREATE INDEX ix_booking_attempt_events_thread_revision ON booking_attempt_events "
    "(hist_thread_revision_id)",
]
_HAND_FUNCTION_BODY = """
        BEGIN
          RAISE EXCEPTION 'booking_attempt_events is append-only (% refused)', TG_OP
            USING ERRCODE = '23514', CONSTRAINT = 'booking_attempt_events_append_only';
        END """
_INSERT = ("INSERT INTO booking_attempt_events (event_uid, source, stage, outcome, reason) "
           "VALUES ('{uid}', 'X', 'AUTHORITY', 'REFUSED', 'R')")


@unittest.skipUnless(PARENT_URL, "CONV_SSA_PARENT_URL not set — no disposable PostgreSQL; "
                                 "nothing proven")
class _Strict(unittest.TestCase):
    """Templates: the parent (given), `ssa_t_orm` (parent + the app's create_all) and
    `ssa_t_head` (parent + `alembic upgrade head`)."""

    templates = {}

    @classmethod
    def setUpClass(cls):
        if not _Strict.templates:
            orm = _clone(PARENT_URL, "ssa_t_orm")
            _create_all(orm)
            head = _clone(PARENT_URL, "ssa_t_head")
            r = _alembic(head, "upgrade", "head")
            assert r.returncode == 0, r.stderr[-2000:]
            _Strict.templates = {"parent": PARENT_URL, "orm": orm, "head": head}

    def setUp(self):
        self._made = []

    def tearDown(self):
        for name in self._made:
            _drop(name)

    def db(self, base="parent", *setup) -> str:
        name = f"ssa_c_{next(_SEQ)}"
        self._made.append(name)
        url = _clone(self.templates[base], name)
        if setup:
            _sql(url, *setup)
        return url

    def upgraded(self, url, *branches):
        r = _alembic(url, "upgrade", "head")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        for b in branches:
            self.assertIn(b, r.stdout)
        self.assertEqual(_scalars(url, "SELECT version_num FROM alembic_version"), [HEAD])
        return r

    def refused(self, url, *expect, typed=True):
        """The upgrade fails before any change: same version, same whole fingerprint."""
        before = _whole(url)
        r = _alembic(url, "upgrade", "head")
        out = r.stderr + r.stdout
        self.assertNotEqual(r.returncode, 0, "a malformed shape was accepted:\n" + out[-1500:])
        if typed:
            self.assertIn("SchemaShapeRefused", out)
        for e in expect:
            self.assertIn(e, out)
        after = _whole(url)
        self.assertEqual(after["version"], before["version"], "a refusal advanced Alembic")
        for part in before:
            self.assertEqual(after[part], before[part], f"partial DDL / data change in {part}")
        return out

    def preflight(self, url) -> list:
        from app.services.schema_preflight import missing_declared_booking_schema
        eng = create_engine(url)
        try:
            return missing_declared_booking_schema(eng)
        finally:
            eng.dispose()

    def read(self, url):
        from app.services import schema_fingerprint as F
        eng = create_engine(url)
        try:
            with eng.connect() as c:
                return (F.table_fingerprint(c, "booking_attempt_events"),
                        F.function_fingerprint(c, "booking_attempt_events_append_only"),
                        F.column_fingerprint(c, "thread_revisions", "notification_sent_at"))
        finally:
            eng.dispose()

    def assert_enforced(self, url):
        """Enforced, not merely present: each object is exercised and must refuse."""
        refusals = {
            "update": ("UPDATE booking_attempt_events SET reason = 'Y'", "append-only"),
            "delete": ("DELETE FROM booking_attempt_events", "append-only"),
            "stage": (_INSERT.format(uid="e2").replace("'AUTHORITY'", "'WHENEVER'"),
                      "ck_booking_attempt_events_stage"),
            "outcome": (_INSERT.format(uid="e3").replace("'REFUSED'", "'OK'"),
                        "ck_booking_attempt_events_outcome"),
            "unique": (_INSERT.format(uid="e1"), "booking_attempt_events_event_uid_key"),
        }
        _sql(url, _INSERT.format(uid="e1"))
        eng = create_engine(url)
        try:
            for name, (stmt, expect) in refusals.items():
                with self.subTest(enforcement=name), eng.connect() as c:
                    with self.assertRaises(Exception) as cm:
                        c.execute(text(stmt))
                        c.commit()
                    self.assertIn(expect, str(cm.exception))
        finally:
            eng.dispose()
        self.assertEqual(_scalars(url, "SELECT reason FROM booking_attempt_events"), ["R"])
        _sql(url, "TRUNCATE booking_attempt_events")


# ══ 8.1 valid paths ═════════════════════════════════════════════════════════════════════════

class SSA_ValidPaths(_Strict):

    def test_ssa_01_parent_first_creates_canonical_objects(self):
        url = self.db("parent")
        self.assertEqual(len(self.preflight(url)), 3, self.preflight(url))
        self.upgraded(url, f"{P4_REV}: CREATED", f"{HEAD}: ADDED")
        m15, m16 = _migration(P4_REV), _migration(HEAD)
        table, fn, col = self.read(url)
        self.assertEqual(table, m15.F_CANONICAL)
        self.assertEqual(fn, m15.F_FUNCTION)
        self.assertEqual(col["column"], m16._ADDED)
        self.assert_enforced(url)
        self.assertEqual(self.preflight(url), [])

    def test_ssa_02_app_first_exact_empty_orm_table_is_adopted(self):
        url = self.db("orm")
        table, fn, _ = self.read(url)
        self.assertEqual(table, _migration(P4_REV).F_ORM, "create_all is not the ORM shape")
        self.assertEqual(fn, [])
        self.assertTrue(self.preflight(url), "the preflight let an app-first schema start")
        self.upgraded(url, f"{P4_REV}: ADOPTED_ORM_SHAPE", f"{HEAD}: ADDED")
        self.assert_enforced(url)
        self.assertEqual(self.preflight(url), [])
        parent_first = self.db("parent")
        self.upgraded(parent_first)
        self.assertEqual(_whole(url), _whole(parent_first),
                         "the two supported orders reached different schemas")

    def test_ssa_03_legacy_column_all_null_is_adopted_without_marker(self):
        url = self.db("parent", _LEGACY_DDL)
        self.upgraded(url, f"{HEAD}: ADOPTED_LEGACY_COLUMN")
        self.assertEqual(self.read(url)[2]["column"], _migration(HEAD)._LEGACY)
        self.assertEqual(self.preflight(url), [])
        r = _alembic(url, "downgrade", P4_REV)              # null-only: the column STAYS
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(self.read(url)[2]["column"], _migration(HEAD)._LEGACY)

    def test_ssa_04_legacy_column_with_timestamps_preserves_every_value(self):
        url = self.db("parent", _LEGACY_DDL,
                      "INSERT INTO whatsapp_contacts (wa_id) VALUES ('549110007777')",
                      "INSERT INTO whatsapp_threads (contact_id) SELECT id FROM "
                      "whatsapp_contacts")
        stamps = ["2026-10-01 12:00:00.123456+00", "2026-10-02 23:59:59.999999+00",
                  "2026-09-30 03:00:00+00", None]
        for s in stamps:
            _sql(url, "INSERT INTO thread_revisions (thread_id, status, notification_sent_at) "
                      "SELECT id, 'booked', " + (f"'{s}'" if s else "NULL")
                      + " FROM whatsapp_threads")
        probe = ("SELECT coalesce(extract(epoch FROM notification_sent_at)::text, 'NULL')||'|'"
                 "||coalesce(notification_sent_at::text, 'NULL') FROM thread_revisions "
                 "ORDER BY id")
        before = _scalars(url, probe)
        self.upgraded(url, f"{HEAD}: ADOPTED_LEGACY_COLUMN")
        self.assertEqual(_scalars(url, probe), before, "the upgrade rewrote evidence")
        r = _alembic(url, "downgrade", P4_REV)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("notification-evidence loss", r.stderr + r.stdout)
        self.assertEqual(_scalars(url, probe), before)
        self.assertEqual(_scalars(url, "SELECT version_num FROM alembic_version"), [HEAD])

    def test_ssa_05_already_canonical_objects_are_verified_not_rebuilt(self):
        url = self.db("head")
        before = _whole(url)
        r = _alembic(url, "stamp", P4_PARENT)              # the version behind the objects
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.upgraded(url, f"{P4_REV}: VERIFIED_CANONICAL", f"{HEAD}: VERIFIED_CANONICAL")
        self.assertEqual(_whole(url), before, "verification rebuilt or changed an object")
        # a pre-existing canonical table is still not adopted with rows in it
        _sql(url, _INSERT.format(uid="kept"))
        _alembic(url, "stamp", P4_PARENT)
        self.refused(url, "holds rows")
        self.assertEqual(_scalars(url, "SELECT event_uid FROM booking_attempt_events"),
                         ["kept"])

    def test_ssa_06_clean_downgrade_and_reupgrade_are_exact(self):
        url = self.db("parent")
        parent = _whole(url)
        self.upgraded(url)
        head = _whole(url)
        r = _alembic(url, "downgrade", P4_PARENT)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(_whole(url), parent)
        self.upgraded(url, f"{P4_REV}: CREATED", f"{HEAD}: ADDED")
        self.assertEqual(_whole(url), head)
        self.assert_enforced(url)

    def test_ssa_07_downgrade_refuses_while_attempt_evidence_exists(self):
        url = self.db("parent")
        self.upgraded(url)
        _sql(url, _INSERT.format(uid="evidence"))
        before = _whole(url)
        r = _alembic(url, "downgrade", P4_PARENT)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("audit-data loss", r.stderr + r.stdout)
        self.assertEqual(_whole(url), before)

    def test_ssa_08_stamp_built_database_downgrades_only_the_orm_shape(self):
        """The project's `create_all` + `stamp head` bootstrap carries the ORM shape, empty:
        it downgrades. The same bootstrap with a malformed table refuses, unchanged."""
        url = self.db("orm")
        parent = _whole(self.db("parent"))
        _alembic(url, "stamp", "head")
        r = _alembic(url, "downgrade", P4_PARENT)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(_whole(url), parent)
        bad = self.db("orm", "ALTER TABLE booking_attempt_events ALTER COLUMN occurred_at "
                             "DROP DEFAULT")
        _alembic(bad, "stamp", "head")
        before = _whole(bad)
        r = _alembic(bad, "downgrade", P4_PARENT)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not exactly a shape this revision owns", r.stderr + r.stdout)
        self.assertEqual(_whole(bad), before)

    def test_ssa_09_literals_equal_the_orm_the_migration_and_a_hand_written_oracle(self):
        """No production constant is its own oracle: the frozen literals must equal what
        `create_all` makes, what the migration makes, AND a hand-written DDL description."""
        m15, m16 = _migration(P4_REV), _migration(HEAD)
        from app.services import schema_preflight as P
        hand = self.db("parent", *_HAND_DDL)
        self.assertEqual(self.read(hand)[0], m15.F_ORM)
        _sql(hand, "CREATE FUNCTION booking_attempt_events_append_only() RETURNS trigger "
                   "LANGUAGE plpgsql AS $$" + _HAND_FUNCTION_BODY + "$$",
             "CREATE TRIGGER trg_booking_attempt_events_append_only BEFORE UPDATE OR DELETE "
             "ON booking_attempt_events FOR EACH ROW EXECUTE FUNCTION "
             "booking_attempt_events_append_only()")
        table, fn, _ = self.read(hand)
        self.assertEqual(table, m15.F_CANONICAL)
        self.assertEqual(fn, m15.F_FUNCTION)
        self.assertEqual(fn[0][-1], hashlib.md5(_HAND_FUNCTION_BODY.encode()).hexdigest())
        self.assertEqual(self.read(self.templates["orm"])[0], m15.F_ORM)
        self.assertEqual(self.read(self.templates["head"])[0], m15.F_CANONICAL)
        self.assertEqual(P._EVENTS_CANONICAL, m15.F_CANONICAL)
        self.assertEqual(P._EVENTS_FUNCTION, m15.F_FUNCTION)
        legacy = self.db("parent", _LEGACY_DDL)
        self.assertEqual(self.read(legacy)[2]["column"], m16._LEGACY)
        self.assertEqual(set(P._NOTIFICATION_PERMITTED), {m16._LEGACY, m16._ADDED})


# ══ 8.2 mandatory refusals — before any mutation, atomically ═══════════════════════════════

_DROP_CK = "ALTER TABLE booking_attempt_events DROP CONSTRAINT {}"
_ALTER = "ALTER TABLE booking_attempt_events "
_SWALLOW_FN = ("CREATE FUNCTION ssa_swallow() RETURNS trigger LANGUAGE plpgsql AS "
               "$$ BEGIN RETURN NULL; END $$")


class SSA_EventTableRefusals(_Strict):

    def cases(self, cases, base="orm", expect=("SchemaShapeRefused",)):
        for name, setup in cases.items():
            with self.subTest(case=name):
                url = self.db(base, *setup)
                self.refused(url, *expect)

    def test_ssa_20_widened_narrowed_renamed_extra_or_rewritten_checks(self):
        ck = "ck_booking_attempt_events_stage"
        self.cases({
            "widened": [_DROP_CK.format(ck), _ALTER + f"ADD CONSTRAINT {ck} CHECK (stage IN "
                        f"({_STAGES}, 'WHENEVER'))"],
            "narrowed": [_DROP_CK.format(ck), _ALTER + f"ADD CONSTRAINT {ck} CHECK (stage IN "
                         "('PRE_EXECUTE', 'AUTHORITY', 'FINALISE'))"],
            "renamed": [_ALTER + f"RENAME CONSTRAINT {ck} TO ck_stage_renamed"],
            "extra": [_ALTER + "ADD CONSTRAINT ck_extra CHECK (reason <> '')"],
            "disjunction": [_DROP_CK.format(ck), _ALTER + f"ADD CONSTRAINT {ck} CHECK "
                            f"(stage IN ({_STAGES}) OR reason = 'X')"],
            "not_valid": [_DROP_CK.format(ck), _ALTER + f"ADD CONSTRAINT {ck} CHECK (stage IN "
                          f"({_STAGES})) NOT VALID"],
            "outcome_widened": [_DROP_CK.format("ck_booking_attempt_events_outcome"),
                                _ALTER + "ADD CONSTRAINT ck_booking_attempt_events_outcome "
                                f"CHECK (outcome IN ({_OUTCOMES}, 'OK'))"],
        })

    def test_ssa_21_missing_altered_or_unexpected_defaults(self):
        self.cases({
            "occurred_at_default_missing": [_ALTER + "ALTER COLUMN occurred_at DROP DEFAULT"],
            "id_default_missing": [_ALTER + "ALTER COLUMN id DROP DEFAULT"],
            "occurred_at_other_default": [_ALTER + "ALTER COLUMN occurred_at SET DEFAULT "
                                          "'2026-01-01 00:00+00'"],
            "unexpected_default": [_ALTER + "ALTER COLUMN reason SET DEFAULT 'R'"],
            "id_identity": [_ALTER + "ALTER COLUMN id DROP DEFAULT",
                            _ALTER + "ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY"],
        })

    def test_ssa_22_extra_missing_reordered_or_retyped_columns(self):
        reordered = [_HAND_DDL[0].replace(
            "      source VARCHAR(32) NOT NULL,\n      stage VARCHAR(16) NOT NULL,",
            "      stage VARCHAR(16) NOT NULL,\n      source VARCHAR(32) NOT NULL,")] \
            + _HAND_DDL[1:]
        self.assertNotEqual(reordered[0], _HAND_DDL[0])
        self.cases({"extra": [_ALTER + "ADD COLUMN note TEXT"],
                    "missing": [_ALTER + "DROP COLUMN hist_offer_id"],
                    "retyped": [_ALTER + "ALTER COLUMN reason TYPE TEXT"],
                    "generated": [_ALTER + "DROP COLUMN hist_offer_id", _ALTER +
                                  "ADD COLUMN hist_offer_id INTEGER GENERATED ALWAYS AS "
                                  "(hist_thread_id) STORED"]})
        self.cases({"reordered": reordered}, base="parent")

    def test_ssa_23_wrong_nullability_length_precision_or_collation(self):
        self.cases({
            "nullable_source": [_ALTER + "ALTER COLUMN source DROP NOT NULL"],
            "not_null_hash": [_ALTER + "ALTER COLUMN command_key_hash SET NOT NULL"],
            "length": [_ALTER + "ALTER COLUMN event_uid TYPE VARCHAR(41)"],
            "precision": [_ALTER + "ALTER COLUMN occurred_at TYPE TIMESTAMPTZ(3)"],
            "collation": [_ALTER + "ALTER COLUMN source TYPE VARCHAR(32) COLLATE \"C\""],
            "bigint_hist": [_ALTER + "ALTER COLUMN hist_thread_id TYPE BIGINT"],
        })

    def test_ssa_24_wrong_primary_or_unique_under_the_expected_name(self):
        uq, pk = "booking_attempt_events_event_uid_key", "booking_attempt_events_pkey"
        self.cases({
            "unique_wrong_keys": [_DROP_CK.format(uq), _ALTER + f"ADD CONSTRAINT {uq} "
                                  "UNIQUE (event_uid, source)"],
            "unique_deferrable": [_DROP_CK.format(uq), _ALTER + f"ADD CONSTRAINT {uq} "
                                  "UNIQUE (event_uid) DEFERRABLE"],
            "unique_nulls_not_distinct": [_DROP_CK.format(uq), _ALTER + f"ADD CONSTRAINT {uq}"
                                          " UNIQUE NULLS NOT DISTINCT (event_uid)"],
            "unique_missing": [_DROP_CK.format(uq)],
            "pk_wrong_keys": [_DROP_CK.format(pk), _ALTER + f"ADD CONSTRAINT {pk} "
                              "PRIMARY KEY (id, event_uid)"],
            "pk_moved": [_DROP_CK.format(pk), _ALTER + f"ADD CONSTRAINT {pk} "
                         "PRIMARY KEY (event_uid)"],
        })

    def test_ssa_25_wrong_index_columns_order_uniqueness_predicate_or_method(self):
        ix = "ix_booking_attempt_events_occurred"
        drop = f"DROP INDEX {ix}"
        on = "ON booking_attempt_events"
        self.cases({
            "columns": [drop, f"CREATE INDEX {ix} {on} (hist_thread_id)"],
            "order": [drop, f"CREATE INDEX {ix} {on} (occurred_at DESC)"],
            "unique": [drop, f"CREATE UNIQUE INDEX {ix} {on} (occurred_at)"],
            "predicate": [drop, f"CREATE INDEX {ix} {on} (occurred_at) WHERE "
                                "hist_thread_id IS NOT NULL"],
            "method": [drop, f"CREATE INDEX {ix} {on} USING hash (occurred_at)"],
            "expression": [drop, f"CREATE INDEX {ix} {on} ((hist_thread_id + 0))"],
            "include": [drop, f"CREATE INDEX {ix} {on} (occurred_at) INCLUDE (reason)"],
            "extra": [f"CREATE INDEX ix_extra {on} (reason)"],
            "missing": [drop],
        })

    def test_ssa_26_unexpected_foreign_keys_either_direction(self):
        self.cases({
            "outgoing": [_ALTER + "ADD CONSTRAINT fk_ssa FOREIGN KEY (hist_thread_id) "
                         "REFERENCES whatsapp_threads (id)"],
            "incoming": ["CREATE TABLE ssa_ref (e BIGINT REFERENCES booking_attempt_events "
                         "(id))"],
        })

    def test_ssa_27_unexpected_before_after_or_swallowing_triggers(self):
        on = "ON booking_attempt_events FOR EACH ROW EXECUTE FUNCTION ssa_swallow()"
        self.cases({
            "before_insert_return_null": [_SWALLOW_FN, f"CREATE TRIGGER ssa_t BEFORE INSERT "
                                                        f"{on}"],
            "after_insert": [_SWALLOW_FN, f"CREATE TRIGGER ssa_t AFTER INSERT {on}"],
            "statement_level": [_SWALLOW_FN,
                                "CREATE TRIGGER ssa_t AFTER TRUNCATE ON booking_attempt_events"
                                " FOR EACH STATEMENT EXECUTE FUNCTION ssa_swallow()"],
        })

    def test_ssa_28_rules_policies_inheritance_persistence_owner_comment(self):
        self.cases({
            "rule": ["CREATE RULE ssa_r AS ON INSERT TO booking_attempt_events DO INSTEAD "
                     "NOTHING"],
            "rls_policy": [_ALTER + "ENABLE ROW LEVEL SECURITY",
                           "CREATE POLICY ssa_p ON booking_attempt_events USING (false)"],
            "rls_only": [_ALTER + "ENABLE ROW LEVEL SECURITY"],
            "inherited_by": ["CREATE TABLE ssa_child () INHERITS (booking_attempt_events)"],
            "unlogged": [_ALTER + "SET UNLOGGED"],
            "table_comment": ["COMMENT ON TABLE booking_attempt_events IS 'hand-made'"],
            "column_comment": ["COMMENT ON COLUMN booking_attempt_events.reason IS 'x'"],
            "storage_option": [_ALTER + "SET (fillfactor = 50)"],
        })
        _sql(PARENT_URL, "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
                         "'ssa_other_owner') THEN CREATE ROLE ssa_other_owner NOLOGIN; END IF; "
                         "END $$")
        try:
            self.cases({"foreign_owner": [_ALTER + "OWNER TO ssa_other_owner"]})
        finally:
            for name in self._made:
                _drop(name)
            self._made = []
            _sql(PARENT_URL, "DROP ROLE ssa_other_owner")

    def test_ssa_29_wrong_function_or_trigger_of_the_canonical_name_is_never_replaced(self):
        fn = ("CREATE FUNCTION booking_attempt_events_append_only() RETURNS trigger LANGUAGE "
              "plpgsql AS $$ BEGIN RETURN NEW; END $$")
        trig = ("CREATE TRIGGER trg_booking_attempt_events_append_only BEFORE {ev} ON "
                "booking_attempt_events FOR EACH ROW EXECUTE FUNCTION "
                "booking_attempt_events_append_only()")
        canonical_fn = ("CREATE FUNCTION booking_attempt_events_append_only() RETURNS trigger "
                        "LANGUAGE plpgsql AS $$" + _HAND_FUNCTION_BODY + "$$")
        self.cases({
            "function_other_body_orm_table": [fn],
            "function_other_body_canonical_trigger": [fn, trig.format(ev="UPDATE OR DELETE")],
            "canonical_function_without_trigger": [canonical_fn],
            "trigger_delete_only": [canonical_fn, trig.format(ev="DELETE")],
            "trigger_disabled": [canonical_fn, trig.format(ev="UPDATE OR DELETE"),
                                 _ALTER + "DISABLE TRIGGER trg_booking_attempt_events_append_only"],
            "function_security_definer": [canonical_fn, trig.format(ev="UPDATE OR DELETE"),
                                          "ALTER FUNCTION booking_attempt_events_append_only() "
                                          "SECURITY DEFINER"],
        })
        self.cases({"function_with_table_absent": [fn]}, base="parent")
        self.cases({"view_of_the_name": ["CREATE VIEW booking_attempt_events AS SELECT 1 AS x"]},
                   base="parent")

    def test_ssa_30_non_empty_orm_table_is_refused_and_its_row_kept(self):
        url = self.db("orm", _INSERT.format(uid="pre-existing"))
        self.refused(url, "holds rows")
        self.assertEqual(_scalars(url, "SELECT event_uid FROM booking_attempt_events"),
                         ["pre-existing"])


class SSA_NotificationColumnRefusals(_Strict):

    def test_ssa_40_default_now_and_every_other_unsafe_column_shape(self):
        add = "ALTER TABLE thread_revisions ADD COLUMN notification_sent_at "
        cases = {
            "default_now": [add + "TIMESTAMPTZ DEFAULT now()"],
            "default_constant": [add + "TIMESTAMPTZ DEFAULT '2026-01-01 00:00+00'"],
            "not_null": [add + "TIMESTAMPTZ NOT NULL"],
            "without_time_zone": [add + "TIMESTAMP"],
            "text": [add + "TEXT"],
            "precision": [add + "TIMESTAMPTZ(0)"],
            "generated": [add + "TIMESTAMPTZ GENERATED ALWAYS AS (NULL::timestamptz) STORED"],
            "other_comment": [add + "TIMESTAMPTZ",
                              "COMMENT ON COLUMN thread_revisions.notification_sent_at IS 'x'"],
            "table_trigger": [add + "TIMESTAMPTZ", _SWALLOW_FN,
                              "CREATE TRIGGER ssa_t BEFORE INSERT ON thread_revisions FOR EACH "
                              "ROW EXECUTE FUNCTION ssa_swallow()"],
            "trigger_without_column": [_SWALLOW_FN,
                                       "CREATE TRIGGER ssa_t BEFORE UPDATE ON thread_revisions "
                                       "FOR EACH ROW EXECUTE FUNCTION ssa_swallow()"],
            "table_rule": [add + "TIMESTAMPTZ", "CREATE RULE ssa_r AS ON UPDATE TO "
                                                "thread_revisions DO INSTEAD NOTHING"],
            "row_security": [add + "TIMESTAMPTZ",
                             "ALTER TABLE thread_revisions ENABLE ROW LEVEL SECURITY"],
        }
        for name, setup in cases.items():
            with self.subTest(case=name):
                url = self.db("parent", *setup)
                # 20261015 ran first in the same transaction: its CREATED table must vanish too
                self.refused(url, "SchemaShapeRefused", HEAD)
        # identity is not expressible on a timestamp column: PostgreSQL itself refuses it
        url = self.db("parent")
        with self.assertRaises(Exception) as cm:
            _sql(url, add + "TIMESTAMPTZ GENERATED BY DEFAULT AS IDENTITY")
        self.assertIn("identity column type must be", str(cm.exception))

    def test_ssa_41_a_marked_column_is_verified_and_dropped_only_by_its_owner(self):
        """State 3 (prompt §3.2): exactly the column this revision adds, marker included."""
        url = self.db("head")
        before = _whole(url)
        _alembic(url, "stamp", P4_REV)
        self.upgraded(url, f"{HEAD}: VERIFIED_CANONICAL")
        self.assertEqual(_whole(url), before)


# ══ 8.2 #15 — failures injected halfway (real DDL failures, via event triggers) ═══════════

_FAULT = """
    CREATE FUNCTION ssa_fault() RETURNS event_trigger LANGUAGE plpgsql AS $$
    DECLARE r record;
    BEGIN
      FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
        IF {cond} THEN {action}; END IF;
      END LOOP;
    END $$"""
_FAULT_ON = "CREATE EVENT TRIGGER ssa_fault ON ddl_command_end EXECUTE FUNCTION ssa_fault()"


class SSA_InjectedFailures(_Strict):

    def fault(self, base, cond, action="RAISE EXCEPTION 'ssa injected fault'"):
        return self.db(base, _FAULT.format(cond=cond, action=action), _FAULT_ON)

    def test_ssa_50_fault_after_the_first_index_leaves_nothing(self):
        url = self.fault("parent", "r.object_identity = 'public.ix_booking_attempt_events_thread'")
        self.refused(url, "ssa injected fault", typed=False)
        self.assertEqual(_scalars(url, "SELECT to_regclass('booking_attempt_events')::text"),
                         [None])

    def test_ssa_51_fault_after_the_function_during_adoption_leaves_nothing(self):
        url = self.fault("orm", "r.command_tag = 'CREATE TRIGGER'")
        self.refused(url, "ssa injected fault", typed=False)
        self.assertEqual(_scalars(url, "SELECT count(*) FROM pg_proc WHERE proname = "
                                       "'booking_attempt_events_append_only'"), [0])

    def test_ssa_52_drift_at_post_install_verification_is_refused_atomically(self):
        url = self.fault("orm", "r.command_tag = 'CREATE TRIGGER'",
                         action="ALTER TABLE booking_attempt_events DISABLE TRIGGER "
                                "trg_booking_attempt_events_append_only")
        out = self.refused(url, "not exactly canonical after installation")
        self.assertIn("triggers", out)

    def test_ssa_53_fault_in_the_column_revision_rolls_back_the_event_table_too(self):
        url = self.fault("parent", "r.command_tag = 'COMMENT'")
        self.refused(url, "ssa injected fault", typed=False)

    def test_ssa_54_a_refusal_never_advances_alembic_one_transaction_pinned(self):
        """env.py runs the whole chain in ONE transaction (no `transaction_per_migration`):
        a refusal in 20261016 must undo 20261015's CREATED table and its version step."""
        env = (_BACKEND / "migrations" / "env.py").read_text()
        self.assertNotIn("transaction_per_migration", env)
        url = self.db("parent", "ALTER TABLE thread_revisions ADD COLUMN notification_sent_at "
                                "TIMESTAMPTZ DEFAULT now()")
        out = self.refused(url, HEAD)
        self.assertIn(f"Running upgrade {P4_PARENT} -> {P4_REV}", out)
        self.assertEqual(_scalars(url, "SELECT version_num FROM alembic_version"), [P4_PARENT])

    def test_ssa_55_server_major_other_than_16_is_refused(self):
        from alembic import command
        from alembic.config import Config
        url = self.db("parent")
        before = _whole(url)
        cfg = Config(str(_BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(_BACKEND / "migrations"))
        prior = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        try:
            with mock.patch("app.services.schema_fingerprint.server_major", return_value=17):
                with self.assertRaises(Exception) as cm:
                    command.upgrade(cfg, "head")
        finally:
            if prior is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prior
        self.assertIn("certified for PostgreSQL 16 only", str(cm.exception))
        self.assertEqual(_whole(url), before)


# ══ 8.1 #7 / §6 — the runtime preflight ═════════════════════════════════════════════════════

class SSA_RuntimePreflight(_Strict):

    def test_ssa_60_preflight_passes_only_on_the_exact_head_and_fingerprint(self):
        from app.services.schema_preflight import EXPECTED_HEAD
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(str(_BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(_BACKEND / "migrations"))
        self.assertEqual(ScriptDirectory.from_config(cfg).get_heads(), [EXPECTED_HEAD])
        self.assertEqual(self.preflight(self.db("head")), [])
        add_trigger = [_SWALLOW_FN, "CREATE TRIGGER ssa_t BEFORE INSERT ON "
                                    "booking_attempt_events FOR EACH ROW EXECUTE FUNCTION "
                                    "ssa_swallow()"]
        cases = {
            "head_behind": (["UPDATE alembic_version SET version_num = "
                             f"'{P4_REV}'"], "Alembic version"),
            "head_ahead": (["UPDATE alembic_version SET version_num = 'zz_future'"],
                           "Alembic version"),
            "two_rows": ([f"INSERT INTO alembic_version VALUES ('{P4_REV}')"],
                         "Alembic version"),
            "no_version_table": (["DROP TABLE alembic_version"], "alembic_version absent"),
            "trigger_disabled": (["ALTER TABLE booking_attempt_events DISABLE TRIGGER "
                                  "trg_booking_attempt_events_append_only"], "triggers"),
            "trigger_dropped": (["DROP TRIGGER trg_booking_attempt_events_append_only ON "
                                 "booking_attempt_events"], "triggers"),
            "function_replaced": (["CREATE OR REPLACE FUNCTION "
                                   "booking_attempt_events_append_only() RETURNS trigger "
                                   "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"],
                                  "function"),
            "extra_trigger": (add_trigger, "triggers"),
            "check_widened": (["ALTER TABLE booking_attempt_events DROP CONSTRAINT "
                               "ck_booking_attempt_events_stage",
                               "ALTER TABLE booking_attempt_events ADD CONSTRAINT "
                               f"ck_booking_attempt_events_stage CHECK (stage IN ({_STAGES}, "
                               "'WHENEVER'))"], "constraints"),
            "event_default_dropped": (["ALTER TABLE booking_attempt_events ALTER COLUMN "
                                       "occurred_at DROP DEFAULT"], "columns"),
            "rule_added": (["CREATE RULE ssa_r AS ON INSERT TO booking_attempt_events DO "
                            "INSTEAD NOTHING"], "rules"),
            "column_default_added": (["ALTER TABLE thread_revisions ALTER COLUMN "
                                      "notification_sent_at SET DEFAULT now()"],
                                     "notification_sent_at is not the canonical shape"),
            "column_not_null": (["ALTER TABLE thread_revisions ALTER COLUMN "
                                 "notification_sent_at SET NOT NULL"],
                                "notification_sent_at is not the canonical shape"),
            "column_dropped": (["ALTER TABLE thread_revisions DROP COLUMN "
                                "notification_sent_at"], "notification_sent_at"),
            "thread_revisions_trigger": ([_SWALLOW_FN, "CREATE TRIGGER ssa_t BEFORE INSERT ON "
                                          "thread_revisions FOR EACH ROW EXECUTE FUNCTION "
                                          "ssa_swallow()"], "table_triggers"),
        }
        from app.services.schema_preflight import (DeclaredSchemaMissing,
                                                   require_declared_booking_schema)
        for name, (setup, expect) in cases.items():
            with self.subTest(case=name):
                url = self.db("head", *setup)
                before = _whole(url)
                problems = self.preflight(url)
                self.assertTrue(problems, "the preflight accepted a drifted schema")
                self.assertIn(expect, "; ".join(problems))
                eng = create_engine(url)
                try:
                    with self.assertRaises(DeclaredSchemaMissing):
                        require_declared_booking_schema(eng)
                finally:
                    eng.dispose()
                self.assertEqual(_whole(url), before, "the preflight wrote")

    def test_ssa_61_real_uvicorn_never_opens_its_port_on_a_drifted_schema(self):
        bad = self.db("head", "ALTER TABLE thread_revisions ALTER COLUMN notification_sent_at "
                              "SET DEFAULT now()")
        good = self.db("head")
        out, code, opened = self._serve(bad)
        self.assertNotEqual(code, 0, out[-1500:])
        self.assertFalse(opened, "the port opened on a drifted schema")
        self.assertIn("SCHEMA_PREFLIGHT_FAILED", out)
        self.assertNotIn(make_url(PARENT_URL).password or "\0", out)
        out, code, opened = self._serve(good)
        self.assertTrue(opened, out[-1500:])
        self.assertIn("Application startup complete", out)

    def _serve(self, url):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {**os.environ, "DATABASE_URL": url, "OUTBOUND_ENABLED": "false",
               "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host",
                                 "127.0.0.1", "--port", str(port)], cwd=str(_BACKEND), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        reader = threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True)
        reader.start()
        opened, deadline = False, time.monotonic() + 90
        while time.monotonic() < deadline and proc.poll() is None:
            with socket.socket() as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    opened = True
                    break
            time.sleep(0.2)
        if proc.poll() is None:
            proc.terminate()
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
        reader.join(timeout=10)
        return "".join(lines), code, opened


# ══ 8.1 #8 / #9 — runtime on migrated databases ═════════════════════════════════════════════

class SSA_RuntimeAfterMigration(_Strict):

    def test_ssa_70_real_admission_replay_and_append_only_after_each_branch(self):
        from app.services.booking_attempt_events import insert_attempt_event
        parent_first = self.db("parent")
        self.upgraded(parent_first, f"{P4_REV}: CREATED")
        app_first = self.db("orm")
        self.upgraded(app_first, f"{P4_REV}: ADOPTED_ORM_SHAPE")
        for name, url in (("parent_first", parent_first), ("app_first", app_first)):
            with self.subTest(order=name):
                eng = create_engine(url)
                try:
                    got = []
                    for reason in ("SLOT", "OTHER"):
                        with Session(eng) as db:
                            got.append(insert_attempt_event(
                                db, source="CE", stage="FINALISE", outcome="REFUSED",
                                reason=reason, attempt_ref="ssa-attempt", thread_id=1,
                                once_per_attempt=True))
                            db.commit()
                    self.assertEqual(got, [True, False])
                    with eng.connect() as c:
                        rows = c.execute(text("SELECT reason, occurred_at IS NOT NULL FROM "
                                              "booking_attempt_events")).all()
                        self.assertEqual([tuple(r) for r in rows], [("SLOT", True)])
                    for stmt in ("UPDATE booking_attempt_events SET reason = 'X'",
                                 "DELETE FROM booking_attempt_events"):
                        with eng.connect() as c, self.assertRaises(Exception) as cm:
                            c.execute(text(stmt))
                        self.assertIn("append-only", str(cm.exception))
                finally:
                    eng.dispose()


try:                                                    # the runtime classes below need both
    from test_convergence_p3_race_pg import PG_URL as _P3_URL, _Race  # noqa: E402
    import _sb2b_harness as H  # noqa: E402
except Exception:                                       # pragma: no cover
    _P3_URL, _Race, H = "", unittest.TestCase, None


def _priced(db, revision, **_):
    revision.precio_base, revision.viaticos, revision.precio_total = 100000, 40000, 140000


@unittest.skipUnless(_P3_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class SSA_NotificationTruth(_Race):
    """R1RV-02: the SENT leg read from the operator surface itself (non-vacuous), a priced
    world, and a new booking that stays PENDING until it is explicitly marked."""

    def book(self, sender):
        import dataclasses
        w = H.seed(self.engine)
        cfg = dataclasses.replace(H.settings(), resend_api_key="probe-not-a-credential",
                                  internal_booking_email_from="from@example.invalid",
                                  internal_booking_email_to="to@example.invalid")
        mails = []
        with H.Meta(), mock.patch("app.services.resend_email.send_booking_notification",
                                  lambda **k: mails.append(k) or sender()), \
                mock.patch("app.services.pricing.PricingService."
                           "recalculate_revision_if_possible",
                           lambda self_, db, revision, **k: _priced(db, revision)):
            out, _ = H.run_turn(self.engine, H.flow_event(w), cfg=cfg)
        self.assertEqual(out.action, "booking_created", out)
        return w, mails

    def truth(self, w):
        from app.routes.ops_dashboard import _notification_truth, booking_attention
        now = datetime.now(timezone.utc) + timedelta(seconds=1)
        with Session(self.engine) as db:
            ledger = db.execute(text("SELECT id FROM booking_commands WHERE thread_id = :t AND "
                                     "command_state = 'COMMITTED'"), {"t": w.thread_id}
                                ).scalar_one()
            states = _notification_truth(db, now=now, settled=now, rows={})
            listed = [r for r in booking_attention(db, pending_older_than_seconds=0)
                      if r.get("thread_id") == w.thread_id]
        return states[ledger]["notification_state"], listed

    def sent_at(self, w):
        with self.engine.connect() as c:
            return c.execute(text("SELECT notification_sent_at FROM thread_revisions WHERE "
                                  "thread_id = :t"), {"t": w.thread_id}).scalar_one()

    def test_ssa_80_a_delivered_priced_email_reads_sent_on_the_operator_surface(self):
        w, mails = self.book(lambda: True)
        self.assertEqual(len(mails), 1)
        self.assertEqual((mails[0]["precio_base"], mails[0]["viaticos"],
                          mails[0]["precio_total"]), ("100000", "40000", "140000"))
        self.assertIsNotNone(self.sent_at(w))
        state, listed = self.truth(w)
        self.assertEqual(state, "SENT")
        self.assertEqual(listed, [], "a SENT booking was listed for attention")

    def test_ssa_81_a_new_booking_stays_pending_until_explicitly_marked(self):
        w, mails = self.book(lambda: False)           # the e-mail was not delivered
        self.assertEqual(len(mails), 1)
        self.assertIsNone(self.sent_at(w), "a booking read as notified without a mark")
        state, listed = self.truth(w)
        self.assertNotEqual(state, "SENT")
        self.assertEqual(len(listed), 1)
        self.assertTrue({"OPERATOR_NOTIFICATION_PENDING", "OPERATOR_NOTIFICATION_FAILED"}
                        & set(listed[0]["reasons"]), listed[0]["reasons"])
        with self.engine.begin() as c:                 # a raw insert writes no default either
            c.execute(text("INSERT INTO thread_revisions (thread_id, status) VALUES "
                           "(:t, 'booked')"), {"t": w.thread_id})
            self.assertEqual(c.execute(text(
                "SELECT count(*) FROM thread_revisions WHERE thread_id = :t AND "
                "notification_sent_at IS NOT NULL"), {"t": w.thread_id}).scalar(), 0)


@unittest.skipUnless(_P3_URL, "CONV_P3_PG_URL not set — no disposable PostgreSQL; nothing proven")
class SSA_ReplayKeepsTheOuterTransaction(_Race):
    """R1RV-02 / m11: an in-caller SAVEPOINT replay rolls back ONLY its savepoint — the
    caller's own work before and after it, flushed or not, and the first finalisation all
    commit with the caller."""

    def test_ssa_90_outer_work_and_the_first_finalisation_survive_a_replay(self):
        from app.models import Lead
        from app.services.booking_refusal import finalise_offer_refusal
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            lead = db.get(Lead, w.lead_id)
            db.execute(text("UPDATE leads SET apellido = 'outer-raw-1' WHERE id = :l"),
                       {"l": w.lead_id})
            lead.nombre = "outer-orm-1"                       # pending, not flushed
            call = dict(source="CE", thread_id=w.thread_id, token=w.token,
                        observed_reason="SLOT_CONFLICT", attempt_ref="ssa-outer",
                        in_caller_transaction=True)
            first = finalise_offer_refusal(db, **call)
            lead.email = "outer-orm-2@example.invalid"        # pending, not flushed
            again = finalise_offer_refusal(db, **call)
            db.execute(text("UPDATE leads SET telefono = 'outer-raw-2' WHERE id = :l"),
                       {"l": w.lead_id})
            db.commit()
        self.assertTrue(first.finalised, first)
        self.assertTrue(again.replay, again)
        with self.engine.connect() as c:
            row = c.execute(text("SELECT apellido, nombre, email, telefono FROM leads "
                                 "WHERE id = :l"), {"l": w.lead_id}).one()
            self.assertEqual(tuple(row), ("outer-raw-1", "outer-orm-1",
                                          "outer-orm-2@example.invalid", "outer-raw-2"))
            self.assertEqual(c.execute(text(
                "SELECT status, last_refusal, refused_count FROM whatsapp_booking_offers "
                "WHERE thread_id = :t"), {"t": w.thread_id}).one()[1:],
                ("SLOT_NO_LONGER_AVAILABLE", 1))
            self.assertEqual(c.execute(text("SELECT count(*) FROM booking_attempt_events "
                                            "WHERE hist_thread_id = :t"),
                                       {"t": w.thread_id}).scalar(), 1)


def tearDownModule():
    if PARENT_URL and _Strict.templates:
        for name in ("ssa_t_orm", "ssa_t_head"):
            _drop(name)
        _Strict.templates = {}


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
