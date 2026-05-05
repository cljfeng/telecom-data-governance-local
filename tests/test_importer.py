import json

from openpyxl import Workbook

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


def test_import_workbook_accepts_grouped_generator_headers(app_config, tmp_path):
    initialize_database(app_config)
    workbook_path = _save_grouped_generator_workbook(tmp_path / "grouped_generator.xlsx")

    result = import_workbook(app_config, workbook_path)

    assert result.errors == []
    with connect(app_config) as conn:
        row_json = conn.execute(
            "select row_json from raw_rows where ledger_type = 'generator'"
        ).fetchone()["row_json"]
    row = json.loads(row_json)
    assert row["发电日期"] == "2026-04-10"
    assert row["发电时间 - 发电开始时间"] == "09:00"
    assert row["发电时间 - 发电结束时间（断电传感器告警消除时间）"] == "12:00"


def test_import_workbook_preserves_excel_row_number_after_blank_rows(app_config, tmp_path):
    initialize_database(app_config)
    workbook_path = _save_workbook_with_blank_site_row(tmp_path / "blank_site_row.xlsx")

    result = import_workbook(app_config, workbook_path)

    assert result.errors == []
    with connect(app_config) as conn:
        row_number = conn.execute(
            "select row_number from raw_rows where ledger_type = 'site'"
        ).fetchone()["row_number"]
    assert row_number == 3


def _save_grouped_generator_workbook(path):
    wb = _base_workbook()
    ws = wb["发电费台账"]
    ws.delete_rows(1, ws.max_row)
    ws.append(["发电事件", None, None, None, None, None, None, "发电时间", None, "时间时长"])
    ws.append([
        "发电日期",
        "账单月份",
        "电信站址编码",
        "电信站址名称",
        "铁塔站址编码",
        "铁塔站址名称",
        "运维系统工单号",
        "发电开始时间",
        "发电结束时间（断电传感器告警消除时间）",
        "发电时长",
    ])
    ws.merge_cells("A1:G1")
    ws.merge_cells("H1:I1")
    ws.append([
        "2026-04-10",
        "2026-04",
        "HZ001",
        "西湖一站",
        "TT001",
        "铁塔西湖一站",
        "WO001",
        "09:00",
        "12:00",
        3,
    ])
    wb.save(path)
    return path


def _save_workbook_with_blank_site_row(path):
    wb = _base_workbook()
    ws = wb["站址台账"]
    ws.insert_rows(2)
    wb.save(path)
    return path


def _base_workbook():
    wb = Workbook()
    default = wb.active
    wb.remove(default)

    ws = wb.create_sheet("站址台账")
    ws.append(["序号", "地市", "区县", "电信站址编码", "电信站址名称", "经度", "纬度", "站址归属"])
    ws.append([1, "杭州", "西湖", "HZ001", "西湖一站", 120.1, 30.2, "铁塔"])

    ws = wb.create_sheet("铁塔租费台账")
    ws.append(["序列", "电信站址编码", "电信站址名称", "地市", "区县", "铁塔站址编码", "铁塔站址名称"])
    ws.append([1, "HZ001", "西湖一站", "杭州", "西湖", "TT001", "铁塔西湖一站"])

    ws = wb.create_sheet("电费台账")
    ws.append(["序号", "地市", "区县", "电信站址编码", "电信站址名称", "电表户号", "报账周期"])
    ws.append([1, "杭州", "西湖", "HZ001", "西湖一站", "M001", "2026-04"])

    ws = wb.create_sheet("发电费台账")
    ws.append(["序号", "发电日期", "账单月份", "电信站址编码", "电信站址名称", "运维系统工单号", "发电时长"])
    ws.append(["", "", "", "", "", "", ""])
    ws.append([1, "2026-04-10", "2026-04", "HZ001", "西湖一站", "WO001", 3])
    return wb
