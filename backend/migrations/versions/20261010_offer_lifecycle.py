"""Add whatsapp_booking_offers — G3-7P canonical booking-offer lifecycle.

The G3-7N independent review proved that `whatsapp_thread_states.flow_booking_token` cannot
be both the offer and the authority over it. The booking path's check was

    if expected and flow_token and flow_token != expected:   # warn / refuse

so a NULL `expected` short-circuited the whole comparison — and NULL is the RESTING STATE of
that column after six different production writes (booking consumption, cycle reset, the
rejected-offer handoff, the motorcycle handoff, the text-path correction withdrawal, and
booking_flow_service's own consumption). Measured on real PostgreSQL: with `expected IS
NULL` the path accepted the old consumed token, a fabricated token, an empty token and NO
TOKEN AT ALL, each creating a ThreadRevision and a CRM Revision. G3-7N's tombstone closed
one of those six doors; this table closes the question itself by moving authority out of a
nullable string.

Two invariants become database constraints, which is the point — they hold against callers
that do not exist yet:

    UNIQUE (token_fingerprint)                     one token -> at most one offer
    UNIQUE (thread_id) WHERE status = 'ISSUED'     at most one LIVE offer per thread

The second is what makes supersession a fact: two concurrent issues cannot both leave a live
offer behind, and "the previous offer is no longer live" is not a convention a handler can
forget. Acceptance is a conditional UPDATE off 'ISSUED' under a row lock, so at most one
transaction can ever accept a given offer.

The raw token is never stored. `token_fingerprint` is sha256(token): enough to match a
submission, useless to replay, safe to render to an operator.

ADDITIVE AND BACKWARD-AWARE. It creates one table and touches no existing column, index,
constraint or row. The currently deployed image (GIT_SHA 840bce0) does not know this table
exists and keeps using `flow_booking_token` exactly as before, so applying this migration
ahead of the image is safe; the reverse — running the new image against a database without
this table — fails closed at `claim()` rather than booking anything, which is the correct
direction for a safety control. The downgrade drops only what the upgrade created.

Revision ID: 20261010_offer_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261010_offer_lifecycle"
down_revision: str = "20261006_clarification_attempts"
branch_labels = None
depends_on = None

#: Mirrors `OfferStatus` in `app/services/offer_lifecycle.py`. Literal SQL so an operator
#: meets a readable constraint in the database itself.
_STATUSES = "('ISSUED', 'ACCEPTED', 'WITHDRAWN', 'SUPERSEDED', 'EXPIRED')"


def upgrade() -> None:
    op.create_table(
        "whatsapp_booking_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("thread_id", sa.Integer(),
                  sa.ForeignKey("whatsapp_threads.id"), nullable=False),
        sa.Column("cycle_key", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(),
                  sa.ForeignKey("whatsapp_thread_candidates.id"), nullable=True),
        sa.Column("tipo_vehiculo", sa.String(40), nullable=True),
        sa.Column("zone_group", sa.String(40), nullable=True),
        sa.Column("zone_detail", sa.String(120), nullable=True),
        sa.Column("offered_date", sa.Date(), nullable=True),
        sa.Column("offered_time", sa.Time(), nullable=True),
        sa.Column("flow_id", sa.String(40), nullable=True),
        sa.Column("contract_version", sa.String(24), nullable=False),
        sa.Column("token_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(64), nullable=True),
        sa.Column("refused_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("last_refusal", sa.String(64), nullable=True),
        sa.Column("last_refused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_revision_id", sa.Integer(),
                  sa.ForeignKey("revisions.id"), nullable=True),
        sa.Column("accepted_thread_revision_id", sa.Integer(),
                  sa.ForeignKey("thread_revisions.id"), nullable=True),
        sa.UniqueConstraint("token_fingerprint", name="uq_booking_offer_token_fingerprint"),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_booking_offer_status"),
        sa.CheckConstraint("(status = 'ISSUED') = (terminal_at IS NULL)",
                           name="ck_booking_offer_terminal_at"),
        sa.CheckConstraint("status = 'ACCEPTED' OR accepted_at IS NULL",
                           name="ck_booking_offer_accepted_at"),
    )
    op.create_index("ix_booking_offers_thread_id", "whatsapp_booking_offers", ["thread_id"])
    op.create_index("ix_booking_offer_thread_status", "whatsapp_booking_offers",
                    ["thread_id", "status"])
    # The live-offer invariant. Partial, so a thread may hold any number of TERMINAL offers
    # as history while never holding two live ones.
    op.create_index("uq_booking_offer_one_live_per_thread", "whatsapp_booking_offers",
                    ["thread_id"], unique=True,
                    postgresql_where=sa.text("status = 'ISSUED'"),
                    sqlite_where=sa.text("status = 'ISSUED'"))


def downgrade() -> None:
    op.drop_index("uq_booking_offer_one_live_per_thread",
                  table_name="whatsapp_booking_offers")
    op.drop_index("ix_booking_offer_thread_status", table_name="whatsapp_booking_offers")
    op.drop_index("ix_booking_offers_thread_id", table_name="whatsapp_booking_offers")
    op.drop_table("whatsapp_booking_offers")
