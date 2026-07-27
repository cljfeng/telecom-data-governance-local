from pathlib import Path

import pytest

from governance_app.config import (
    AppConfig,
    ConfigurationError,
    RuntimeMode,
)


def test_default_config_uses_workspace_data_dir(tmp_path: Path):
    config = AppConfig.for_workspace(tmp_path)

    assert config.runtime_mode is RuntimeMode.LOCAL
    assert config.workspace_dir == tmp_path
    assert config.data_dir == tmp_path / "data"
    assert config.database_path == tmp_path / "data" / "governance.sqlite3"
    assert config.export_dir == tmp_path / "exports"
    assert config.static_dir.name == "static"


def test_environment_defaults_to_local_mode(tmp_path: Path):
    config = AppConfig.from_environment(tmp_path, {})

    assert config.runtime_mode is RuntimeMode.LOCAL


def test_environment_rejects_unknown_mode(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="APP_MODE must be one of"):
        AppConfig.from_environment(tmp_path, {"APP_MODE": "desktop"})


def test_online_mode_requires_secure_bootstrap_configuration(tmp_path: Path):
    with pytest.raises(
        ConfigurationError,
        match="BOOTSTRAP_ADMIN_PASSWORD",
    ):
        AppConfig.from_environment(tmp_path, {"APP_MODE": "online"})


def test_online_environment_builds_complete_runtime_config(tmp_path: Path):
    config = AppConfig.from_environment(
        tmp_path,
        {
            "APP_MODE": "online",
            "DATABASE_URL": "postgresql://db/governance",
            "OBJECT_STORAGE_BUCKET": "governance",
            "BOOTSTRAP_ADMIN_USERNAME": "root",
            "BOOTSTRAP_ADMIN_PASSWORD": "a-strong-password",
            "TASK_WORKERS": "3",
        },
    )

    assert config.runtime_mode is RuntimeMode.ONLINE
    assert config.bootstrap_admin_username == "root"
    assert config.task_worker_count == 3


def test_online_config_requires_postgres_and_object_storage(tmp_path: Path):
    config = AppConfig.for_online_workspace(
        tmp_path,
        database_url="postgresql://user:secret@db/governance",
        object_store_bucket="governance-files",
        object_store_endpoint="https://objects.example.test",
        object_store_region="cn-east-1",
        object_store_prefix="/tenant-data/",
    )

    assert config.runtime_mode is RuntimeMode.ONLINE
    assert config.database_url == (
        "postgresql://user:secret@db/governance"
    )
    assert config.object_store_bucket == "governance-files"
    assert config.object_store_prefix == "tenant-data"

    with pytest.raises(ConfigurationError, match="must use PostgreSQL"):
        AppConfig.for_online_workspace(
            tmp_path,
            database_url="sqlite:///governance.sqlite3",
            object_store_bucket="governance-files",
        )
