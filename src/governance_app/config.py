import os
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Mapping


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
    database_url: str | None = None
    object_store_bucket: str | None = None
    object_store_endpoint: str | None = None
    object_store_region: str | None = None
    object_store_prefix: str = "governance"

    @classmethod
    def for_workspace(
        cls,
        workspace_dir: Path,
        runtime_mode: RuntimeMode = RuntimeMode.LOCAL,
    ) -> "AppConfig":
        root = workspace_dir.resolve()
        package_dir = Path(__file__).resolve().parent
        data_dir = root / "data"
        return cls(
            runtime_mode=runtime_mode,
            workspace_dir=root,
            data_dir=data_dir,
            database_path=data_dir / "governance.sqlite3",
            export_dir=root / "exports",
            static_dir=package_dir / "static",
        )

    @classmethod
    def for_online_workspace(
        cls,
        workspace_dir: Path,
        *,
        database_url: str,
        object_store_bucket: str,
        object_store_endpoint: str | None = None,
        object_store_region: str | None = None,
        object_store_prefix: str = "governance",
    ) -> "AppConfig":
        local = cls.for_workspace(
            workspace_dir,
            runtime_mode=RuntimeMode.ONLINE,
        )
        if not database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ConfigurationError("online database URL must use PostgreSQL")
        if not object_store_bucket.strip():
            raise ConfigurationError("online object storage bucket is required")
        return replace(
            local,
            database_url=database_url,
            object_store_bucket=object_store_bucket.strip(),
            object_store_endpoint=object_store_endpoint,
            object_store_region=object_store_region,
            object_store_prefix=object_store_prefix.strip("/"),
        )

    @classmethod
    def from_environment(
        cls,
        workspace_dir: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "AppConfig":
        values = os.environ if environ is None else environ
        raw_mode = values.get("APP_MODE", RuntimeMode.LOCAL.value).strip().lower()
        try:
            runtime_mode = RuntimeMode(raw_mode)
        except ValueError as error:
            supported = ", ".join(mode.value for mode in RuntimeMode)
            raise ConfigurationError(
                f"APP_MODE must be one of: {supported}; got {raw_mode!r}"
            ) from error
        if runtime_mode is RuntimeMode.ONLINE:
            raise ConfigurationError(
                "online mode is not ready: authentication and authorization "
                "adapters must be configured"
            )
        return cls.for_workspace(workspace_dir, runtime_mode=runtime_mode)
