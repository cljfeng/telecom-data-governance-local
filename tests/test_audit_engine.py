from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.importer import import_workbook


def test_run_audit_generates_issue_for_invalid_electricity_price(app_config, sample_workbook):
    initialize_database(app_config)
    result = import_workbook(app_config, sample_workbook)

    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )

    audit = run_audit(app_config, result.batch_id)

    assert audit.issue_count >= 1
    with connect(app_config) as conn:
        issue = conn.execute("select rule_id, status from issues where rule_id = 'electricity_price_range'").fetchone()
        assert issue["status"] == "pending_export"


def test_run_audit_generates_stable_issue_codes(app_config, sample_workbook):
    initialize_database(app_config)
    result = import_workbook(app_config, sample_workbook)

    first = run_audit(app_config, result.batch_id)
    second = run_audit(app_config, result.batch_id)

    assert first.issue_count == second.issue_count
    with connect(app_config) as conn:
        total = conn.execute("select count(*) as c from issues").fetchone()["c"]
        distinct_total = conn.execute("select count(distinct issue_code) as c from issues").fetchone()["c"]
        assert total == distinct_total
