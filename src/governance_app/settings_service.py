from pathlib import Path

from governance_app.backup import create_backup, restore_backup
from governance_app.config import AppConfig


def local_settings(config: AppConfig) -> dict[str, str]:
    return {
        "workspace_dir": str(config.workspace_dir),
        "database_path": str(config.database_path),
        "export_dir": str(config.export_dir),
        "backup_dir": str(config.workspace_dir / "backups"),
        "template_version": "2026-05-05",
    }


def restore_backup_safely(config: AppConfig, backup_path: Path) -> tuple[Path, str]:
    safety_backup_path = create_backup(config)
    restore_backup(config, backup_path)
    return safety_backup_path, "restored"
