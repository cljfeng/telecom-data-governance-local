import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import initialize_database


def _module():
    assert importlib.util.find_spec("governance_app.routes.reports") is not None
    return importlib.import_module("governance_app.routes.reports")


def test_report_handler_validates_export_and_archive(app_config):
    initialize_database(app_config)
    handler = _module().handle_report_route

    export_response = handler(app_config, "POST", urlparse("/api/export"), "{}")
    archive_response = handler(app_config, "GET", urlparse("/api/archive/precheck?batch_id=99"), "")

    assert export_response[0] == 400
    assert json.loads(export_response[2])["error"] == "invalid batch_id"
    assert archive_response[0] == 400
    assert "batch not found" in json.loads(archive_response[2])["error"]


def test_report_upload_validates_missing_correction_file(app_config):
    response = _module().handle_report_upload(app_config, "/api/corrections/upload", {}, {})

    assert response[0] == 400
    assert "请选择" in json.loads(response[2])["error"]


def test_report_handlers_return_none_for_other_domain(app_config):
    assert _module().handle_report_route(app_config, "GET", urlparse("/api/health"), "") is None
    assert _module().handle_report_upload(app_config, "/api/import/upload", {}, {}) is None
