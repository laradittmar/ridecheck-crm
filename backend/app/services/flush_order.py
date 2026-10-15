"""Convergence Phase 4 (CD-07) — rule K-E, enforced for every session: the thread state is
taken BEFORE the lead.

Lock order K puts `whatsapp_thread_states` (K1) before `leads` (K8). SQLAlchemy's unit of work
does not know that: `Lead` and `WhatsAppThreadState` have no ORM dependency between them, so its
alphabetical tie-break flushes `UPDATE leads` before `UPDATE whatsapp_thread_states` whatever
order the attributes were assigned in (E5, measured). A transaction that writes both therefore
takes K8 before K1, and against a booking that holds K1 and wants K8 that is a deadlock (E4,
`40P01` 5/5 measured; CD-07).

One `before_flush` listener on `Session` makes that order structural, without touching any
call site:

1. **Same flush.** When a flush carries a changed `WhatsAppThreadState` AND a changed `Lead`,
   the listener first takes those state rows `FOR NO KEY UPDATE`, sorted by `thread_id`, on the
   session's own connection — so K1 is held before the unit of work writes the lead.
2. **Across flushes.** Within one transaction, once a `Lead` has been written, a thread state
   that this transaction never locked may not be written: the flush is refused with a typed
   `LockOrderViolation` BEFORE any SQL. (A state already locked in this transaction — by an
   earlier flush, or by an explicit K1 acquisition registered through
   `note_thread_states_locked` — may be written freely.)
3. **Unaffected:** a flush that changes only leads, only thread states, or neither; and every
   transaction that takes K1 first (the booking authority, the offer lifecycle, the
   Conversation Engine's bounded entry), which register their acquisitions here.

Session-safe and test-isolated: the bookkeeping lives in `session.info`, scoped to the root
`SessionTransaction` object, so a new transaction starts clean and sessions never share it.
Row locks are taken on PostgreSQL only; on SQLite (tests) the ordering rule 2 is still checked,
so a violating path fails the suite rather than deadlocking production.

Not covered, by construction: raw-SQL `text()` writes (they bypass the unit of work). The only
raw thread-state writers (`quote_followup`, `buscando_followup`, `unanswered_alert`) write no
lead in the same transaction.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import event, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_INFO_KEY = "_convergence_k_e"
_INSTALLED = False


class LockOrderViolation(RuntimeError):
    """A transaction tried to write a thread state after it had written a lead without first
    taking that thread state (rule K-E). Typed: `code` is LOCK_ORDER_VIOLATION."""
    code = "LOCK_ORDER_VIOLATION"

    def __init__(self, thread_ids):
        self.thread_ids = tuple(thread_ids)
        super().__init__(
            f"LOCK_ORDER_VIOLATION: whatsapp_thread_states {list(self.thread_ids)} written "
            "after leads in one transaction without being taken first (rule K-E, CD-07)")


def _state(session: Session) -> dict:
    root = session.get_transaction()
    st = session.info.get(_INFO_KEY)
    if st is None or st.get("tx") is not root:
        st = {"tx": root, "locked": set(), "lead_written": False}
        session.info[_INFO_KEY] = st
    return st


def note_thread_states_locked(session: Session, thread_ids: Iterable) -> None:
    """Record an explicit K1 acquisition in this session's current transaction."""
    try:
        _state(session)["locked"].update(int(t) for t in thread_ids if t is not None)
    except Exception:                                   # bookkeeping must never fail a lock
        logger.debug("K-E bookkeeping skipped", exc_info=True)


def _changed(session: Session, obj) -> bool:
    return (obj in session.new or obj in session.deleted
            or session.is_modified(obj, include_collections=False))


def _before_flush(session: Session, flush_context, instances) -> None:
    from ..models import Lead, WhatsAppThreadState
    objects = list(session.new) + list(session.dirty) + list(session.deleted)
    states = [o for o in objects if isinstance(o, WhatsAppThreadState) and _changed(session, o)]
    lead_changed = any(isinstance(o, Lead) and _changed(session, o) for o in objects)
    if not states and not lead_changed:
        return
    st = _state(session)
    existing = sorted({int(o.thread_id) for o in states
                       if o not in session.new and o.thread_id is not None} - st["locked"])
    if existing:
        if st["lead_written"]:
            logger.error("LOCK_ORDER_VIOLATION thread_ids=%s — refused before any SQL", existing)
            raise LockOrderViolation(existing)
        if lead_changed and session.get_bind().dialect.name == "postgresql":
            # K1 before the unit of work writes the lead (which it would do first)
            session.connection().execute(
                text("SELECT thread_id FROM whatsapp_thread_states "
                     "WHERE thread_id = ANY(:ids) ORDER BY thread_id FOR NO KEY UPDATE"),
                {"ids": existing})
    st["locked"].update(int(o.thread_id) for o in states if o.thread_id is not None)
    if lead_changed:
        st["lead_written"] = True


def install() -> None:
    """Register the listener once, for every `Session`."""
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _before_flush)
    _INSTALLED = True
