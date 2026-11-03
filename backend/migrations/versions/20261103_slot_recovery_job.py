"""PRELAUNCH-MINIMUM-LAUNCH-R1 — the durable slot-recovery job, booking attention conditions,
and the booking-associated seller phone.

The independent review of the minimum-launch candidate (audit 2026-11-03, F-01/F-02/F-03/F-05)
measured that a claimed-but-unsent recovery was silent, that the recovery ran its provider
calls inside the Data Exchange request, that a failed write of the Flow details was silent,
and that `booking_attempt_events` (forensic, "never authority") was being used as the
recovery's admission. This revision adds the three objects the corrections need, and nothing
else:

  slot_recovery_jobs            one row per LOSING OFFER (UNIQUE recovery_identity): the
                                admission authority and the worker's lease/state record
                                (PENDING → SENDING_TEXT → TEXT_SENT → SENDING_FLOW → RECOVERED,
                                or terminal HANDOFF / FAILED). No foreign keys (history).
  booking_attention_conditions  a typed, durable reason a person must look at a thread no job
                                owns (DETAILS_NOT_PERSISTED). UNIQUE condition_uid.
  thread_revisions.seller_phone nullable VARCHAR(40), no default: the seller phone written by
                                the booking's OWN transaction, so a completed booking no longer
                                depends on a draft evidence row for it.

Upgrade — strict, under a lock, in Alembic's single transaction:
  * every object absent                           → created canonically          (CREATED)
  * an object present and EXACTLY canonical, EMPTY (a table) or holding no value (the column)
    — the application's `create_all` builds the identical shape        (ADOPTED_EMPTY_CANONICAL)
  * every object present and EXACTLY canonical, rows included — this revision's own finished
    state, e.g. a version rewound over an upgraded database            (VERIFIED_CANONICAL)
  Anything else — a differing column, constraint, index, trigger, rule or policy; a partial
  set holding rows — REFUSES with nothing changed.

Downgrade — never erases evidence. It REFUSES while either table holds ANY row (a nonterminal
job is a customer still owed a recovery; a terminal one is the record of what they were told)
or any booking carries a seller phone, unless that data was first archived and removed under
a separately authorised procedure. Otherwise it drops exactly what is PRESENT of this
revision's objects, after checking the columns are this revision's. An absent object is not an
error: the project's `create_all` + `stamp head` bootstrap on an older database creates the two
new tables but cannot add the column to the existing thread_revisions, and that empty partial
shape must downgrade (strict-schema ssa_08).

Deployment order: this revision runs BEFORE the image that carries it (its
`schema_preflight.EXPECTED_HEAD` is this revision and it refuses to start below it).

Revision ID: 20261103_slot_recovery_job
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import (SchemaShapeRefused, closest_differences,
                                             column_fingerprint, differences, matching,
                                             require_certified_server, table_fingerprint)

revision: str = "20261103_slot_recovery_job"
down_revision: str = "20261102_lead_phone_identity"
branch_labels = None
depends_on = None

JOBS = "slot_recovery_jobs"
CONDITIONS = "booking_attention_conditions"
TR = "thread_revisions"
SELLER_PHONE = "seller_phone"
_STATES = ("PENDING", "SENDING_TEXT", "TEXT_SENT", "SENDING_FLOW", "RECOVERED", "HANDOFF",
           "FAILED")
_TERMINAL = ("RECOVERED", "HANDOFF", "FAILED")


def _col(name, type_, not_null, default=None):
    return (name, type_, not_null, "", "", True, default, None, None)


def _btree(name, column, opclass, collation="", unique=False, primary=False):
    return (name, "btree", unique, primary, False, True, True, 1, 1, (column,), "[0:0]={0}",
            (opclass,), (collation,), None, None, None)


def _con(name, kind, columns, deparse):
    return (name, kind, tuple(columns), False, False, True, kind != "c", True, deparse)


def _any(values, restored=False):
    if restored:
        inner = ", ".join(f"('{v}'::character varying)::text" for v in values)
        return f"ANY (ARRAY[{inner}])"
    inner = ", ".join(f"'{v}'::character varying" for v in values)
    return f"ANY ((ARRAY[{inner}])::text[])"


_TABLE_META = (("relkind", "r"), ("persistence", "p"), ("owned_by_current_user", True),
               ("row_security", False), ("force_row_security", False), ("partition", False),
               ("typed", True), ("default_tablespace", True), ("options", None),
               ("comment", None), ("inherits_from", 0), ("inherited_by", 0))

# ── frozen fingerprints, measured on PostgreSQL 16 (immutable history: never edit) ─────────
F_JOBS_COLUMNS = [
    _col("id", "bigint", True, "nextval('slot_recovery_jobs_id_seq'::regclass)"),
    _col("recovery_identity", "character varying(64)", True),
    _col("thread_id", "integer", True),
    _col("lead_id", "integer", False),
    _col("contact_id", "integer", False),
    _col("candidate_id", "integer", False),
    _col("losing_offer_id", "integer", True),
    _col("new_offer_id", "integer", False),
    _col("origin_delivery_hash", "character varying(16)", False),
    _col("state", "character varying(16)", True),
    _col("outcome", "character varying(48)", False),
    _col("attempts", "integer", True, "0"),
    _col("text_attempts", "integer", True, "0"),
    _col("flow_attempts", "integer", True, "0"),
    _col("claim_token", "character varying(36)", False),
    _col("lease_expires_at", "timestamp with time zone", False),
    _col("next_attempt_at", "timestamp with time zone", False),
    _col("last_error", "character varying(64)", False),
    _col("text_delivery_identity", "character varying(64)", False),
    _col("flow_delivery_identity", "character varying(64)", False),
    _col("text_provider_message_id", "character varying(191)", False),
    _col("flow_provider_message_id", "character varying(191)", False),
    _col("attention_applied", "boolean", True, "false"),
    _col("owns_state_flag", "boolean", True, "false"),
    _col("owns_lead_flag", "boolean", True, "false"),
    _col("inbound_marker", "character varying(16)", False),
    _col("created_at", "timestamp with time zone", True, "now()"),
    _col("updated_at", "timestamp with time zone", True, "now()"),
    _col("completed_at", "timestamp with time zone", False),
]


def _jobs_fingerprint(restored=False):
    return {
        "table": _TABLE_META,
        "columns": F_JOBS_COLUMNS,
        "sequences": [("id", "slot_recovery_jobs_id_seq", "a", "bigint", 1, 1, 1,
                       9223372036854775807, 1, False)],
        "constraints": [
            _con("ck_slot_recovery_jobs_attempts", "c", ["attempts"],
                 "CHECK (((attempts >= 0) AND (attempts <= 6)))"),
            _con("ck_slot_recovery_jobs_flow_attempts", "c", ["flow_attempts"],
                 "CHECK (((flow_attempts >= 0) AND (flow_attempts <= 1)))"),
            _con("ck_slot_recovery_jobs_state", "c", ["state"],
                 f"CHECK (((state)::text = {_any(_STATES, restored)}))"),
            _con("ck_slot_recovery_jobs_terminal", "c", ["state", "outcome", "completed_at"],
                 f"CHECK ((((state)::text = {_any(_TERMINAL, restored)}) = "
                 "((outcome IS NOT NULL) AND (completed_at IS NOT NULL))))"),
            _con("ck_slot_recovery_jobs_text_attempts", "c", ["text_attempts"],
                 "CHECK (((text_attempts >= 0) AND (text_attempts <= 2)))"),
            _con("slot_recovery_jobs_pkey", "p", ["id"], "PRIMARY KEY (id)"),
            _con("uq_slot_recovery_jobs_identity", "u", ["recovery_identity"],
                 "UNIQUE (recovery_identity)"),
        ],
        "referenced_by": [],
        "indexes": [
            _btree("ix_slot_recovery_jobs_state", "state", "text_ops", "default"),
            _btree("ix_slot_recovery_jobs_thread", "thread_id", "int4_ops"),
            _btree("slot_recovery_jobs_pkey", "id", "int8_ops", unique=True, primary=True),
            _btree("uq_slot_recovery_jobs_identity", "recovery_identity", "text_ops",
                   "default", unique=True),
        ],
        "triggers": [],
        "rules": [],
        "policies": [],
    }


F_CONDITIONS_COLUMNS = [
    _col("id", "bigint", True, "nextval('booking_attention_conditions_id_seq'::regclass)"),
    _col("condition_uid", "character varying(64)", True),
    _col("kind", "character varying(32)", True),
    _col("reason", "character varying(64)", True),
    _col("thread_id", "integer", True),
    _col("lead_id", "integer", False),
    _col("offer_id", "integer", False),
    _col("flags_applied", "boolean", True, "false"),
    _col("created_at", "timestamp with time zone", True, "now()"),
]


def _conditions_fingerprint():
    return {
        "table": _TABLE_META,
        "columns": F_CONDITIONS_COLUMNS,
        "sequences": [("id", "booking_attention_conditions_id_seq", "a", "bigint", 1, 1, 1,
                       9223372036854775807, 1, False)],
        "constraints": [
            _con("booking_attention_conditions_pkey", "p", ["id"], "PRIMARY KEY (id)"),
            _con("ck_booking_attention_conditions_kind", "c", ["kind"],
                 "CHECK (((kind)::text = 'DETAILS_NOT_PERSISTED'::text))"),
            _con("uq_booking_attention_conditions_uid", "u", ["condition_uid"],
                 "UNIQUE (condition_uid)"),
        ],
        "referenced_by": [],
        "indexes": [
            _btree("booking_attention_conditions_pkey", "id", "int8_ops", unique=True,
                   primary=True),
            _btree("ix_booking_attention_conditions_thread", "thread_id", "int4_ops"),
            _btree("uq_booking_attention_conditions_uid", "condition_uid", "text_ops",
                   "default", unique=True),
        ],
        "triggers": [],
        "rules": [],
        "policies": [],
    }


#: Every form PostgreSQL 16 was measured to report for the canonical tables: as created here
#: (= `create_all`), and as a `pg_dump` restore re-deparses the `IN (...)` CHECKs.
F_JOBS_PERMITTED = [_jobs_fingerprint(), _jobs_fingerprint(restored=True)]
F_CONDITIONS_PERMITTED = [_conditions_fingerprint()]
#: `thread_revisions.seller_phone`: nullable varchar(40), no default, not identity or
#: generated, default collation, no comment or options.
F_SELLER_PHONE = (("type", "character varying(40)"), ("not_null", False), ("identity", ""),
                  ("generated", ""), ("default_collation", True), ("default", None),
                  ("comment", None), ("options", None))


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing before any change — nothing was created, altered or "
        "dropped and the Alembic version is unchanged. Capture this message and a schema-only "
        "dump for the owner. Differences: " + "; ".join(problems or ["-"]))


def _lock_and_read(bind):
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text(f"LOCK TABLE {TR} IN SHARE ROW EXCLUSIVE MODE"))
    for t in (JOBS, CONDITIONS):
        if table_fingerprint(bind, t) is not None:
            bind.execute(sa.text(f"LOCK TABLE {t} IN ACCESS EXCLUSIVE MODE"))
    return (table_fingerprint(bind, JOBS), table_fingerprint(bind, CONDITIONS),
            column_fingerprint(bind, TR, SELLER_PHONE))


def _rows(bind, table) -> int:
    return int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar())


def _seller_phones(bind) -> int:
    return int(bind.execute(sa.text(
        f"SELECT count(*) FROM {TR} WHERE {SELLER_PHONE} IS NOT NULL")).scalar())


def _column_canonical(col) -> bool:
    return (col is not None and col["column"] == F_SELLER_PHONE
            and not col["column_constraints"] and not col["column_indexes"])


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    jobs, conds, col = _lock_and_read(bind)
    if jobs is not None and matching(F_JOBS_PERMITTED, jobs) is None:
        _refuse(f"{JOBS} exists and is not exactly the canonical shape",
                closest_differences(F_JOBS_PERMITTED, jobs))
    if conds is not None and matching(F_CONDITIONS_PERMITTED, conds) is None:
        _refuse(f"{CONDITIONS} exists and is not exactly the canonical shape",
                closest_differences(F_CONDITIONS_PERMITTED, conds))
    if col is None:
        _refuse(f"{TR} is absent", ["object absent"])
    has_col = col["column"] is not None
    if has_col and not _column_canonical(col):
        _refuse(f"{TR}.{SELLER_PHONE} exists and is not exactly the canonical column",
                [f"found: {col['column']!r}", f"constraints: {col['column_constraints']!r}",
                 f"indexes: {col['column_indexes']!r}"])
    present = [jobs is not None, conds is not None, has_col]
    held = ((jobs is not None and _rows(bind, JOBS) > 0)
            or (conds is not None and _rows(bind, CONDITIONS) > 0)
            or (has_col and _seller_phones(bind) > 0))
    if all(present):
        print(f"{revision}: VERIFIED_CANONICAL" if held
              else f"{revision}: ADOPTED_EMPTY_CANONICAL", flush=True)
        return
    if held:
        _refuse("part of this revision's schema already exists and holds data while the rest "
                "is missing — its provenance is unknown",
                [f"{JOBS} present={present[0]}", f"{CONDITIONS} present={present[1]}",
                 f"{TR}.{SELLER_PHONE} present={present[2]}"])
    if jobs is None:
        _create_jobs()
        problems = differences(F_JOBS_PERMITTED[0], table_fingerprint(bind, JOBS))
        if problems:
            _refuse(f"{JOBS} is not exactly canonical after creation", problems)
    if conds is None:
        _create_conditions()
        problems = differences(F_CONDITIONS_PERMITTED[0], table_fingerprint(bind, CONDITIONS))
        if problems:
            _refuse(f"{CONDITIONS} is not exactly canonical after creation", problems)
    if not has_col:
        op.add_column(TR, sa.Column(SELLER_PHONE, sa.String(40), nullable=True))
        if not _column_canonical(column_fingerprint(bind, TR, SELLER_PHONE)):
            _refuse(f"{TR}.{SELLER_PHONE} is not exactly canonical after creation", ["-"])
    print(f"{revision}: CREATED" if not any(present)
          else f"{revision}: CREATED (adopting the empty canonical part present)", flush=True)


def _create_jobs() -> None:
    op.create_table(
        JOBS,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("recovery_identity", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("losing_offer_id", sa.Integer(), nullable=False),
        sa.Column("new_offer_id", sa.Integer(), nullable=True),
        sa.Column("origin_delivery_hash", sa.String(16), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(48), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("text_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("flow_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column("text_delivery_identity", sa.String(64), nullable=True),
        sa.Column("flow_delivery_identity", sa.String(64), nullable=True),
        sa.Column("text_provider_message_id", sa.String(191), nullable=True),
        sa.Column("flow_provider_message_id", sa.String(191), nullable=True),
        sa.Column("attention_applied", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("owns_state_flag", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("owns_lead_flag", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("inbound_marker", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("recovery_identity", name="uq_slot_recovery_jobs_identity"),
        sa.CheckConstraint("state IN ('PENDING', 'SENDING_TEXT', 'TEXT_SENT', 'SENDING_FLOW', "
                           "'RECOVERED', 'HANDOFF', 'FAILED')",
                           name="ck_slot_recovery_jobs_state"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 6",
                           name="ck_slot_recovery_jobs_attempts"),
        sa.CheckConstraint("text_attempts >= 0 AND text_attempts <= 2",
                           name="ck_slot_recovery_jobs_text_attempts"),
        sa.CheckConstraint("flow_attempts >= 0 AND flow_attempts <= 1",
                           name="ck_slot_recovery_jobs_flow_attempts"),
        sa.CheckConstraint("(state IN ('RECOVERED', 'HANDOFF', 'FAILED')) = "
                           "(outcome IS NOT NULL AND completed_at IS NOT NULL)",
                           name="ck_slot_recovery_jobs_terminal"),
    )
    op.create_index("ix_slot_recovery_jobs_state", JOBS, ["state"])
    op.create_index("ix_slot_recovery_jobs_thread", JOBS, ["thread_id"])


def _create_conditions() -> None:
    op.create_table(
        CONDITIONS,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("condition_uid", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("flags_applied", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("condition_uid", name="uq_booking_attention_conditions_uid"),
        sa.CheckConstraint("kind IN ('DETAILS_NOT_PERSISTED')",
                           name="ck_booking_attention_conditions_kind"),
    )
    op.create_index("ix_booking_attention_conditions_thread", CONDITIONS, ["thread_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    jobs, conds, col = _lock_and_read(bind)
    if col is None:
        _refuse(f"{TR} is absent", ["object absent"])
    has_col = col["column"] is not None
    # Each object is dropped only when it is present; an absent one is not an error. The
    # project's `create_all` + `stamp head` bootstrap on an older database creates the two
    # NEW TABLES but cannot add a column to the EXISTING thread_revisions, so that partial
    # shape is legitimate (strict-schema ssa_08) — and empty.
    held = {}
    if jobs is not None:
        held[JOBS] = _rows(bind, JOBS)
    if conds is not None:
        held[CONDITIONS] = _rows(bind, CONDITIONS)
    if has_col:
        held[f"{TR}.{SELLER_PHONE}"] = _seller_phones(bind)
    if any(held.values()):
        nonterminal = int(bind.execute(sa.text(
            f"SELECT count(*) FROM {JOBS} WHERE state NOT IN "
            "('RECOVERED', 'HANDOFF', 'FAILED')")).scalar()) if jobs is not None else 0
        raise RuntimeError(
            f"{revision} downgrade REFUSED: durable recovery / attention / booking data is "
            f"present ({', '.join(f'{k}={v}' for k, v in held.items())}; nonterminal recovery "
            f"jobs={nonterminal}). A nonterminal job is a customer still owed a recovery; the "
            "rest is the record of what customers were told and what a person must check. "
            "Archive it under a separately authorised procedure first. Nothing was dropped.")
    if jobs is not None and jobs["columns"] != F_JOBS_COLUMNS:
        _refuse(f"{JOBS} does not carry exactly this revision's columns, so it is not dropped",
                differences({"columns": F_JOBS_COLUMNS}, {"columns": jobs["columns"]}))
    if conds is not None and conds["columns"] != F_CONDITIONS_COLUMNS:
        _refuse(f"{CONDITIONS} does not carry exactly this revision's columns, so it is not "
                "dropped", differences({"columns": F_CONDITIONS_COLUMNS},
                                       {"columns": conds["columns"]}))
    if has_col and not _column_canonical(col):
        _refuse(f"{TR}.{SELLER_PHONE} is not exactly this revision's column, so it is not "
                "dropped", [f"found: {col['column']!r}"])
    dropped = []
    if jobs is not None:
        op.drop_table(JOBS)
        dropped.append(JOBS)
    if conds is not None:
        op.drop_table(CONDITIONS)
        dropped.append(CONDITIONS)
    if has_col:
        op.drop_column(TR, SELLER_PHONE)
        dropped.append(f"{TR}.{SELLER_PHONE}")
    print(f"{revision}: DROPPED {', '.join(dropped) or 'nothing (absent)'} "
          "(no row changed; every object was empty)", flush=True)
