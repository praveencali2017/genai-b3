from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.models import Document
from ai_restaurant_ordering_assistant.models.enums import DocumentStatus


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> Sequence[Document]:
        return self._session.scalars(
            select(Document).order_by(Document.filename, Document.version.desc())
        ).all()

    def active_ids(self) -> list[int]:
        return list(
            self._session.scalars(
                select(Document.id).where(Document.status == DocumentStatus.ACTIVE)
            )
        )

    def find_by_sha256(self, sha256: str) -> Document | None:
        return self._session.scalars(
            select(Document).where(Document.sha256 == sha256)
        ).one_or_none()

    def latest_version(self, filename: str) -> int:
        """The highest version stored for `filename`, 0 if there is none."""
        return (
            self._session.scalar(
                select(func.coalesce(func.max(Document.version), 0)).where(
                    Document.filename == filename
                )
            )
            or 0
        )

    def lock_active(self, filename: str) -> Sequence[Document]:
        """`SELECT ... FOR UPDATE` the active versions of a filename."""
        return self._session.scalars(
            select(Document)
            .where(
                Document.filename == filename,
                Document.status == DocumentStatus.ACTIVE,
            )
            .with_for_update()
        ).all()

    def add(self, document: Document) -> Document:
        self._session.add(document)
        self._session.flush()
        return document
