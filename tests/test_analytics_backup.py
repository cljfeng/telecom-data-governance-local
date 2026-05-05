from governance_app.analytics import dashboard_summary
from governance_app.audit_engine import run_audit
from governance_app.backup import create_backup, restore_backup
from governance_app.db import connect, initialize_database
from governance_app.importer import import_workbook


def test_dashboard_summary_counts_batch_and_issues(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    run_audit(app_config, imported.batch_id)

    summary = dashboard_summary(app_config, imported.batch_id)

    assert summary["batch_id"] == imported.batch_id
    assert summary["ledger_counts"]["site"] == 1
    assert "issues_by_city" in summary


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
