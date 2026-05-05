import json

from governance_app.db import initialize_database
from governance_app.server import create_app


def test_health_endpoint_returns_ok(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("GET", "/api/health")

    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_dashboard_endpoint_returns_json(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("GET", "/api/dashboard?batch_id=1")

    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert "ledger_counts" in json.loads(body)
