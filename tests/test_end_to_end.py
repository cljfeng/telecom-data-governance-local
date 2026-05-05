from governance_app.audit_engine import run_audit
from governance_app.corrections import import_correction_return
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook


def test_full_local_governance_flow(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    audit = run_audit(app_config, imported.batch_id)
    packages = export_city_issue_packages(app_config, imported.batch_id)

    assert imported.batch_id == 1
    assert audit.audit_run_id == 1
    assert packages

    correction = import_correction_return(app_config, packages[0])
    assert correction.errors == []
