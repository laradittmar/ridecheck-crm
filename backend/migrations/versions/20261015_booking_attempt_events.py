"""Convergence Phase 4 — `booking_attempt_events`: durable, FK-free, append-only evidence.

A booking attempt that produced no committed command — a refusal, a contention, an internal
failure — left no durable trace outside CE's own telemetry (P2R-10 / R3RV-02): the authority
rolls its transaction back entirely, and the Data Exchange endpoint and operator commands have
no telemetry at all. An operator booking e-mail that was not delivered left nothing either
(SB2B-RV-04). This table is that evidence, and only evidence:

    id                        identity
    event_uid                 the idempotency rule (UNIQUE): one logical attempt, one row
    occurred_at               when
    source / stage / outcome  typed codes (two CHECK constraints)
    reason                    a typed code, never prose
    command_key_hash          16-hex SHA-256 prefix of the ledger key — never the key
    attempt_ref_hash          16-hex SHA-256 prefix of the attempt's identity
    hist_*                    HISTORICAL, NON-AUTHORITATIVE scalar ids (thread, offer,
                              revision, thread revision) — never a foreign key

No foreign key, by design: an FK to an offer would take `FOR KEY SHARE` on a row a contender
holds `FOR UPDATE`, and an FK to any business row would block a tester reset. Append-only: one
trigger refuses every UPDATE and DELETE (TRUNCATE, a table-level administrative operation, is
not a row event and is not blocked).

Additive only. Nothing existing changes; no business row is read or written; no professional
is created; `DETACHED`, the exclusion constraint and the partial offer index are untouched.

Order — strict one-time adoption (Phase 4 STRICT-SCHEMA-ADOPTION, Decision A of the Phase 4
architectural review). Before any DDL, under a lock, in Alembic's single transaction, the
table must be EXACTLY one of:
  * absent                                   → created canonically            (CREATED)
  * the application's `create_all` shape, EMPTY, and no function of the canonical name
                                             → enforcement installed          (ADOPTED_ORM_SHAPE)
  * the canonical shape (enforcement included), EMPTY
                                             → verified, nothing changed      (VERIFIED_CANONICAL)
"Exactly" is the complete catalogue fingerprint (`app.services.schema_fingerprint`) compared
with the frozen literals below: every column with its type, nullability, default, identity,
generated status and collation, every constraint by PostgreSQL's own deparse, every index by
structure, every user trigger, rule and policy, the owned sequence and the table's own
properties. Anything else — a widened CHECK under the right name, a missing default, an extra
trigger, a rule, a row — REFUSES the whole upgrade; nothing is altered, dropped or replaced, so
the version and the original fingerprint stay as they were. After installation the fingerprint
is verified again, exactly: what this revision created must read in the creation form; a table
it adopted or verified must read exactly as classified, plus what was installed. One line names
the branch taken, for the deployment record (record it only from a run that exited 0).

Restore (STRICT-SCHEMA-ADOPTION-CORRECTION, SSRV-01): PostgreSQL 16 deparses each of the two
CHECKs of a table restored by `pg_dump` (plain or custom format) in ONE other form — the same
stored constraint, re-read — and that form is a fixpoint of further dump/restore rounds
(measured on 16.13). For exactly those two constraints, and only in their deparse field, both
measured forms are accepted (`CHECK_FORMS`); every other part of the fingerprint, and every
other deparse — a widened, narrowed, reordered, renamed or disjunctive CHECK, `NOT VALID` —
still refuses. Nothing is normalised and nothing is rewritten: a restored table keeps its
restored form. Migrations still run BEFORE the replacement application container starts
(`docs/operations/RECOVERY_MANIFEST.md` §6); the application refuses to start on a schema
that is not at this shape.

Reversibility: `downgrade()` drops the table, its trigger and function — ONLY while the table
is EMPTY and EXACTLY the canonical shape, either CHECK form (or the `create_all` shape, which
a database built by `create_all` + `alembic stamp` carries). With evidence rows present it
REFUSES: dropping them
is audit-data loss and cannot be undone. Export or deliberately discard the rows first — an
explicit owner checkpoint, never automatic (`docs/operations/RECOVERY_MANIFEST.md` §6). Any
other shape refuses too: an object this revision cannot recognise is never erased.

Revision ID: 20261015_booking_attempt_events
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import (SchemaShapeRefused, closest_differences,
                                             differences, function_fingerprint, matching,
                                             require_certified_server, table_fingerprint)

revision: str = "20261015_booking_attempt_events"
down_revision: str = "20261014_ledger_detached_state"
branch_labels = None
depends_on = None

_TABLE = "booking_attempt_events"
_FUNCTION = "booking_attempt_events_append_only"
_TRIGGER = "trg_booking_attempt_events_append_only"
_CHECKS = {
    "ck_booking_attempt_events_stage":
        "stage IN ('PRE_EXECUTE', 'AUTHORITY', 'FINALISE', 'NOTIFICATION')",
    "ck_booking_attempt_events_outcome":
        "outcome IN ('REFUSED', 'RETRYABLE', 'CONTENDED', 'INTERNAL', 'FAILED', 'HELD')",
}
_UNIQUE = "booking_attempt_events_event_uid_key"
_INDEXES = {
    "ix_booking_attempt_events_occurred": "occurred_at",
    "ix_booking_attempt_events_thread": "hist_thread_id",
    "ix_booking_attempt_events_thread_revision": "hist_thread_revision_id",
}
_FUNCTION_SQL = f"""
        CREATE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'booking_attempt_events is append-only (% refused)', TG_OP
            USING ERRCODE = '23514', CONSTRAINT = 'booking_attempt_events_append_only';
        END $$"""


# ── frozen fingerprints, measured on PostgreSQL 16.13 (immutable history: never edit) ──────

def _col(name, type_, not_null, default=None):
    # (name, type, NOT NULL, identity, generated, default collation, default, comment, options)
    return (name, type_, not_null, "", "", True, default, None, None)


def _btree(name, column, opclass, collation="", unique=False, primary=False):
    # (name, method, unique, primary, nulls-not-distinct, immediate, valid, key atts, atts,
    #  key columns, options, opclasses, collations, expressions, predicate, reloptions)
    return (name, "btree", unique, primary, False, True, True, 1, 1, (column,), "[0:0]={0}",
            (opclass,), (collation,), None, None, None)


def _check(name, column, values, form="created"):
    # (name, type, key columns, deferrable, deferred, validated, no-inherit, local, deparse)
    if form == "created":       # as this revision (or create_all) creates it
        arr = ", ".join(f"'{v}'::character varying" for v in values)
        deparse = f"CHECK ((({column})::text = ANY ((ARRAY[{arr}])::text[])))"
    elif form == "restored":    # the same constraint after pg_dump → restore (a fixpoint)
        arr = ", ".join(f"('{v}'::character varying)::text" for v in values)
        deparse = f"CHECK ((({column})::text = ANY (ARRAY[{arr}])))"
    else:
        raise ValueError(form)
    return (name, "c", (column,), False, False, True, False, True, deparse)


#: The only two measured PostgreSQL 16 deparses of each CHECK (SSRV-01). Finite by
#: measurement: a restored table re-dumped and restored again reads the restored form.
CHECK_FORMS = ("created", "restored")
_STAGES = ("PRE_EXECUTE", "AUTHORITY", "FINALISE", "NOTIFICATION")
_OUTCOMES = ("REFUSED", "RETRYABLE", "CONTENDED", "INTERNAL", "FAILED", "HELD")


def _checks(outcome_form="created", stage_form="created"):
    return [_check("ck_booking_attempt_events_outcome", "outcome", _OUTCOMES, outcome_form),
            _check("ck_booking_attempt_events_stage", "stage", _STAGES, stage_form)]


#: What `Base.metadata.create_all` makes (the model declares the columns, CHECKs, UNIQUE and
#: the three `ix_` indexes); the only pre-existing shape this revision may adopt.
F_ORM = {
    "table": (("relkind", "r"), ("persistence", "p"), ("owned_by_current_user", True),
              ("row_security", False), ("force_row_security", False), ("partition", False),
              ("typed", True), ("default_tablespace", True), ("options", None),
              ("comment", None), ("inherits_from", 0), ("inherited_by", 0)),
    "columns": [
        _col("id", "bigint", True, "nextval('booking_attempt_events_id_seq'::regclass)"),
        _col("event_uid", "character varying(40)", True),
        _col("occurred_at", "timestamp with time zone", True, "now()"),
        _col("source", "character varying(32)", True),
        _col("stage", "character varying(16)", True),
        _col("outcome", "character varying(16)", True),
        _col("reason", "character varying(64)", True),
        _col("command_key_hash", "character varying(16)", False),
        _col("attempt_ref_hash", "character varying(16)", False),
        _col("hist_thread_id", "integer", False),
        _col("hist_offer_id", "integer", False),
        _col("hist_revision_id", "integer", False),
        _col("hist_thread_revision_id", "integer", False),
    ],
    "sequences": [("id", "booking_attempt_events_id_seq", "a", "bigint", 1, 1, 1,
                   9223372036854775807, 1, False)],
    "constraints": [
        (_UNIQUE, "u", ("event_uid",), False, False, True, True, True, "UNIQUE (event_uid)"),
        ("booking_attempt_events_pkey", "p", ("id",), False, False, True, True, True,
         "PRIMARY KEY (id)"),
        *_checks(),
    ],
    "referenced_by": [],
    "indexes": [
        _btree(_UNIQUE, "event_uid", "text_ops", "default", unique=True),
        _btree("booking_attempt_events_pkey", "id", "int8_ops", unique=True, primary=True),
        _btree("ix_booking_attempt_events_occurred", "occurred_at", "timestamptz_ops"),
        _btree("ix_booking_attempt_events_thread", "hist_thread_id", "int4_ops"),
        _btree("ix_booking_attempt_events_thread_revision", "hist_thread_revision_id",
               "int4_ops"),
    ],
    "triggers": [],
    "rules": [],
    "policies": [],
}
#: F_ORM plus exactly one user trigger: ROW | BEFORE | DELETE | UPDATE (tgtype 27), enabled,
#: no arguments, no column list, no WHEN, bound to the function below.
F_CANONICAL = {**F_ORM, "triggers": [
    (_TRIGGER, 27, "O", _FUNCTION, True, 0, "{}", None, True, False, False, None, None)]}
#: The append-only function: plpgsql, returns trigger, no arguments, volatile, not strict,
#: not SECURITY DEFINER, no settings, owned by the migration role, md5 of its body.
F_FUNCTION = [(_FUNCTION, "plpgsql", "trigger", 0, "f", False, "v", False, False, None, True,
               "348ed4cacd362157761a7ef24ec208da")]


def _with_check_forms(shape):
    """`shape` with each CHECK independently in either measured form — the constraints part's
    deparse fields are the ONLY thing that varies (the UNIQUE and PRIMARY KEY stay first)."""
    return [{**shape, "constraints": shape["constraints"][:2] + _checks(o, st)}
            for o in CHECK_FORMS for st in CHECK_FORMS]


#: Every exact fingerprint each state may have; the creation form first.
F_ORM_PERMITTED = _with_check_forms(F_ORM)
F_CANONICAL_PERMITTED = _with_check_forms(F_CANONICAL)


def _lock_and_read(bind):
    """Bounded lock, then the fingerprint read under it (None when the table is absent)."""
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    if table_fingerprint(bind, _TABLE) is not None:
        bind.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    return table_fingerprint(bind, _TABLE), function_fingerprint(bind, _FUNCTION)


def _has_rows(bind) -> bool:
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE})")).scalar())


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing the upgrade before any change — nothing was created, "
        "altered or dropped and the Alembic version is unchanged. Do not repair by script: "
        "capture this message and a schema-only dump for the owner "
        "(docs/operations/RECOVERY_MANIFEST.md §6). Differences: " + "; ".join(problems))


def _classify(bind, table, fn) -> tuple[str, dict]:
    """The single permitted branch for what exists, or a typed refusal, with the EXACT
    fingerprint the table must have once the branch is done. Reads only."""
    if table is None:
        if fn:
            _refuse(f"{_TABLE} is absent but a function {_FUNCTION} already exists",
                    differences({"function": []}, {"function": fn}))
        return "CREATED", F_CANONICAL                  # created here: the creation form
    orm, canonical = matching(F_ORM_PERMITTED, table), matching(F_CANONICAL_PERMITTED, table)
    if orm is not None:
        if fn:
            _refuse(f"{_TABLE} has the create_all shape but a function {_FUNCTION} already "
                    "exists", differences({"function": []}, {"function": fn}))
        branch, after = "ADOPTED_ORM_SHAPE", {**orm, "triggers": F_CANONICAL["triggers"]}
    elif canonical is not None:
        if fn != F_FUNCTION:
            _refuse(f"{_TABLE} has the canonical trigger but {_FUNCTION} is not the canonical "
                    "function", differences({"function": F_FUNCTION}, {"function": fn}))
        branch, after = "VERIFIED_CANONICAL", canonical
    else:
        _refuse(f"{_TABLE} exists and is not exactly a shape this revision may adopt",
                closest_differences(F_ORM_PERMITTED + F_CANONICAL_PERMITTED, table))
    if _has_rows(bind):
        _refuse(f"{_TABLE} exists and holds rows; a pre-existing table is adopted only when "
                "EMPTY (rows written without append-only enforcement cannot be certified)",
                ["rows present"])
    return branch, after


def _verify_canonical(bind, expected) -> None:
    """Exactly `expected` (the classified shape plus what was installed) and the function."""
    problems = differences(expected, table_fingerprint(bind, _TABLE)) + differences(
        {"function": F_FUNCTION}, {"function": function_fingerprint(bind, _FUNCTION)})
    if problems:
        _refuse(f"{_TABLE} is not exactly canonical after installation", problems)


def upgrade() -> None:
    """Classify what exists BEFORE any DDL (see the module docstring), then create or install
    with plain CREATE statements — never `OR REPLACE`, never `IF EXISTS`: absence of every
    object created was certified first — and verify the canonical fingerprint exactly. Any
    refusal raises inside Alembic's single transaction, so nothing of this or any earlier
    revision of the run is recorded."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 4 is PostgreSQL-only")
    require_certified_server(bind, revision)
    table, fn = _lock_and_read(bind)
    branch, after = _classify(bind, table, fn)
    if branch == "CREATED":
        _create_table()
    if branch in ("CREATED", "ADOPTED_ORM_SHAPE"):
        op.execute(_FUNCTION_SQL)
        op.execute(f"""
        CREATE TRIGGER {_TRIGGER}
          BEFORE UPDATE OR DELETE ON {_TABLE}
          FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()""")
    _verify_canonical(bind, after)
    print(f"{revision}: {branch}", flush=True)


def _create_table() -> None:
    op.create_table(
        "booking_attempt_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event_uid", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("command_key_hash", sa.String(16), nullable=True),
        sa.Column("attempt_ref_hash", sa.String(16), nullable=True),
        sa.Column("hist_thread_id", sa.Integer(), nullable=True),
        sa.Column("hist_offer_id", sa.Integer(), nullable=True),
        sa.Column("hist_revision_id", sa.Integer(), nullable=True),
        sa.Column("hist_thread_revision_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("event_uid", name=_UNIQUE),
        *[sa.CheckConstraint(sql, name=name) for name, sql in _CHECKS.items()],
    )
    for name, column in _INDEXES.items():
        op.create_index(name, "booking_attempt_events", [column])


def downgrade() -> None:
    bind = op.get_bind()
    require_certified_server(bind, revision)
    table, fn = _lock_and_read(bind)
    if table is not None and _has_rows(bind):
        rows = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar()
        raise RuntimeError(
            f"booking_attempt_events holds {rows} evidence row(s); dropping them is audit-data "
            "loss and cannot be undone. Downgrade refused — export and hash the evidence, "
            "then follow the owner checkpoint in docs/operations/RECOVERY_MANIFEST.md §6.")
    if matching(F_CANONICAL_PERMITTED, table) is not None and fn == F_FUNCTION:
        op.execute(f"DROP TRIGGER {_TRIGGER} ON {_TABLE}")
        op.execute(f"DROP FUNCTION {_FUNCTION}()")
    elif matching(F_ORM_PERMITTED, table) is None or fn:
        # Only the two shapes this revision owns are removed (either measured CHECK form);
        # anything else is left for the owner, never erased (a `create_all` + `stamp`
        # database carries F_ORM, empty).
        raise SchemaShapeRefused(
            f"{revision}: downgrade refused — {_TABLE} is not exactly a shape this revision "
            "owns, so it is not dropped. Differences from the canonical shape: "
            + "; ".join((closest_differences(F_CANONICAL_PERMITTED, table) if table else
                         ["object absent"])
                        + differences({"function": F_FUNCTION}, {"function": fn})))
    for name in reversed(_INDEXES):
        op.execute(f"DROP INDEX {name}")
    op.drop_table(_TABLE)
