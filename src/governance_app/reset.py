from typing import Any

from governance_app.backup import create_backup
from governance_app.config import AppConfig
from governance_app.database_admin_runtime import database_admin_for
from governance_app.db import initialize_database
from governance_app.file_storage_runtime import file_storage_for
from governance_app.ports.database_admin import DatabaseAdministration
from governance_app.ports.file_storage import FileStorage


def reset_system(
    config: AppConfig,
    confirmation: str,
    preserve_exports: bool = True,
    preserve_backups: bool = True,
    *,
    administration: DatabaseAdministration | None = None,
    storage: FileStorage | None = None,
) -> dict[str, Any]:
    if confirmation != "复位":
        raise ValueError("请输入“复位”确认后再执行")
    initialize_database(config)
    selected_administration = administration or database_admin_for(config)
    selected_storage = storage or file_storage_for(config)
    safety_backup_path = (
        create_backup(config, administration=selected_administration)
        if config.database_path.exists()
        else None
    )
    selected_administration.reset_business_data()
    removed_exports = (
        0 if preserve_exports else selected_storage.clear("exports")
    )
    removed_backups = 0
    if not preserve_backups:
        keep_file_ids = (
            {selected_storage.publish(safety_backup_path).file_id}
            if safety_backup_path
            else None
        )
        removed_backups = selected_storage.clear(
            "backups",
            keep_file_ids=keep_file_ids,
        )
    return {
        "cleared": True,
        "safety_backup_path": str(safety_backup_path) if safety_backup_path else "",
        "preserve_exports": preserve_exports,
        "preserve_backups": preserve_backups,
        "removed_exports": removed_exports,
        "removed_backups": removed_backups,
    }
