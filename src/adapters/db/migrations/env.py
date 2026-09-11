import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from src.adapters.db.tables import Base
from src.config import settings

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate support
target_metadata = Base.metadata


def get_database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if "db_url" in x_args:
        return x_args["db_url"]
    url = config.get_main_option("sqlalchemy.url")
    if not url or "driver://user:pass" in url:
        return settings.DATABASE_URL
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        db_url = get_database_url()
        async_engine = create_async_engine(
            db_url,
            poolclass=pool.NullPool,
        )

        async with async_engine.connect() as connection:
            await connection.run_sync(do_run_migrations)

        await async_engine.dispose()
    else:
        if isinstance(connectable, AsyncConnection) or hasattr(connectable, "run_sync"):
            await connectable.run_sync(do_run_migrations)
        else:
            do_run_migrations(connectable)


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # If an event loop is running in this thread, execute in a worker thread without active loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(run_async_migrations()))
            future.result()
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
