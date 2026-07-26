import os
from dataclasses import dataclass
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
                "online mode is not ready: PostgreSQL, server-side file storage, "
                "authentication, and authorization adapters must be configured first"
            )
        return cls.for_workspace(workspace_dir, runtime_mode=runtime_mode)
