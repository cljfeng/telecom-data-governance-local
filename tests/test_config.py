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


def test_online_mode_fails_closed_until_adapters_are_ready(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="online mode is not ready"):
        AppConfig.from_environment(tmp_path, {"APP_MODE": "online"})
