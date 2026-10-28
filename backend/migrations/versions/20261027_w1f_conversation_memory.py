"""W1F-W-01 R1+R2 — conversation memory: the evidence a turn produced, and what we asked.

ONE additive migration for BOTH tables, by owner decision (OC-R1 §15.3): R1's evidence
schema (architectural review §8.2 + §8.3) and R2's outstanding-question schema (§8.1) land
together, in one migration window, because asking the owner to renew the window twice for
two halves of one defect is a cost with no benefit. Nothing existing is altered or dropped.

    whatsapp_thread_evidence          every typed claim a turn produced, ACCEPTED OR
                                      REFUSED, with role, cycle, turn and provenance. The
                                      persistence of `schemas/claims.ClaimEvidence`, which
                                      already carries every one of these attributes in
                                      memory — not a new model.
    whatsapp_outstanding_questions     every question the system asked, with its SIX
                                      explicit states and the Meta delivery status its
                                      activation rests on. OC-R2 CORRECTION 1: acceptance
                                      by Meta (a WAMID) and delivery to the recipient are
                                      different facts, so `PENDING_DELIVERY` owns nothing
                                      and only an observed `delivered`/`read` promotes a
                                      row to `OUTSTANDING`; `failed` ends it as
                                      `UNDELIVERED`.
    whatsapp_thread_candidates
      + zone_evidence_id              which evidence row set this candidate's location
      + vehicle_evidence_id           which evidence row set its identity
                                      (both `ON DELETE SET NULL`, review §8.3)

**Why the two foreign keys are SET NULL and `outbound_message_id` is no key at all.**
G37R-RV-03 measured a tester reset failing `23503` on exactly this shape: the certified
reset (`services/tester_reset.py`) deletes candidates and `whatsapp_messages` BEFORE the
thread, so a `NO ACTION` reference from a surviving row blocks the whole reset and deletes
nothing. `20261022_operator_notifications` drew the conclusion in its own docstring —
"HISTORICAL scalar ids — never a foreign key, so a tester reset is never blocked" — and this
revision follows it: the thread references are the only `NO ACTION` keys (the reset deletes
threads last, and R2 adds both tables to it before the messages), every reference to a
candidate or an evidence row is `SET NULL`, and the gate's ledger row is a scalar.

Upgrade — strict, before any DDL, under a lock, in Alembic's single transaction. Per object:
  * absent                                   → created canonically          (CREATED)
  * present, EMPTY and EXACTLY canonical      → adopted as is               (ADOPTED_EMPTY)
    (the application's `create_all` produces the same shape, which is how a database built
     by start-up rather than by Alembic reaches this revision)
  * present, EXACTLY canonical, with rows     → this revision's own finished state, e.g. a
                                                version rewound over an upgraded database →
                                                nothing changed, rows kept  (VERIFIED)
Anything else — a differing column, constraint, index, trigger, rule or policy — REFUSES
with nothing changed, nothing dropped and the Alembic version untouched.

Downgrade — **data-safe, and it refuses rather than deletes.** With any row in either table
it REFUSES: those rows are the record of what the customer was told and what the system
decided about what they said, and `superseded_by_id` means the history is not reconstructible
from canonical state. An EMPTY pair is dropped, and the two candidate columns are dropped
only when every value in them is NULL.

**What a rollback actually requires once these tables hold rows** — stated here because the
refusal message is read by an operator in a recovery window, and the earlier wording was
ambiguous enough to mislead one. Both tables are unread when `EVIDENCE_ADMISSION_ENABLED` /
`OUTSTANDING_QUESTION_ENABLED` are OFF (review §15: "the two new tables are additive and
unread when the flags are OFF"), so turning the flags off restores the committed behaviour
**on an image that accepts this head** — the integrated image with the flags off, or the
certified compatibility image built for this head. It does **not** make the parent-head
image startable: that image's `schema_preflight.EXPECTED_HEAD` is `down_revision`, and it
refuses a database at this revision. So a behavioural rollback keeps this schema; returning
to the parent schema means archiving and removing the evidence rows under separate
authorisation, and only then running this downgrade.

Revision ID: 20261027_w1f_conversation_memory
"""
from alembic import op
import sqlalchemy as sa

from app.services.schema_fingerprint import (SchemaShapeRefused, differences,
                                             require_certified_server, table_fingerprint)

revision: str = "20261027_w1f_conversation_memory"
down_revision: str = "20261022_operator_notifications"
branch_labels = None
depends_on = None

EVIDENCE = "whatsapp_thread_evidence"
QUESTIONS = "whatsapp_outstanding_questions"
CANDIDATES = "whatsapp_thread_candidates"

#: OC-R2 CORRECTION 1: six states, because acceptance by Meta and delivery to the
#: recipient are different facts. A WAMID proves the first; only an observed `delivered` or
#: `read` status proves the second, and only the second may be ACTIVE. The literals here are
#: pinned to the ORM's by a test.
OQ_STATUSES = ("PENDING_DELIVERY", "OUTSTANDING", "ANSWERED", "SUPERSEDED", "ABANDONED",
               "UNDELIVERED")
OQ_KINDS = ("INSPECTION_LOCATION", "VEHICLE_IDENTITY")
OQ_ASKED_BY = ("REPLY_MODEL", "DETERMINISTIC_TEMPLATE", "FLOW", "OPERATOR")
EV_OUTCOMES = ("ACCEPT", "CLARIFY", "HOLD", "NEEDS_HUMAN")

#: The two additive candidate columns, by name — imported by the test that pins this
#: revision's literals to the ORM's.
CANDIDATE_COLUMNS = ("zone_evidence_id", "vehicle_evidence_id")


def _in_list(column: str, values) -> str:
    """`col IN ('A', 'B')` — the CHECK form, built from the one tuple of permitted values."""
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


_EVIDENCE_DDL = f"""
CREATE TABLE {EVIDENCE} (
    id                     SERIAL PRIMARY KEY,
    thread_id              INTEGER NOT NULL REFERENCES whatsapp_threads(id),
    cycle_key              INTEGER NOT NULL,
    turn_id                VARCHAR(64),
    claim_type             VARCHAR(48) NOT NULL,
    role                   VARCHAR(32),
    value                  TEXT,
    normalized_value       VARCHAR(160),
    evidence_class         VARCHAR(32),
    producer               VARCHAR(64),
    producer_version       VARCHAR(32),
    status                 VARCHAR(16),
    polarity               VARCHAR(16),
    explicitness           VARCHAR(16),
    outcome                VARCHAR(16) NOT NULL,
    reason                 VARCHAR(48),
    source_message_ids     JSONB,
    validated_as           JSONB,
    claim_id               VARCHAR(64) NOT NULL,
    bound_to_candidate_id  INTEGER REFERENCES {CANDIDATES}(id) ON DELETE SET NULL,
    bound_at               TIMESTAMP WITH TIME ZONE,
    superseded_by_id       INTEGER REFERENCES {EVIDENCE}(id) ON DELETE SET NULL,
    created_at             TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_thread_evidence_claim UNIQUE (thread_id, claim_id),
    CONSTRAINT ck_thread_evidence_outcome CHECK ({_in_list('outcome', EV_OUTCOMES)})
)
"""

_QUESTIONS_DDL = f"""
CREATE TABLE {QUESTIONS} (
    id                        SERIAL PRIMARY KEY,
    thread_id                 INTEGER NOT NULL REFERENCES whatsapp_threads(id),
    cycle_key                 INTEGER NOT NULL,
    question_kind             VARCHAR(32) NOT NULL,
    expected_slots            JSONB,
    asked_by                  VARCHAR(32) NOT NULL,
    identity                  VARCHAR(36) NOT NULL,
    outbound_message_id       INTEGER,
    wa_message_id             VARCHAR(128),
    asked_turn_id             VARCHAR(64),
    candidate_id              INTEGER REFERENCES {CANDIDATES}(id) ON DELETE SET NULL,
    status                    VARCHAR(16) NOT NULL,
    asked_at                  TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_after_turns       INTEGER NOT NULL DEFAULT 3,
    turns_observed            INTEGER NOT NULL DEFAULT 0,
    delivery_status           VARCHAR(16),
    activated_at              TIMESTAMP WITH TIME ZONE,
    answered_by_claim_id      VARCHAR(64),
    answered_by_evidence_id   INTEGER REFERENCES {EVIDENCE}(id) ON DELETE SET NULL,
    superseded_by_id          INTEGER REFERENCES {QUESTIONS}(id) ON DELETE SET NULL,
    closed_reason             VARCHAR(48),
    closed_at                 TIMESTAMP WITH TIME ZONE,
    created_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_outstanding_question_identity UNIQUE (identity),
    CONSTRAINT ck_outstanding_question_kind CHECK ({_in_list('question_kind', OQ_KINDS)}),
    CONSTRAINT ck_outstanding_question_status CHECK ({_in_list('status', OQ_STATUSES)}),
    CONSTRAINT ck_outstanding_question_asked_by
        CHECK ({_in_list('asked_by', OQ_ASKED_BY)}),
    CONSTRAINT ck_outstanding_question_expiry CHECK (expires_after_turns >= 1)
)
"""

_INDEXES = (
    f"CREATE INDEX ix_thread_evidence_lookup ON {EVIDENCE} "
    f"(thread_id, cycle_key, claim_type)",
    f"CREATE INDEX ix_thread_evidence_unbound ON {EVIDENCE} "
    f"(thread_id, cycle_key, bound_to_candidate_id)",
    f"CREATE INDEX ix_outstanding_question_live ON {QUESTIONS} "
    f"(thread_id, cycle_key, status)",
    # OC-R2 CORRECTION 1. The status webhook arrives keyed by WAMID and nothing else, so
    # activation looks the question up by `wa_message_id`. Without this index that lookup is
    # a sequential scan on every status callback for every message the system ever sent.
    f"CREATE INDEX ix_outstanding_question_wamid ON {QUESTIONS} (wa_message_id)",
    # Review §8.1 / R2 scope: "at most the contractually permitted active question may own a
    # field at a time". The PARTIAL unique index is the guarantee; the service's supersede
    # step is coordination in front of it, exactly as `whatsapp_booking_offers` does for
    # ISSUED offers. Two concurrent asks cannot both leave a live row behind.
    #
    # OC-R2 CORRECTION 1: the predicate stays `status = 'OUTSTANDING'` and that is now
    # load-bearing in a second way — a `PENDING_DELIVERY` row deliberately does NOT own the
    # field, so several accepted-but-undelivered asks may coexist and the first one Meta
    # confirms is the one that takes ownership.
    f"CREATE UNIQUE INDEX uq_outstanding_question_one_live_per_field ON {QUESTIONS} "
    f"(thread_id, cycle_key, question_kind) WHERE status = 'OUTSTANDING'",
)


def _refuse(why, problems):
    raise SchemaShapeRefused(
        f"{revision}: {why}. Refusing before any change — nothing was created, altered or "
        "dropped, no value was touched and the Alembic version is unchanged. Do not repair "
        "by script: capture this message for the owner "
        "(docs/operations/RECOVERY_MANIFEST.md §6). Differences: " + "; ".join(problems))


def _rows(bind, table) -> int:
    return bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() or 0


def _lock(bind) -> None:
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    bind.execute(sa.text(f"LOCK TABLE {CANDIDATES} IN SHARE ROW EXCLUSIVE MODE"))


def _candidate_columns(bind) -> dict:
    rows = bind.execute(sa.text(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_name = :t "
        "AND column_name = ANY(:c)"), {"t": CANDIDATES, "c": list(CANDIDATE_COLUMNS)})
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    _lock(bind)

    if table_fingerprint(bind, CANDIDATES) is None:
        _refuse(f"{CANDIDATES} is absent", ["object absent"])

    branches = {}
    # ── the evidence table first: the questions table references it ──────────────
    for table, ddl in ((EVIDENCE, _EVIDENCE_DDL), (QUESTIONS, _QUESTIONS_DDL)):
        found = table_fingerprint(bind, table)
        if found is None:
            op.execute(ddl)
            branches[table] = "CREATED"
        else:
            # An existing table is adopted only when it is EXACTLY what this revision
            # would have created. The comparison is the deployed one (`create_all` builds
            # the same shape), and the row count only decides which branch is reported —
            # never whether a differing shape is acceptable.
            branches[table] = ("VERIFIED" if _rows(bind, table) else "ADOPTED_EMPTY")

    for statement in _INDEXES:
        name = statement.split(" ON ")[0].split()[-1]
        exists = bind.execute(sa.text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}).first()
        if not exists:
            op.execute(statement)

    # ── the two additive candidate columns (review §8.3) ─────────────────────────
    present = _candidate_columns(bind)
    for column in CANDIDATE_COLUMNS:
        if column in present:
            kind, nullable, default = present[column]
            if kind != "integer" or nullable != "YES" or default is not None:
                _refuse(f"{CANDIDATES}.{column} exists and is not the nullable integer "
                        "this revision adds",
                        [f"{column}: {kind} nullable={nullable} default={default!r}"])
            continue
        op.execute(f"ALTER TABLE {CANDIDATES} ADD COLUMN {column} INTEGER NULL")
        op.execute(f"ALTER TABLE {CANDIDATES} ADD CONSTRAINT "
                   f"fk_{CANDIDATES}_{column} FOREIGN KEY ({column}) "
                   f"REFERENCES {EVIDENCE}(id) ON DELETE SET NULL")

    after = _candidate_columns(bind)
    missing = [c for c in CANDIDATE_COLUMNS if c not in after]
    if missing:
        _refuse("the additive candidate columns are absent after the upgrade", missing)
    for table in (EVIDENCE, QUESTIONS):
        if table_fingerprint(bind, table) is None:
            _refuse(f"{table} is absent after the upgrade", ["object absent"])

    print(f"{revision}: {EVIDENCE}={branches[EVIDENCE]} "
          f"{QUESTIONS}={branches[QUESTIONS]} candidates=+"
          f"{len([c for c in CANDIDATE_COLUMNS if c not in present])}", flush=True)


def downgrade() -> None:
    """Data-safe: it refuses rather than deletes. See the module docstring."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(f"{revision} is PostgreSQL-only")
    require_certified_server(bind, revision)
    _lock(bind)

    held = {}
    for table in (QUESTIONS, EVIDENCE):
        if table_fingerprint(bind, table) is not None:
            count = _rows(bind, table)
            if count:
                held[table] = count
    if held:
        raise RuntimeError(
            f"{revision} downgrade REFUSED: "
            + ", ".join(f"{t} holds {n} row(s)" for t, n in held.items())
            + ". Those rows are the record of what the customer was told and what the "
              "system decided about what they said, and `superseded_by_id` means the "
              "history is not reconstructible from canonical state. "
              "NO EVIDENCE ROW WAS DELETED and nothing was dropped. "
              f"LEAVE THE DATABASE AT {revision}. "
              "A behavioural rollback keeps this schema and changes the APPLICATION: it "
              "requires an image whose schema preflight accepts this head. Two are valid "
              "fallbacks — the integrated image with EVIDENCE_ADMISSION_ENABLED, "
              "OUTSTANDING_QUESTION_ENABLED and LOCALITY_RESOLUTION_SAFETY_ENABLED off, "
              "and the certified compatibility image built for this head. The "
              f"parent-head image ({down_revision}) is NOT valid against this schema and "
              "will refuse to start. "
              f"Returning to {down_revision} is a separate, separately authorised "
              "operation: archive and remove the evidence rows first, then run this "
              "downgrade again.")

    present = _candidate_columns(bind)
    for column in CANDIDATE_COLUMNS:
        if column not in present:
            continue
        set_rows = bind.execute(sa.text(
            f"SELECT count(*) FROM {CANDIDATES} WHERE {column} IS NOT NULL")).scalar() or 0
        if set_rows:
            raise RuntimeError(
                f"{revision} downgrade REFUSED: {CANDIDATES}.{column} is set on "
                f"{set_rows} candidate(s) — dropping it discards which evidence set that "
                "candidate's value. Nothing was dropped.")
        op.execute(f"ALTER TABLE {CANDIDATES} DROP CONSTRAINT IF EXISTS "
                   f"fk_{CANDIDATES}_{column}")
        op.execute(f"ALTER TABLE {CANDIDATES} DROP COLUMN {column}")

    # The questions table references the evidence table, so it goes first.
    for table in (QUESTIONS, EVIDENCE):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    print(f"{revision}: DROPPED (both tables empty, no candidate reference set)",
          flush=True)
