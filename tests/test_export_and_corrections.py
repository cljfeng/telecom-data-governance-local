from openpyxl import Workbook, load_workbook

from governance_app.audit_engine import run_audit
from governance_app.corrections import import_correction_return
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook


def test_export_city_issue_packages_writes_issue_workbook(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)

    paths = export_city_issue_packages(app_config, imported.batch_id)

    assert paths
    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    assert ws["A1"].value == "问题编号"
    assert ws["K1"].value == "整改结果"


def test_export_city_issue_packages_sanitizes_city_filename(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    unsafe_city = "杭/州..测试"
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
        conn.execute("update ledger_rows set city = ? where ledger_type = 'electricity'", (unsafe_city,))
    run_audit(app_config, imported.batch_id)

    paths = export_city_issue_packages(app_config, imported.batch_id)

    assert len(paths) == 1
    path = paths[0]
    assert path.resolve().is_relative_to(app_config.export_dir.resolve())
    assert "/" not in path.name
    assert ".." not in path.name
    wb = load_workbook(path)
    ws = wb["整改问题清单"]
    assert ws["B2"].value == unsafe_city


def test_export_city_issue_packages_escapes_formula_like_values(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
        conn.execute(
            """
            update ledger_rows
               set city = '=HYPERLINK("http://bad")',
                   telecom_site_name = '+SUM(1,1)'
             where ledger_type = 'electricity'
            """
        )
    run_audit(app_config, imported.batch_id)

    path = export_city_issue_packages(app_config, imported.batch_id)[0]

    wb = load_workbook(path, data_only=False)
    ws = wb["整改问题清单"]
    assert ws["B2"].value == "'=HYPERLINK(\"http://bad\")"
    assert ws["B2"].data_type == "s"
    assert ws["E2"].value == "'+SUM(1,1)"
    assert ws["E2"].data_type == "s"


def test_import_correction_return_updates_issue_status(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)

    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    ws["K2"] = "已修复"
    ws["L2"] = "已补正"
    wb.save(paths[0])

    result = import_correction_return(app_config, paths[0])

    assert result.matched_count == 1
    with connect(app_config) as conn:
        status = conn.execute("select status from issues limit 1").fetchone()["status"]
        assert status == "needs_review"


def test_import_correction_return_skips_blank_rows(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)

    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    ws["K2"] = "已修复"
    ws["L2"] = "已补正"
    ws.append([""] * 14)
    wb.save(paths[0])

    result = import_correction_return(app_config, paths[0])

    assert result.matched_count == 1
    assert result.errors == []


def test_import_correction_return_skips_issue_code_without_correction(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update ledger_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)

    result = import_correction_return(app_config, paths[0])

    assert result.matched_count == 0
    assert result.errors == []
    with connect(app_config) as conn:
        status = conn.execute("select status from issues limit 1").fetchone()["status"]
        assert status == "pending_correction"


def test_import_correction_return_reports_missing_issue_sheet(app_config, tmp_path):
    initialize_database(app_config)
    path = tmp_path / "malformed_return.xlsx"
    wb = Workbook()
    wb.active.title = "其他"
    wb.save(path)

    result = import_correction_return(app_config, path)

    assert result.matched_count == 0
    assert result.errors == ["缺少 sheet：整改问题清单"]
    with connect(app_config) as conn:
        row = conn.execute("select error_count, errors_json from correction_returns").fetchone()
        assert row["error_count"] == 1
