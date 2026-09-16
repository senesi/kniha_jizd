import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def include_schemas(schema_name: str | None) -> bool:
    return schema_name in ("core", "fleet")


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema="core",
        include_schemas=True,
        include_object=lambda obj, name, type_, reflected, compare_to: (
            include_schemas(getattr(obj, "schema", None)) if type_ == "table" else True
        ),
    )
    with context.begin_transaction():
        context.run_migrations()


async def _ensure_version_table_schema(connectable) -> None:
    async with connectable.begin() as connection:
        await connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS core")


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    await _ensure_version_table_schema(connectable)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema="core",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
