"""SB2A-R2 — acknowledgement lease on the booking ledger.

SB2A's acknowledgement moved PENDING/FAILED → SENDING → SENT/FAILED with no lease: a process
that died after claiming left the row in SENDING forever, and nothing could ever send that
receipt (SB2A-R-03). These columns make the claim a bounded lease with an owner:

    ack_claim_token       the claimant's identity; only it may record the outcome
    ack_claimed_at        when the current claim was taken
    ack_lease_expires_at  after this an unfinished SENDING may be reclaimed
    ack_transport_id      the transport's message identity (WAMID) once sent
    ack_last_error        error category of the last failed attempt
    ack_duplicate_risk    TRUE once a SENDING lease had to be reclaimed: the earlier attempt
                          may have reached Meta before dying, so a resend may duplicate.
                          Recorded, never hidden — exactly-once external delivery is not
                          claimed.

Additive and reversible; no existing value changes.

Revision ID: 20261009_sb2a_r2_ack_lease
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261009_sb2a_r2_ack_lease"
down_revision: str = "20261008_sb2a_booking_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("SB2A-R2 is PostgreSQL-only")
    op.add_column("booking_commands", sa.Column("ack_claim_token", sa.String(36), nullable=True))
    op.add_column("booking_commands", sa.Column("ack_claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("booking_commands", sa.Column("ack_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("booking_commands", sa.Column("ack_transport_id", sa.String(128), nullable=True))
    op.add_column("booking_commands", sa.Column("ack_last_error", sa.String(64), nullable=True))
    op.add_column("booking_commands", sa.Column(
        "ack_duplicate_risk", sa.Boolean(), nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("SB2A-R2 is PostgreSQL-only")
    for col in ("ack_duplicate_risk", "ack_last_error", "ack_transport_id",
                "ack_lease_expires_at", "ack_claimed_at", "ack_claim_token"):
        op.drop_column("booking_commands", col)
