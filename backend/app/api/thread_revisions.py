from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..repositories.thread_revisions import ThreadRevisionRepository
from ..schemas.thread_revisions import ThreadRevisionCreateIn, ThreadRevisionCreateOut, ThreadRevisionOut, ThreadRevisionPatch
from ..services.thread_revisions import ThreadRevisionService

router = APIRouter(prefix="/api/revisions", tags=["thread-revisions"])


def get_thread_revision_service(db: Session = Depends(get_db)) -> ThreadRevisionService:
    return ThreadRevisionService(repository=ThreadRevisionRepository(db=db))


@router.post("", response_model=ThreadRevisionCreateOut)
def create_thread_revision(
    payload: ThreadRevisionCreateIn,
    service: ThreadRevisionService = Depends(get_thread_revision_service),
):
    revision = service.create_revision(payload)
    return ThreadRevisionCreateOut(revision_id=revision.id)


@router.patch("/{revision_id}", response_model=ThreadRevisionOut)
def patch_thread_revision(
    revision_id: int,
    payload: ThreadRevisionPatch,
    service: ThreadRevisionService = Depends(get_thread_revision_service),
):
    return service.patch_revision(revision_id=revision_id, payload=payload)
