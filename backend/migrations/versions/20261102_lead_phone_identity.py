"""PRELAUNCH-MINIMUM-LAUNCH (D) — one lead per canonical phone identity.

The 2026-11-01 Wild measured NP-09: two concurrent n8n executions for one customer both looked
the phone up (both empty) and both created a lead; the second thread link orphaned the first.
`leads.telefono` had no unique constraint or index of any kind. This revision adds the one
guarantee no caller can bypass:

    CREATE UNIQUE INDEX uq_leads_phone_identity
        ON leads (regexp_replace(telefono, '[^0-9]', '', 'g'))
        WHERE regexp_replace(telefono, '[^0-9]', '', 'g') <> ''

DIGITS ONLY, because the display form keeps a leading '+' (`phone_utils.normalize_phone`), so
a uniqueness rule on the column itself would let "+549…" and "549…" coexist. Leads without a
phone stay legal. `POST /leads` uses this index as its `ON CONFLICT` arbiter
(`services/lead_identity.py`).

R1 (review F-11): the predicate is the IDENTITY being non-empty. A digit-free value ("-",
"s/n") carries NO identity and behaves exactly like NULL for uniqueness — it used to map to
identity '' and collide with every other digit-free value. NULL is excluded too (the
expression is NULL, so the predicate is not true). This is the one rule shared by this index,
the ORM declaration, the ON CONFLICT arbiter and every lookup (`phone_utils`).

**Pre-existing duplicates — REFUSED, never resolved here.** Before any DDL, under a lock that
excludes concurrent inserts, every group of leads sharing one identity is counted. If any
exists the upgrade REFUSES with nothing changed and reports each group by its LEAD IDS and
the group count only. R1 (review F-09): no phone-derived digest is printed — an unsalted hash
of a 13-digit number is brute-forceable in seconds, so it is not anonymisation. It never chooses a winner, merges, deletes
or edits a lead: which row is the real customer is an operator decision. The live database is
known to hold exactly one such group (leads 169/170, NP-09 evidence), so on live this revision
refuses until that group is resolved under separate owner authorisation.

Upgrade — strict, under a lock, in Alembic's single transaction:
  * index absent, no duplicate group        → created                     (CREATED)
  * index present and EXACTLY canonical      → nothing changed            (VERIFIED)
    (`create_all` on PostgreSQL builds the same index, which is how a database built by
     start-up reaches this revision)
  * index present with any other definition → REFUSED, nothing changed
  * any duplicate group                      → REFUSED, nothing changed

Downgrade — drops ONLY the canonical index, and changes no row. The index holds no data of
its own, so there is no data a downgrade could lose; what a downgrade gives up is the
guarantee, and the parent-head code then behaves exactly as before this revision (duplicates
become possible again, nothing existing is touched). An index under this name that is NOT the
canonical one is not this revision's and is REFUSED rather than dropped.

Deployment order: this revision runs BEFORE the image that carries it, because that image's
`schema_preflight.EXPECTED_HEAD` is this revision and it refuses to start below it. The
parent-head image refuses a database AT this revision for the same reason, so a rollback of
the application without a downgrade needs a compatibility image whose EXPECTED_HEAD is this
revision; a rollback to the parent image needs this downgrade first.

Revision ID: 20261102_lead_phone_identity
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import SchemaShapeRefused, require_certified_server

revision: str = "20261102_lead_phone_identity"
down_revision: str = "20261027_w1f_conversation_memory"
branch_labels = None
depends_on = None

TABLE = "leads"
INDEX = "uq_leads_phone_identity"
IDENTITY = "regexp_replace(telefono, '[^0-9]', '', 'g')"
PREDICATE = "regexp_replace(telefono, '[^0-9]', '', 'g') <> ''"

#: The index as PostgreSQL 16 deparses it (`pg_get_indexdef`), schema-qualified by the
#: catalogue. Measured on the certified server; a differing definition is not adopted.
CANONICAL_INDEXDEF = (
    "CREATE UNIQUE INDEX uq_leads_phone_identity ON {schema}.leads USING btree "
    "(regexp_replace((telefono)::text, '[^0-9]'::text, ''::text, 'g'::text)) "
    "WHERE (regexp_replace((telefono)::text, '[^0-9]'::text, ''::text, 'g'::text) <> ''::text)"
)

_DDL = f"CREATE UNIQUE INDEX {INDEX} ON {TABLE} ({IDENTITY}) WHERE {PREDICATE}"


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing before any change — nothing was created, altered or "
        "dropped, no lead was touched and the Alembic version is unchanged. Do not repair "
        "by script: capture this message for the owner. Details: " + "; ".join(problems))


def _lock(bind) -> None:
    # SHARE ROW EXCLUSIVE: no concurrent INSERT/UPDATE/DELETE on leads between the duplicate
    # scan and the index build, so a duplicate cannot appear in between.
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE"))


def _indexdef(bind):
    return bind.execute(sa.text(
        "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = :n AND c.relnamespace = current_schema()::regnamespace"),
        {"n": INDEX}).scalar()


def _canonical(bind) -> str:
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar()
    return CANONICAL_INDEXDEF.format(schema=schema)


def duplicate_groups(bind) -> list:
    """Every identity held by more than one lead, as its [lead ids] only. Read only. Nothing
    derived from the phone leaves the database (R1, F-09)."""
    rows = bind.execute(sa.text(
        f"SELECT array_agg(id ORDER BY id) FROM {TABLE} WHERE {PREDICATE} "
        f"GROUP BY {IDENTITY} HAVING count(*) > 1 ORDER BY min(id)")).all()
    return [list(r[0]) for r in rows]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    _lock(bind)

    found = _indexdef(bind)
    if found is not None:
        if found != _canonical(bind):
            _refuse(f"an index named {INDEX} exists and is not the canonical one",
                    [f"found: {found}", f"expected: {_canonical(bind)}"])
        print(f"{revision}: {INDEX}=VERIFIED", flush=True)
        return

    groups = duplicate_groups(bind)
    if groups:
        _refuse(f"{len(groups)} group(s) of leads share one phone identity; this revision "
                "never chooses a winner, merges, deletes or edits a lead — resolve each group "
                "under owner authorisation, then run it again",
                [f"group {n}: lead_ids={ids}" for n, ids in enumerate(groups, 1)])

    op.execute(_DDL)
    after = _indexdef(bind)
    if after != _canonical(bind):
        _refuse("the index does not read back as the canonical definition",
                [f"found: {after}"])
    print(f"{revision}: {INDEX}=CREATED", flush=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    _lock(bind)
    found = _indexdef(bind)
    if found is None:
        print(f"{revision}: {INDEX} absent — nothing to drop", flush=True)
        return
    if found != _canonical(bind):
        raise RuntimeError(
            f"{revision} downgrade REFUSED: an index named {INDEX} exists and is not the "
            f"canonical one this revision creates ({found}). It is not this revision's to "
            "drop. Nothing was dropped and no lead was touched.")
    op.execute(f"DROP INDEX {INDEX}")
    print(f"{revision}: {INDEX} DROPPED (no row changed)", flush=True)
