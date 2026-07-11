import sqlite3
from contextlib import contextmanager
from typing import Iterator

from governance_app.config import AppConfig
from governance_app.migrations import (
    SCHEMA_VERSION,
    apply_migrations,
    current_schema_version,
)


@contextmanager
def connect(config: AppConfig) -> Iterator[sqlite3.Connection]:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(config: AppConfig) -> None:
    if _needs_pre_migration_backup(config):
        from governance_app.backup import create_backup

        create_backup(config)
    with connect(config) as conn:
        apply_migrations(conn)


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _needs_pre_migration_backup(config: AppConfig) -> bool:
    if not config.database_path.exists() or config.database_path.stat().st_size == 0:
        return False
    try:
        with sqlite3.connect(config.database_path) as conn:
            conn.row_factory = sqlite3.Row
            version = current_schema_version(conn)
            has_tables = conn.execute(
                "select 1 from sqlite_master where type = 'table' and name not like 'sqlite_%' limit 1"
            ).fetchone() is not None
    except sqlite3.DatabaseError:
        return False
    return has_tables and version < SCHEMA_VERSION
