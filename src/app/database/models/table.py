from sqlalchemy import BigInteger, Boolean, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from src.app.database.base import Base

class TableRoom(Base):
    __tablename__ = "table_rooms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False) # Raqamli ID (diapazon uchun)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country_code: Mapped[str] = mapped_column(Text, server_default="all", nullable=False) # uz, kz, ru, all
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    min_level: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, server_default="12", nullable=False)
