from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_travel_agent.models import PendingAction


class PendingActionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, action: PendingAction) -> PendingAction:
        self._session.add(action)
        self._session.flush()
        return action

    def lock(self, action_id: int) -> PendingAction | None:
        """`SELECT ... FOR UPDATE`: a second confirmation of the same action
        waits here until the first has committed, then sees its outcome."""
        return self._session.scalars(
            select(PendingAction)
            .where(PendingAction.id == action_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()

    def list_for_session(self, session_id: int) -> Sequence[PendingAction]:
        return self._session.scalars(
            select(PendingAction).where(PendingAction.session_id == session_id)
        ).all()
