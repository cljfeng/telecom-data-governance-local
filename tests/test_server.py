import json
from pathlib import Path

from governance_app.config import AppConfig
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


def test_import_preview_endpoint_returns_counts_without_creating_batch(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/import/preview",
        json.dumps({"path": str(sample_workbook)}),
    )

    data = json.loads(body)
    assert status == 200
    assert data["ok"] is True
    assert data["batch_name"] == "sample_template"
    assert data["ledger_counts"]["site"] == 1
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from import_batches").fetchone()["c"] == 0


def test_import_recent_files_and_error_export_endpoints(app_config, workbook_missing_site_code):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/import/preview",
        json.dumps({"path": str(workbook_missing_site_code)}),
    )

    assert status == 400
    preview = json.loads(body)
    assert preview["error_export_path"].endswith(".xlsx")

    status, headers, body = app.handle_test_request("GET", "/api/import/recent")

    assert status == 200
    recent = json.loads(body)["files"]
    assert recent[0]["path"] == str(workbook_missing_site_code)
    assert recent[0]["ok"] is False


def test_workbench_management_endpoints(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/batches",
        json.dumps({"name": "2026年专项治理"}),
    )

    assert status == 200
    batch_id = json.loads(body)["batch_id"]

    status, headers, body = app.handle_test_request("GET", "/api/batches")

    assert status == 200
    assert json.loads(body)["batches"][0]["name"] == "2026年专项治理"

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/batches/current",
        json.dumps({"batch_id": batch_id}),
    )

    assert status == 200
    assert json.loads(body)["status"] == "selected"

    status, headers, body = app.handle_test_request("GET", f"/api/workflow?batch_id={batch_id}")

    assert status == 200
    assert json.loads(body)["next_action"] == "导入台账"


def test_issue_city_progress_status_and_archive_endpoints(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]
    with connect(app_config) as conn:
        conn.execute("update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": batch_id}))
    status, headers, body = app.handle_test_request("POST", "/api/export", json.dumps({"batch_id": batch_id}))
    exported = json.loads(body)["paths"][0]

    status, headers, body = app.handle_test_request("GET", f"/api/issues?batch_id={batch_id}&city=杭州")

    assert status == 200
    issue = json.loads(body)["issues"][0]
    assert issue["city"] == "杭州"

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/issues/status",
        json.dumps({"issue_code": issue["issue_code"], "status": "closed"}),
    )

    assert status == 200
    assert json.loads(body)["status"] == "updated"

    status, headers, body = app.handle_test_request("GET", f"/api/city-progress?batch_id={batch_id}")

    assert status == 200
    assert json.loads(body)["cities"][0]["completion_rate"] == 100.0

    from openpyxl import load_workbook

    wb = load_workbook(exported)
    ws = wb["整改问题清单"]
    ws["L2"] = "已修复"
    ws["M2"] = "已补正"
    wb.save(exported)

    status, headers, body = app.handle_test_request("POST", "/api/corrections", json.dumps({"path": exported}))

    assert status == 200

    status, headers, body = app.handle_test_request("POST", "/api/archive", json.dumps({"batch_id": batch_id}))

    assert status == 200
    assert json.loads(body)["path"].endswith("专项治理归档汇总.xlsx")


def test_export_endpoint_returns_json_error_before_audit(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]

    status, headers, body = app.handle_test_request("POST", "/api/export", json.dumps({"batch_id": batch_id}))

    assert status == 400
    assert json.loads(body)["error"] == "batch must be audited before export"


def test_archive_endpoint_returns_json_error_before_return(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]

    status, headers, body = app.handle_test_request("POST", "/api/archive", json.dumps({"batch_id": batch_id}))

    assert status == 400
    assert json.loads(body)["error"] == "batch must be ready for archive"


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


def test_static_handler_disables_browser_cache():
    from governance_app.server import RequestHandler

    assert RequestHandler.extra_static_headers()["Cache-Control"] == "no-store, max-age=0"
    assert RequestHandler.extra_static_headers()["Pragma"] == "no-cache"


def test_workbench_static_assets_use_lightweight_modules():
    static_dir = AppConfig.for_workspace(Path(".")).static_dir
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")

    assert '<script type="module" src="/app.js' in index_html
    assert 'from "/api.js' in app_js
    assert 'from "/state.js' in app_js
    assert 'from "/ui.js' in app_js
    assert (static_dir / "api.js").exists()
    assert (static_dir / "state.js").exists()
    assert (static_dir / "ui.js").exists()


def test_readme_includes_operator_flow_and_faq():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "业务人员使用流程" in readme
    assert "常见问题" in readme
    assert "验收流程" in readme
