from openpyxl import Workbook, load_workbook
import pytest

from governance_app.audit_engine import run_audit
from governance_app.archive import archive_batch
from governance_app.corrections import import_correction_return
from governance_app.db import connect, initialize_database
from governance_app.exporter import export_city_issue_packages, export_issue_packages
from governance_app.importer import import_workbook


def test_export_city_issue_packages_writes_issue_workbook(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)

    paths = export_city_issue_packages(app_config, imported.batch_id)

    assert paths
    wb = load_workbook(paths[0])
    assert "填写说明" in wb.sheetnames
    assert wb["填写说明"]["A1"].value == "整改包填写说明"
    ws = wb["整改问题清单"]
    assert ws["A1"].value == "问题编号"
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref
    assert ws["F2"].value == "电费"
    assert ws["G1"].value == "规则分类"
    assert ws["G2"].value == "问题稽核"
    assert ws["I1"].value == "规则名称"
    assert ws["I2"].value == "电费高单价"
    assert ws["J2"].value == "高"
    assert ws["K1"].value == "原台账sheet"
    assert ws["L1"].value == "原始行号"
    assert ws["M1"].value == "命中字段"
    assert ws["N1"].value == "原始字段值"
    assert "电费单价" in ws["O2"].value
    assert "当前值" not in ws["O2"].value
    assert "：" not in ws["O2"].value
    assert ws["Q1"].value == "整改结果"
    assert ws.data_validations.count >= 1


def test_export_issue_packages_can_write_single_province_workbook(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)

    paths = export_issue_packages(app_config, imported.batch_id, mode="province")

    assert len(paths) == 1
    assert "全省" in paths[0].name
    wb = load_workbook(paths[0])
    assert wb.sheetnames == ["填写说明", "整改问题清单"]
    ws = wb["整改问题清单"]
    assert ws["B1"].value == "地市"
    assert ws["B2"].value == "杭州"


def test_export_city_issue_packages_requires_audited_batch(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, status) values (?, ?, ?)",
            ("manual.xlsx", "未稽核批次", "imported"),
        ).lastrowid

    with pytest.raises(ValueError, match="batch must be audited"):
        export_city_issue_packages(app_config, batch_id)


def test_export_city_issue_packages_marks_no_issue_batch_ready_for_archive(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    run_audit(app_config, imported.batch_id)

    paths = export_city_issue_packages(app_config, imported.batch_id)

    assert paths == []
    with connect(app_config) as conn:
        batch = conn.execute("select status from import_batches where id = ?", (imported.batch_id,)).fetchone()
        log = conn.execute("select message from operation_logs where batch_id = ? order by id desc", (imported.batch_id,)).fetchone()
        assert batch["status"] == "returning"
        assert "无待导出问题" in log["message"]


def test_export_city_issue_packages_sanitizes_city_filename(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    unsafe_city = "杭/州..测试"
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
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


def test_export_city_issue_packages_groups_normalized_city_names(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
        conn.execute(
            """
            insert into ledger_rows(
                batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name, row_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                imported.batch_id,
                "electricity",
                "兰州市",
                "城关",
                "LZ002",
                "兰州二站",
                '{"地市":"兰州市","区县":"城关","电信站址编码":"LZ002","电信站址名称":"兰州二站","电费单价":9.9,"分摊比例(%)":100}',
            ),
        )
        conn.execute("update ledger_rows set city = '兰州' where ledger_type = 'electricity' and telecom_site_code = 'HZ001'")

    run_audit(app_config, imported.batch_id)

    paths = export_city_issue_packages(app_config, imported.batch_id)

    assert len(paths) == 1
    assert paths[0].name.startswith("兰州_整改问题清单_")
    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    exported_cities = {ws.cell(row=row, column=2).value for row in range(2, ws.max_row + 1)}
    assert exported_cities == {"兰州"}


def test_export_city_issue_packages_formats_long_decimal_issue_messages(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, batch_code, status) values (?, ?, ?, ?)",
            ("manual.xlsx", "长小数批次", "TEST-DECIMAL", "imported"),
        ).lastrowid
        site_json = '{"地市":"兰州","区县":"城关","电信站址编码":"S001","电信站址名称":"已有站"}'
        conn.execute(
            "insert into ledger_rows(batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name, row_json) values (?, ?, ?, ?, ?, ?, ?)",
            (batch_id, "site", "兰州", "城关", "S001", "已有站", site_json),
        )
        fee_json = '{"地市":"兰州市","区县":"城关","电信站址编码":"S003","电信站址名称":"无站付费","产品服务费合计（元/年）（不含税）":100.123456}'
        conn.execute(
            "insert into ledger_rows(batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name, row_json) values (?, ?, ?, ?, ?, ?, ?)",
            (batch_id, "tower_rent", "兰州市", "城关", "S003", "无站付费", fee_json),
        )
    run_audit(app_config, batch_id)

    path = export_city_issue_packages(app_config, batch_id)[0]

    wb = load_workbook(path)
    ws = wb["整改问题清单"]
    messages = [ws.cell(row=row, column=15).value for row in range(2, ws.max_row + 1)]
    fee_message = next(message for message in messages if "无站址仍支付费用" not in message and "正向费用" in message)
    assert "100.12" in fee_message
    assert "100.123" not in fee_message


def test_export_city_issue_packages_escapes_formula_like_values(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
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
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)

    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    _set_correction_cells(ws, 2, result="已修复", note="已补正")
    wb.save(paths[0])

    result = import_correction_return(app_config, paths[0])

    assert result.matched_count == 1
    with connect(app_config) as conn:
        status = conn.execute("select status from issues limit 1").fetchone()["status"]
        assert status == "needs_review"


def test_import_correction_return_reports_duplicate_issue_codes(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)

    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    _set_correction_cells(ws, 2, result="已修复", note="已补正")
    duplicate = [cell.value for cell in ws[2]]
    headers = {cell.value: index for index, cell in enumerate(ws[1])}
    duplicate[headers["整改结果"]] = "已修复"
    duplicate[headers["整改说明"]] = "重复回传"
    ws.append(duplicate)
    duplicate_row_number = ws.max_row
    wb.save(paths[0])

    result = import_correction_return(app_config, paths[0])

    assert result.matched_count == 1
    assert result.errors == [f"第{duplicate_row_number}行问题编号重复：{ws['A2'].value}"]


def test_import_correction_return_skips_blank_rows(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)

    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    _set_correction_cells(ws, 2, result="已修复", note="已补正")
    ws.append([""] * 15)
    wb.save(paths[0])

    result = import_correction_return(app_config, paths[0])

    assert result.matched_count == 1
    assert result.errors == []


def test_import_correction_return_skips_issue_code_without_correction(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
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


def test_import_correction_return_rejects_archived_batch(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update raw_rows set row_json = replace(row_json, '0.8', '9.9') where ledger_type = 'electricity'"
        )
    run_audit(app_config, imported.batch_id)
    paths = export_city_issue_packages(app_config, imported.batch_id)
    with connect(app_config) as conn:
        conn.execute("update import_batches set status = 'returning' where id = ?", (imported.batch_id,))
    archive_batch(app_config, imported.batch_id)

    wb = load_workbook(paths[0])
    ws = wb["整改问题清单"]
    _set_correction_cells(ws, 2, result="已修复")
    wb.save(paths[0])

    with pytest.raises(ValueError, match="batch is archived"):
        import_correction_return(app_config, paths[0])


def _set_correction_cells(ws, row_number: int, result: str = "", note: str = "") -> None:
    headers = {cell.value: cell.column for cell in ws[1]}
    if result:
        ws.cell(row=row_number, column=headers["整改结果"]).value = result
    if note:
        ws.cell(row=row_number, column=headers["整改说明"]).value = note
