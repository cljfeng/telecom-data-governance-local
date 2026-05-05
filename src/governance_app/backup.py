import shutil
from datetime import datetime
from pathlib import Path

from governance_app.config import AppConfig


def create_backup(config: AppConfig) -> Path:
    backup_dir = config.workspace_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"governance-{timestamp}.sqlite3"
    shutil.copy2(config.database_path, backup_path)
    return backup_path


def restore_backup(config: AppConfig, backup_path: Path) -> None:
    backup_root = (config.workspace_dir / "backups").resolve()
    source = backup_path.resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    if not source.is_relative_to(backup_root):
        raise ValueError(f"Backup file must be inside {backup_root}")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, config.database_path)
