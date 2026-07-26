import shutil
from pathlib import Path
from typing import Any

from governance_app.config import AppConfig
from governance_app.database_admin_runtime import database_admin_for
from governance_app.db import initialize_database
from governance_app.ports.database_admin import DatabaseAdministration


def compact_database(
    config: AppConfig,
    clear_uploads: bool = False,
    *,
    administration: DatabaseAdministration | None = None,
) -> dict[str, Any]:
    initialize_database(config)
    selected_administration = administration or database_admin_for(config)
    before_bytes = _file_size(config.database_path)
    removed_uploads = _clear_uploads(config.data_dir / "uploads") if clear_uploads else 0
    compaction = selected_administration.compact()
    return {
        "before_bytes": before_bytes,
        "after_bytes": _file_size(config.database_path),
        "removed_uploads": removed_uploads,
        "linked_ledger_rows": compaction.linked_ledger_rows,
        "deduplicated_ledger_rows": compaction.deduplicated_ledger_rows,
    }


def _clear_uploads(upload_dir: Path) -> int:
    if not upload_dir.exists():
        return 0
    removed = 0
    for item in upload_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed += 1
    return removed
def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0
