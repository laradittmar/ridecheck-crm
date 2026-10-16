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

Order (Phase 4-R1, P4RV-05): the upgrade also converges when the application's `create_all`
ran first and created the table without its trigger — it reconciles and verifies, never
stamps over. Migrations still run BEFORE the replacement application container starts
(`docs/operations/RECOVERY_MANIFEST.md` §6); the application refuses to start on a schema
that lacks this enforcement.

Reversibility: `downgrade()` drops the table, its trigger and function — ONLY while the table
is EMPTY. With evidence rows present it REFUSES: dropping them is audit-data loss and cannot
be undone. Export or deliberately discard the rows first — an explicit owner checkpoint,
never automatic (`docs/operations/RECOVERY_MANIFEST.md` §6). Nothing else is touched, so a
downgrade restores the previous schema exactly.

Revision ID: 20261015_booking_attempt_events
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261015_booking_attempt_events"
down_revision: str = "20261014_ledger_detached_state"
branch_labels = None
depends_on = None


#: The complete enforcement set this revision guarantees, verified at the end of every
#: upgrade whichever order produced the table (see `_verify`).
_COLUMNS = {
    # name: (data_type, is_nullable, character_maximum_length)
    "id": ("bigint", "NO", None),
    "event_uid": ("character varying", "NO", 40),
    "occurred_at": ("timestamp with time zone", "NO", None),
    "source": ("character varying", "NO", 32),
    "stage": ("character varying", "NO", 16),
    "outcome": ("character varying", "NO", 16),
    "reason": ("character varying", "NO", 64),
    "command_key_hash": ("character varying", "YES", 16),
    "attempt_ref_hash": ("character varying", "YES", 16),
    "hist_thread_id": ("integer", "YES", None),
    "hist_offer_id": ("integer", "YES", None),
    "hist_revision_id": ("integer", "YES", None),
    "hist_thread_revision_id": ("integer", "YES", None),
}
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
_FUNCTION = "booking_attempt_events_append_only"
_TRIGGER = "trg_booking_attempt_events_append_only"


def upgrade() -> None:
    """Creates the table — or, Phase 4-R1 (P4RV-05), RECONCILES one that the application's
    `Base.metadata.create_all` created first (an image started before this migration ran).
    That table has the ORM's columns, CHECKs, UNIQUE and indexes but no append-only trigger,
    and the old unconditional `create_table` failed on it with DuplicateTable, leaving the
    chain behind and append-only unenforced. Now every column is compared with the declared
    shape (any difference refuses the upgrade — nothing is altered silently), any missing
    constraint or index is created, the function and trigger are (re)installed, and the whole
    enforcement set is verified before the revision is recorded. Existing rows are kept."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("convergence Phase 4 is PostgreSQL-only")
    if bind.execute(sa.text("SELECT to_regclass('booking_attempt_events')")).scalar() is None:
        _create_table()
    else:
        _reconcile_existing(bind)
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'booking_attempt_events is append-only (% refused)', TG_OP
            USING ERRCODE = '23514', CONSTRAINT = 'booking_attempt_events_append_only';
        END $$""")
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON booking_attempt_events")
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER}
          BEFORE UPDATE OR DELETE ON booking_attempt_events
          FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()""")
    _verify(bind)


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


def _reconcile_existing(bind) -> None:
    got = {r.column_name: (r.data_type, r.is_nullable, r.character_maximum_length)
           for r in bind.execute(sa.text(
               "SELECT column_name, data_type, is_nullable, character_maximum_length "
               "FROM information_schema.columns WHERE table_schema = current_schema() "
               "AND table_name = 'booking_attempt_events'"))}
    if got != _COLUMNS:
        raise RuntimeError(
            "booking_attempt_events exists with columns that differ from the declared shape "
            f"(missing {sorted(set(_COLUMNS) - set(got))}, unexpected "
            f"{sorted(set(got) - set(_COLUMNS))}, differing "
            f"{sorted(k for k in set(got) & set(_COLUMNS) if got[k] != _COLUMNS[k])}); "
            "refusing to adopt it.")
    cons = {r.conname: r.contype for r in bind.execute(sa.text(
        "SELECT conname, contype FROM pg_constraint "
        "WHERE conrelid = 'booking_attempt_events'::regclass"))}
    if _UNIQUE not in cons:
        op.create_unique_constraint(_UNIQUE, "booking_attempt_events", ["event_uid"])
    for name, sql in _CHECKS.items():
        if name not in cons:
            op.create_check_constraint(name, "booking_attempt_events", sql)
    idx = {r[0] for r in bind.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename = 'booking_attempt_events'"))}
    for name, column in _INDEXES.items():
        if name not in idx:
            op.create_index(name, "booking_attempt_events", [column])


def _verify(bind) -> None:
    """Every enforcement object, by DEFINITION rather than by name; any gap refuses."""
    problems = []
    defs = {r.conname: (r.contype, r.d) for r in bind.execute(sa.text(
        "SELECT conname, contype, pg_get_constraintdef(oid) AS d FROM pg_constraint "
        "WHERE conrelid = 'booking_attempt_events'::regclass"))}
    if any(t == "f" for t, _ in defs.values()):
        problems.append("a foreign key exists")
    if defs.get(_UNIQUE) != ("u", "UNIQUE (event_uid)"):
        problems.append(f"{_UNIQUE} is {defs.get(_UNIQUE)!r}")
    for name, column in (("ck_booking_attempt_events_stage", "stage"),
                         ("ck_booking_attempt_events_outcome", "outcome")):
        kind, d = defs.get(name, (None, ""))
        values = _CHECKS[name].split("IN", 1)[1]
        if kind != "c" or column not in d or any(
                v.strip(" ()'") not in d for v in values.split(",")):
            problems.append(f"{name} is {defs.get(name)!r}")
    idx = {r.indexname: r.indexdef for r in bind.execute(sa.text(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND tablename = 'booking_attempt_events'"))}
    for name, column in _INDEXES.items():
        if f"({column})" not in idx.get(name, ""):
            problems.append(f"index {name} is {idx.get(name)!r}")
    trig = bind.execute(sa.text(
        "SELECT t.tgenabled, t.tgtype, p.proname FROM pg_trigger t "
        "JOIN pg_proc p ON p.oid = t.tgfoid "
        "WHERE t.tgrelid = 'booking_attempt_events'::regclass AND t.tgname = :n "
        "AND NOT t.tgisinternal"), {"n": _TRIGGER}).first()
    # tgtype bits: ROW 1, BEFORE 2, INSERT 4, DELETE 8, UPDATE 16 → ROW|BEFORE|DELETE|UPDATE.
    if trig is None or trig.tgenabled != "O" or trig.tgtype != (1 | 2 | 8 | 16) \
            or trig.proname != _FUNCTION:
        problems.append(f"append-only trigger is {tuple(trig) if trig else None!r}")
    if problems:
        raise RuntimeError("booking_attempt_events enforcement incomplete: "
                           + "; ".join(problems))


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT count(*) FROM booking_attempt_events")).scalar()
    if rows:
        raise RuntimeError(
            f"booking_attempt_events holds {rows} evidence row(s); dropping them is audit-data "
            "loss and cannot be undone. Downgrade refused — export and hash the evidence, "
            "then follow the owner checkpoint in docs/operations/RECOVERY_MANIFEST.md §6.")
    # IF EXISTS: a schema built by `create_all` and stamped (the project's test bootstrap)
    # has the table and indexes but not the trigger, and must still downgrade cleanly.
    op.execute("DROP TRIGGER IF EXISTS trg_booking_attempt_events_append_only "
               "ON booking_attempt_events")
    op.execute("DROP FUNCTION IF EXISTS booking_attempt_events_append_only()")
    op.execute("DROP INDEX IF EXISTS ix_booking_attempt_events_thread_revision")
    op.execute("DROP INDEX IF EXISTS ix_booking_attempt_events_thread")
    op.execute("DROP INDEX IF EXISTS ix_booking_attempt_events_occurred")
    op.drop_table("booking_attempt_events")
