import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from governance_app.migrations import current_schema_version
from governance_app.ports.database_admin import (
    DatabaseAdministration,
    DatabaseCompaction,
)

_BUSINESS_TABLES = (
    "correction_returns",
    "issues",
    "audit_results",
    "audit_runs",
    "ledger_rows",
    "raw_rows",
    "operation_logs",
    "recent_files",
    "import_batches",
)


class SqliteDatabaseAdministration(DatabaseAdministration):
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def compact(self) -> DatabaseCompaction:
        with self._connect(self._database_path) as connection:
            linked_rows = self._link_raw_rows(connection)
            deduplicated_rows = connection.execute(
                """
                update ledger_rows
                   set row_json = '{}'
                 where raw_row_id is not null
                   and row_json <> '{}'
                """
            ).rowcount
        with self._connect(self._database_path) as connection:
            connection.execute("vacuum")
        return DatabaseCompaction(
            linked_ledger_rows=linked_rows,
            deduplicated_ledger_rows=deduplicated_rows,
        )

    def reset_business_data(self) -> None:
        with self._connect(self._database_path) as connection:
            for table_name in _BUSINESS_TABLES:
                connection.execute(f"delete from {table_name}")
            connection.execute(
                "delete from settings where key = 'current_batch_id'"
            )
            placeholders = ",".join("?" for _ in _BUSINESS_TABLES)
            connection.execute(
                f"delete from sqlite_sequence where name in ({placeholders})",
                _BUSINESS_TABLES,
            )

    def create_backup(self, destination: Path) -> None:
        self._copy_database(self._database_path, destination)

    def restore_backup(self, source: Path) -> None:
        self._copy_database(source, self._database_path)

    def check_integrity(self, path: Path) -> None:
        try:
            with self._connect(path) as connection:
                rows = connection.execute("pragma integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ValueError("备份数据库完整性校验失败") from exc
        if [row[0] for row in rows] != ["ok"]:
            raise ValueError("备份数据库完整性校验失败")

    def schema_version(self, path: Path) -> int:
        try:
            with self._connect(path) as connection:
                return current_schema_version(connection)
        except sqlite3.DatabaseError as exc:
            raise ValueError("备份数据库完整性校验失败") from exc

    @staticmethod
    @contextmanager
    def _connect(path: Path) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("pragma foreign_keys = on")
            connection.execute("pragma busy_timeout = 5000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _link_raw_rows(connection: sqlite3.Connection) -> int:
        connection.execute("drop table if exists temp_ledger_raw_map")
        connection.execute(
            """
            create temp table temp_ledger_raw_map as
            with
            ledger_ranked as (
                select id,
                       batch_id,
                       ledger_type,
                       row_number() over (
                           partition by batch_id, ledger_type order by id
                       ) as position
                  from ledger_rows
                 where raw_row_id is null
            ),
            raw_ranked as (
                select id,
                       batch_id,
                       ledger_type,
                       row_number() over (
                           partition by batch_id, ledger_type order by id
                       ) as position
                  from raw_rows
            )
            select ledger_ranked.id as ledger_row_id,
                   raw_ranked.id as raw_row_id
              from ledger_ranked
              join raw_ranked
                on raw_ranked.batch_id = ledger_ranked.batch_id
               and raw_ranked.ledger_type = ledger_ranked.ledger_type
               and raw_ranked.position = ledger_ranked.position
            """
        )
        linked_rows = connection.execute(
            """
            update ledger_rows
               set raw_row_id = (
                   select raw_row_id
                     from temp_ledger_raw_map
                    where temp_ledger_raw_map.ledger_row_id = ledger_rows.id
               )
             where raw_row_id is null
               and id in (select ledger_row_id from temp_ledger_raw_map)
            """
        ).rowcount
        connection.execute("drop table temp_ledger_raw_map")
        return linked_rows

    @staticmethod
    def _copy_database(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with SqliteDatabaseAdministration._connect(
                source
            ) as source_connection:
                with SqliteDatabaseAdministration._connect(
                    destination
                ) as destination_connection:
                    source_connection.backup(destination_connection)
        except sqlite3.DatabaseError as exc:
            raise ValueError("备份数据库完整性校验失败") from exc
