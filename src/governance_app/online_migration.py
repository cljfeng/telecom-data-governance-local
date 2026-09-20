from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, func, select, text

from governance_app.adapters.postgres_database import (
    PostgresDatabase,
    _psycopg_url,
)

_TABLE_ORDER = (
    "organizations",
    "roles",
    "role_permissions",
    "users",
    "user_roles",
    "import_batches",
    "batch_organizations",
    "raw_rows",
    "ledger_rows",
    "audit_runs",
    "audit_results",
    "issues",
    "issue_events",
    "analysis_opportunities",
    "analysis_opportunity_reviews",
    "correction_returns",
    "settings",
    "operation_logs",
    "recent_files",
    "audit_rule_settings",
    "request_audit_logs",
    "background_tasks",
)


@dataclass(frozen=True)
class MigrationReport:
    source_counts: dict[str, int]
    target_counts: dict[str, int]
    migrated_rows: int
    dry_run: bool


def migrate_sqlite_to_postgres(
    sqlite_path: Path,
    postgres_url: str,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    source_engine = create_engine(
        f"sqlite+pysqlite:///{sqlite_path.resolve()}"
    )
    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    source_table_names = [
        name for name in _TABLE_ORDER if name in source_metadata.tables
    ]
    with source_engine.connect() as source:
        source_counts = {
            name: _row_count(source, source_metadata.tables[name])
            for name in source_table_names
        }
    if dry_run:
        source_engine.dispose()
        return MigrationReport(
            source_counts=source_counts,
            target_counts={},
            migrated_rows=0,
            dry_run=True,
        )
    target_database = PostgresDatabase(postgres_url)
    target_database.initialize()
    target_engine = create_engine(
        _psycopg_url(postgres_url),
        pool_pre_ping=True,
    )
    target_metadata = MetaData()
    target_metadata.reflect(bind=target_engine)
    table_names = [
        name
        for name in _TABLE_ORDER
        if name in source_metadata.tables
        and name in target_metadata.tables
    ]
    with source_engine.connect() as source:
        source_counts = {
            name: source_counts[name] for name in table_names
        }
        with target_engine.begin() as target:
            nonempty = {}
            for name in table_names:
                count = _row_count(target, target_metadata.tables[name])
                if count:
                    nonempty[name] = count
            if nonempty:
                names = ", ".join(sorted(nonempty))
                raise ValueError(
                    "target PostgreSQL business schema must be empty; "
                    f"nonempty tables: {names}"
                )
            migrated_rows = 0
            for name in table_names:
                source_table = source_metadata.tables[name]
                target_table = target_metadata.tables[name]
                target_columns = set(target_table.c.keys())
                rows = source.execute(select(source_table)).mappings()
                batch: list[dict[str, Any]] = []
                for row in rows:
                    batch.append(
                        {
                            key: value
                            for key, value in row.items()
                            if key in target_columns
                        }
                    )
                    if len(batch) >= 500:
                        target.execute(target_table.insert(), batch)
                        migrated_rows += len(batch)
                        batch = []
                if batch:
                    target.execute(target_table.insert(), batch)
                    migrated_rows += len(batch)
                if "id" in target_columns:
                    _reset_postgres_sequence(target, name)
            target_counts = {
                name: _row_count(target, target_metadata.tables[name])
                for name in table_names
            }
            if target_counts != source_counts:
                raise RuntimeError(
                    "migration verification failed: target row counts differ"
                )
    target_engine.dispose()
    target_database.dispose()
    source_engine.dispose()
    return MigrationReport(
        source_counts=source_counts,
        target_counts=target_counts,
        migrated_rows=migrated_rows,
        dry_run=False,
    )


def _row_count(connection, table: Table) -> int:
    return int(
        connection.execute(
            select(func.count()).select_from(table)
        ).scalar_one()
    )


def _reset_postgres_sequence(connection, table_name: str) -> None:
    connection.execute(
        text(
            """
            do $$
            declare sequence_name text;
            begin
                sequence_name := pg_get_serial_sequence(:table_name, 'id');
                if sequence_name is not null then
                    execute format(
                        'select setval(%L, coalesce((select max(id) from %I), 1), '
                        '(select count(*) > 0 from %I))',
                        sequence_name,
                        :table_name,
                        :table_name
                    );
                end if;
            end
            $$
            """
        ),
        {"table_name": table_name},
    )
