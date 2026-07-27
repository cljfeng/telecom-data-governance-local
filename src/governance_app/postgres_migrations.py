from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Connection, text

from governance_app.adapters.sqlite_database import _metadata

POSTGRES_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PostgresMigration:
    version: int
    apply: Callable[[Connection], None]


def apply_postgres_migrations(connection: Connection) -> None:
    connection.execute(
        text(
            """
            create table if not exists schema_migrations (
                version integer primary key,
                applied_at timestamp with time zone not null
                    default current_timestamp
            )
            """
        )
    )
    connection.execute(
        text(
            "select pg_advisory_xact_lock("
            "hashtext('governance-schema-migrations'))"
        )
    )
    current = connection.execute(
        text("select coalesce(max(version), 0) from schema_migrations")
    ).scalar_one()
    if int(current) > POSTGRES_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于应用支持版本 "
            f"{POSTGRES_SCHEMA_VERSION}，请使用更新版本的程序"
        )
    for migration in POSTGRES_MIGRATIONS:
        if migration.version <= int(current):
            continue
        migration.apply(connection)
        connection.execute(
            text(
                "insert into schema_migrations(version) "
                "values (:version)"
            ),
            {"version": migration.version},
        )


def _create_initial_schema(connection: Connection) -> None:
    _metadata.create_all(connection)


POSTGRES_MIGRATIONS = (
    PostgresMigration(1, _create_initial_schema),
)
