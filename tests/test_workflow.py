import pytest

from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook
from governance_app.workflow import (
    city_progress,
    create_batch,
    get_batch_workflow,
    list_batches,
    list_issue_groups,
    list_issues,
    list_ledger_rows,
    set_current_batch,
    transition_batch,
    update_issue_status,
    update_issue_group_status,
)


def test_create_list_and_select_batches(app_config):
    initialize_database(app_config)

    first_id = create_batch(app_config, "2026年第一批核查")
    second_id = create_batch(app_config, "2026年第二批核查")
    set_current_batch(app_config, second_id)

    batches = list_batches(app_config)

    assert [batch["name"] for batch in batches] == ["2026年第二批核查", "2026年第一批核查"]
    assert batches[0]["batch_code"]
    assert len(batches[0]["batch_code"]) == len("20260604-153012")
    assert batches[0]["is_current"] is True
    assert batches[1]["is_current"] is False
    assert first_id != second_id


def test_workflow_next_action_tracks_batch_status(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)

    workflow = get_batch_workflow(app_config, imported.batch_id)

    assert workflow["batch"]["status"] == "imported"
    assert workflow["next_action"] == "执行稽核"
    assert workflow["steps"][0]["state"] == "done"
    assert workflow["steps"][1]["state"] == "current"
    current_step = workflow["steps"][1]
    assert current_step["can_operate"] is True
    assert current_step["primary_action"]["view"] == "audit"
    assert workflow["steps"][3]["blocked_reason"] == "请先完成执行稽核"
    assert workflow["guidance"]["title"] == "下一步：执行稽核"
    assert workflow["guidance"]["primary_view"] == "audit"
    assert workflow["guidance"]["reason"] == "台账已导入，当前批次还没有形成可整改的问题清单。"
    assert workflow["todo_summary"]["open_issue_count"] == 0
    assert workflow["todo_summary"]["review_count"] == 0


def test_issue_filters_and_city_progress(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    export_city_issue_packages(app_config, imported.batch_id)

    issues = list_issues(app_config, imported.batch_id, {"city": "杭州", "ledger_type": "electricity", "severity": "high"})
    update_issue_status(app_config, issues[0]["issue_code"], "closed")
    progress = city_progress(app_config, imported.batch_id)

    assert len(issues) == 1
    assert issues[0]["city"] == "杭州"
    assert issues[0]["ledger_type"] == "electricity"
    assert issues[0]["rule_name"] == "电费高单价"
    assert issues[0]["explanation"]["rule_name"] == "电费高单价"
    assert issues[0]["confidence"] == "high"
    assert issues[0]["confidence_label"] == "确定性问题"
    assert issues[0]["evidence"]["field"] == "电费单价"
    assert issues[0]["group"]["same_site_rule_count"] == 1
    assert issues[0]["explanation"]["recommended_action"]
    assert issues[0]["review_suggestion"]["decision"] == "等待整改"
    assert progress[0]["city"] == "杭州"
    assert progress[0]["total_count"] == 2
    assert progress[0]["closed_count"] == 1
    assert progress[0]["completion_rate"] == 50.0
    assert progress[0]["top_rules"][0]["rule_name"] == "电费高单价"


def test_list_issues_returns_total_and_supports_pagination(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)

    page = list_issues(app_config, imported.batch_id, {}, limit=1, offset=0)

    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert len(page["issues"]) == 1


def test_list_issues_filters_by_closure_state(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    issues = list_issues(app_config, imported.batch_id, {})
    update_issue_status(app_config, issues[0]["issue_code"], "closed")

    open_page = list_issues(app_config, imported.batch_id, {"closure": "open"}, limit=20)
    closed_page = list_issues(app_config, imported.batch_id, {"closure": "closed"}, limit=20)

    assert open_page["total"] == 1
    assert open_page["issues"][0]["status"] != "closed"
    assert closed_page["total"] == 1
    assert closed_page["issues"][0]["status"] == "closed"


def test_issue_groups_can_be_reviewed_in_bulk(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
        row_json = '{"地市":"杭州","区县":"西湖","电信站址编码":"HZ001","电信站址名称":"西湖一站","电表户号":"M002","报账周期":"2026-05","电费单价":9.9,"供电方式":"直供电","分摊比例(%)":100}'
        conn.execute(
            """
            insert into ledger_rows(batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name, row_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (imported.batch_id, "electricity", "杭州", "西湖", "HZ001", "西湖一站", row_json),
        )
    run_audit(app_config, imported.batch_id)

    groups = list_issue_groups(app_config, imported.batch_id, {})
    target = next(group for group in groups if group["rule_id"] == "electricity_price_range" and group["telecom_site_code"] == "HZ001")

    assert target["issue_count"] == 2
    assert target["open_count"] == 2
    assert target["representative_issue_code"]

    updated = update_issue_group_status(
        app_config,
        imported.batch_id,
        {
            "city": "杭州",
            "ledger_type": "electricity",
            "rule_id": "electricity_price_range",
            "telecom_site_code": "HZ001",
        },
        "not_required",
    )

    assert updated == 2
    refreshed = list_issue_groups(app_config, imported.batch_id, {})
    target = next(group for group in refreshed if group["rule_id"] == "electricity_price_range" and group["telecom_site_code"] == "HZ001")
    assert target["open_count"] == 0
    assert target["not_required_count"] == 2


def test_list_ledger_rows_filters_and_exposes_grouped_fields(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)

    rows = list_ledger_rows(
        app_config,
        imported.batch_id,
        {"ledger_type": "electricity", "city": "杭州", "site_code": "HZ001"},
    )

    assert len(rows) == 1
    assert rows[0]["ledger_type"] == "electricity"
    assert rows[0]["city"] == "杭州"
    assert rows[0]["telecom_site_code"] == "HZ001"
    assert rows[0]["field_groups"]["电表报账"]["电表户号"] == "M001"
    assert rows[0]["field_groups"]["供电分摊"]["电费单价"] == 0.8


def test_workflow_returns_recent_operations(app_config):
    initialize_database(app_config)
    batch_id = create_batch(app_config, "专项批次")

    workflow = get_batch_workflow(app_config, batch_id)

    assert workflow["operations"][0]["operation"] == "create_batch"
    assert "专项批次" in workflow["operations"][0]["message"]


def test_transition_batch_allows_configured_forward_transition(app_config):
    initialize_database(app_config)
    batch_id = create_batch(app_config, "专项批次")

    transition_batch(app_config, batch_id, "import")

    workflow = get_batch_workflow(app_config, batch_id)
    assert workflow["batch"]["status"] == "imported"


def test_transition_batch_rejects_invalid_forward_transition(app_config):
    initialize_database(app_config)
    batch_id = create_batch(app_config, "专项批次")

    with pytest.raises(ValueError, match="invalid batch transition"):
        transition_batch(app_config, batch_id, "export")


def test_update_issue_status_rejects_unknown_status(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    issue = list_issues(app_config, imported.batch_id, {})[0]

    with pytest.raises(ValueError, match="invalid issue status"):
        update_issue_status(app_config, issue["issue_code"], "bad_status")


def test_update_issue_status_records_manual_event(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    issue = list_issues(app_config, imported.batch_id, {})[0]

    update_issue_status(app_config, issue["issue_code"], "closed")

    with connect(app_config) as conn:
        event = conn.execute(
            """
            select from_status, to_status, source
              from issue_events e
              join issues i on i.id = e.issue_id
             where i.issue_code = ? and e.source = 'manual'
            """,
            (issue["issue_code"],),
        ).fetchone()
    assert dict(event) == {"from_status": "pending_export", "to_status": "closed", "source": "manual"}


def test_update_issue_status_rejects_archived_batch(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    issue = list_issues(app_config, imported.batch_id, {})[0]
    with connect(app_config) as conn:
        conn.execute("update import_batches set status = 'archived', is_archived = 1 where id = ?", (imported.batch_id,))

    with pytest.raises(ValueError, match="batch is archived"):
        update_issue_status(app_config, issue["issue_code"], "closed")
