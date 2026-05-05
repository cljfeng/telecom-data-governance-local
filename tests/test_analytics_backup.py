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
