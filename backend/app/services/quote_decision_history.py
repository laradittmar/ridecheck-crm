"""WILD1-C — the quote-decision history an operator sees on the lead card (read only).

The engine records each explicit quote decision as an append-only `whatsapp_thread_evidence`
row (`claim_type='quote_decision'`, `producer='ENGINE'`, value DECLINED | RECONSIDERED,
reason = the `motivo_perdida` at the time). The lead's CURRENT state is its flag; this is
the history beside it, so "declined for price at 13:58, reconsidered at 14:01, now
ACEPTADO" is unambiguous.

WILD1-C-R3 (WC-02). The ONE writer here is `record_operator_flag_action`: every operator or
API action that can affect `leads.flag` / `leads.motivo_perdida` appends an OPERATOR row to
the same `quote_decision` history, in the SAME transaction as the mutation (the caller
commits both or neither). The engine may automatically reactivate a PERDIDO only when the
newest decision row is its own DECLINED with the same motivo; an operator row is newer than
it by construction, so any operator intervention after an engine decline revokes the
engine's authority to reactivate — there is no implicit return of control.
"""
from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..models import WhatsAppThread, WhatsAppThreadEvidence, WhatsAppThreadState

QUOTE_DECISION_CLAIM = "quote_decision"
_BA = ZoneInfo("America/Argentina/Buenos_Aires")

#: operator-facing labels (Spanish, like the rest of the board)
DECISION_LABELS = {
    "DECLINED": "Presupuesto rechazado por el cliente",
    "RECONSIDERED": "Presupuesto retomado por el cliente",
    "OPERATOR_FLAG_ACTION": "Flag modificado por un operador",
}

#: WILD1-C-R3: the operator-provenance row's value and producers. The engine's own rows
#: carry producer ENGINE; anything else is not the engine's and revokes reactivation.
OPERATOR_FLAG_ACTION = "OPERATOR_FLAG_ACTION"
PRODUCER_OPERATOR_UI = "OPERATOR_UI"
PRODUCER_OPERATOR_API = "OPERATOR_API"
_OPERATOR_VERSION = "wild1-c-r3/1"


def record_operator_flag_action(db, lead, *, action: str, route: str, producer: str,
                                flag_before: Optional[str],
                                motivo_before: Optional[str]) -> int:
    """Append one OPERATOR provenance row per thread of `lead`. Returns the rows added.

    Called by every operator/API path that can affect the lead's flag or loss reason —
    AFTER it mutated the lead and BEFORE it commits, so the row and the mutation share one
    transaction. It never commits, never swallows an error: if the row cannot be written the
    caller's commit is never reached and the mutation rolls back with it.

    Append-only: a fresh `claim_id` per action (re-confirming the same PERDIDO twice is two
    interventions). A lead with no thread gets no row — the engine has no thread on which
    it could have declined or could reactivate, so there is nothing to revoke."""
    if lead is None or getattr(lead, "id", None) is None:
        return 0
    threads = db.execute(select(WhatsAppThread.id).where(
        WhatsAppThread.lead_id == lead.id).order_by(WhatsAppThread.id)).scalars().all()
    if not threads:
        return 0
    action_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    added = 0
    for thread_id in threads:
        cycle = db.execute(select(WhatsAppThreadState.current_cycle_start_message_db_id)
                           .where(WhatsAppThreadState.thread_id == thread_id)).scalar()
        cycle_key = cycle if isinstance(cycle, int) else 0
        claim_id = hashlib.sha256("|".join((
            "operator-flag-action:v1", str(thread_id), action_id)).encode("utf-8")
        ).hexdigest()[:64]
        db.add(WhatsAppThreadEvidence(
            thread_id=thread_id, cycle_key=cycle_key, turn_id=None,
            claim_type=QUOTE_DECISION_CLAIM, value=OPERATOR_FLAG_ACTION,
            evidence_class="OPERATOR", producer=str(producer)[:64],
            producer_version=_OPERATOR_VERSION, status="CONFIRMED", outcome="ACCEPT",
            reason=(str(getattr(lead, "motivo_perdida", None))[:48]
                    if getattr(lead, "motivo_perdida", None) else None),
            source_message_ids=[],
            validated_as={"action": str(action), "route": str(route),
                          "flag_before": flag_before, "flag_after": getattr(lead, "flag", None),
                          "motivo_before": motivo_before,
                          "motivo_after": getattr(lead, "motivo_perdida", None)},
            claim_id=claim_id, created_at=now))
        added += 1
    db.flush()
    return added


def flag_fields(lead) -> tuple:
    """(flag, motivo_perdida) — the two fields whose change is an operator intervention."""
    return (getattr(lead, "flag", None), getattr(lead, "motivo_perdida", None))


def lead_quote_decisions(db, lead_ids: Iterable[int]) -> dict:
    """{lead_id: [(decision, motivo, created_at, producer), ...]} in recording order."""
    ids = [int(i) for i in (lead_ids or ()) if i is not None]
    if not ids:
        return {}
    rows = db.execute(
        select(WhatsAppThread.lead_id, WhatsAppThreadEvidence.value,
               WhatsAppThreadEvidence.reason, WhatsAppThreadEvidence.created_at,
               WhatsAppThreadEvidence.producer)
        .join(WhatsAppThreadEvidence, WhatsAppThreadEvidence.thread_id == WhatsAppThread.id)
        .where(WhatsAppThread.lead_id.in_(ids),
               WhatsAppThreadEvidence.claim_type == QUOTE_DECISION_CLAIM)
        .order_by(WhatsAppThreadEvidence.id)).all()
    out: dict = defaultdict(list)
    for lead_id, value, reason, created_at, producer in rows:
        out[lead_id].append((value, reason, created_at, producer))
    return dict(out)


def history_lines(entries) -> list:
    """Plain-text lines (the caller escapes them) for one lead's decisions."""
    lines = []
    for value, reason, created_at, producer in entries or ():
        label = DECISION_LABELS.get(str(value), str(value))
        when = ""
        if isinstance(created_at, datetime):
            stamp = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            when = stamp.astimezone(_BA).strftime("%d/%m %H:%M")
        if value == OPERATOR_FLAG_ACTION:
            # The operator's own action: no "automático", and the motivo only as context.
            detail = str(reason) if reason else ""
        else:
            detail = ", ".join(p for p in (str(reason) if reason else "",
                                           "automático" if producer == "ENGINE" else "") if p)
        lines.append(f"{label}" + (f" ({detail})" if detail else "") + (f" · {when}" if when else ""))
    return lines
