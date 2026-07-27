from functools import lru_cache

from governance_app.adapters.sqlite_database import SqliteDatabase
from governance_app.config import AppConfig, RuntimeMode
from governance_app.ports.database import Database


@lru_cache(maxsize=32)
def database_for(config: AppConfig) -> Database:
    if config.runtime_mode is RuntimeMode.LOCAL:
        return SqliteDatabase(config.database_path)
    if config.database_url:
        from governance_app.adapters.postgres_database import PostgresDatabase

        return PostgresDatabase(config.database_url)
    raise RuntimeError(f"database adapter is not configured for {config.runtime_mode.value} mode")
