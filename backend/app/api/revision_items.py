from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Revision
from ..schemas.revisions import RevisionOut, RevisionUpdate
from ..services.db_errors import commit_or_400
from .revisions import _apply_revision_update

router = APIRouter(prefix="/revisions", tags=["revisions"])


@router.patch("/{revision_id}", response_model=RevisionOut)
def update_revision(revision_id: int, payload: RevisionUpdate, db: Session = Depends(get_db)):
    revision = db.get(Revision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found")

    _apply_revision_update(db, revision, payload)
    commit_or_400(db, detail="No se pudo guardar la revisión: revisá longitudes y valores permitidos")
    db.refresh(revision)
    return revision
