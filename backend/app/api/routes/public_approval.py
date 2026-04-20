from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import ThreadRevision


router = APIRouter(prefix="/public", tags=["public-approval"])


def _get_revision_or_404(db: Session, revision_id: int) -> ThreadRevision:
    revision = db.get(ThreadRevision, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found")
    return revision


def _validate_token(revision: ThreadRevision, token: str) -> None:
    if revision.appointment_approval_token is None or token != revision.appointment_approval_token:
        raise HTTPException(status_code=400, detail="Invalid token")


@router.post("/revisions/{revision_id}/approve", response_class=HTMLResponse)
def approve_revision_appointment(
    revision_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    revision = _get_revision_or_404(db, revision_id)
    _validate_token(revision, token)

    revision.appointment_approval_status = "APPROVED"
    revision.appointment_approved_at = datetime.utcnow()
    db.add(revision)
    db.commit()

    return HTMLResponse("<html><body><h2>✅ Turno confirmado. ¡Gracias!</h2></body></html>")


@router.post("/revisions/{revision_id}/reject", response_class=HTMLResponse)
def reject_revision_appointment(
    revision_id: int,
    token: str = Query(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    revision = _get_revision_or_404(db, revision_id)
    _validate_token(revision, token)

    revision.appointment_approval_status = "REJECTED"
    db.add(revision)
    db.commit()

    return HTMLResponse(
        "<html><body><h2>Entendido. Un asesor se pondrá en contacto para reagendar.</h2></body></html>"
    )
