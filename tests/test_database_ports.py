import pytest

from governance_app.adapters.sqlite_database import SqliteDatabase
from governance_app.archive import archive_batch
from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.electricity_analysis import (
    get_electricity_summary,
    run_electricity_analysis,
)
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook
from governance_app.workflow import (
    city_progress,
    count_ledger_rows,
    create_batch,
    get_batch_workflow,
    list_batches,
    list_issue_groups,
    list_issue_rules,
    list_issues,
    list_ledger_rows,
    set_current_batch,
    transition_batch,
    update_issue_status,
)


def test_sqlite_database_commits_batch_unit_of_work(app_config):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        batch_id = create_batch(app_config, "接口迁移批次", database=database)
        transition_batch(app_config, batch_id, "import", database=database)

        batches = list_batches(app_config, database=database)
    finally:
        database.dispose()

    assert batches == [
        {
            "id": batch_id,
            "name": "接口迁移批次",
            "batch_code": batches[0]["batch_code"],
            "source_file": "",
            "template_version": "2026-05-05",
            "created_at": batches[0]["created_at"],
            "status": "imported",
            "is_archived": False,
            "archived_at": None,
            "is_current": True,
        }
    ]


def test_sqlite_database_rolls_back_failed_unit_of_work(app_config):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        with pytest.raises(RuntimeError, match="force rollback"):
            with database.unit_of_work() as unit_of_work:
                unit_of_work.batches.create(name="不会提交", batch_code="rollback")
                raise RuntimeError("force rollback")

        assert list_batches(app_config, database=database) == []
    finally:
        database.dispose()


def test_batch_services_reject_unknown_batch_through_database_port(app_config):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        with pytest.raises(ValueError, match="batch not found"):
            set_current_batch(app_config, 999, database=database)
        with pytest.raises(ValueError, match="batch not found"):
            transition_batch(app_config, 999, "import", database=database)
    finally:
        database.dispose()


def test_issue_services_query_and_update_through_database_port(
    app_config,
    sample_workbook,
):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as connection:
        connection.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') "
            "where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    database = SqliteDatabase(app_config.database_path)

    try:
        page = list_issues(
            app_config,
            imported.batch_id,
            {"city": "杭州", "closure": "open"},
            limit=20,
            database=database,
        )
        issues = page["issues"]
        rules = list_issue_rules(app_config, imported.batch_id, database=database)
        groups = list_issue_groups(app_config, imported.batch_id, database=database)
        update_issue_status(
            app_config,
            issues[0]["issue_code"],
            "closed",
            database=database,
        )
        closed = list_issues(
            app_config,
            imported.batch_id,
            {"closure": "closed"},
            limit=20,
            database=database,
        )
    finally:
        database.dispose()

    assert page["total"] == 2
    assert issues[0]["group"]["same_site_rule_count"] == 1
    assert rules[0]["issue_count"] == 1
    assert groups
    assert closed["total"] == 1
    assert closed["issues"][0]["status"] == "closed"


def test_issue_update_rolls_back_status_and_event_together(
    app_config,
    sample_workbook,
):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as connection:
        connection.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') "
            "where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    database = SqliteDatabase(app_config.database_path)

    try:
        issue_code = list_issues(
            app_config,
            imported.batch_id,
            {},
            database=database,
        )[0]["issue_code"]
        with pytest.raises(RuntimeError, match="force rollback"):
            with database.unit_of_work() as unit_of_work:
                issue = unit_of_work.issues.get_with_batch(issue_code)
                assert issue is not None
                unit_of_work.issues.update_status(
                    issue,
                    "closed",
                    source="test",
                    event_note="rollback",
                )
                raise RuntimeError("force rollback")
    finally:
        database.dispose()

    with connect(app_config) as connection:
        issue = connection.execute(
            "select id, status from issues where issue_code = ?",
            (issue_code,),
        ).fetchone()
        events = connection.execute(
            "select count(*) as count from issue_events "
            "where issue_id = ? and source = 'test'",
            (issue["id"],),
        ).fetchone()["count"]

    assert issue["status"] != "closed"
    assert events == 0


def test_import_audit_and_ledger_queries_share_database_port(
    app_config,
    sample_workbook,
):
    initialize_database(app_config)
    database = SqliteDatabase(app_config.database_path)

    try:
        imported = import_workbook(
            app_config,
            sample_workbook,
            database=database,
        )
        with connect(app_config) as connection:
            connection.execute(
                "update raw_rows set row_json = replace(row_json, '0.8', '9.9') "
                "where ledger_type = 'electricity'"
            )
        ledger_rows = list_ledger_rows(
            app_config,
            imported.batch_id,
            {"city": "杭州"},
            database=database,
        )
        ledger_count = count_ledger_rows(
            app_config,
            imported.batch_id,
            {},
            database=database,
        )
        audit = run_audit(
            app_config,
            imported.batch_id,
            database=database,
        )
        workflow = get_batch_workflow(
            app_config,
            imported.batch_id,
            database=database,
        )
        progress = city_progress(
            app_config,
            imported.batch_id,
            database=database,
        )
        analysis = run_electricity_analysis(
            app_config,
            imported.batch_id,
            database=database,
        )
        electricity_summary = get_electricity_summary(
            app_config,
            imported.batch_id,
            database=database,
        )
        export_paths = export_city_issue_packages(
            app_config,
            imported.batch_id,
            database=database,
        )
        page = list_issues(
            app_config,
            imported.batch_id,
            {},
            database=database,
        )
        for issue in page:
            update_issue_status(
                app_config,
                issue["issue_code"],
                "closed",
                database=database,
            )
        with database.unit_of_work() as unit_of_work:
            unit_of_work.batches.update_status(imported.batch_id, "returning")
        archive_path = archive_batch(
            app_config,
            imported.batch_id,
            database=database,
        )
    finally:
        database.dispose()

    assert ledger_count == 4
    assert len(ledger_rows) == 3
    assert audit.audit_run_id > 0
    assert audit.issue_count == 2
    assert workflow["todo_summary"]["ledger_count"] == 4
    assert workflow["todo_summary"]["total_issue_count"] == 2
    assert progress[0]["total_count"] == 2
    assert analysis["opportunity_count"] >= 1
    assert electricity_summary["analysis_generated"] is True
    assert export_paths
    assert archive_path.exists()
