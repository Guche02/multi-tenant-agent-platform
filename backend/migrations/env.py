import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.models import Base
from app.retrieval import KB_DB_TABLE

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# The database URL comes from .env, like the app's, rather than from alembic.ini.
DATABASE_URL = get_settings().database_url


def include_name(name, type_, parent_names) -> bool:
    # Only our own schema. Supabase's schemas (auth, storage, ...) are never compared or changed.
    if type_ == "schema":
        return name in (None, "public")
    # The knowledge-base table is created and managed by LlamaIndex. Without this, autogenerate
    # would see a table missing from our models and write a migration that drops it.
    if type_ == "table":
        return name != KB_DB_TABLE
    return True


def run_migrations_offline() -> None:
    """Prints the SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, include_name=include_name
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
