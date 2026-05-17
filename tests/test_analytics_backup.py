from governance_app.analytics import dashboard_summary
from governance_app.archive import archive_batch
from governance_app.audit_engine import run_audit
from governance_app.backup import create_backup, restore_backup
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook


def test_dashboard_summary_counts_batch_and_issues(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)

    summary = dashboard_summary(app_config, imported.batch_id)

    assert summary["batch_id"] == imported.batch_id
    assert summary["ledger_counts"]["site"] == 1
    assert "issues_by_city" in summary
    assert summary["issues_by_rule"][0]["rule_name"]


def test_backup_and_restore_database(app_config, sample_workbook):
    initialize_database(app_config)
    import_workbook(app_config, sample_workbook)

    backup_path = create_backup(app_config)

    app_config.database_path.unlink()
    restore_backup(app_config, backup_path)

    with connect(app_config) as conn:
        count = conn.execute("select count(*) as c from import_batches").fetchone()["c"]
        assert count == 1


def test_create_backup_does_not_overwrite_same_second_backup(app_config, sample_workbook):
    initialize_database(app_config)
    import_workbook(app_config, sample_workbook)

    first = create_backup(app_config)
    second = create_backup(app_config)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_restore_backup_rejects_file_outside_backup_dir(app_config, sample_workbook):
    initialize_database(app_config)
    import_workbook(app_config, sample_workbook)
    outside = app_config.workspace_dir / "outside.sqlite3"
    outside.write_text("not a backup", encoding="utf-8")

    try:
        restore_backup(app_config, outside)
    except ValueError as exc:
        assert "backups" in str(exc)
    else:
        raise AssertionError("restore_backup should reject files outside backups directory")


def test_archive_batch_requires_returning_or_reviewed_batch(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    run_audit(app_config, imported.batch_id)

    try:
        archive_batch(app_config, imported.batch_id)
    except ValueError as exc:
        assert "batch must be ready for archive" in str(exc)
    else:
        raise AssertionError("archive_batch should reject batches before correction return")


def test_archive_batch_writes_operation_log_sheet_and_locks_batch(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    export_city_issue_packages(app_config, imported.batch_id)
    with connect(app_config) as conn:
        conn.execute("update issues set status = 'closed' where batch_id = ?", (imported.batch_id,))
        conn.execute("update import_batches set status = 'returning' where id = ?", (imported.batch_id,))

    path = archive_batch(app_config, imported.batch_id)

    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert "操作日志" in wb.sheetnames
    issue_ws = wb["问题清单"]
    assert issue_ws["H1"].value == "规则名称"
    assert issue_ws["H2"].value == "电费单价合理性"
    with connect(app_config) as conn:
        batch = conn.execute("select status, is_archived from import_batches where id = ?", (imported.batch_id,)).fetchone()
        assert batch["status"] == "archived"
        assert batch["is_archived"] == 1
