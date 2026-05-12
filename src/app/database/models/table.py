from sqlalchemy import Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.app.database.base import Base


class TableRoom(Base):
    __tablename__ = "table_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str] = mapped_column(
        Text, server_default="ALL", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    min_level: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, server_default="12", nullable=False)
