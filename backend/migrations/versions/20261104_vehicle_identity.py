"""PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY — `Kilometraje` and `Dominio / patente`.

Two nullable columns on `revisions`, the entity that already owns the reviewed car's make,
model and year, and nothing else:

  revisions.kilometraje  integer, no default. NULL is "not given", never zero.
  revisions.dominio      varchar(16), no default. The canonical form
                         (`services/vehicle_identity`). NOT unique, and no index: one vehicle
                         can have many inspections, and nothing queries it yet.

Nothing is backfilled and no existing row changes. The drafts a customer types before a
booking commits are `whatsapp_thread_evidence` rows under two new claim types, which need no
schema: that is why `thread_revisions`, `whatsapp_thread_candidates` and `slot_recovery_jobs`
are not touched.

Upgrade — strict, under a lock, in Alembic's single transaction:
  * both columns absent                                   → added            (CREATED)
  * both present, EXACTLY canonical, holding no value — the application's `create_all` builds
    the identical columns                                 → adopted (ADOPTED_EMPTY_CANONICAL)
  * both present, EXACTLY canonical, holding values — this revision's own finished state,
    e.g. a version rewound over an upgraded database      → verified     (VERIFIED_CANONICAL)
  * one present and empty and canonical                   → the other added (CREATED, adopting)
  Anything else — a differing type, default, constraint or index; one column present and
  holding values while the other is missing — REFUSES with nothing changed.

Downgrade — never erases a customer's data. It REFUSES while any revision carries either value,
unless that data was first archived and removed under a separately authorised procedure.
Otherwise it drops exactly what is PRESENT, after checking each column is this revision's.

Deployment order: this revision runs BEFORE the image that carries it
(`schema_preflight.EXPECTED_HEAD`), and it is safe for the image below it: that image neither
reads nor writes either column, and both are nullable with no default.

Revision ID: 20261104_vehicle_identity
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import (SchemaShapeRefused, column_fingerprint,
                                             require_certified_server)

revision: str = "20261104_vehicle_identity"
down_revision: str = "20261103_slot_recovery_job"
branch_labels = None
depends_on = None

TABLE = "revisions"
KILOMETRAJE = "kilometraje"
DOMINIO = "dominio"


def _canonical(type_):
    """A nullable column of `type_`: no default, not identity or generated, default collation,
    no comment or options. Frozen, measured on PostgreSQL 16 (immutable history: never edit)."""
    return (("type", type_), ("not_null", False), ("identity", ""), ("generated", ""),
            ("default_collation", True), ("default", None), ("comment", None),
            ("options", None))


#: name -> (the fingerprint it must have, the column it is created as).
COLUMNS = {
    KILOMETRAJE: (_canonical("integer"), sa.Integer()),
    DOMINIO: (_canonical("character varying(16)"), sa.String(16)),
}


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing before any change — nothing was created, altered or "
        "dropped and the Alembic version is unchanged. Capture this message and a schema-only "
        "dump for the owner. Differences: " + "; ".join(problems or ["-"]))


def _lock_and_read(bind) -> dict:
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))
    found = {name: column_fingerprint(bind, TABLE, name) for name in COLUMNS}
    if any(fp is None for fp in found.values()):
        _refuse(f"{TABLE} is absent", ["object absent"])
    return found


def _present(fp) -> bool:
    return fp["column"] is not None


def _is_canonical(name: str, fp) -> bool:
    return (fp["column"] == COLUMNS[name][0] and not fp["column_constraints"]
            and not fp["column_indexes"])


def _held(bind, name: str) -> int:
    return int(bind.execute(sa.text(
        f"SELECT count(*) FROM {TABLE} WHERE {name} IS NOT NULL")).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    found = _lock_and_read(bind)
    for name, fp in found.items():
        if _present(fp) and not _is_canonical(name, fp):
            _refuse(f"{TABLE}.{name} exists and is not exactly the canonical column",
                    [f"found: {fp['column']!r}", f"constraints: {fp['column_constraints']!r}",
                     f"indexes: {fp['column_indexes']!r}"])
    present = [name for name, fp in found.items() if _present(fp)]
    held = {name: _held(bind, name) for name in present}
    if len(present) == len(COLUMNS):
        print(f"{revision}: VERIFIED_CANONICAL" if any(held.values())
              else f"{revision}: ADOPTED_EMPTY_CANONICAL", flush=True)
        return
    if any(held.values()):
        _refuse("part of this revision's schema already exists and holds data while the rest "
                "is missing — its provenance is unknown",
                [f"{TABLE}.{name} rows={count}" for name, count in held.items()])
    for name, (_, type_) in COLUMNS.items():
        if name in present:
            continue
        op.add_column(TABLE, sa.Column(name, type_, nullable=True))
        if not _is_canonical(name, column_fingerprint(bind, TABLE, name)):
            _refuse(f"{TABLE}.{name} is not exactly canonical after creation", ["-"])
    print(f"{revision}: CREATED" if not present
          else f"{revision}: CREATED (adopting the empty canonical part present)", flush=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    found = _lock_and_read(bind)
    present = [name for name, fp in found.items() if _present(fp)]
    held = {name: _held(bind, name) for name in present}
    if any(held.values()):
        raise RuntimeError(
            f"{revision} downgrade REFUSED: customer vehicle data is present "
            f"({', '.join(f'{TABLE}.{k}={v}' for k, v in held.items())}). Archive it under a "
            "separately authorised procedure first. Nothing was dropped.")
    for name in present:
        if not _is_canonical(name, found[name]):
            _refuse(f"{TABLE}.{name} is not exactly this revision's column, so it is not "
                    "dropped", [f"found: {found[name]['column']!r}"])
    for name in present:
        op.drop_column(TABLE, name)
    print(f"{revision}: DROPPED {', '.join(present) or 'nothing (absent)'} "
          "(no row changed; every column was empty)", flush=True)
