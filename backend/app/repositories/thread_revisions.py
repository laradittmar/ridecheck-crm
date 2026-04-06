from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import ThreadRevision, WhatsAppThread, WhatsAppThreadCandidate


class ThreadRevisionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_thread(self, thread_id: int) -> WhatsAppThread | None:
        return self.db.get(WhatsAppThread, thread_id)

    def get_candidate(self, candidate_id: int) -> WhatsAppThreadCandidate | None:
        return self.db.get(WhatsAppThreadCandidate, candidate_id)

    def get_revision(self, revision_id: int) -> ThreadRevision | None:
        return self.db.get(ThreadRevision, revision_id)

    def add_revision(self, revision: ThreadRevision) -> ThreadRevision:
        self.db.add(revision)
        self.db.commit()
        self.db.refresh(revision)
        return revision

    def save_revision(self, revision: ThreadRevision) -> ThreadRevision:
        self.db.add(revision)
        self.db.commit()
        self.db.refresh(revision)
        return revision
