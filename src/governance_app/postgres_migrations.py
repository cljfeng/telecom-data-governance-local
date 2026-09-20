from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Connection, text

from governance_app.adapters.sqlite_database import _metadata
from governance_app.identity_store import identity_metadata

POSTGRES_SCHEMA_VERSION = 2


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


def _add_identity_and_runtime_schema(connection: Connection) -> None:
    identity_metadata.create_all(connection)
    for statement in (
        "alter table recent_files add column if not exists "
        "organization_id integer",
        "alter table operation_logs add column if not exists "
        "user_id integer",
        "alter table operation_logs add column if not exists "
        "organization_id integer",
        "alter table operation_logs add column if not exists "
        "request_id varchar",
        "alter table operation_logs add column if not exists "
        "source_ip varchar",
        "alter table operation_logs add column if not exists "
        "task_id integer",
        "create index if not exists idx_recent_files_organization "
        "on recent_files(organization_id, last_used_at)",
    ):
        connection.execute(text(statement))


POSTGRES_MIGRATIONS = (
    PostgresMigration(1, _create_initial_schema),
    PostgresMigration(2, _add_identity_and_runtime_schema),
)
