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
    list_issues,
    set_current_batch,
    update_issue_status,
)


def test_create_list_and_select_batches(app_config):
    initialize_database(app_config)

    first_id = create_batch(app_config, "2026年第一批核查")
    second_id = create_batch(app_config, "2026年第二批核查")
    set_current_batch(app_config, second_id)

    batches = list_batches(app_config)

    assert [batch["name"] for batch in batches] == ["2026年第二批核查", "2026年第一批核查"]
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


def test_issue_filters_and_city_progress(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    export_city_issue_packages(app_config, imported.batch_id)

    issues = list_issues(app_config, imported.batch_id, {"city": "杭州", "ledger_type": "electricity", "severity": "high"})
    update_issue_status(app_config, issues[0]["issue_code"], "closed")
    progress = city_progress(app_config, imported.batch_id)

    assert len(issues) == 1
    assert issues[0]["city"] == "杭州"
    assert issues[0]["ledger_type"] == "electricity"
    assert issues[0]["rule_name"] == "电费单价合理性"
    assert progress[0]["city"] == "杭州"
    assert progress[0]["total_count"] == 1
    assert progress[0]["closed_count"] == 1
    assert progress[0]["completion_rate"] == 100.0


def test_workflow_returns_recent_operations(app_config):
    initialize_database(app_config)
    batch_id = create_batch(app_config, "专项批次")

    workflow = get_batch_workflow(app_config, batch_id)

    assert workflow["operations"][0]["operation"] == "create_batch"
    assert "专项批次" in workflow["operations"][0]["message"]


def test_update_issue_status_rejects_unknown_status(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    issue = list_issues(app_config, imported.batch_id, {})[0]

    with pytest.raises(ValueError, match="invalid issue status"):
        update_issue_status(app_config, issue["issue_code"], "bad_status")


def test_update_issue_status_rejects_archived_batch(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute("update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'")
    run_audit(app_config, imported.batch_id)
    issue = list_issues(app_config, imported.batch_id, {})[0]
    with connect(app_config) as conn:
        conn.execute("update import_batches set status = 'archived', is_archived = 1 where id = ?", (imported.batch_id,))

    with pytest.raises(ValueError, match="batch is archived"):
        update_issue_status(app_config, issue["issue_code"], "closed")
