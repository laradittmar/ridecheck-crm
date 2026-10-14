"""Convergence Phase 1 — join the two migration heads. No schema or data change.

Track A (Gate 3 offer authority) and Track B (Smart Booking authority) each grew a migration
chain from `20261006_clarification_attempts`:

    Track A  20261010_offer_lifecycle -> 20261013_booking_projection
    Track B  20261007_sb1_booking_representation -> 20261008_sb2a_booking_authority
             -> 20261009_sb2a_r2_ack_lease -> 20261013_sb2b_identity_outcome

The two chains create or alter disjoint objects. They share one table, `thread_revisions`:
Track A adds `offer_id` (its FK and partial unique index), and Track B widens
`ck_thread_revisions_status`. Track A writes only `booked` and `provisional`, which both the
old and the widened constraint admit. So neither branch's DDL depends on the other's, and this
revision only records that both have been applied.

Upgrading to it applies whichever branch is missing. To return to the two separate heads,
downgrade to either parent by name; a relative `downgrade -1` from a merge point is ambiguous
in Alembic and is refused. `booking_attempt_events` is NOT added here — it belongs to
convergence Phase 4.
"""
from typing import Sequence, Union

revision: str = "20261014_converge_track_heads"
down_revision: Union[str, Sequence[str], None] = ("20261013_booking_projection", "20261013_sb2b_identity_outcome")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
