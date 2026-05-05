from pathlib import Path

import pytest

from governance_app.config import AppConfig


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig.for_workspace(tmp_path)
