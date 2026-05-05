from pathlib import Path

from governance_app.config import AppConfig


def test_default_config_uses_workspace_data_dir(tmp_path: Path):
    config = AppConfig.for_workspace(tmp_path)

    assert config.workspace_dir == tmp_path
    assert config.data_dir == tmp_path / "data"
    assert config.database_path == tmp_path / "data" / "governance.sqlite3"
    assert config.export_dir == tmp_path / "exports"
    assert config.static_dir.name == "static"
