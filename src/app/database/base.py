from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(
            url,
            echo=False,
            pool_size=20,
            max_overflow=40,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_timeout=30,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self.session_factory() as session:
            yield session
