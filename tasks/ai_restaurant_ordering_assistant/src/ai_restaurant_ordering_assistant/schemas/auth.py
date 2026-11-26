from typing import Self

from pydantic import BaseModel

from ai_restaurant_ordering_assistant.models import User
from ai_restaurant_ordering_assistant.models.enums import Role


class MeResponse(BaseModel):
    id: int
    username: str
    role: Role

    @classmethod
    def from_user(cls, user: User) -> Self:
        return cls(id=user.id, username=user.username, role=user.role)
