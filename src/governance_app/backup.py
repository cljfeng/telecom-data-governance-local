import sqlite3
from datetime import datetime
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.migrations import SCHEMA_VERSION, current_schema_version


def create_backup(config: AppConfig) -> Path:
    backup_dir = config.workspace_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"governance-{timestamp}.sqlite3"
    try:
        _copy_database(config.database_path, backup_path)
        check_database_integrity(backup_path)
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def check_database_integrity(path: Path) -> None:
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute("pragma integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError("备份数据库完整性校验失败") from exc
    if [row[0] for row in rows] != ["ok"]:
        raise ValueError("备份数据库完整性校验失败")


def database_schema_version(path: Path) -> int:
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            return current_schema_version(conn)
    except sqlite3.DatabaseError as exc:
        raise ValueError("备份数据库完整性校验失败") from exc


def validate_backup(config: AppConfig, backup_path: Path) -> Path:
    backup_root = (config.workspace_dir / "backups").resolve()
    source = backup_path.resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    if not source.is_relative_to(backup_root):
        raise ValueError(f"Backup file must be inside {backup_root}")
    check_database_integrity(source)
    version = database_schema_version(source)
    if version > SCHEMA_VERSION:
        raise ValueError(f"备份数据库版本 {version} 高于应用支持版本 {SCHEMA_VERSION}")
    return source


def restore_backup(config: AppConfig, backup_path: Path) -> None:
    source = validate_backup(config, backup_path)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _copy_database(source, config.database_path)
    check_database_integrity(config.database_path)


def _copy_database(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
            source.backup(destination)
    except sqlite3.DatabaseError as exc:
        raise ValueError("备份数据库完整性校验失败") from exc
