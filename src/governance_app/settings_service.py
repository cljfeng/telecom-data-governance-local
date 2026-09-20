from pathlib import Path

from governance_app.backup import create_backup, restore_backup, validate_backup
from governance_app.config import AppConfig
from governance_app.database_admin_runtime import database_admin_for
from governance_app.db import initialize_database
from governance_app.ports.database_admin import DatabaseAdministration
from governance_app.version import TEMPLATE_VERSION


def local_settings(config: AppConfig) -> dict[str, str]:
    return {
        "workspace_dir": str(config.workspace_dir),
        "database_path": str(config.database_path),
        "database_size_bytes": str(config.database_path.stat().st_size if config.database_path.exists() else 0),
        "export_dir": str(config.export_dir),
        "backup_dir": str(config.workspace_dir / "backups"),
        "template_version": TEMPLATE_VERSION,
    }


def restore_backup_safely(
    config: AppConfig,
    backup_path: Path,
    *,
    administration: DatabaseAdministration | None = None,
) -> tuple[Path, str]:
    selected_administration = administration or database_admin_for(config)
    validate_backup(
        config,
        backup_path,
        administration=selected_administration,
    )
    safety_backup_path = create_backup(
        config,
        administration=selected_administration,
    )
    try:
        restore_backup(
            config,
            backup_path,
            administration=selected_administration,
        )
        initialize_database(config)
    except Exception as exc:
        restore_backup(
            config,
            safety_backup_path,
            administration=selected_administration,
        )
        raise ValueError("恢复失败，已还原恢复前数据库") from exc
    return safety_backup_path, "restored"
