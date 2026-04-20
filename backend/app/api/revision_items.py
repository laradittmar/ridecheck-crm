from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Revision
from ..schemas.revisions import RevisionAppointmentApprovalUpdate, RevisionOut, RevisionUpdate
from ..services.db_errors import commit_or_400
from .revisions import _apply_revision_update

router = APIRouter(prefix="/revisions", tags=["revisions"])
api_router = APIRouter(prefix="/api/revisions", tags=["revisions"])


@router.patch("/{revision_id}", response_model=RevisionOut)
def update_revision(revision_id: int, payload: RevisionUpdate, db: Session = Depends(get_db)):
    revision = db.get(Revision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

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
    else:
        revision.appointment_approval_status = "REJECTED"

    commit_or_400(db, detail="No se pudo guardar la revisiÃ³n: revisÃ¡ longitudes y valores permitidos")
    db.refresh(revision)
    return revision
