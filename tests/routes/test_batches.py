import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import initialize_database


def _handler():
    assert importlib.util.find_spec("governance_app.routes.batches") is not None
    return importlib.import_module("governance_app.routes.batches").handle_batch_route


def test_batch_handler_lists_batches(app_config):
    initialize_database(app_config)

    response = _handler()(app_config, "GET", urlparse("/api/batches"), "")

    assert response[0] == 200
    assert json.loads(response[2]) == {"batches": []}


def test_batch_handler_validates_creation_and_dashboard_id(app_config):
    initialize_database(app_config)

    create_response = _handler()(app_config, "POST", urlparse("/api/batches"), "{}")
    dashboard_response = _handler()(app_config, "GET", urlparse("/api/dashboard?batch_id=bad"), "")

    assert create_response[0] == 400
    assert json.loads(create_response[2])["error"] == "name is required"
    assert dashboard_response[0] == 400
    assert json.loads(dashboard_response[2])["error"] == "invalid batch_id"


def test_batch_handler_returns_none_for_audit_domain(app_config):
    assert _handler()(app_config, "POST", urlparse("/api/audit"), "{}") is None
