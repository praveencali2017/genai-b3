from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_restaurant_ordering_assistant.models import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        return self._session.scalars(
            select(User).where(User.username == username)
        ).one_or_none()
