from governance_app.templates import (
    EXPECTED_SHEETS,
    FIELD_GROUPS,
    ledger_type_for_sheet,
    required_headers_for,
)


def test_expected_sheets_match_governance_template():
    assert EXPECTED_SHEETS == {
        "站址台账": "site",
        "铁塔租费台账": "tower_rent",
        "电费台账": "electricity",
        "发电费台账": "generator",
    }


def test_required_headers_include_site_keys():
    assert "电信站址编码" in required_headers_for("site")
    assert "电信站址名称" in required_headers_for("site")
    assert "地市" in required_headers_for("site")


def test_ledger_type_for_sheet_rejects_unknown_sheet():
    assert ledger_type_for_sheet("电费台账") == "electricity"
    assert ledger_type_for_sheet("未知") is None


def test_generator_time_fields_use_standard_end_time_label():
    time_fields = FIELD_GROUPS["generator"]["时间时长"]

    assert "发电时间 - 发电结束时间（断电传感器告警消除时间）" in time_fields
    assert all("发点结束时间" not in field for field in time_fields)
