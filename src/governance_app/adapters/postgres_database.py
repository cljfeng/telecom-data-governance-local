from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from governance_app.adapters.sqlite_database import SqliteUnitOfWork
from governance_app.ports.database import UnitOfWork
from governance_app.postgres_migrations import apply_postgres_migrations


class PostgresDatabase:
    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 20,
        engine: Engine | None = None,
    ) -> None:
        self._engine = engine or create_engine(
            _psycopg_url(database_url),
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )

    def initialize(self) -> None:
        with self._engine.begin() as connection:
            apply_postgres_migrations(connection)

    @contextmanager
    def unit_of_work(self) -> Iterator[UnitOfWork]:
        try:
            with self._engine.begin() as connection:
                yield SqliteUnitOfWork(connection)
        except DBAPIError as exc:
            original = exc.orig
            if isinstance(original, BaseException):
                raise original from exc
            raise

    def dispose(self) -> None:
        self._engine.dispose()


def _psycopg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    return database_url
