"""PRELAUNCH-MINIMUM-LAUNCH (D) — one lead per canonical phone identity.

The live Wild of 2026-11-01 measured NP-09: two concurrent n8n executions for one customer's
opening burst both ran `GET /leads?telefono=` (both empty), both ran `POST /leads`, and the
second `POST …/link` re-pointed the thread at the second lead, orphaning the first as a
nameless CONSULTA_NUEVA card. Lead find/create/link is per-message and UPSTREAM of the
20-second debounce, so any opening burst of more than one message (voice notes make that
common) can reproduce it.

How the guarantee is made:

* **The database is the authority.** A partial UNIQUE index on the digits-only expression of
  `leads.telefono` (`phone_utils.PHONE_IDENTITY_SQL`), excluding values whose identity is
  empty, so a lead without a phone stays legal and "+549…" / "549…" cannot coexist.
* **Digit-free values have NO identity (R1, review F-11).** "-", "s/n" or any non-empty value
  without a digit behaves exactly like NULL: it is stored as typed, never deduplicated, never
  collides, and never matches a lookup. Nothing invents an identity for it. The index
  predicate, the ORM declaration, the ON CONFLICT arbiter and `phone_identity_clause` all use
  the one rule `regexp_replace(telefono, '[^0-9]', '', 'g') <> ''`.
* **Every caller that can assign a phone resolves or refuses deterministically** (R1):
  `POST /leads` returns the existing lead; the CRM's create and edit forms answer a typed
  `LEAD_PHONE_IDENTITY_CONFLICT` page naming the lead that holds the phone (nothing changed);
  the Conversation Engine's website-form path assigns the phone only when no other lead holds
  it (`assign_phone_if_free`).
* **Logs carry lead ids only (R1, review F-09)** — never the digits and never a digest of
  them: an unsalted hash of a 13-digit phone is brute-forceable and is not anonymisation.
* **The create is idempotent.** On PostgreSQL `INSERT … ON CONFLICT (<identity>) WHERE
  <predicate> DO NOTHING RETURNING id`: the insert that wins returns its id; the loser waits on
  the index for the winner's commit, inserts nothing, and then reads the winner's row in a new
  statement snapshot. Both racing executions therefore return THE SAME lead id, and both
  thread links write the same value — no orphan.
* **Nothing is overwritten.** An existing lead is returned exactly as it is: no field is
  filled, cleared or regressed by a create request that lost a race.
* **No winner is chosen for history.** Pre-existing duplicate groups are not merged, deleted or
  edited here; the migration refuses until they are resolved (see its docstring).

SQLite (tests only) has no `regexp_replace` and no index; the lookup there matches the two
stored display forms a normalised value can take (digits, or '+' and digits), which is every
value `normalize_phone` produces. Concurrency is proven on PostgreSQL only.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from ..models import Lead
from .phone_utils import PHONE_IDENTITY_PREDICATE, PHONE_IDENTITY_SQL, canonical_phone_identity

logger = logging.getLogger(__name__)

INDEX_NAME = "uq_leads_phone_identity"


def _postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def phone_identity_clause(db: Session, telefono: Optional[str]):
    """A WHERE clause matching leads whose canonical phone identity equals `telefono`'s.
    Matches nothing when `telefono` carries no digits."""
    identity = canonical_phone_identity(telefono)
    if identity is None:
        return text("1 = 0")
    if _postgres(db):
        return text(f"({PHONE_IDENTITY_SQL} = :phone_identity AND {PHONE_IDENTITY_PREDICATE})"
                    ).bindparams(phone_identity=identity)
    return or_(Lead.telefono == identity, Lead.telefono == f"+{identity}")


def find_lead_by_phone(db: Session, telefono: Optional[str]) -> Optional[Lead]:
    """The lead holding this canonical phone identity, or None. Read only.

    With the index in place there is at most one; without it (a database not yet migrated)
    the OLDEST is returned, deterministically, and nothing is written."""
    if canonical_phone_identity(telefono) is None:
        return None
    stmt = select(Lead).where(phone_identity_clause(db, telefono))
    return db.execute(stmt.order_by(Lead.id).limit(1)).scalars().first()


def find_or_create_lead(db: Session, *, telefono: Optional[str], values: dict
                        ) -> "tuple[Lead, bool]":
    """Return (lead, created). `telefono` is the already-normalised display value and is
    stored as given; `values` are the other columns of a NEW lead only.

    Commits. Without a phone identity this is a plain insert (a lead without a phone is
    legal and is never deduplicated)."""
    identity = canonical_phone_identity(telefono)
    if identity is None:
        lead = Lead(telefono=telefono, **values)
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead, True

    if not _postgres(db):
        existing = find_lead_by_phone(db, telefono)
        if existing is not None:
            return existing, False
        lead = Lead(telefono=telefono, **values)
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead, True

    from sqlalchemy.dialects.postgresql import insert
    row = dict(values)
    row["telefono"] = telefono
    # Python-side column defaults (created_at, estado, necesita_humano) are applied by the
    # Core insert exactly as the ORM would apply them.
    stmt = (insert(Lead.__table__).values(**row)
            .on_conflict_do_nothing(index_elements=[text(PHONE_IDENTITY_SQL)],
                                    index_where=text(PHONE_IDENTITY_PREDICATE))
            .returning(Lead.__table__.c.id))
    inserted = db.execute(stmt).scalar()
    db.commit()
    if inserted is not None:
        lead = db.get(Lead, int(inserted))
        logger.info("LEAD_CREATED lead_id=%s", inserted)
        return lead, True
    existing = find_lead_by_phone(db, telefono)
    if existing is None:                       # pragma: no cover - the index says otherwise
        raise RuntimeError("lead identity conflict reported but no lead holds the identity")
    logger.info("LEAD_IDENTITY_REUSED lead_id=%s — a concurrent or earlier create already "
                "holds this phone; nothing written", existing.id)
    return existing, False


#: The typed, operator-visible refusal when a phone already belongs to another lead.
LEAD_PHONE_IDENTITY_CONFLICT = "LEAD_PHONE_IDENTITY_CONFLICT"


def identity_holder(db: Session, telefono: Optional[str], *,
                    exclude_lead_id: Optional[int] = None) -> Optional[int]:
    """The id of the lead (other than `exclude_lead_id`) holding `telefono`'s canonical
    identity, or None — also None when `telefono` has no identity. Read only."""
    if canonical_phone_identity(telefono) is None:
        return None
    stmt = select(Lead.id).where(phone_identity_clause(db, telefono))
    if exclude_lead_id is not None:
        stmt = stmt.where(Lead.id != int(exclude_lead_id))
    # never autoflush a pending phone edit into the unique index from inside a read
    with db.no_autoflush:
        found = db.execute(stmt.order_by(Lead.id).limit(1)).scalar()
    return int(found) if found is not None else None


def assign_phone_if_free(db: Session, lead: Lead, telefono: Optional[str]) -> str:
    """Give `lead` (which has no phone) the phone `telefono`, or refuse deterministically.

    Returns ASSIGNED, NO_IDENTITY (digit-free: stored as typed, like NULL for uniqueness),
    HELD_BY_OTHER (another lead holds the identity: nothing assigned), or CONTENDED (the
    lead row could not be written within a bounded wait: nothing assigned). Never raises an
    IntegrityError into the caller's turn: on PostgreSQL the write is a conditional UPDATE in
    its own short transaction, so the unique index arbitrates a concurrent creator there, and
    the caller's session only learns the committed value. On SQLite (tests only) a read check
    precedes the in-session assignment."""
    from sqlalchemy.exc import DBAPIError, IntegrityError
    from sqlalchemy.orm.attributes import set_committed_value
    if not telefono:
        return "NO_IDENTITY"
    if canonical_phone_identity(telefono) is None:
        lead.telefono = telefono
        return "NO_IDENTITY"
    if identity_holder(db, telefono, exclude_lead_id=lead.id) is not None:
        return "HELD_BY_OTHER"
    if not _postgres(db) or lead.id is None:
        lead.telefono = telefono
        return "ASSIGNED"
    from .booking_ce_adapter import isolated_session
    try:
        with isolated_session(db) as s:
            s.execute(text("SET LOCAL lock_timeout = '1500ms'"))
            res = s.execute(text("UPDATE leads SET telefono = :t WHERE id = :i AND "
                                 "(telefono IS NULL OR telefono = '')"),
                            {"t": telefono, "i": int(lead.id)})
            s.commit()
            if res.rowcount != 1:
                return "HELD_BY_OTHER"
    except IntegrityError:
        return "HELD_BY_OTHER"
    except DBAPIError:
        return "CONTENDED"
    set_committed_value(lead, "telefono", telefono)
    return "ASSIGNED"
