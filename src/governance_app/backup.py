from datetime import datetime
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.database_admin_runtime import (
    database_admin_for,
    sqlite_backup_admin_for,
)
from governance_app.migrations import SCHEMA_VERSION
from governance_app.ports.database_admin import DatabaseAdministration


def create_backup(
    config: AppConfig,
    *,
    administration: DatabaseAdministration | None = None,
) -> Path:
    selected_administration = administration or database_admin_for(config)
    backup_dir = config.workspace_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"governance-{timestamp}.sqlite3"
    try:
        selected_administration.create_backup(backup_path)
        selected_administration.check_integrity(backup_path)
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def check_database_integrity(path: Path) -> None:
    sqlite_backup_admin_for(path).check_integrity(path)


def database_schema_version(path: Path) -> int:
    return sqlite_backup_admin_for(path).schema_version(path)


def validate_backup(
    config: AppConfig,
    backup_path: Path,
    *,
    administration: DatabaseAdministration | None = None,
) -> Path:
    selected_administration = administration or database_admin_for(config)
    backup_root = (config.workspace_dir / "backups").resolve()
    source = backup_path.resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    if not source.is_relative_to(backup_root):
        raise ValueError(f"Backup file must be inside {backup_root}")
    selected_administration.check_integrity(source)
    version = selected_administration.schema_version(source)
    if version > SCHEMA_VERSION:
        raise ValueError(f"备份数据库版本 {version} 高于应用支持版本 {SCHEMA_VERSION}")
    return source


def restore_backup(
    config: AppConfig,
    backup_path: Path,
    *,
    administration: DatabaseAdministration | None = None,
) -> None:
    selected_administration = administration or database_admin_for(config)
    source = validate_backup(
        config,
        backup_path,
        administration=selected_administration,
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    selected_administration.restore_backup(source)
    selected_administration.check_integrity(config.database_path)
