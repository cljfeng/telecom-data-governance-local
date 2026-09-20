import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


class RuntimeMode(StrEnum):
    LOCAL = "local"
    ONLINE = "online"


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AppConfig:
    runtime_mode: RuntimeMode
    workspace_dir: Path
    data_dir: Path
    database_path: Path
    export_dir: Path
    static_dir: Path

    @classmethod
    def for_workspace(cls, workspace_dir: Path) -> "AppConfig":
        root = workspace_dir.resolve()
        package_dir = Path(__file__).resolve().parent
        data_dir = root / "data"
        return cls(
            runtime_mode=RuntimeMode.LOCAL,
            workspace_dir=root,
            data_dir=data_dir,
            database_path=data_dir / "governance.sqlite3",
            export_dir=root / "exports",
            static_dir=package_dir / "static",
        )

    @classmethod
    def from_environment(
        cls,
        workspace_dir: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "AppConfig":
        config = load_runtime_config(workspace_dir, environ)
        if isinstance(config, OnlineConfig):
            raise ConfigurationError("desktop launcher supports local mode only")
        return config

    def require_local_runtime(self) -> None:
        if self.runtime_mode is not RuntimeMode.LOCAL:
            raise ConfigurationError(
                "local workspace storage cannot serve online requests"
            )


@dataclass(frozen=True)
class OnlineConfig:
    database_url: str = field(repr=False)
    object_storage_bucket: str
    object_storage_endpoint: str | None = None
    object_storage_region: str | None = None
    runtime_mode: RuntimeMode = RuntimeMode.ONLINE

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> "OnlineConfig":
        database_url = values.get("DATABASE_URL", "").strip()
        parsed = urlsplit(database_url)
        if not database_url:
            raise ConfigurationError("DATABASE_URL is required for online mode")
        if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname or not parsed.path.strip("/"):
            raise ConfigurationError("DATABASE_URL must name a PostgreSQL database")
        bucket = values.get("OBJECT_STORAGE_BUCKET", "").strip()
        if not bucket:
            raise ConfigurationError("OBJECT_STORAGE_BUCKET is required for online mode")
        endpoint = values.get("OBJECT_STORAGE_ENDPOINT", "").strip() or None
        if endpoint and urlsplit(endpoint).scheme not in {"http", "https"}:
            raise ConfigurationError("OBJECT_STORAGE_ENDPOINT must be an HTTP URL")
        return cls(
            database_url=database_url,
            object_storage_bucket=bucket,
            object_storage_endpoint=endpoint,
            object_storage_region=values.get("OBJECT_STORAGE_REGION", "").strip() or None,
        )


def load_runtime_config(
    workspace_dir: Path,
    environ: Mapping[str, str] | None = None,
) -> AppConfig | OnlineConfig:
    values = os.environ if environ is None else environ
    raw_mode = values.get("APP_MODE", RuntimeMode.LOCAL.value).strip().lower()
    try:
        mode = RuntimeMode(raw_mode)
    except ValueError as error:
        supported = ", ".join(item.value for item in RuntimeMode)
        raise ConfigurationError(
            f"APP_MODE must be one of: {supported}; got {raw_mode!r}"
        ) from error
    if mode is RuntimeMode.ONLINE:
        return OnlineConfig.from_environment(values)
    return AppConfig.for_workspace(workspace_dir)
