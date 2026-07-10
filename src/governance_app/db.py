import sqlite3
from contextlib import contextmanager
from typing import Iterator

from governance_app.config import AppConfig
from governance_app.migrations import SCHEMA_VERSION, apply_migrations


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
    with connect(config) as conn:
        apply_migrations(conn)


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}
