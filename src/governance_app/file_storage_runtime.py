from functools import lru_cache

from governance_app.adapters.local_file_storage import LocalFileStorage
from governance_app.config import AppConfig, RuntimeMode
from governance_app.ports.file_storage import FileStorage


@lru_cache(maxsize=32)
def file_storage_for(config: AppConfig) -> FileStorage:
    if config.runtime_mode is RuntimeMode.LOCAL:
        return LocalFileStorage(
            upload_dir=config.data_dir / "uploads",
            export_dir=config.export_dir,
            backup_dir=config.workspace_dir / "backups",
        )
    raise RuntimeError(
        f"file storage is not configured for {config.runtime_mode.value} mode"
    )
