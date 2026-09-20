import os
import tempfile
from dataclasses import dataclass, field, replace
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
    database_url: str | None = field(default=None, repr=False)
    object_store_bucket: str | None = None
    object_store_endpoint: str | None = None
    object_store_region: str | None = None
    object_store_prefix: str = "governance"
    session_ttl_seconds: int = 8 * 60 * 60
    task_worker_count: int = 4
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = field(default=None, repr=False, compare=False)

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
        staging_dir: Path,
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
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ConfigurationError("online database URL must use PostgreSQL")
        if not object_store_bucket.strip():
            raise ConfigurationError("online object storage bucket is required")
        if session_ttl_seconds < 300:
            raise ConfigurationError("session TTL must be at least 300 seconds")
        if not 1 <= task_worker_count <= 32:
            raise ConfigurationError("task worker count must be between 1 and 32")
        staging = cls.for_workspace(staging_dir, runtime_mode=RuntimeMode.ONLINE)
        return replace(
            staging,
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
        config = load_runtime_config(workspace_dir, environ)
        if isinstance(config, OnlineConfig):
            raise ConfigurationError("desktop launcher supports local mode only")
        return config

    def require_local_runtime(self) -> None:
        if self.runtime_mode is not RuntimeMode.LOCAL:
            raise ConfigurationError("local workspace storage cannot serve online requests")


@dataclass(frozen=True)
class OnlineConfig:
    database_url: str = field(repr=False)
    object_storage_bucket: str
    staging_dir: Path
    bootstrap_admin_password: str = field(repr=False)
    bootstrap_admin_username: str = "admin"
    object_storage_endpoint: str | None = None
    object_storage_region: str | None = None
    object_storage_prefix: str = "governance"
    session_ttl_seconds: int = 8 * 60 * 60
    task_worker_count: int = 4
    runtime_mode: RuntimeMode = RuntimeMode.ONLINE

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> "OnlineConfig":
        database_url = values.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError("DATABASE_URL is required for online mode")
        parsed = urlsplit(database_url)
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or not parsed.hostname or not parsed.path.strip("/"):
            raise ConfigurationError("DATABASE_URL must name a PostgreSQL database")
        bucket = values.get("OBJECT_STORAGE_BUCKET", "").strip()
        if not bucket:
            raise ConfigurationError("OBJECT_STORAGE_BUCKET is required for online mode")
        password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise ConfigurationError("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters")
        try:
            session_ttl = int(values.get("SESSION_TTL_SECONDS", str(8 * 60 * 60)))
            task_workers = int(values.get("TASK_WORKERS", "4"))
        except ValueError as error:
            raise ConfigurationError("SESSION_TTL_SECONDS and TASK_WORKERS must be integers") from error
        if session_ttl < 300 or not 1 <= task_workers <= 32:
            raise ConfigurationError("invalid session TTL or task worker count")
        endpoint = values.get("OBJECT_STORAGE_ENDPOINT", "").strip() or None
        if endpoint and urlsplit(endpoint).scheme not in {"http", "https"}:
            raise ConfigurationError("OBJECT_STORAGE_ENDPOINT must be an HTTP URL")
        staging = Path(
            values.get("ONLINE_STAGING_DIR", "").strip()
            or Path(tempfile.gettempdir()) / "governance-online"
        ).resolve()
        return cls(
            database_url=database_url,
            object_storage_bucket=bucket,
            staging_dir=staging,
            bootstrap_admin_password=password,
            bootstrap_admin_username=values.get("BOOTSTRAP_ADMIN_USERNAME", "admin").strip(),
            object_storage_endpoint=endpoint,
            object_storage_region=values.get("OBJECT_STORAGE_REGION", "").strip() or None,
            object_storage_prefix=values.get("OBJECT_STORAGE_PREFIX", "governance").strip("/"),
            session_ttl_seconds=session_ttl,
            task_worker_count=task_workers,
        )

    def to_app_config(self) -> AppConfig:
        return AppConfig.for_online_workspace(
            self.staging_dir,
            database_url=self.database_url,
            object_store_bucket=self.object_storage_bucket,
            object_store_endpoint=self.object_storage_endpoint,
            object_store_region=self.object_storage_region,
            object_store_prefix=self.object_storage_prefix,
            session_ttl_seconds=self.session_ttl_seconds,
            task_worker_count=self.task_worker_count,
            bootstrap_admin_username=self.bootstrap_admin_username,
            bootstrap_admin_password=self.bootstrap_admin_password,
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
        raise ConfigurationError(f"APP_MODE must be one of: {supported}; got {raw_mode!r}") from error
    if mode is RuntimeMode.ONLINE:
        return OnlineConfig.from_environment(values)
    return AppConfig.for_workspace(workspace_dir)
