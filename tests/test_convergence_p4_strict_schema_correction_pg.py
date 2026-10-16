"""Convergence Phase 4 STRICT-SCHEMA-ADOPTION-CORRECTION — proven on real PostgreSQL 16.

SSRV-01: a `pg_dump` → restore (plain SQL through `psql`, custom format through `pg_restore`) of
a correctly migrated database re-deparses the two event-table CHECKs into ONE other form, a
fixpoint. That restored database must start, verify on re-upgrade and downgrade evidence-free,
while every semantically different CHECK — in either textual style — still refuses.
SSRV-02: a constraint or index referencing `thread_revisions.notification_sent_at`, and
inheritance or partitioning of `thread_revisions`, must refuse at upgrade and at start.
SSRV-03: held locks, a patched server major and a rule on `thread_revisions` have detectors.

  CONV_SSA_PARENT_URL   as in `test_convergence_p4_strict_schema_pg.py` (the parent template;
                        every case clones it, so the role needs CREATEDB).
  CONV_SSC_PGBIN        a directory with PostgreSQL 16 `bin/pg_dump`, `bin/pg_restore`,
                        `bin/psql` (and `lib/` for them). Credentials reach them through the
                        environment only, never their command line.

Oracles independent of `app.services.schema_fingerprint`: the full `pg_dump --schema-only`
text, this file's own `pg_get_constraintdef` read, hand-written deparse literals, the values
each CHECK actually admits, and the strict suite's whole-database fingerprint.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

import test_convergence_p4_strict_schema_pg as S  # noqa: E402

PGBIN = os.environ.get("CONV_SSC_PGBIN", "")
HEAD, P4_REV, P4_PARENT = S.HEAD, S.P4_REV, S.P4_PARENT
_whole, _sql, _scalars, _alembic = S._whole, S._sql, S._scalars, S._alembic

_STAGES = ("PRE_EXECUTE", "AUTHORITY", "FINALISE", "NOTIFICATION")
_OUTCOMES = ("REFUSED", "RETRYABLE", "CONTENDED", "INTERNAL", "FAILED", "HELD")
_CK_STAGE, _CK_OUTCOME = "ck_booking_attempt_events_stage", "ck_booking_attempt_events_outcome"

# ── hand-written oracles: the two deparses PostgreSQL 16.13 gives each CHECK ────────────────
_CREATED = {
    _CK_OUTCOME: "CHECK (((outcome)::text = ANY ((ARRAY['REFUSED'::character varying, "
                 "'RETRYABLE'::character varying, 'CONTENDED'::character varying, "
                 "'INTERNAL'::character varying, 'FAILED'::character varying, "
                 "'HELD'::character varying])::text[])))",
    _CK_STAGE: "CHECK (((stage)::text = ANY ((ARRAY['PRE_EXECUTE'::character varying, "
               "'AUTHORITY'::character varying, 'FINALISE'::character varying, "
               "'NOTIFICATION'::character varying])::text[])))",
}
_RESTORED = {
    _CK_OUTCOME: "CHECK (((outcome)::text = ANY (ARRAY[('REFUSED'::character varying)::text, "
                 "('RETRYABLE'::character varying)::text, ('CONTENDED'::character varying)"
                 "::text, ('INTERNAL'::character varying)::text, ('FAILED'::character varying)"
                 "::text, ('HELD'::character varying)::text])))",
    _CK_STAGE: "CHECK (((stage)::text = ANY (ARRAY[('PRE_EXECUTE'::character varying)::text, "
               "('AUTHORITY'::character varying)::text, ('FINALISE'::character varying)::text, "
               "('NOTIFICATION'::character varying)::text])))",
}
_CHECK_DEFS = ("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
               "'booking_attempt_events'::regclass AND contype = 'c' ORDER BY 1")


def _restored_style(column, values):
    """A CHECK written in the restored textual style (for hostile variants of it)."""
    arr = ", ".join(f"('{v}'::character varying)::text" for v in values)
    return f"(({column})::text = ANY (ARRAY[{arr}]))"


def _tool_env(url):
    u = make_url(url)
    return {**os.environ, "PGHOST": u.host, "PGPORT": str(u.port or 5432),
            "PGUSER": u.username, "PGPASSWORD": u.password or "", "PGDATABASE": u.database,
            "LD_LIBRARY_PATH": os.path.join(PGBIN, "lib")}


def _tool(name, url, *args, data=None):
    r = subprocess.run([os.path.join(PGBIN, "bin", name), *args], env=_tool_env(url),
                       input=data, capture_output=True, timeout=600)
    assert r.returncode == 0, (name, r.stderr.decode()[-1500:])
    return r.stdout


def _schema_dump(url) -> list[str]:
    """`pg_dump --schema-only` text, minus the per-dump random `\\restrict` key lines."""
    out = _tool("pg_dump", url, "--schema-only").decode()
    return [ln for ln in out.splitlines() if not ln.startswith(("\\restrict", "\\unrestrict"))]


def _empty_db(name) -> str:
    eng = S._admin()
    try:
        with eng.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        eng.dispose()
    return make_url(S.PARENT_URL).set(database=name).render_as_string(hide_password=False)


def _restore(src_url, name, fmt) -> str:
    """A real dump of `src_url` restored into a NEW database `name`: plain SQL through psql or
    custom format through pg_restore, stopping on the first error."""
    dst = _empty_db(name)
    if fmt == "plain":
        dump = _tool("pg_dump", src_url)
        _tool("psql", dst, "-X", "-q", "-v", "ON_ERROR_STOP=1", data=dump)
    else:
        dump = _tool("pg_dump", src_url, "-Fc")
        _tool("pg_restore", dst, "--exit-on-error", "-d", make_url(dst).database, data=dump)
    return dst


def _admits(url) -> dict:
    """Which values each CHECK really admits (insert, then roll back) — the semantic oracle."""
    probes = {"stage": _STAGES + ("WHENEVER", "", "pre_execute", "AUTHORITY ", "FINALISED"),
              "outcome": _OUTCOMES + ("OK", "", "refused", "HELD ", "FAILURE")}
    eng = create_engine(url)
    out = {}
    try:
        for column, values in probes.items():
            for v in values:
                with eng.connect() as c:
                    row = {"stage": "AUTHORITY", "outcome": "REFUSED", column: v}
                    try:
                        c.execute(text("INSERT INTO booking_attempt_events (event_uid, source, "
                                       "stage, outcome, reason) VALUES ('probe', 'X', :stage, "
                                       ":outcome, 'R')"), row)
                        out[(column, v)] = True
                    except Exception as exc:          # noqa: BLE001 — the CHECK's verdict
                        assert f"ck_booking_attempt_events_{column}" in str(exc), exc
                        out[(column, v)] = False
                    c.rollback()
    finally:
        eng.dispose()
    return out


def _alembic_bounded(url, seconds, *args):
    """Alembic in a subprocess that may not wait longer than `seconds` (None = it did)."""
    try:
        return subprocess.run([sys.executable, "-m", "alembic", *args],
                              env={**os.environ, "DATABASE_URL": url}, cwd=str(S._BACKEND),
                              capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return None


@unittest.skipUnless(S.PARENT_URL and PGBIN, "CONV_SSA_PARENT_URL / CONV_SSC_PGBIN not set — "
                                             "no disposable PostgreSQL or tools; nothing proven")
class _Base(S._Strict):
    """Extra templates: `legacy` (parent + the old start-up column), `rhead` (head, dumped and
    restored as plain SQL) and `rorm` (the app-first create_all shape, restored from custom
    format)."""

    extra = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _Base.extra:
            legacy = S._clone(S.PARENT_URL, "ssc_t_legacy")
            _sql(legacy, S._LEGACY_DDL)
            _Base.extra = {
                "legacy": legacy,
                "rhead": _restore(S._Strict.templates["head"], "ssc_t_rhead", "plain"),
                "rorm": _restore(S._Strict.templates["orm"], "ssc_t_rorm", "custom"),
            }
        cls.templates = {**S._Strict.templates, **_Base.extra}

    def restore(self, src_url, fmt) -> str:
        name = f"ssc_r_{next(S._SEQ)}"
        self._made.append(name)
        return _restore(src_url, name, fmt)

    def down_refused(self, url, target, *expect):
        before = _whole(url)
        r = _alembic(url, "downgrade", target)
        out = r.stderr + r.stdout
        self.assertNotEqual(r.returncode, 0, "a downgrade erased what it must keep:\n"
                            + out[-1500:])
        for e in expect:
            self.assertIn(e, out)
        self.assertEqual(_whole(url), before, "a refused downgrade changed something")
        return out


def _with_checks(shape, forms):
    """`shape` with the two CHECK deparses replaced by this file's hand-written literals."""
    cons = [c[:-1] + (forms[c[0]],) if c[0] in forms else c for c in shape["constraints"]]
    return {**shape, "constraints": cons}


# ══ SSRV-01 — dump / restore ═══════════════════════════════════════════════════════════════

class SSC_Restore(_Base):

    def test_ssc_01_restored_form_is_one_measured_fixpoint_of_the_same_constraint(self):
        head = self.db("head")
        plain = self.restore(head, "plain")
        custom = self.restore(head, "custom")
        plain2 = self.restore(plain, "plain")              # a second round, each format
        custom2 = self.restore(custom, "custom")
        crossed = self.restore(custom, "plain")
        self.assertEqual(dict(map(tuple, _scalars_rows(head, _CHECK_DEFS))), _CREATED)
        for url in (plain, custom, plain2, custom2, crossed):
            self.assertEqual(dict(map(tuple, _scalars_rows(url, _CHECK_DEFS))), _RESTORED)
        # fixpoint by the independent oracle: every restored schema dumps byte-identically
        dumps = {u: _schema_dump(u) for u in (plain, custom, plain2, custom2, crossed)}
        self.assertEqual(len({tuple(d) for d in dumps.values()}), 1, "not a fixpoint")
        # and the restore changed NOTHING but CHECK deparses (here and on other tables)
        a, b = _schema_dump(head), dumps[plain]
        self.assertEqual(len(a), len(b))
        changed = [(x, y) for x, y in zip(a, b) if x != y]
        self.assertTrue(changed)
        for x, y in changed:
            self.assertIn(" CHECK (", x)
            self.assertIn(" CHECK (", y)
            self.assertEqual(x.split(" CHECK (")[0], y.split(" CHECK (")[0])
        self.assertEqual({k: v for k, v in _whole(head).items() if k != "constraints"},
                         {k: v for k, v in _whole(plain).items() if k != "constraints"})
        # the same constraint: it admits exactly the same values, before and after
        admitted = _admits(head)
        self.assertEqual(_admits(plain), admitted)
        self.assertEqual({k for k, ok in admitted.items() if ok},
                         {("stage", v) for v in _STAGES} | {("outcome", v) for v in _OUTCOMES})

    def test_ssc_02_production_reader_sees_exactly_the_hand_written_restored_shape(self):
        m15 = S._migration(P4_REV)
        table, fn, _ = self.read(self.templates["rhead"])
        self.assertNotEqual(table, m15.F_CANONICAL)
        self.assertEqual(table, _with_checks(m15.F_CANONICAL, _RESTORED))
        self.assertIn(table, m15.F_CANONICAL_PERMITTED)
        self.assertEqual(fn, m15.F_FUNCTION)
        self.assertEqual(self.read(self.templates["rorm"])[0],
                         _with_checks(m15.F_ORM, _RESTORED))

    def test_ssc_03_preflight_is_clean_after_each_restore_and_reads_only(self):
        head = self.db("head")
        for fmt in ("plain", "custom"):
            with self.subTest(fmt=fmt):
                url = self.restore(self.restore(head, fmt), fmt)
                before = _whole(url)
                self.assertEqual(self.preflight(url), [])
                self.assertEqual(_whole(url), before)

    def test_ssc_04_the_application_starts_on_a_restored_database(self):
        url = self.restore(self.db("head"), "custom")
        out, code, opened = S.SSA_RuntimePreflight._serve(self, url)
        self.assertTrue(opened, out[-1500:])
        self.assertIn("Application startup complete", out)
        self.assertNotIn("SCHEMA_PREFLIGHT_FAILED", out)
        self.assertNotIn(make_url(S.PARENT_URL).password or "\0", out)

    def test_ssc_05_restored_stamped_back_reupgrades_as_verified_without_ddl(self):
        for fmt in ("plain", "custom"):
            with self.subTest(fmt=fmt):
                url = self.restore(self.db("head"), fmt)
                schema, before = _schema_dump(url), _whole(url)
                r = _alembic(url, "stamp", P4_PARENT)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                self.upgraded(url, f"{P4_REV}: VERIFIED_CANONICAL",
                              f"{HEAD}: VERIFIED_CANONICAL")
                self.assertEqual(_schema_dump(url), schema, "verification changed the schema")
                self.assertEqual(_whole(url), before)
                self.assertEqual(dict(map(tuple, _scalars_rows(url, _CHECK_DEFS))), _RESTORED)

    def test_ssc_06_restored_evidence_free_downgrade_returns_the_restored_parent(self):
        url = self.restore(self.db("head"), "plain")
        parent = self.restore(self.db("parent"), "plain")      # the parent, restored alike
        r = _alembic(url, "downgrade", P4_PARENT)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(_schema_dump(url), _schema_dump(parent))
        self.assertEqual(_whole(url), _whole(parent))
        self.upgraded(url, f"{P4_REV}: CREATED", f"{HEAD}: ADDED")   # and back, cleanly
        self.assertEqual(self.preflight(url), [])

    def test_ssc_07_restored_evidence_bearing_downgrades_still_refuse(self):
        url = self.restore(self.db("head"), "custom")
        _sql(url, S._INSERT.format(uid="restored-evidence"))
        self.down_refused(url, P4_PARENT, "audit-data loss")
        self.assertEqual(_scalars(url, "SELECT event_uid FROM booking_attempt_events"),
                         ["restored-evidence"])
        legacy = self.db("legacy", "INSERT INTO whatsapp_contacts (wa_id) VALUES "
                                   "('549110007777')",
                         "INSERT INTO whatsapp_threads (contact_id) SELECT id FROM "
                         "whatsapp_contacts",
                         "INSERT INTO thread_revisions (thread_id, status, notification_sent_at)"
                         " SELECT id, 'booked', '2026-10-01 12:00:00.123456+00' FROM "
                         "whatsapp_threads")
        self.upgraded(legacy, f"{HEAD}: ADOPTED_LEGACY_COLUMN")
        restored = self.restore(legacy, "plain")
        self.down_refused(restored, P4_PARENT, "notification-evidence loss")
        self.assertEqual(_scalars(restored, "SELECT notification_sent_at::text FROM "
                                            "thread_revisions"),
                         ["2026-10-01 12:00:00.123456+00"])

    def test_ssc_08_restored_app_first_table_is_adopted_and_keeps_its_form(self):
        m15 = S._migration(P4_REV)
        url = self.db("rorm")
        self.upgraded(url, f"{P4_REV}: ADOPTED_ORM_SHAPE", f"{HEAD}: ADDED")
        self.assertEqual(self.read(url)[0], _with_checks(m15.F_CANONICAL, _RESTORED))
        self.assertEqual(self.preflight(url), [])
        self.assert_enforced(url)
        stamped = self.db("rorm")                       # create_all + stamp, restored: owned
        _alembic(stamped, "stamp", "head")
        r = _alembic(stamped, "downgrade", P4_PARENT)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(_scalars(stamped, "SELECT to_regclass('booking_attempt_events')"
                                           "::text"), [None])

    def test_ssc_09_each_check_independently_in_either_measured_form(self):
        """The acceptance is per constraint identity: one CHECK restored, one as created."""
        url = self.db("orm", f"ALTER TABLE booking_attempt_events DROP CONSTRAINT {_CK_STAGE}",
                      f"ALTER TABLE booking_attempt_events ADD CONSTRAINT {_CK_STAGE} CHECK "
                      f"{_restored_style('stage', _STAGES)}")
        self.assertEqual(dict(map(tuple, _scalars_rows(url, _CHECK_DEFS))),
                         {_CK_OUTCOME: _CREATED[_CK_OUTCOME], _CK_STAGE: _RESTORED[_CK_STAGE]})
        self.upgraded(url, f"{P4_REV}: ADOPTED_ORM_SHAPE")
        self.assertEqual(self.preflight(url), [])

    def test_ssc_10_migration_created_objects_must_read_in_the_creation_form(self):
        """Post-install verification stays strict: a CREATED table that reads restored-style
        right after installation is refused (fault: rewrite the CHECK at the last index)."""
        url = self.db("parent", S._FAULT.format(
            cond="r.object_identity = 'public.ix_booking_attempt_events_thread_revision'",
            action=f"ALTER TABLE booking_attempt_events DROP CONSTRAINT {_CK_STAGE}; "
                   f"ALTER TABLE booking_attempt_events ADD CONSTRAINT {_CK_STAGE} CHECK "
                   f"{_restored_style('stage', _STAGES)}"), S._FAULT_ON)
        out = self.refused(url, "not exactly canonical after installation")
        self.assertIn("constraints", out)


def _scalars_rows(url, sql):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return [tuple(r) for r in c.execute(text(sql)).all()]
    finally:
        eng.dispose()


# ══ SSRV-01 — semantically different CHECKs refuse, in either textual style ══════════════════

def _hostile_checks():
    alter = "ALTER TABLE booking_attempt_events "
    drop = alter + "DROP CONSTRAINT {}"
    add = alter + "ADD CONSTRAINT {} CHECK {}"
    st = _restored_style
    return {
        "widened": [drop.format(_CK_STAGE),
                    add.format(_CK_STAGE, st("stage", _STAGES + ("WHENEVER",)))],
        "narrowed": [drop.format(_CK_STAGE), add.format(_CK_STAGE, st("stage", _STAGES[:3]))],
        "reordered": [drop.format(_CK_STAGE),
                      add.format(_CK_STAGE, st("stage", tuple(reversed(_STAGES))))],
        "added_empty_value": [drop.format(_CK_OUTCOME),
                              add.format(_CK_OUTCOME, st("outcome", _OUTCOMES + ("",)))],
        "disjunctive": [drop.format(_CK_STAGE),
                        add.format(_CK_STAGE, f"({st('stage', _STAGES)} OR reason = 'X')")],
        "not_valid": [drop.format(_CK_STAGE),
                      add.format(_CK_STAGE, st("stage", _STAGES)) + " NOT VALID"],
        "no_inherit": [drop.format(_CK_STAGE),
                       add.format(_CK_STAGE, st("stage", _STAGES)) + " NO INHERIT"],
        "renamed": [alter + f"RENAME CONSTRAINT {_CK_STAGE} TO ck_stage_restored"],
        "stage_form_under_outcome_name": [
            drop.format(_CK_OUTCOME), add.format(_CK_OUTCOME, st("stage", _STAGES))],
        "text_array_equivalent": [
            drop.format(_CK_STAGE),
            add.format(_CK_STAGE, "(stage::text = ANY (ARRAY['PRE_EXECUTE', 'AUTHORITY', "
                                  "'FINALISE', 'NOTIFICATION']::text[]))")],
        "extra_restored_style_check": [
            alter + "ADD CONSTRAINT ck_extra CHECK " + st("source", ("X",))],
    }


class SSC_HostileChecks(_Base):

    def test_ssc_20_hostile_checks_refuse_adoption_on_created_and_restored_bases(self):
        for base in ("orm", "rorm"):
            for name, setup in _hostile_checks().items():
                with self.subTest(base=base, case=name):
                    self.refused(self.db(base, *setup), "SchemaShapeRefused",
                                 "not exactly a shape this revision may adopt")

    def test_ssc_21_hostile_checks_refuse_canonical_verification_preflight_and_downgrade(self):
        from app.services.schema_preflight import (DeclaredSchemaMissing,
                                                   require_declared_booking_schema)
        for base in ("head", "rhead"):
            for name, setup in _hostile_checks().items():
                with self.subTest(base=base, case=name):
                    url = self.db(base, *setup)
                    before = _whole(url)
                    problems = self.preflight(url)
                    self.assertTrue(problems, "the preflight accepted a hostile CHECK")
                    self.assertIn("constraints", "; ".join(problems))
                    eng = create_engine(url)
                    try:
                        with self.assertRaises(DeclaredSchemaMissing):
                            require_declared_booking_schema(eng)
                    finally:
                        eng.dispose()
                    self.assertEqual(_whole(url), before, "the preflight wrote")
                    self.down_refused(url, P4_PARENT, "not exactly a shape this revision owns")
                    _alembic(url, "stamp", P4_PARENT)
                    self.refused(url, "SchemaShapeRefused")


# ══ SSRV-02 — what references notification_sent_at, and how thread_revisions is built ═══════

_TR = "ALTER TABLE thread_revisions "
_DEPENDENTS = {
    "check_is_null": [_TR + "ADD CONSTRAINT ssc_ck CHECK (notification_sent_at IS NULL)"],
    "check_is_null_not_valid": [_TR + "ADD CONSTRAINT ssc_ck CHECK (notification_sent_at IS "
                                      "NULL) NOT VALID"],
    "check_in_an_expression": [_TR + "ADD CONSTRAINT ssc_ck CHECK (status <> 'booked' OR "
                                     "notification_sent_at IS NULL) NOT VALID"],
    "index_on_the_column": ["CREATE INDEX ssc_ix ON thread_revisions (notification_sent_at)"],
    "expression_index": ["CREATE INDEX ssc_ix ON thread_revisions "
                         "((notification_sent_at IS NULL))"],
    "partial_index": ["CREATE INDEX ssc_ix ON thread_revisions (id) WHERE "
                      "notification_sent_at IS NULL"],
    "include_index": ["CREATE INDEX ssc_ix ON thread_revisions (id) INCLUDE "
                      "(notification_sent_at)"],
    "unique_on_the_column": [_TR + "ADD CONSTRAINT ssc_uq UNIQUE (notification_sent_at)"],
    "incoming_foreign_key": [_TR + "ADD CONSTRAINT ssc_uq UNIQUE (notification_sent_at)",
                             "CREATE TABLE ssc_ref (t TIMESTAMPTZ REFERENCES thread_revisions "
                             "(notification_sent_at))"],
}
_STRUCTURE = {
    "inheritance_child": ["CREATE TABLE ssc_child () INHERITS (thread_revisions)"],
    "inheritance_parent": ["CREATE TABLE ssc_parent ()", _TR + "INHERIT ssc_parent"],
    "partition_of_a_parent": [
        "CREATE TABLE ssc_p (LIKE thread_revisions) PARTITION BY RANGE (id)",
        "ALTER TABLE ssc_p ATTACH PARTITION thread_revisions FOR VALUES FROM (MINVALUE) TO "
        "(MAXVALUE)"],
    "partitioned_table": [_TR + "RENAME TO ssc_thread_revisions_old",
                          "CREATE TABLE thread_revisions (LIKE ssc_thread_revisions_old) "
                          "PARTITION BY RANGE (id)"],
}


class SSC_NotificationDependents(_Base):

    def test_ssc_30_a_check_on_the_column_blocks_the_explicit_mark(self):
        """The premise of SSRV-02, measured: M18's mark is an UPDATE of this column."""
        url = self.db("head", *_DEPENDENTS["check_is_null_not_valid"])
        _sql(url, "INSERT INTO whatsapp_contacts (wa_id) VALUES ('549110007777')",
             "INSERT INTO whatsapp_threads (contact_id) SELECT id FROM whatsapp_contacts",
             "INSERT INTO thread_revisions (thread_id, status) SELECT id, 'booked' FROM "
             "whatsapp_threads")
        with self.assertRaises(Exception) as cm:
            _sql(url, "UPDATE thread_revisions SET notification_sent_at = now()")
        self.assertIn("ssc_ck", str(cm.exception))

    def test_ssc_31_upgrade_refuses_every_dependent_and_structure_before_head_advance(self):
        cases = [("legacy", n, s) for n, s in {**_DEPENDENTS, **_STRUCTURE}.items()]
        cases += [("parent", n, s) for n, s in _STRUCTURE.items()]
        for base, name, setup in cases:
            with self.subTest(base=base, case=name):
                url = self.db(base, *setup)
                out = self.refused(url, "SchemaShapeRefused", HEAD)
                self.assertIn(f"Running upgrade {P4_PARENT} -> {P4_REV}", out)
                self.assertEqual(_scalars(url, "SELECT to_regclass('booking_attempt_events')"
                                               "::text"), [None], "20261015 survived")

    def test_ssc_32_preflight_refuses_every_dependent_structure_rule_and_trigger(self):
        cases = {**_DEPENDENTS, **_STRUCTURE,
                 "rule_on_thread_revisions": ["CREATE RULE ssc_r AS ON UPDATE TO "
                                              "thread_revisions DO INSTEAD NOTHING"],
                 "trigger_on_thread_revisions": [S._SWALLOW_FN, "CREATE TRIGGER ssc_t BEFORE "
                                                 "UPDATE ON thread_revisions FOR EACH ROW "
                                                 "EXECUTE FUNCTION ssa_swallow()"]}
        expect = {"rule_on_thread_revisions": "table_rules",
                  "trigger_on_thread_revisions": "table_triggers",
                  "index_on_the_column": "column_indexes", "expression_index": "column_indexes",
                  "partial_index": "column_indexes", "include_index": "column_indexes",
                  **{n: "column_constraints" for n in _DEPENDENTS if "index" not in n},
                  **{n: "table_inheritance" for n in _STRUCTURE}}
        for name, setup in cases.items():
            with self.subTest(case=name):
                url = self.db("head", *setup)
                before = _whole(url)
                problems = self.preflight(url)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("notification_sent_at is not the canonical shape", problems[0])
                self.assertIn(expect[name], problems[0])
                self.assertEqual(_whole(url), before, "the preflight wrote")

    def test_ssc_33_downgrade_never_drops_a_dependent_it_did_not_create(self):
        for name in ("index_on_the_column", "check_is_null"):
            with self.subTest(case=name):
                url = self.db("parent")
                self.upgraded(url, f"{HEAD}: ADDED")
                _sql(url, *_DEPENDENTS[name])
                self.down_refused(url, P4_REV, "constraint or index this revision did not "
                                               "create")

    def test_ssc_34_unrelated_thread_revisions_constraints_and_indexes_are_not_its_business(self):
        url = self.db("legacy", "CREATE INDEX ssc_unrelated ON thread_revisions (status)",
                      _TR + "ADD CONSTRAINT ssc_unrelated_ck CHECK (id > 0)")
        self.upgraded(url, f"{HEAD}: ADOPTED_LEGACY_COLUMN")
        self.assertEqual(self.preflight(url), [])
        found = self.read(url)[2]
        self.assertEqual((found["column_constraints"], found["column_indexes"]), ([], []))


# ══ SSRV-03 — locks, a patched server major ═══════════════════════════════════════════════════

class SSC_Locks(_Base):

    def held(self, url, *statements):
        """Another session holding whatever `statements` take, inside an open transaction."""
        eng = create_engine(url)
        conn = eng.connect()
        for s in statements:
            conn.execute(text(s))
        return eng, conn

    def bounded_refusal(self, url, *holder, expect_stdout=()):
        before = _whole(url)
        eng, conn = self.held(url, *holder)
        try:
            t0 = time.monotonic()
            r = _alembic_bounded(url, 90, "upgrade", "head")
            waited = time.monotonic() - t0
        finally:
            conn.rollback()
            conn.close()
            eng.dispose()
        self.assertIsNotNone(r, "the upgrade waited on a held lock without bound")
        out = r.stderr + r.stdout
        self.assertNotEqual(r.returncode, 0, "the upgrade ran past a lock it must hold:\n"
                            + out[-1500:])
        self.assertIn("LockNotAvailable", out)
        self.assertGreaterEqual(waited, 9)
        for e in expect_stdout:
            self.assertIn(e, r.stdout)
        self.assertEqual(_whole(url), before, "a lock timeout left something behind")
        return out

    def test_ssc_40_a_reader_of_the_app_first_table_bounds_adoption(self):
        self.bounded_refusal(self.db("orm"), "SELECT 1 FROM booking_attempt_events LIMIT 0")

    def test_ssc_41_an_uncommitted_writer_of_the_app_first_table_bounds_adoption(self):
        self.bounded_refusal(self.db("orm"), S._INSERT.format(uid="in-flight"))

    def test_ssc_42_a_reader_of_a_canonical_table_bounds_verification(self):
        url = self.db("head")
        _alembic(url, "stamp", P4_PARENT)
        self.bounded_refusal(url, "SELECT 1 FROM booking_attempt_events LIMIT 0")

    def test_ssc_43_a_held_thread_revisions_lock_rolls_back_the_earlier_revision(self):
        url = self.db("parent")
        out = self.bounded_refusal(url, "LOCK TABLE thread_revisions IN ROW EXCLUSIVE MODE",
                                   expect_stdout=(f"{P4_REV}: CREATED",))
        self.assertIn(f"Running upgrade {P4_REV} -> {HEAD}", out)
        self.assertEqual(_scalars(url, "SELECT to_regclass('booking_attempt_events')::text"),
                         [None])
        self.assertEqual(_scalars(url, "SELECT version_num FROM alembic_version"), [P4_PARENT])

    def test_ssc_44_the_column_revision_bounds_its_own_lock_when_run_alone(self):
        """From `20261015` only `20261016` runs: no earlier revision set a bound for it."""
        url = self.db("parent")
        r = _alembic(url, "upgrade", P4_REV)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.bounded_refusal(url, "LOCK TABLE thread_revisions IN ROW EXCLUSIVE MODE")
        self.assertEqual(_scalars(url, "SELECT version_num FROM alembic_version"), [P4_REV])


class SSC_ServerMajor(_Base):

    def test_ssc_50_preflight_refuses_a_server_reporting_another_major(self):
        """The server's answer is patched at the driver (no code path is stubbed): what the
        preflight is told is what a 15 or 17 server would say."""
        from app.services.schema_preflight import missing_declared_booking_schema
        url = self.db("head")
        self.assertEqual(_scalars(url, "SELECT current_setting('server_version_num')::int "
                                       "/ 10000"), [16])
        self.assertEqual(self.preflight(url), [])
        for reported in ("150008", "170002", "160013"):
            with self.subTest(reported=reported):
                eng = create_engine(url)

                @event.listens_for(eng, "before_cursor_execute", retval=True)
                def _major(conn, cursor, statement, params, context, executemany,
                           reported=reported):
                    if "current_setting('server_version_num')" in statement:
                        statement = f"SELECT '{reported}'"
                    return statement, params
                try:
                    problems = missing_declared_booking_schema(eng)
                finally:
                    eng.dispose()
                if reported == "160013":                # the certified major, patched alike
                    self.assertEqual(problems, [])
                    continue
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("certified for PostgreSQL 16 only", problems[0])
                self.assertIn(f"PostgreSQL {int(reported) // 10000}", problems[0])


def tearDownModule():
    if S.PARENT_URL and _Base.extra:
        for name in ("ssc_t_legacy", "ssc_t_rhead", "ssc_t_rorm"):
            S._drop(name)
        _Base.extra = {}
    if S.PARENT_URL and S._Strict.templates:
        for name in ("ssa_t_orm", "ssa_t_head"):
            S._drop(name)
        S._Strict.templates = {}


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
