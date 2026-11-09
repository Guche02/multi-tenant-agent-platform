from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


@lru_cache
def session_factory() -> async_sessionmaker[AsyncSession]:
    """For work outside a request's own session, such as saving a reply while it streams."""
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


# scope="function": the session closes as soon as the endpoint function returns, so a
# streaming response doesn't hold a database connection for as long as it streams.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
