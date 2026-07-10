import platform
from typing import Any

from governance_app.db import SCHEMA_VERSION

TEMPLATE_VERSION = "2026-05-05"
APP_VERSION = "0.2.0"


def app_version() -> str:
    return APP_VERSION


def version_payload() -> dict[str, Any]:
    return {
        "app_version": app_version(),
        "template_version": TEMPLATE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "python_version": platform.python_version(),
    }
