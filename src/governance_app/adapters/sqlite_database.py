from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.pool import NullPool

from governance_app.ports.database import BatchRecord, BatchRepository, UnitOfWork

_metadata = MetaData()

_import_batches = Table(
    "import_batches",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("source_file", String, nullable=False),
    Column("name", String),
    Column("batch_code", String),
    Column("template_version", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("status", String, nullable=False),
    Column("is_archived", Integer, nullable=False),
    Column("archived_at", String),
)

_settings = Table(
    "settings",
    _metadata,
    Column("key", String, primary_key=True),
    Column("value_json", String, nullable=False),
)

_operation_logs = Table(
    "operation_logs",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("batch_id", Integer),
    Column("operation", String, nullable=False),
    Column("message", String, nullable=False),
    Column("created_at", String, nullable=False),
)


class SqliteBatchRepository(BatchRepository):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def create(self, *, name: str, batch_code: str) -> int:
        result = self._connection.execute(
            insert(_import_batches).values(
                source_file="",
                name=name,
                batch_code=batch_code,
                status="created",
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None or primary_key[0] is None:
            raise RuntimeError("database did not return a batch id")
        return int(primary_key[0])

    def get(self, batch_id: int) -> BatchRecord | None:
        statement = (
            select(
                _import_batches.c.id,
                func.coalesce(
                    _import_batches.c.name,
                    _import_batches.c.source_file,
                    "未命名批次",
                ).label("name"),
                _import_batches.c.source_file,
                _import_batches.c.template_version,
                _import_batches.c.batch_code,
                _import_batches.c.created_at,
                _import_batches.c.status,
                _import_batches.c.is_archived,
                _import_batches.c.archived_at,
            )
            .where(_import_batches.c.id == batch_id)
        )
        row = self._connection.execute(statement).mappings().one_or_none()
        return None if row is None else dict(row)

    def list_all(self) -> list[BatchRecord]:
        statement = select(
            _import_batches.c.id,
            func.coalesce(
                _import_batches.c.name,
                _import_batches.c.source_file,
                "未命名批次",
            ).label("name"),
            _import_batches.c.batch_code,
            _import_batches.c.source_file,
            _import_batches.c.template_version,
            _import_batches.c.created_at,
            _import_batches.c.status,
            _import_batches.c.is_archived,
            _import_batches.c.archived_at,
        ).order_by(_import_batches.c.id.desc())
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def current_id(self) -> int | None:
        value = self._connection.execute(
            select(_settings.c.value_json).where(_settings.c.key == "current_batch_id")
        ).scalar_one_or_none()
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def set_current(self, batch_id: int) -> None:
        result = self._connection.execute(
            update(_settings)
            .where(_settings.c.key == "current_batch_id")
            .values(value_json=str(batch_id))
        )
        if not result.rowcount:
            self._connection.execute(
                insert(_settings).values(key="current_batch_id", value_json=str(batch_id))
            )

    def update_status(self, batch_id: int, status: str, *, archive: bool = False) -> None:
        values: dict[str, Any] = {"status": status}
        if archive:
            values.update(is_archived=1, archived_at=func.current_timestamp())
        self._connection.execute(
            update(_import_batches).where(_import_batches.c.id == batch_id).values(**values)
        )

    def add_operation(self, batch_id: int, operation: str, message: str) -> None:
        self._connection.execute(
            insert(_operation_logs).values(
                batch_id=batch_id,
                operation=operation,
                message=message,
            )
        )


class SqliteUnitOfWork(UnitOfWork):
    def __init__(self, connection: Connection) -> None:
        self.batches = SqliteBatchRepository(connection)


class SqliteDatabase:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        url = URL.create("sqlite+pysqlite", database=str(database_path))
        self._engine: Engine = create_engine(url, poolclass=NullPool)
        event.listen(self._engine, "connect", _configure_connection)

    @contextmanager
    def unit_of_work(self) -> Iterator[UnitOfWork]:
        with self._engine.begin() as connection:
            yield SqliteUnitOfWork(connection)

    def dispose(self) -> None:
        self._engine.dispose()


def _configure_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("pragma foreign_keys = on")
        cursor.execute("pragma busy_timeout = 5000")
    finally:
        cursor.close()
