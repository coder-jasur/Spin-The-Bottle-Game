from datetime import datetime, timedelta
from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.database.base import Base


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)

    media_url: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)  # "image" yoki "video"
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )

    # Relationships
    user = relationship("User", backref="stories")
    views = relationship("StoryView", back_populates="story", cascade="all, delete-orphan")
    likes = relationship("StoryLike", back_populates="story", cascade="all, delete-orphan")


class StoryView(Base):
    __tablename__ = "story_views"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    story_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stories.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    # Relationships
    story = relationship("Story", back_populates="views")
    user = relationship("User")


class StoryLike(Base):
    __tablename__ = "story_likes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    story_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("stories.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), nullable=False
    )

    # Relationships
    story = relationship("Story", back_populates="likes")
    user = relationship("User")
