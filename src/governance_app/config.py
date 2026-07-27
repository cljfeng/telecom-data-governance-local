import os
from dataclasses import dataclass, field, replace
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
    session_ttl_seconds: int = 8 * 60 * 60
    task_worker_count: int = 4
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )

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
        session_ttl_seconds: int = 8 * 60 * 60,
        task_worker_count: int = 4,
        bootstrap_admin_username: str | None = None,
        bootstrap_admin_password: str | None = None,
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
        if session_ttl_seconds < 300:
            raise ConfigurationError("session TTL must be at least 300 seconds")
        if task_worker_count < 1 or task_worker_count > 32:
            raise ConfigurationError("task worker count must be between 1 and 32")
        return replace(
            local,
            database_url=database_url,
            object_store_bucket=object_store_bucket.strip(),
            object_store_endpoint=object_store_endpoint,
            object_store_region=object_store_region,
            object_store_prefix=object_store_prefix.strip("/"),
            session_ttl_seconds=session_ttl_seconds,
            task_worker_count=task_worker_count,
            bootstrap_admin_username=bootstrap_admin_username,
            bootstrap_admin_password=bootstrap_admin_password,
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
            username = values.get(
                "BOOTSTRAP_ADMIN_USERNAME",
                "admin",
            ).strip()
            password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
            if len(password) < 12:
                raise ConfigurationError(
                    "BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters"
                )
            try:
                session_ttl = int(
                    values.get("SESSION_TTL_SECONDS", str(8 * 60 * 60))
                )
                task_workers = int(values.get("TASK_WORKERS", "4"))
            except ValueError as error:
                raise ConfigurationError(
                    "SESSION_TTL_SECONDS and TASK_WORKERS must be integers"
                ) from error
            return cls.for_online_workspace(
                workspace_dir,
                database_url=values.get("DATABASE_URL", ""),
                object_store_bucket=values.get(
                    "OBJECT_STORAGE_BUCKET",
                    "",
                ),
                object_store_endpoint=values.get(
                    "OBJECT_STORAGE_ENDPOINT"
                ),
                object_store_region=values.get("OBJECT_STORAGE_REGION"),
                object_store_prefix=values.get(
                    "OBJECT_STORAGE_PREFIX",
                    "governance",
                ),
                session_ttl_seconds=session_ttl,
                task_worker_count=task_workers,
                bootstrap_admin_username=username,
                bootstrap_admin_password=password,
            )
        return cls.for_workspace(workspace_dir, runtime_mode=runtime_mode)
