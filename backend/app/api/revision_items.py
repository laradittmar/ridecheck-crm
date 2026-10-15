from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Lead, Revision
from ..schemas.revisions import RevisionAppointmentApprovalUpdate, RevisionOut, RevisionUpdate
from ..services.db_errors import commit_or_400
from .revisions import _apply_revision_update

router = APIRouter(prefix="/revisions", tags=["revisions"])
api_router = APIRouter(prefix="/api/revisions", tags=["revisions"])


#: Convergence Phase 4 (CD-05): the columns that create, move or remove schedule-visible
#: occupancy. This endpoint is not a booking authority: a change to any of them is refused
#: here, before anything is written, and belongs to `PATCH /leads/{id}/revisions/latest`
#: (BookingAuthority, keyed by `If-Match`/`Idempotency-Key`).
_BOOKING_FIELDS = ("turno_fecha", "turno_hora", "estado_revision")


def _booking_fields_changed(revision: Revision, payload: RevisionUpdate) -> list[str]:
    from ..services.booking_authority import normalize_status
    data = payload.model_dump(exclude_unset=True)
    changed = []
    for field in _BOOKING_FIELDS:
        if field not in data:
            continue
        new, cur = data[field], getattr(revision, field)
        if field == "estado_revision":
            new = normalize_status(new) or new
            cur = normalize_status(cur) or cur
        if new != cur:
            changed.append(field)
    return changed


@router.patch("/{revision_id}", response_model=RevisionOut)
def update_revision(revision_id: int, payload: RevisionUpdate, db: Session = Depends(get_db)):
    revision = db.get(Revision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    changed = _booking_fields_changed(revision, payload)
    if changed:
        from ..services.booking_attempt_events import record_attempt_event
        db.rollback()
        record_attempt_event(db, source="API", stage="PRE_EXECUTE", outcome="REFUSED",
                             reason="BOOKING_CHANGE_REQUIRES_AUTHORITY",
                             attempt_ref=f"revision-items:{revision_id}:{','.join(changed)}",
                             revision_id=revision_id)
        return JSONResponse(status_code=409, content={"detail": {
            "code": "BOOKING_CHANGE_REQUIRES_AUTHORITY",
            "fields": changed,
            "message": ("Este endpoint no modifica turnos. Usá PATCH "
                        "/leads/{lead_id}/revisions/latest con If-Match o Idempotency-Key."),
        }})

    _apply_revision_update(db, revision, payload)
    commit_or_400(db, detail="No se pudo guardar la revisiÃ³n: revisÃ¡ longitudes y valores permitidos")
    db.refresh(revision)
    return revision


@api_router.patch("/{revision_id}/appointment-approval", response_model=RevisionOut)
def update_revision_appointment_approval(
    revision_id: int,
    payload: RevisionAppointmentApprovalUpdate,
    db: Session = Depends(get_db),
):
    revision = db.get(Revision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    if payload.status == "APPROVED":
        revision.appointment_approval_status = "APPROVED"
        revision.appointment_approved_at = datetime.utcnow()
        if revision.lead_id:
            lead = db.get(Lead, revision.lead_id)
            if lead and lead.estado == "COORDINAR_DISPONIBILIDAD":
                lead.estado = "AGENDADO"
    else:
        revision.appointment_approval_status = "REJECTED"

    commit_or_400(db, detail="No se pudo guardar la revisiÃ³n: revisÃ¡ longitudes y valores permitidos")
    db.refresh(revision)
    return revision
