from governance_app.db import connect, initialize_database
from governance_app.importer import import_workbook


def test_import_workbook_stores_batch_raw_rows_and_ledger_rows(app_config, sample_workbook):
    initialize_database(app_config)

    result = import_workbook(app_config, sample_workbook)

    assert result.batch_id == 1
    assert result.errors == []
    assert result.ledger_counts == {"site": 1, "tower_rent": 1, "electricity": 1, "generator": 1}

    with connect(app_config) as conn:
        raw_count = conn.execute("select count(*) as c from raw_rows").fetchone()["c"]
        ledger_count = conn.execute("select count(*) as c from ledger_rows").fetchone()["c"]
        assert raw_count == 4
        assert ledger_count == 4


def test_import_workbook_reports_missing_required_header(app_config, workbook_missing_site_code):
    initialize_database(app_config)

    result = import_workbook(app_config, workbook_missing_site_code)

    assert result.batch_id is None
    assert result.errors[0].field_name == "电信站址编码"
    assert "缺少必需字段" in result.errors[0].message
