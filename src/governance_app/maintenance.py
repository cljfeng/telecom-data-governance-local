from pathlib import Path
from typing import Any

from governance_app.config import AppConfig
from governance_app.database_admin_runtime import database_admin_for
from governance_app.db import initialize_database
from governance_app.file_storage_runtime import file_storage_for
from governance_app.ports.database_admin import DatabaseAdministration
from governance_app.ports.file_storage import FileStorage


def compact_database(
    config: AppConfig,
    clear_uploads: bool = False,
    *,
    administration: DatabaseAdministration | None = None,
    storage: FileStorage | None = None,
) -> dict[str, Any]:
    initialize_database(config)
    selected_administration = administration or database_admin_for(config)
    selected_storage = storage or file_storage_for(config)
    before_bytes = _file_size(config.database_path)
    removed_uploads = (
        selected_storage.clear("uploads") if clear_uploads else 0
    )
    compaction = selected_administration.compact()
    return {
        "before_bytes": before_bytes,
        "after_bytes": _file_size(config.database_path),
        "removed_uploads": removed_uploads,
        "linked_ledger_rows": compaction.linked_ledger_rows,
        "deduplicated_ledger_rows": compaction.deduplicated_ledger_rows,
    }
def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0
