from governance_app.db import connect, initialize_database
from governance_app.import_preview import preview_workbook


def test_preview_workbook_reports_counts_without_writing_database(app_config, sample_workbook):
    initialize_database(app_config)

    result = preview_workbook(app_config, sample_workbook)

    assert result.ok is True
    assert result.ledger_counts == {"site": 1, "tower_rent": 1, "electricity": 1, "generator": 1}
    assert result.errors == []
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from import_batches").fetchone()["c"] == 0
        assert conn.execute("select count(*) as c from ledger_rows").fetchone()["c"] == 0


def test_preview_workbook_reports_missing_required_headers(app_config, workbook_missing_site_code):
    initialize_database(app_config)

    result = preview_workbook(app_config, workbook_missing_site_code)

    assert result.ok is False
    assert result.batch_name == "missing_site_code"
    assert result.errors[0].field_name == "电信站址编码"
    assert result.errors[0].message == "缺少必需字段"
