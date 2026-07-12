import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import connect, initialize_database


def _handler():
    assert importlib.util.find_spec("governance_app.routes.analysis") is not None
    return importlib.import_module("governance_app.routes.analysis").handle_analysis_route


def _review_batch(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, status) values ('review.xlsx', 'review', 'audited')"
        ).lastrowid
        audit_run_id = conn.execute(
            "insert into audit_runs(batch_id, rule_count) values (?, 2)", (batch_id,)
        ).lastrowid
        opportunities = {}
        for storage_domain, suffix in (("electricity", "ELEC"), ("tower_rent", "TOWER")):
            audit_result_id = conn.execute(
                """
                insert into audit_results(
                    audit_run_id, rule_id, severity, message, result_json
                ) values (?, ?, 'high', '待核查', '{}')
                """,
                (audit_run_id, f"RULE-{suffix}"),
            ).lastrowid
            issue_code = f"ISSUE-{suffix}"
            conn.execute(
                """
                insert into issues(
                    issue_code, audit_result_id, batch_id, ledger_type, rule_id,
                    severity, message, suggestion
                ) values (?, ?, ?, ?, ?, 'high', '待核查', '请核查')
                """,
                (issue_code, audit_result_id, batch_id, storage_domain, f"RULE-{suffix}"),
            )
            opportunity_code = f"OPP-{suffix}"
            conn.execute(
                """
                insert into analysis_opportunities(
                    batch_id, domain, opportunity_code, source_issue_code,
                    opportunity_type, severity, recoverable_amount,
                    saving_opportunity_amount, confidence, message, suggestion
                ) values (?, ?, ?, ?, '异常费用', 'high', 1500, 900, 'high', '待核查', '请核查')
                """,
                (batch_id, storage_domain, opportunity_code, issue_code),
            )
            opportunities[storage_domain] = opportunity_code
    return batch_id, opportunities


def test_analysis_handler_owns_dynamic_summary_path(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute(
            "insert into import_batches(source_file, name, status) values ('test.xlsx', 'test', 'audited')"
        )

    response = _handler()(app_config, "GET", urlparse("/api/batches/1/electricity-analysis/summary"), "")

    assert response[0] == 200
    assert json.loads(response[2])["batch_id"] == 1


def test_analysis_handler_rejects_invalid_batch_id(app_config):
    response = _handler()(
        app_config,
        "GET",
        urlparse("/api/batches/bad/electricity-analysis/summary"),
        "",
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "invalid batch_id"


def test_analysis_handler_returns_none_for_other_domain(app_config):
    assert _handler()(app_config, "GET", urlparse("/api/health"), "") is None


def test_analysis_handler_saves_review_for_both_specialist_domains(app_config):
    batch_id, opportunities = _review_batch(app_config)

    for route_domain, storage_domain in (
        ("electricity-analysis", "electricity"),
        ("tower-rent-analysis", "tower_rent"),
    ):
        response = _handler()(
            app_config,
            "POST",
            urlparse(f"/api/batches/{batch_id}/{route_domain}/review"),
            json.dumps(
                {
                    "opportunity_code": opportunities[storage_domain],
                    "status": "closed",
                    "verified_recoverable_amount": 1200.5,
                    "realized_saving_amount": 800,
                    "review_note": "已退款并完成优化",
                }
            ),
        )

        assert response[0] == 200
        payload = json.loads(response[2])
        assert payload["issue_status"] == "closed"
        assert payload["verified_recoverable_amount"] == 1200.5
        assert payload["realized_saving_amount"] == 800.0


def test_analysis_handler_rejects_invalid_review_json(app_config):
    batch_id, _ = _review_batch(app_config)

    response = _handler()(
        app_config,
        "POST",
        urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
        "{bad json",
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "请求内容不是有效 JSON"


def test_analysis_handler_requires_review_json_object(app_config):
    batch_id, _ = _review_batch(app_config)

    response = _handler()(
        app_config,
        "POST",
        urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
        json.dumps([]),
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "请求内容必须是 JSON 对象"


def test_analysis_handler_requires_review_opportunity_code(app_config):
    batch_id, _ = _review_batch(app_config)

    response = _handler()(
        app_config,
        "POST",
        urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
        json.dumps({"status": "closed"}),
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "机会编号不能为空"


def test_analysis_handler_rejects_invalid_review_status(app_config):
    batch_id, opportunities = _review_batch(app_config)

    response = _handler()(
        app_config,
        "POST",
        urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
        json.dumps({"opportunity_code": opportunities["electricity"], "status": "bad"}),
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "专题核查状态无效"


def test_analysis_handler_rejects_opportunity_from_other_domain(app_config):
    batch_id, opportunities = _review_batch(app_config)

    response = _handler()(
        app_config,
        "POST",
        urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
        json.dumps({"opportunity_code": opportunities["tower_rent"], "status": "closed"}),
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "机会不存在或不属于当前批次专题"


def test_analysis_handler_rejects_review_for_archived_batch(app_config):
    batch_id, opportunities = _review_batch(app_config)
    with connect(app_config) as conn:
        conn.execute(
            "update import_batches set status = 'archived', is_archived = 1 where id = ?",
            (batch_id,),
        )

    response = _handler()(
        app_config,
        "POST",
        urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
        json.dumps({"opportunity_code": opportunities["electricity"], "status": "closed"}),
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "批次已归档，不能修改专题核查结果"
