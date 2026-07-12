import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import connect, initialize_database
from governance_app.server import create_app


def _module():
    assert importlib.util.find_spec("governance_app.routes.reports") is not None
    return importlib.import_module("governance_app.routes.reports")


def _audited_batch_with_review(app_config, source_status, other_status=None):
    initialize_database(app_config)
    with connect(app_config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, status) values ('review.xlsx', 'review', 'audited')"
        ).lastrowid
        audit_run_id = conn.execute(
            "insert into audit_runs(batch_id, rule_count) values (?, 1)",
            (batch_id,),
        ).lastrowid

        def insert_issue(issue_code, status):
            audit_result_id = conn.execute(
                """
                insert into audit_results(
                    audit_run_id, rule_id, severity, message, result_json
                ) values (?, 'electricity_price_range', 'high', '待核查', '{}')
                """,
                (audit_run_id,),
            ).lastrowid
            conn.execute(
                """
                insert into issues(
                    issue_code, audit_result_id, batch_id, ledger_type, rule_id,
                    severity, status, message, suggestion
                ) values (?, ?, ?, 'electricity', 'electricity_price_range',
                          'high', ?, '待核查', '请核查')
                """,
                (issue_code, audit_result_id, batch_id, status),
            )

        source_issue_code = "ISSUE-REVIEWED"
        insert_issue(source_issue_code, source_status)
        if other_status is not None:
            insert_issue("ISSUE-OTHER", other_status)
        conn.execute(
            """
            insert into analysis_opportunity_reviews(
                batch_id, domain, opportunity_code, opportunity_type,
                source_issue_code, estimated_recoverable_amount,
                estimated_saving_amount, verified_recoverable_amount,
                realized_saving_amount, review_note
            ) values (?, 'electricity', 'OPP-REVIEWED', '异常费用', ?,
                      1500, 900, 1200.5, 800, '已完成核查')
            """,
            (batch_id, source_issue_code),
        )
    return batch_id


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


def test_archive_api_accepts_initial_audited_batch_with_closed_review(app_config):
    batch_id = _audited_batch_with_review(app_config, "closed")
    app = create_app(app_config)

    precheck = app.handle_test_request(
        "GET", f"/api/archive/precheck?batch_id={batch_id}"
    )
    archive = app.handle_test_request(
        "POST", "/api/archive", json.dumps({"batch_id": batch_id})
    )

    assert precheck[0] == 200
    assert json.loads(precheck[2])["ready"] is True
    assert json.loads(precheck[2])["blockers"] == []
    assert archive[0] == 200
    assert json.loads(archive[2])["path"].endswith("专项治理归档汇总.xlsx")


def test_archive_api_rejects_post_review_reaudit_with_other_open_issue(app_config):
    batch_id = _audited_batch_with_review(
        app_config, "resolved_by_reaudit", other_status="pending_export"
    )
    app = create_app(app_config)

    precheck = app.handle_test_request(
        "GET", f"/api/archive/precheck?batch_id={batch_id}"
    )
    archive = app.handle_test_request(
        "POST", "/api/archive", json.dumps({"batch_id": batch_id})
    )

    assert precheck[0] == 200
    payload = json.loads(precheck[2])
    assert payload["ready"] is False
    assert payload["open_issue_count"] == 1
    assert [item["type"] for item in payload["blockers"]] == ["open_issues"]
    assert archive[0] == 400
    assert json.loads(archive[2])["error"] == "batch must be ready for archive"


def test_archive_api_accepts_fully_closed_post_review_reaudit(app_config):
    batch_id = _audited_batch_with_review(app_config, "resolved_by_reaudit")
    app = create_app(app_config)

    precheck = app.handle_test_request(
        "GET", f"/api/archive/precheck?batch_id={batch_id}"
    )
    archive = app.handle_test_request(
        "POST", "/api/archive", json.dumps({"batch_id": batch_id})
    )

    assert precheck[0] == 200
    payload = json.loads(precheck[2])
    assert payload["ready"] is True
    assert payload["blockers"] == []
    assert archive[0] == 200
    assert json.loads(archive[2])["path"].endswith("专项治理归档汇总.xlsx")
