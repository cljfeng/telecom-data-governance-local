from governance_app.audit_engine import run_audit
from governance_app.backup import create_backup
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook
from governance_app.reset import reset_system


def test_reset_system_clears_business_data_and_keeps_exports_and_backups_by_default(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    export_paths = export_city_issue_packages(app_config, imported.batch_id)
    backup_path = create_backup(app_config)

    result = reset_system(app_config, confirmation="复位")

    assert result["cleared"] is True
    assert export_paths[0].exists()
    assert backup_path.exists()
    assert result["safety_backup_path"]
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from import_batches").fetchone()["c"] == 0
        assert conn.execute("select count(*) as c from ledger_rows").fetchone()["c"] == 0
        assert conn.execute("select count(*) as c from issues").fetchone()["c"] == 0
        assert conn.execute("select count(*) as c from recent_files").fetchone()["c"] == 0
        assert conn.execute("select value_json from settings where key = 'current_batch_id'").fetchone() is None


def test_reset_system_can_clear_exports_and_old_backups(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    export_path = export_city_issue_packages(app_config, imported.batch_id)[0]
    old_backup = create_backup(app_config)

    result = reset_system(
        app_config,
        confirmation="复位",
        preserve_exports=False,
        preserve_backups=False,
    )

    assert not export_path.exists()
    assert not old_backup.exists()
    assert result["safety_backup_path"]
