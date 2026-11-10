from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

# Set on every connection the app opens. If a client dies mid-transaction (a crash, a dropped
# network), Postgres would otherwise keep its row locks until it notices, which can be never.
# Our transactions are short (none waits on the LLM), so a minute is generous.
CONNECTION_SETTINGS = {
    "idle_in_transaction_session_timeout": "60s",
    # Postgres probes an idle client after 60s, every 10s, and drops it after 3 missed probes.
    "tcp_keepalives_idle": "60",
    "tcp_keepalives_interval": "10",
    "tcp_keepalives_count": "3",
}


def apply_connection_settings(engine: Engine) -> None:
    """Runs SET for CONNECTION_SETTINGS on each new connection. SET, rather than connection
    startup parameters, also works through Supabase's connection pooler."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        for name, value in CONNECTION_SETTINGS.items():
            cursor.execute(f"SET {name} = '{value}'")
        cursor.close()
        # The SETs ran in an implicit transaction under some drivers: end it.
        dbapi_connection.commit()


@lru_cache
def session_factory() -> async_sessionmaker[AsyncSession]:
    """For work outside a request's own session, such as saving a reply while it streams."""
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    apply_connection_settings(engine.sync_engine)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


# scope="function": the session closes as soon as the endpoint function returns, so a
# streaming response doesn't hold a database connection for as long as it streams.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
