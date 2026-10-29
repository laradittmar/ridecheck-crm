"""WILD1-F1 — the one authority over automatic booking dispatch.

The 2026-10-29 natural Wild proved `SMART_BOOKING_CE_ENABLED=false` did not stop a
booking offer being issued or a Booking Flow being dispatched. The flag was tested in
exactly two places — the CE booking *writer* (`conversation_engine._smart_booking_ce_on`,
used at the `_sb_refusal` OFF switch) and the Data Exchange *confirm* step
(`booking_flow_service._smart_booking_enabled`) — and the **dispatch** sat in front of
both. A customer asking "podes hoy?" therefore received a real Flow and a real offer row
while the system was supposed to be inert.

This module is the single boundary. It is deliberately tiny, import-light and stateless so
that every automatic dispatch site can call it without creating an import cycle, and so a
reviewer can read the whole invariant in one screen.

WHAT IT GOVERNS — the *automatic*, customer-facing booking path only:

  * issuing a `whatsapp_booking_offers` row (`OfferLifecycle.issue`), which is the backstop
    a direct or internal caller cannot bypass;
  * minting a booking token / Flow context and dispatching the published Booking Flow
    (`ConversationEngine._send_booking_flow`);
  * the legacy data-collection Flow, whose token `_process_flow_response` also accepts and
    which therefore is a booking offer too.

WHAT IT MUST NOT GOVERN — the *operator* authority. `BookingAuthority.execute` is reached
from `ui/kanban_actions.py` and `api/revisions.py`, where a logged-in human or a machine
credential books by hand. `docs/architecture/BOOKING_UX_CONTRACT.md` and CLAUDE.md both
record that as a legitimate authority which does not pass through the offer path. Gating
`execute` on SMART_BOOKING_CE_ENABLED would take the operator's own booking away whenever
the automatic path is off, which is the opposite of what the flag means. The automatic
writers are already refused upstream, at `conversation_engine.py` (`_sb_refusal`,
`SMART_BOOKING_DISABLED`) and `booking_flow_service.py` (the confirm OFF switch); this
module does not duplicate those and does not weaken them.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The typed, observable reason. Shared with the CE writer and the Data Exchange confirm
#: step so one grep finds every refusal of this kind, whichever layer produced it.
SMART_BOOKING_DISABLED = "SMART_BOOKING_DISABLED"


class BookingDispatchDisabled(RuntimeError):
    """Raised by the backstop when an offer would be issued while dispatch is OFF.

    It is an error, not a return value, precisely because the backstop exists for callers
    that did not ask permission. A caller that *can* answer the customer politely checks
    `booking_dispatch_allowed()` first and never provokes this.
    """

    reason = SMART_BOOKING_DISABLED

    def __init__(self, source: str, thread_id: int | None = None):
        self.source = source
        self.thread_id = thread_id
        super().__init__(
            "booking dispatch refused (%s): SMART_BOOKING_CE_ENABLED is not true "
            "[source=%s thread_id=%s]" % (SMART_BOOKING_DISABLED, source, thread_id)
        )


def booking_dispatch_allowed(settings=None) -> bool:
    """True only when SMART_BOOKING_CE_ENABLED is exactly the boolean True.

    The `is True` test is the same one the CE writer and the Data Exchange confirm step
    apply, and it is the reason a missing, malformed or merely truthy value reads as OFF.
    `settings` may be passed by a caller that already holds one (CE holds `self.settings`);
    otherwise the cached factory is used — never a bare `Settings()`, which reads no
    environment and would answer for the defaults instead of this process.
    """
    if settings is None:
        from ..settings import get_settings
        settings = get_settings()
    return getattr(settings, "smart_booking_ce_enabled", False) is True


def assert_booking_dispatch_allowed(*, source: str, thread_id: int | None = None,
                                    settings=None) -> None:
    """Backstop. Raises `BookingDispatchDisabled` unless dispatch is allowed."""
    if booking_dispatch_allowed(settings):
        return
    logger.warning(
        "BOOKING_DISPATCH_REFUSED reason=%s source=%s thread_id=%s — "
        "no offer, no token, no Flow",
        SMART_BOOKING_DISABLED, source, thread_id,
    )
    raise BookingDispatchDisabled(source, thread_id)


def note_offer_issued_while_dispatch_off(*, source: str, thread_id: int | None = None,
                                         settings=None) -> bool:
    """Record an offer issued while dispatch is OFF. Returns True when it noted one.

    The product paths refuse before reaching `OfferLifecycle.issue`, so this firing means
    a caller outside them created an offer in a posture that forbids one. It is logged at
    ERROR and raised as an operational condition so it surfaces on the operations view
    rather than only in a log file — see the comment at the call site for why this
    observes instead of refusing, and what it would cost to make it refuse.
    """
    if booking_dispatch_allowed(settings):
        return False
    logger.error(
        "BOOKING_OFFER_ISSUED_WHILE_DISPATCH_OFF reason=%s source=%s thread_id=%s — "
        "an offer was created in a posture that forbids one",
        SMART_BOOKING_DISABLED, source, thread_id,
    )
    return True
