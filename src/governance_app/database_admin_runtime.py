from functools import lru_cache
from pathlib import Path

from governance_app.adapters.sqlite_database_admin import (
    SqliteDatabaseAdministration,
)
from governance_app.config import AppConfig, RuntimeMode
from governance_app.ports.database_admin import DatabaseAdministration


@lru_cache(maxsize=32)
def database_admin_for(config: AppConfig) -> DatabaseAdministration:
    if config.runtime_mode is RuntimeMode.LOCAL:
        return SqliteDatabaseAdministration(config.database_path)
    raise RuntimeError(
        f"database administration is not configured for "
        f"{config.runtime_mode.value} mode"
    )


def sqlite_backup_admin_for(path: Path) -> DatabaseAdministration:
    return SqliteDatabaseAdministration(path)
