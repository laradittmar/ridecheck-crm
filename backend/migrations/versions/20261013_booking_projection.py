"""G3-7R — one offer, at most one booking, guaranteed by the database.

Additive. One nullable column, one foreign key, one partial unique index, and two
bookkeeping columns on the offer table. No existing column, constraint, index or row is
touched, and nothing is backfilled.

WHY A CONSTRAINT AND NOT MORE APPLICATION CODE. The G3-7P-R3A independent review bypassed
the acceptance row lock and the conditional `UPDATE ... WHERE status = 'ISSUED'` — the two
guards the offer authority relied on — and measured the result on real PostgreSQL: two
concurrent confirmations of ONE offer produced TWO bookings in 6 of 6 trials, at two
different slots that were both genuinely free, so no calendar conflict caught them either.
The entire 6,000-test suite stayed identity-identical while those guards were gone. The
same review then prototyped this index and measured one booking in 6 of 6 trials with both
guards STILL removed, the loser stopped by the database.

    The advisory lock is coordination. The exclusion constraint is the guarantee.

WHY PARTIAL. `offer_id` is NULL for every booking that predates this migration, and for
every booking written by the authenticated operator/machine endpoint
(`PATCH /api/revisions/{id}`), which is a legitimate authority and not a customer accepting
an offer. A plain UNIQUE would allow exactly one such booking in the whole table; the
`WHERE offer_id IS NOT NULL` predicate lets any number of them coexist while still
admitting at most one booking per offer.

REPLAY BOOKKEEPING. `replay_count` / `last_replay_at` exist because a replay is not a
refusal, and until G3-7R the system recorded it as one. The architecture audit measured a
Flow completion arriving at the ConversationEngine after the Data Exchange writer had
already committed the booking: it left the offer reading
`ACCEPTED, refused_count=1, last_refusal='OFFER_ALREADY_ACCEPTED'` — the shape of a
rejected submission, on the happy path — and told the customer their session had expired.

BACKWARD-AWARE. The currently deployed image (GIT_SHA 840bce0) does not know any of this
exists: it never writes `thread_revisions.offer_id`, so every booking it creates leaves the
column NULL and the partial index never applies to it. Applying this migration ahead of the
image is therefore safe. The reverse — the new image against a database without these
columns — fails closed at the booking write rather than booking anything, which is the
correct direction for a safety control.

Revision ID: 20261013_booking_projection
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261013_booking_projection"
down_revision: str | None = "20261010_offer_lifecycle"
branch_labels = None
depends_on = None

#: Mirrors `offer_lifecycle.BOOKING_PROJECTION_CONSTRAINT`. The one function that translates
#: a violation of this index into a typed domain outcome matches on this exact name, so the
#: name is part of the contract and not a cosmetic choice.
_PROJECTION_INDEX = "uq_thread_revision_one_booking_per_offer"

#: R2/RV-04. Imported from the model rather than repeated, so an Alembic-built schema and a
#: `metadata.create_all()` schema cannot disagree about the constraint's identifier. They
#: did: `fk_thread_revisions_offer_id` here against SQLAlchemy's implicit
#: `thread_revisions_offer_id_fkey`, while `downgrade()` drops by this one.
from app.models import BOOKING_PROJECTION_FK as _PROJECTION_FK


def upgrade() -> None:
    # ── the booking projection ───────────────────────────────────────────────
    op.add_column("thread_revisions",
                  sa.Column("offer_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        _PROJECTION_FK, "thread_revisions",
        "whatsapp_booking_offers", ["offer_id"], ["id"])
    # THE guarantee. Partial, so historical and operator-written bookings keep NULL.
    op.create_index(_PROJECTION_INDEX, "thread_revisions", ["offer_id"], unique=True,
                    postgresql_where=sa.text("offer_id IS NOT NULL"),
                    sqlite_where=sa.text("offer_id IS NOT NULL"))

    # ── replay bookkeeping, so a replay is not recorded as a refusal ─────────
    op.add_column("whatsapp_booking_offers",
                  sa.Column("replay_count", sa.Integer(), nullable=False,
                            server_default=sa.text("0")))
    op.add_column("whatsapp_booking_offers",
                  sa.Column("last_replay_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("whatsapp_booking_offers", "last_replay_at")
    op.drop_column("whatsapp_booking_offers", "replay_count")
    op.drop_index(_PROJECTION_INDEX, table_name="thread_revisions")
    op.drop_constraint(_PROJECTION_FK, "thread_revisions", type_="foreignkey")
    op.drop_column("thread_revisions", "offer_id")
