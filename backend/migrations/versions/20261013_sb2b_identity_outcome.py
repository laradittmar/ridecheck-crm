"""SB2B — the identity outcome of a booking, persisted with the booking.

SB2A-R4 decides, inside the booking transaction, what happens to the person identity a
booking submits (ADMITTED | FILLED | UNCHANGED | CONFLICT). Until now that decision lived only
in the returned result and in a log line, so a CONFLICT — the booking stands on the canonical
lead and the submitted contact details were NOT applied — could not be found again. SB2B
writes it on the ledger row in the same transaction as the booking (D1 §8.6, §11).

Exactly one column, additive and nullable: existing rows keep NULL ("not recorded"), no
existing value changes, no constraint is added. The downgrade drops only this column.

Revision ID: 20261013_sb2b_identity_outcome
"""
from alembic import op
import sqlalchemy as sa

revision: str = "20261013_sb2b_identity_outcome"
down_revision: str = "20261009_sb2a_r2_ack_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("SB2B is PostgreSQL-only")
    op.add_column("booking_commands",
                  sa.Column("identity_outcome", sa.String(16), nullable=True))


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("SB2B is PostgreSQL-only")
    op.drop_column("booking_commands", "identity_outcome")
