"""W1-F2 — `operator_notifications`: the operator's one WhatsApp notice per committed booking.

The first controlled Wild (2026-10-21) measured that a Flow booking reaches nobody on the team:
the Data Exchange path sends no booking e-mail and the dashboard listed the booking as
`NOTIFY_TEAM_MANUALLY` only after five minutes (W1-F2). The owner decided that the launch
professional receives exactly one operational WhatsApp notice per committed booking, on his
own business number, idempotently and backed by a transaction. This table is that record:

    id                       identity
    booking_command_id       the committed ledger row (FK to `booking_commands`, UNIQUE):
                             one booking command, at most one notice — ever
    identity                 64-hex identity derived from the ledger key (UNIQUE)
    kind                     BOOKING_CREATED (CHECK)
    status                   PENDING | SENDING | SENT | FAILED | HELD (CHECK)
    attempts                 0..2 (CHECK): one attempt plus at most one retry
    provider                 the transport, e.g. META_WHATSAPP_TEMPLATE
    destination_hash         16-hex SHA-256 prefix of the destination — never the number
    template_name / _language  the configured template used
    provider_message_id      the WAMID Meta returned
    outbound_message_id      the gate's `whatsapp_messages` row of the last attempt
    last_error               a typed code, never prose
    claim_token / lease_expires_at / next_attempt_at / sent_at / created_at / updated_at
    hist_*                   HISTORICAL scalar ids — never a foreign key, so a tester reset
                             (which deletes threads, leads and revisions) is never blocked

The only foreign key is to `booking_commands`, whose rows are never deleted (a reset DETACHES
them, Convergence Phase 2), so it can never block a reset either.

It also records the no-backfill floor: `system_settings['operator_notification_floor_ledger_id']`
= the highest `booking_commands.id` at upgrade time. Only ledger rows ABOVE it are ever
notified, so neither the historical bookings nor any booking committed before this revision
existed receive a late notice.

Upgrade — strict, before any DDL, under a lock, in Alembic's single transaction:
  * table absent and no floor key            → created canonically, floor recorded (CREATED)
  * the table EMPTY and EXACTLY the canonical fingerprint (the application's `create_all`
    produces the same shape), no floor key   → adopted as is, floor recorded
                                                                  (ADOPTED_EMPTY_CANONICAL)
  (either of the two measured forms of the status CHECK — as created, or as a `pg_dump`
  restore re-reads it — is the canonical shape; nothing else is)
  * EXACTLY the canonical table AND a well-formed floor — this revision's own finished state,
    e.g. a version rewound over an upgraded database → nothing changed, rows and floor kept
                                                                  (VERIFIED_CANONICAL)
Anything else — a differing column, constraint, index, trigger, rule or policy; rows without
a floor; a floor without the table or not a ledger id — REFUSES with nothing changed.

Downgrade — never erases evidence. With any row present it REFUSES: the rows are the record
of what the team was told. An empty table is dropped only when its COLUMNS are exactly this
revision's (constraint deparse may legitimately differ after a `pg_dump` restore); the floor
key is removed with it, so a later re-upgrade records a fresh floor.

Revision ID: 20261022_operator_notifications
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import (SchemaShapeRefused, closest_differences,
                                             differences, matching, require_certified_server,
                                             table_fingerprint)

revision: str = "20261022_operator_notifications"
down_revision: str = "20261016_notification_sent_at"
branch_labels = None
depends_on = None

_TABLE = "operator_notifications"
FLOOR_KEY = "operator_notification_floor_ledger_id"
_STATUSES = ("PENDING", "SENDING", "SENT", "FAILED", "HELD")
_INDEX = "ix_operator_notifications_status"


def _col(name, type_, not_null, default=None):
    return (name, type_, not_null, "", "", True, default, None, None)


# ── frozen fingerprint, measured on PostgreSQL 16 (immutable history: never edit) ─────────
F_COLUMNS = [
    _col("id", "bigint", True, "nextval('operator_notifications_id_seq'::regclass)"),
    _col("booking_command_id", "bigint", True),
    _col("identity", "character varying(64)", True),
    _col("kind", "character varying(32)", True),
    _col("status", "character varying(16)", True),
    _col("attempts", "integer", True, "0"),
    _col("provider", "character varying(32)", True),
    _col("destination_hash", "character varying(16)", False),
    _col("template_name", "character varying(128)", False),
    _col("template_language", "character varying(16)", False),
    _col("provider_message_id", "character varying(191)", False),
    _col("outbound_message_id", "integer", False),
    _col("last_error", "character varying(64)", False),
    _col("claim_token", "character varying(36)", False),
    _col("lease_expires_at", "timestamp with time zone", False),
    _col("next_attempt_at", "timestamp with time zone", False),
    _col("sent_at", "timestamp with time zone", False),
    _col("created_at", "timestamp with time zone", True, "now()"),
    _col("updated_at", "timestamp with time zone", True, "now()"),
    _col("hist_thread_id", "integer", False),
    _col("hist_lead_id", "integer", False),
    _col("hist_revision_id", "integer", False),
    _col("hist_thread_revision_id", "integer", False),
]
def _btree(name, column, opclass, collation="", unique=False, primary=False):
    return (name, "btree", unique, primary, False, True, True, 1, 1, (column,), "[0:0]={0}",
            (opclass,), (collation,), None, None, None)


def _con(name, kind, column, deparse):
    # (name, type, columns, deferrable, deferred, validated, NO INHERIT, local, deparse) — as
    # measured, PostgreSQL reports NO INHERIT false for the CHECKs and true for the rest
    return (name, kind, (column,), False, False, True, kind != "c", True, deparse)


_STATUS_ARRAY = ", ".join(f"'{s}'::character varying" for s in _STATUSES)
#: The table as `_create_table` creates it — and, measured identical, as the application's
#: `create_all` creates it from the model.
_F_CANONICAL: dict = {
    "table": (("relkind", "r"), ("persistence", "p"), ("owned_by_current_user", True),
              ("row_security", False), ("force_row_security", False), ("partition", False),
              ("typed", True), ("default_tablespace", True), ("options", None),
              ("comment", None), ("inherits_from", 0), ("inherited_by", 0)),
    "columns": F_COLUMNS,
    "sequences": [("id", "operator_notifications_id_seq", "a", "bigint", 1, 1, 1,
                   9223372036854775807, 1, False)],
    "constraints": [
        _con("ck_operator_notifications_attempts", "c", "attempts",
             "CHECK (((attempts >= 0) AND (attempts <= 2)))"),
        _con("ck_operator_notifications_kind", "c", "kind",
             "CHECK (((kind)::text = 'BOOKING_CREATED'::text))"),
        _con("ck_operator_notifications_status", "c", "status",
             f"CHECK (((status)::text = ANY ((ARRAY[{_STATUS_ARRAY}])::text[])))"),
        _con("operator_notifications_booking_command_id_fkey", "f", "booking_command_id",
             "FOREIGN KEY (booking_command_id) REFERENCES booking_commands(id)"),
        _con("operator_notifications_pkey", "p", "id", "PRIMARY KEY (id)"),
        _con("uq_operator_notifications_command", "u", "booking_command_id",
             "UNIQUE (booking_command_id)"),
        _con("uq_operator_notifications_identity", "u", "identity", "UNIQUE (identity)"),
    ],
    "referenced_by": [],
    "indexes": [
        _btree(_INDEX, "status", "text_ops", "default"),
        _btree("operator_notifications_pkey", "id", "int8_ops", unique=True, primary=True),
        _btree("uq_operator_notifications_command", "booking_command_id", "int8_ops",
               unique=True),
        _btree("uq_operator_notifications_identity", "identity", "text_ops", "default",
               unique=True),
    ],
    "triggers": [],
    "rules": [],
    "policies": [],
}
_RESTORED_STATUS_ARRAY = ", ".join(f"('{s}'::character varying)::text" for s in _STATUSES)
#: The same table after `pg_dump` → restore (plain and custom format, measured on PG 16):
#: PostgreSQL re-deparses the stored `status IN (...)` CHECK in this one other form (the
#: SSRV-01 phenomenon of migration 20261015). Only that deparse differs; nothing is normalised
#: or rewritten. A table created HERE must still read in the creation form.
_F_RESTORED: dict = {**_F_CANONICAL, "constraints": [
    c if c[0] != "ck_operator_notifications_status" else
    _con("ck_operator_notifications_status", "c", "status",
         f"CHECK (((status)::text = ANY (ARRAY[{_RESTORED_STATUS_ARRAY}])))")
    for c in _F_CANONICAL["constraints"]]}
_F_PERMITTED = [_F_CANONICAL, _F_RESTORED]


def _lock_and_read(bind):
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text("LOCK TABLE booking_commands IN SHARE MODE"))
    if table_fingerprint(bind, _TABLE) is not None:
        bind.execute(sa.text(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE"))
    return table_fingerprint(bind, _TABLE)


def _has_rows(bind) -> bool:
    return bool(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE})")).scalar())


def _floor_value(bind):
    return bind.execute(sa.text("SELECT value FROM system_settings WHERE key = :k"),
                        {"k": FLOOR_KEY}).scalar()


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing before any change — nothing was created, altered or "
        "dropped and the Alembic version is unchanged. Capture this message and a schema-only "
        "dump for the owner. Differences: " + "; ".join(problems or ["-"]))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    table = _lock_and_read(bind)
    floor_value = _floor_value(bind)
    if table is not None and matching(_F_PERMITTED, table) is None:
        _refuse(f"{_TABLE} exists and is not exactly the canonical shape",
                closest_differences(_F_PERMITTED, table))
    if floor_value is not None:
        # Only this revision's own finished state carries a floor: the canonical table with
        # a well-formed floor (e.g. a version rewound over an upgraded database) is verified
        # and left exactly as it is, rows included. A floor anywhere else is unexplained.
        if table is None or not floor_value.isdigit():
            _refuse(f"system_settings already holds {FLOOR_KEY} of unknown provenance",
                    [f"{FLOOR_KEY} present without this revision's table"
                     if table is None else f"{FLOOR_KEY} is not a ledger id"])
        print(f"{revision}: VERIFIED_CANONICAL floor={floor_value}", flush=True)
        return
    if table is None:
        branch = "CREATED"
        _create_table()
    elif _has_rows(bind):
        _refuse(f"{_TABLE} exists, already holds rows and has no floor", ["rows present"])
    else:
        branch = "ADOPTED_EMPTY_CANONICAL"
    after = table_fingerprint(bind, _TABLE)
    # created here: the creation form exactly; adopted: exactly the form it was found in
    problems = differences(_F_CANONICAL if table is None else table, after)
    if problems:
        _refuse(f"{_TABLE} is not exactly canonical after creation", problems)
    floor = bind.execute(sa.text("SELECT coalesce(max(id), 0) FROM booking_commands")).scalar()
    bind.execute(sa.text("INSERT INTO system_settings (key, value, updated_at) "
                         "VALUES (:k, :v, now())"), {"k": FLOOR_KEY, "v": str(int(floor))})
    print(f"{revision}: {branch} floor={int(floor)}", flush=True)


def _create_table() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("booking_command_id", sa.BigInteger(),
                  sa.ForeignKey("booking_commands.id"), nullable=False),
        sa.Column("identity", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("destination_hash", sa.String(16), nullable=True),
        sa.Column("template_name", sa.String(128), nullable=True),
        sa.Column("template_language", sa.String(16), nullable=True),
        sa.Column("provider_message_id", sa.String(191), nullable=True),
        sa.Column("outbound_message_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(64), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("hist_thread_id", sa.Integer(), nullable=True),
        sa.Column("hist_lead_id", sa.Integer(), nullable=True),
        sa.Column("hist_revision_id", sa.Integer(), nullable=True),
        sa.Column("hist_thread_revision_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("booking_command_id", name="uq_operator_notifications_command"),
        sa.UniqueConstraint("identity", name="uq_operator_notifications_identity"),
        sa.CheckConstraint("kind IN ('BOOKING_CREATED')", name="ck_operator_notifications_kind"),
        sa.CheckConstraint("status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'HELD')",
                           name="ck_operator_notifications_status"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 2",
                           name="ck_operator_notifications_attempts"),
    )
    op.create_index(_INDEX, _TABLE, ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    require_certified_server(bind, revision)
    table = _lock_and_read(bind)
    if table is None:
        _refuse(f"{_TABLE} is absent at this revision", ["object absent"])
    if _has_rows(bind):
        rows = bind.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar()
        raise RuntimeError(
            f"{_TABLE} holds {rows} operator-notification row(s): the record of what the team "
            "was told about each booking. Downgrade refused — export and hash them first and "
            "take the owner's decision.")
    if table["columns"] != F_COLUMNS:
        _refuse(f"{_TABLE} does not carry exactly this revision's columns, so it is not dropped",
                differences({"columns": F_COLUMNS}, {"columns": table["columns"]}))
    op.execute(f"DROP INDEX {_INDEX}")
    op.drop_table(_TABLE)
    bind.execute(sa.text("DELETE FROM system_settings WHERE key = :k"), {"k": FLOOR_KEY})
