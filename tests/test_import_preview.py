from governance_app.db import connect, initialize_database
from openpyxl import load_workbook

from governance_app.import_preview import export_preview_errors, list_recent_files, preview_workbook


def test_preview_workbook_reports_counts_without_writing_database(app_config, sample_workbook):
    initialize_database(app_config)

    result = preview_workbook(app_config, sample_workbook)

    assert result.ok is True
    assert result.ledger_counts == {"site": 1, "tower_rent": 1, "electricity": 1, "generator": 1}
    assert result.errors == []
    with connect(app_config) as conn:
        assert conn.execute("select count(*) as c from import_batches").fetchone()["c"] == 0
        assert conn.execute("select count(*) as c from ledger_rows").fetchone()["c"] == 0
        assert conn.execute("select count(*) as c from recent_files").fetchone()["c"] == 1


def test_preview_workbook_reports_missing_required_headers(app_config, workbook_missing_site_code):
    initialize_database(app_config)

    result = preview_workbook(app_config, workbook_missing_site_code)

    assert result.ok is False
    assert result.batch_name == "missing_site_code"
    assert result.errors[0].field_name == "电信站址编码"
    assert result.errors[0].message == "缺少必需字段"


def test_export_preview_errors_writes_excel_detail(app_config, workbook_missing_site_code):
    initialize_database(app_config)
    result = preview_workbook(app_config, workbook_missing_site_code)

    path = export_preview_errors(app_config, workbook_missing_site_code, result)

    assert path.exists()
    wb = load_workbook(path)
    ws = wb["导入错误明细"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("来源文件", "行号", "字段名", "错误类型", "处理建议")
    assert rows[1][2] == "电信站址编码"
    assert rows[1][3] == "缺少必需字段"


def test_recent_files_keep_latest_preview_and_import_results(app_config, sample_workbook, workbook_missing_site_code):
    initialize_database(app_config)
    preview_workbook(app_config, workbook_missing_site_code)
    preview_workbook(app_config, sample_workbook)

    recent = list_recent_files(app_config)

    assert [item["path"] for item in recent] == [str(sample_workbook), str(workbook_missing_site_code)]
    assert recent[0]["ok"] is True
    assert recent[0]["ledger_counts"]["site"] == 1
    assert recent[1]["ok"] is False
    assert recent[1]["error_count"] == 1
