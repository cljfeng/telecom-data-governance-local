import platform
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from governance_app.db import SCHEMA_VERSION

TEMPLATE_VERSION = "2026-05-05"


def app_version() -> str:
    try:
        return version("local-base-data-governance")
    except PackageNotFoundError:
        return "0.1.0"


def version_payload() -> dict[str, Any]:
    return {
        "app_version": app_version(),
        "template_version": TEMPLATE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "python_version": platform.python_version(),
    }
