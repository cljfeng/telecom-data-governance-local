from pathlib import Path

import pytest

from governance_app.config import (
    AppConfig,
    ConfigurationError,
    OnlineConfig,
    RuntimeMode,
    load_runtime_config,
)


def test_default_config_uses_workspace_data_dir(tmp_path: Path):
    config = AppConfig.for_workspace(tmp_path)

    assert config.runtime_mode is RuntimeMode.LOCAL
    assert config.workspace_dir == tmp_path
    assert config.data_dir == tmp_path / "data"
    assert config.database_path == tmp_path / "data" / "governance.sqlite3"
    assert config.export_dir == tmp_path / "exports"
    assert config.static_dir.name == "static"


def test_online_mode_has_separate_staging_and_no_local_workspace_paths(tmp_path: Path):
    staging = tmp_path / "online-staging"
    local_workspace = tmp_path / "local-workspace"
    config = load_runtime_config(
        local_workspace,
        {
            "APP_MODE": "online",
            "DATABASE_URL": "postgresql://example/db",
            "OBJECT_STORAGE_BUCKET": "governance",
            "BOOTSTRAP_ADMIN_PASSWORD": "administrator-password",
            "ONLINE_STAGING_DIR": str(staging),
        },
    )

    assert isinstance(config, OnlineConfig)
    assert config.runtime_mode is RuntimeMode.ONLINE
    assert config.database_url == "postgresql://example/db"
    assert config.object_storage_bucket == "governance"
    assert config.staging_dir == staging
    assert not hasattr(config, "workspace_dir")
    assert config.to_app_config().workspace_dir == staging
    assert not (local_workspace / "data").exists()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"APP_MODE": "online"}, "DATABASE_URL"),
        ({"APP_MODE": "online", "DATABASE_URL": "sqlite:///db"}, "PostgreSQL"),
        (
            {"APP_MODE": "online", "DATABASE_URL": "postgresql://example/db"},
            "OBJECT_STORAGE_BUCKET",
        ),
        (
            {
                "APP_MODE": "online",
                "DATABASE_URL": "postgresql://example/db",
                "OBJECT_STORAGE_BUCKET": "governance",
            },
            "BOOTSTRAP_ADMIN_PASSWORD",
        ),
    ],
)
def test_online_mode_rejects_missing_or_invalid_settings(values, message, tmp_path: Path):
    with pytest.raises(ConfigurationError, match=message):
        load_runtime_config(tmp_path, values)


def test_environment_mode_defaults_to_local_and_rejects_unknown_values(tmp_path: Path):
    config = AppConfig.from_environment(tmp_path, {})
    assert config.runtime_mode is RuntimeMode.LOCAL

    explicit_local = AppConfig.from_environment(
        tmp_path,
        {
            "APP_MODE": "local",
            "DATABASE_URL": "postgresql://example/db",
            "OBJECT_STORAGE_BUCKET": "governance",
        },
    )
    assert explicit_local.database_path == tmp_path / "data" / "governance.sqlite3"

    with pytest.raises(ConfigurationError, match="APP_MODE must be one of"):
        AppConfig.from_environment(tmp_path, {"APP_MODE": "desktop"})


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
    assert config.database_url == "postgresql://user:secret@db/governance"
    assert config.object_store_bucket == "governance-files"
    assert config.object_store_prefix == "tenant-data"

    with pytest.raises(ConfigurationError, match="must use PostgreSQL"):
        AppConfig.for_online_workspace(
            tmp_path,
            database_url="sqlite:///governance.sqlite3",
            object_store_bucket="governance-files",
        )
