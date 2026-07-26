import shutil
from pathlib import Path
from typing import Any

from governance_app.backup import create_backup
from governance_app.config import AppConfig
from governance_app.database_admin_runtime import database_admin_for
from governance_app.db import initialize_database
from governance_app.ports.database_admin import DatabaseAdministration


def reset_system(
    config: AppConfig,
    confirmation: str,
    preserve_exports: bool = True,
    preserve_backups: bool = True,
    *,
    administration: DatabaseAdministration | None = None,
) -> dict[str, Any]:
    if confirmation != "复位":
        raise ValueError("请输入“复位”确认后再执行")
    initialize_database(config)
    selected_administration = administration or database_admin_for(config)
    safety_backup_path = (
        create_backup(config, administration=selected_administration)
        if config.database_path.exists()
        else None
    )
    selected_administration.reset_business_data()
    removed_exports = 0 if preserve_exports else _clear_directory(config.export_dir)
    removed_backups = 0
    if not preserve_backups:
        backup_dir = config.workspace_dir / "backups"
        keep = safety_backup_path.resolve() if safety_backup_path else None
        removed_backups = _clear_directory(backup_dir, keep=keep)
    return {
        "cleared": True,
        "safety_backup_path": str(safety_backup_path) if safety_backup_path else "",
        "preserve_exports": preserve_exports,
        "preserve_backups": preserve_backups,
        "removed_exports": removed_exports,
        "removed_backups": removed_backups,
    }


def _clear_directory(path: Path, keep: Path | None = None) -> int:
    if not path.exists():
        return 0
    removed = 0
    for item in path.iterdir():
        if keep is not None and item.resolve() == keep:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed += 1
    return removed
