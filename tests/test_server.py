import json

from governance_app.db import connect, initialize_database
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


def test_dashboard_endpoint_rejects_invalid_batch_id(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("GET", "/api/dashboard?batch_id=abc")

    assert status == 400
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(body)["error"] == "invalid batch_id"


def test_import_audit_export_and_correction_endpoints(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/import",
        json.dumps({"path": str(sample_workbook)}),
    )

    assert status == 200
    assert json.loads(body)["batch_id"] == 1

    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/audit",
        json.dumps({"batch_id": 1}),
    )

    assert status == 200
    assert json.loads(body)["audit_run_id"] == 1

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/export",
        json.dumps({"batch_id": 1}),
    )

    assert status == 200
    exported = json.loads(body)["paths"]
    assert exported

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/corrections",
        json.dumps({"path": exported[0]}),
    )

    assert status == 200
    assert "matched_count" in json.loads(body)


def test_backup_and_restore_endpoints(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("POST", "/api/backup", "{}")

    assert status == 200
    backup_path = json.loads(body)["path"]

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/restore",
        json.dumps({"path": backup_path}),
    )

    assert status == 200
    assert json.loads(body)["status"] == "restored"
