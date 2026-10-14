"""SB1 — canonical booking representation: interval, resource, overlap authority.

Before this, an appointment was a start (`turno_fecha` + `turno_hora`) whose length lived in
Python, nobody was recorded as attending it, and nothing in the database prevented two
appointments from overlapping (SB0-01, SB0-02, SB0-06).

Interval — derived, not parallel
    Five writers set `turno_fecha`/`turno_hora` today and SB1 moves none of them (that is
    SB2). An added `start`/`end` pair that only new code fills would leave every existing
    writer outside the guarantee. So the interval is a STORED generated column computed from
    the columns every writer already sets:

        occupied_during = tsrange(turno_fecha + turno_hora,
                                  turno_fecha + turno_hora + (service_minutes + buffer_minutes) min,
                                  '[)')

    Half-open, so back-to-back appointments coexist. `service_minutes` (30) and
    `buffer_minutes` (15) are separate columns with owner defaults; travel is not included.

    `tsrange`, not `tstzrange`: the source columns are Buenos Aires wall-clock and always
    have been, and every consumer reads them that way. PostgreSQL would accept an
    instant-valued generated range (`timezone(text, timestamp)` is declared immutable), but a
    STORED instant freezes whatever tzdata said at write time and is never recomputed, so a
    tz-rules update could leave stored instants disagreeing with a fresh conversion. The
    wall-clock range cannot drift. Its interpretation is named by `turno_timezone`, pinned by
    CHECK to the one zone the business uses; Argentina observes no DST, so wall-clock order
    is instant order and overlap is identical under either representation.

Occupancy
    `agenda_occupying` mirrors `schedule._NON_OCCUPYING_ESTADOS` exactly, including its
    `upper()` case handling: CANCELADO and REPROGRAMAR vacate the slot; every other estado
    with a date and time occupies it.

Overlap authority
    EXCLUDE USING gist (profesional_id WITH =, occupied_during WITH &&)
        WHERE (agenda_occupying AND profesional_id IS NOT NULL)
    Needs `btree_gist` for the integer equality. Per professional: two professionals may hold
    the same interval. Holds against every writer, including ones that skip the
    application check.

Attribution (OD-01)
    `profesionales` gains `resource_key` and `is_launch_default` (at most one, by partial
    unique index). A BEFORE INSERT/UPDATE trigger gives an occupying revision that names no
    professional the launch default, and REFUSES the write (SQLSTATE 23514) when none is
    designated — never a silent unattributed booking. One exception, so that legacy data is
    classified rather than re-owned: an already-unattributed occupying row whose interval is
    unchanged by the update keeps NULL. Those rows are the explicit legacy backlog
    (`booking_representation.LegacyClass.OCCUPYING_UNATTRIBUTED`), attributed only by the
    owner-checkpoint backfill. The migration writes no data: it creates no professional and
    attributes no row.

Calendar exceptions (OD-05)
    `calendar_exceptions`: operator-recorded CLOSED_DATE / UNAVAILABLE_INTERVAL. Read by
    nothing yet.

Reversible: downgrade removes exactly what upgrade added, and drops `btree_gist` only if this
migration created it (recorded in the constraint's comment). PostgreSQL only.

Revision ID: 20261007_sb1_booking_repr
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261007_sb1_booking_repr"
down_revision: str = "20261006_clarification_attempts"
branch_labels = None
depends_on = None

_TZ = "America/Argentina/Buenos_Aires"
_SERVICE_MINUTES = 30
_BUFFER_MINUTES = 15
_NON_OCCUPYING = "('CANCELADO', 'REPROGRAMAR')"

_EXCLUSION = "ex_revisions_professional_occupied_overlap"
_TRIGGER = "trg_revisions_sb1_attribute_professional"
_FUNCTION = "sb1_revisions_attribute_professional"
_CREATED_EXTENSION_MARK = "sb1:created-btree_gist"


def _occupying(prefix: str = "") -> str:
    p = prefix
    return (f"({p}turno_fecha IS NOT NULL AND {p}turno_hora IS NOT NULL "
            f"AND upper(coalesce({p}estado_revision, 'PENDIENTE')) NOT IN {_NON_OCCUPYING})")


def _require_postgresql() -> None:
    dialect = op.get_bind().dialect.name
    if dialect != "postgresql":
        raise RuntimeError(f"SB1 booking representation is PostgreSQL-only (got {dialect})")


def upgrade() -> None:
    _require_postgresql()
    bind = op.get_bind()

    had_btree_gist = bind.execute(sa.text(
        "SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")).scalar() is not None
    if not had_btree_gist:
        op.execute("CREATE EXTENSION btree_gist")

    # ── professional resource (OD-01) ────────────────────────────────────────
    op.add_column("profesionales", sa.Column("resource_key", sa.String(64), nullable=True))
    op.add_column("profesionales", sa.Column(
        "is_launch_default", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_unique_constraint("uq_profesionales_resource_key", "profesionales",
                                ["resource_key"])
    op.create_check_constraint("ck_profesionales_launch_default_has_key", "profesionales",
                               "NOT is_launch_default OR resource_key IS NOT NULL")
    op.create_index("uq_profesionales_single_launch_default", "profesionales",
                    ["is_launch_default"], unique=True,
                    postgresql_where=sa.text("is_launch_default"))

    # ── interval parts (OD-03/04) and interpretation ────────────────────────
    op.add_column("revisions", sa.Column(
        "service_minutes", sa.Integer(), nullable=False,
        server_default=sa.text(str(_SERVICE_MINUTES))))
    op.add_column("revisions", sa.Column(
        "buffer_minutes", sa.Integer(), nullable=False,
        server_default=sa.text(str(_BUFFER_MINUTES))))
    op.add_column("revisions", sa.Column(
        "turno_timezone", sa.String(64), nullable=False, server_default=sa.text(f"'{_TZ}'")))
    op.create_check_constraint("ck_revisions_service_minutes_positive", "revisions",
                               "service_minutes > 0")
    op.create_check_constraint("ck_revisions_buffer_minutes_nonneg", "revisions",
                               "buffer_minutes >= 0")
    op.create_check_constraint("ck_revisions_turno_timezone", "revisions",
                               f"turno_timezone = '{_TZ}'")

    # ── derived occupancy and interval ──────────────────────────────────────
    op.execute(f"""
        ALTER TABLE revisions
          ADD COLUMN agenda_occupying boolean
            GENERATED ALWAYS AS {_occupying()} STORED,
          ADD COLUMN occupied_during tsrange
            GENERATED ALWAYS AS (
              CASE WHEN turno_fecha IS NOT NULL AND turno_hora IS NOT NULL THEN
                tsrange(turno_fecha + turno_hora,
                        turno_fecha + turno_hora
                          + make_interval(mins => service_minutes + buffer_minutes),
                        '[)')
              END) STORED
    """)

    # ── overlap authority ───────────────────────────────────────────────────
    op.execute(f"""
        ALTER TABLE revisions ADD CONSTRAINT {_EXCLUSION}
          EXCLUDE USING gist (profesional_id WITH =, occupied_during WITH &&)
          WHERE (agenda_occupying AND profesional_id IS NOT NULL)
    """)
    if not had_btree_gist:
        op.execute(f"COMMENT ON CONSTRAINT {_EXCLUSION} ON revisions "
                   f"IS '{_CREATED_EXTENSION_MARK}'")

    # ── attribution contract ────────────────────────────────────────────────
    op.execute(f"""
        CREATE FUNCTION {_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $fn$
        DECLARE
            default_id integer;
        BEGIN
            IF NOT {_occupying('NEW.')} OR NEW.profesional_id IS NOT NULL THEN
                RETURN NEW;
            END IF;
            -- Legacy backlog: already unattributed, already occupying, interval unchanged.
            -- Classified, not re-owned; only the owner-checkpoint backfill attributes it.
            IF TG_OP = 'UPDATE' AND OLD.profesional_id IS NULL AND {_occupying('OLD.')}
               AND OLD.turno_fecha = NEW.turno_fecha AND OLD.turno_hora = NEW.turno_hora
               AND OLD.service_minutes = NEW.service_minutes
               AND OLD.buffer_minutes = NEW.buffer_minutes THEN
                RETURN NEW;
            END IF;
            SELECT id INTO default_id FROM profesionales WHERE is_launch_default;
            IF default_id IS NULL THEN
                RAISE EXCEPTION 'occupying revision has no professional and no launch-default professional is designated'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'sb1_occupying_revision_requires_professional',
                          TABLE = 'revisions';
            END IF;
            NEW.profesional_id := default_id;
            RETURN NEW;
        END
        $fn$
    """)
    op.execute(f"""
        CREATE TRIGGER {_TRIGGER}
          BEFORE INSERT OR UPDATE ON revisions
          FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
    """)

    # ── operator calendar exceptions (OD-05) ────────────────────────────────
    op.create_table(
        "calendar_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("exception_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False,
                  server_default=sa.text(f"'{_TZ}'")),
        sa.Column("profesional_id", sa.Integer(), sa.ForeignKey("profesionales.id"),
                  nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("kind IN ('CLOSED_DATE', 'UNAVAILABLE_INTERVAL')",
                           name="ck_calendar_exceptions_kind"),
        sa.CheckConstraint(
            "(kind = 'CLOSED_DATE' AND start_time IS NULL AND end_time IS NULL) OR "
            "(kind = 'UNAVAILABLE_INTERVAL' AND start_time IS NOT NULL "
            "AND end_time IS NOT NULL AND start_time < end_time)",
            name="ck_calendar_exceptions_shape"),
        sa.CheckConstraint(f"timezone = '{_TZ}'", name="ck_calendar_exceptions_timezone"),
    )
    op.create_index("ix_calendar_exceptions_date", "calendar_exceptions", ["exception_date"])


def downgrade() -> None:
    _require_postgresql()
    bind = op.get_bind()

    created_extension = bind.execute(sa.text(
        "SELECT obj_description(c.oid, 'pg_constraint') FROM pg_constraint c "
        "WHERE c.conname = :name"), {"name": _EXCLUSION}).scalar() == _CREATED_EXTENSION_MARK

    op.drop_index("ix_calendar_exceptions_date", table_name="calendar_exceptions")
    op.drop_table("calendar_exceptions")

    op.execute(f"DROP TRIGGER {_TRIGGER} ON revisions")
    op.execute(f"DROP FUNCTION {_FUNCTION}()")
    op.execute(f"ALTER TABLE revisions DROP CONSTRAINT {_EXCLUSION}")
    op.execute("ALTER TABLE revisions DROP COLUMN occupied_during, DROP COLUMN agenda_occupying")

    op.drop_constraint("ck_revisions_turno_timezone", "revisions", type_="check")
    op.drop_constraint("ck_revisions_buffer_minutes_nonneg", "revisions", type_="check")
    op.drop_constraint("ck_revisions_service_minutes_positive", "revisions", type_="check")
    op.drop_column("revisions", "turno_timezone")
    op.drop_column("revisions", "buffer_minutes")
    op.drop_column("revisions", "service_minutes")

    op.drop_index("uq_profesionales_single_launch_default", table_name="profesionales")
    op.drop_constraint("ck_profesionales_launch_default_has_key", "profesionales",
                       type_="check")
    op.drop_constraint("uq_profesionales_resource_key", "profesionales", type_="unique")
    op.drop_column("profesionales", "is_launch_default")
    op.drop_column("profesionales", "resource_key")

    if created_extension:
        op.execute("DROP EXTENSION btree_gist")
