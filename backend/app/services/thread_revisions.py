from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import DataError, IntegrityError

from ..models import ThreadRevision
from ..repositories.thread_revisions import ThreadRevisionRepository
from ..schemas.thread_revisions import ThreadRevisionCreateIn, ThreadRevisionPatch


class ThreadRevisionService:
    def __init__(self, repository: ThreadRevisionRepository):
        self.repository = repository

    def create_revision(self, payload: ThreadRevisionCreateIn) -> ThreadRevision:
        thread = self.repository.get_thread(payload.thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        candidate = self.repository.get_candidate(payload.candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if candidate.thread_id != payload.thread_id:
            raise HTTPException(status_code=400, detail="Candidate does not belong to thread")

        revision = ThreadRevision(
            thread_id=payload.thread_id,
            candidate_id=payload.candidate_id,
            status="collecting_data",
        )
        return self._persist_create(revision)

    def patch_revision(self, revision_id: int, payload: ThreadRevisionPatch) -> ThreadRevision:
        revision = self.repository.get_revision(revision_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="Revision not found")

        patch_data = payload.model_dump(exclude_unset=True)
        changed = False
        for field, value in patch_data.items():
            if value is None:
                continue
            setattr(revision, field, value)
            changed = True

        if (
            patch_data.get("appointment_approval_status") == "PENDING"
            and not revision.appointment_approval_token
        ):
            revision.appointment_approval_token = secrets.token_urlsafe(32)
            changed = True

        if changed:
            revision.updated_at = datetime.now(timezone.utc)

        return self._persist_update(revision)

    def _persist_create(self, revision: ThreadRevision) -> ThreadRevision:
        try:
            return self.repository.add_revision(revision)
        except (DataError, IntegrityError) as exc:
            self.repository.db.rollback()
            raise HTTPException(status_code=400, detail="No se pudo crear la revision") from exc

    def _persist_update(self, revision: ThreadRevision) -> ThreadRevision:
        try:
            return self.repository.save_revision(revision)
        except (DataError, IntegrityError) as exc:
            self.repository.db.rollback()
            raise HTTPException(status_code=400, detail="No se pudo actualizar la revision") from exc
