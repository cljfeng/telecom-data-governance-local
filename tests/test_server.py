import json
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.db import connect, initialize_database
from governance_app.server import create_app


def _multipart_upload_body(
    file_path: Path,
    fields: dict[str, str] | None = None,
    file_field: str = "file",
) -> tuple[str, bytes]:
    boundary = "----codex-test-boundary"
    parts: list[bytes] = []
    for name, value in (fields or {}).items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
        ).encode("utf-8")
        + file_path.read_bytes()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


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


def test_export_endpoint_accepts_province_mode(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": 1}))

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/export",
        json.dumps({"batch_id": 1, "mode": "province"}),
    )

    assert status == 200
    paths = json.loads(body)["paths"]
    assert len(paths) == 1
    assert "全省" in paths[0]


def test_notice_report_endpoint_exports_excel(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": 1}))

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/reports/notice",
        json.dumps({"batch_id": 1}),
    )

    assert status == 200
    assert json.loads(body)["path"].endswith(".xlsx")


def test_reset_endpoint_requires_confirmation(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/reset",
        json.dumps({"confirmation": "reset"}),
    )

    assert status == 400
    assert "复位" in json.loads(body)["error"]

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/reset",
        json.dumps({"confirmation": "复位"}),
    )

    assert status == 200
    assert json.loads(body)["cleared"] is True
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from import_batches").fetchone()["c"] == 0


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


def test_import_preview_upload_endpoint_accepts_selected_file(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    content_type, body = _multipart_upload_body(sample_workbook)

    status, headers, response_body = app.handle_test_upload_request("/api/import/preview/upload", content_type, body)

    data = json.loads(response_body)
    assert status == 200
    assert data["ok"] is True
    assert data["ledger_counts"]["site"] == 1
    assert (app_config.data_dir / "uploads").exists()
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from import_batches").fetchone()["c"] == 0


def test_import_upload_endpoint_imports_selected_file(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    content_type, body = _multipart_upload_body(sample_workbook, {"strategy": "new"})

    status, headers, response_body = app.handle_test_upload_request("/api/import/upload", content_type, body)

    data = json.loads(response_body)
    assert status == 200
    assert data["batch_id"] == 1
    assert data["ledger_counts"]["site"] == 1
    with connect(app_config) as conn:
        batch = conn.execute("select source_file, name from import_batches where id = 1").fetchone()
        assert "/uploads/" in batch["source_file"]
        assert batch["name"] == "sample_template"


def test_import_upload_endpoint_reports_missing_file_without_generic_http_400(app_config):
    initialize_database(app_config)
    app = create_app(app_config)
    boundary = "----codex-test-boundary"
    content_type = f"multipart/form-data; boundary={boundary}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="strategy"\r\n\r\n'
        "new\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    status, headers, response_body = app.handle_test_upload_request("/api/import/upload", content_type, body)

    assert status == 400
    assert json.loads(response_body)["error"] == "请选择台账文件"


def test_import_endpoint_accepts_append_and_replace_strategy(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/import",
        json.dumps({"path": str(sample_workbook), "strategy": "append", "batch_id": batch_id}),
    )

    assert status == 200
    assert json.loads(body)["batch_id"] == batch_id
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from ledger_rows where batch_id = ?", (batch_id,)).fetchone()["c"] == 8

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/import",
        json.dumps({"path": str(sample_workbook), "strategy": "replace", "batch_id": batch_id}),
    )

    assert status == 200
    assert json.loads(body)["batch_id"] == batch_id
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from ledger_rows where batch_id = ?", (batch_id,)).fetchone()["c"] == 4


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


def test_import_endpoint_returns_readable_error_with_validation_details(app_config, workbook_missing_site_code):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/import",
        json.dumps({"path": str(workbook_missing_site_code)}),
    )

    data = json.loads(body)
    assert status == 400
    assert data["error"] == "导入未通过，请按错误明细修正后重试"
    assert data["errors"][0]["field_name"] == "电信站址编码"


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
    payload = json.loads(body)
    issue = payload["issues"][0]
    assert issue["city"] == "杭州"
    assert payload["rules"][0]["rule_id"] == issue["rule_id"]
    assert payload["rules"][0]["issue_count"] == 1

    status, headers, body = app.handle_test_request("GET", f"/api/issues?batch_id={batch_id}&rule_id={issue['rule_id']}")

    assert status == 200
    assert json.loads(body)["issues"][0]["rule_id"] == issue["rule_id"]

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
    headers_by_name = {cell.value: cell.column for cell in ws[1]}
    ws.cell(row=2, column=headers_by_name["整改结果"]).value = "已修复"
    ws.cell(row=2, column=headers_by_name["整改说明"]).value = "已补正"
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


def test_correction_upload_endpoint_imports_selected_file(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]
    with connect(app_config) as conn:
        conn.execute("update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": batch_id}))
    status, headers, body = app.handle_test_request("POST", "/api/export", json.dumps({"batch_id": batch_id}))
    exported = Path(json.loads(body)["paths"][0])

    from openpyxl import load_workbook

    wb = load_workbook(exported)
    ws = wb["整改问题清单"]
    headers_by_name = {cell.value: cell.column for cell in ws[1]}
    ws.cell(row=2, column=headers_by_name["整改结果"]).value = "已修复"
    ws.cell(row=2, column=headers_by_name["整改说明"]).value = "已按原台账修正"
    wb.save(exported)
    content_type, upload_body = _multipart_upload_body(exported)

    status, headers, body = app.handle_test_upload_request("/api/corrections/upload", content_type, upload_body)

    assert status == 200
    assert json.loads(body)["matched_count"] == 1


def test_archive_endpoint_returns_json_error_before_return(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]

    status, headers, body = app.handle_test_request("POST", "/api/archive", json.dumps({"batch_id": batch_id}))

    assert status == 400
    assert json.loads(body)["error"] == "batch must be ready for archive"


def test_archive_precheck_endpoint_reports_blockers(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]
    with connect(app_config) as conn:
        conn.execute("update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": batch_id}))
    app.handle_test_request("POST", "/api/export", json.dumps({"batch_id": batch_id}))
    with connect(app_config) as conn:
        conn.execute("update import_batches set status = 'returning' where id = ?", (batch_id,))

    status, headers, body = app.handle_test_request("GET", f"/api/archive/precheck?batch_id={batch_id}")

    assert status == 200
    payload = json.loads(body)
    assert payload["ready"] is False
    assert payload["open_issue_count"] == 1


def test_rules_api_lists_settings_and_updates_rule_config(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("GET", "/api/rules")

    assert status == 200
    payload = json.loads(body)
    price_rule = next(rule for rule in payload["rules"] if rule["rule_id"] == "electricity_price_range")
    assert price_rule["enabled"] is True
    assert price_rule["config"] == {}

    status, headers, body = app.handle_test_request(
        "POST",
        "/api/rules/settings",
        json.dumps({"rule_id": "electricity_price_range", "enabled": False, "config": {"max": 3}}, ensure_ascii=False),
    )

    assert status == 200
    assert json.loads(body) == {"status": "updated"}
    status, headers, body = app.handle_test_request("GET", "/api/rules")
    payload = json.loads(body)
    price_rule = next(rule for rule in payload["rules"] if rule["rule_id"] == "electricity_price_range")
    assert price_rule["enabled"] is False
    assert price_rule["config"] == {"max": 3}


def test_ledger_rows_endpoint_filters_current_batch_data(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    status, headers, body = app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    batch_id = json.loads(body)["batch_id"]

    status, headers, body = app.handle_test_request(
        "GET",
        f"/api/ledger-rows?batch_id={batch_id}&ledger_type=electricity&city=杭州&site_code=HZ001",
    )

    assert status == 200
    rows = json.loads(body)["rows"]
    assert len(rows) == 1
    assert rows[0]["field_groups"]["电表报账"]["电表户号"] == "M001"


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


def test_settings_endpoint_reports_local_paths(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, headers, body = app.handle_test_request("GET", "/api/settings")

    assert status == 200
    payload = json.loads(body)
    assert payload["database_path"].endswith("governance.sqlite3")
    assert payload["export_dir"].endswith("exports")
    assert payload["backup_dir"].endswith("backups")
    assert payload["template_version"] == "2026-05-05"


def test_restore_endpoint_creates_safety_backup(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    status, headers, body = app.handle_test_request("POST", "/api/backup", "{}")
    backup_path = json.loads(body)["path"]

    status, headers, body = app.handle_test_request("POST", "/api/restore", json.dumps({"path": backup_path}))

    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "restored"
    assert payload["safety_backup_path"].endswith(".sqlite3")
    assert Path(payload["safety_backup_path"]).exists()


def test_static_handler_disables_browser_cache():
    from governance_app.server import RequestHandler

    assert RequestHandler.extra_static_headers()["Cache-Control"] == "no-store, max-age=0"
    assert RequestHandler.extra_static_headers()["Pragma"] == "no-cache"


def test_workbench_static_assets_use_lightweight_modules():
    static_dir = AppConfig.for_workspace(Path(".")).static_dir
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    ledger_data_js = (static_dir / "ledger-data.js").read_text(encoding="utf-8")

    assert '<script type="module" src="/app.js' in index_html
    assert 'data-view="ledgerData">数据整理' in index_html
    assert 'from "/api.js' in app_js
    assert 'from "/state.js' in app_js
    assert 'from "/ui.js' in app_js
    assert 'from "/ledger-data.js' in app_js
    assert 'from "/rules.js' in app_js
    assert 'from "/settings.js' in app_js
    assert 'from "/analytics.js' in app_js
    assert "/api/ledger-rows?" in ledger_data_js
    assert 'data-view="rules">规则设置' in index_html
    assert 'data-view="settings">本地设置' in index_html
    assert (static_dir / "api.js").exists()
    assert (static_dir / "state.js").exists()
    assert (static_dir / "ui.js").exists()
    assert (static_dir / "ledger-data.js").exists()
    assert (static_dir / "rules.js").exists()
    assert (static_dir / "settings.js").exists()
    assert (static_dir / "analytics.js").exists()


def test_rules_static_module_calls_rules_api():
    static_dir = AppConfig.for_workspace(Path(".")).static_dir
    rules_js = (static_dir / "rules.js").read_text(encoding="utf-8")

    assert "/api/rules" in rules_js
    assert "/api/rules/settings" in rules_js
    assert "electricity_price_range" in rules_js


def test_import_page_exposes_import_strategies():
    static_dir = AppConfig.for_workspace(Path(".")).static_dir
    app_js = (static_dir / "app.js").read_text(encoding="utf-8")

    assert 'id="import-strategy"' in app_js
    assert 'value="new"' in app_js
    assert 'value="append"' in app_js
    assert 'value="replace"' in app_js


def test_settings_static_module_calls_settings_and_backup_api():
    static_dir = AppConfig.for_workspace(Path(".")).static_dir
    settings_js = (static_dir / "settings.js").read_text(encoding="utf-8")

    assert "/api/settings" in settings_js
    assert "/api/backup" in settings_js
    assert "/api/restore" in settings_js


def test_analytics_static_module_uses_dashboard_summary():
    static_dir = AppConfig.for_workspace(Path(".")).static_dir
    analytics_js = (static_dir / "analytics.js").read_text(encoding="utf-8")

    assert "/api/dashboard?batch_id=" in analytics_js
    assert "issues_by_severity" in analytics_js
    assert "closure_rate" in analytics_js


def test_readme_includes_operator_flow_and_faq():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "业务人员使用流程" in readme
    assert "常见问题" in readme
    assert "验收流程" in readme


def test_start_script_documents_local_launch_command():
    script = Path("scripts/start.sh")

    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert "PYTHONPATH=src" in content
    assert "governance_app.server" in content
    assert "--port" in content


def test_check_script_runs_project_verification_commands():
    script = Path("scripts/check.sh")

    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert ".venv/bin/python -m pytest -q" in content
    assert "node --check src/governance_app/static/app.js" in content
    assert "bash -n scripts/start.sh" in content
