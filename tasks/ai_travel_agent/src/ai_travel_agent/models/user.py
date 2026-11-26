from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from ai_travel_agent.models.base import (
    Base,
    Timestamp,
    enum_column,
    utc_now,
)
from ai_travel_agent.models.enums import Role


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(enum_column(Role), default=Role.USER)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utc_now)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN
