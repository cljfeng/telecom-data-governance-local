import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import initialize_database


def _module():
    assert importlib.util.find_spec("governance_app.routes.imports") is not None
    return importlib.import_module("governance_app.routes.imports")


def test_import_handler_previews_and_imports_workbook(app_config, sample_workbook):
    initialize_database(app_config)
    handler = _module().handle_import_route

    preview = handler(
        app_config,
        "POST",
        urlparse("/api/import/preview"),
        json.dumps({"path": str(sample_workbook)}),
    )
    imported = handler(
        app_config,
        "POST",
        urlparse("/api/import"),
        json.dumps({"path": str(sample_workbook)}),
    )

    assert preview[0] == 200
    assert json.loads(preview[2])["ok"] is True
    assert imported[0] == 200
    assert json.loads(imported[2])["batch_id"] == 1


def test_import_upload_handler_validates_missing_file(app_config):
    response = _module().handle_import_upload(app_config, "/api/import/upload", {}, {})

    assert response[0] == 400
    assert "请选择" in json.loads(response[2])["error"]


def test_import_handlers_return_none_for_other_domain(app_config):
    assert _module().handle_import_route(app_config, "GET", urlparse("/api/health"), "") is None
    assert _module().handle_import_upload(app_config, "/api/corrections/upload", {}, {}) is None
