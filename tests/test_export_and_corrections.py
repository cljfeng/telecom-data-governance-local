from openpyxl import load_workbook

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
