from governance_app.templates import EXPECTED_SHEETS, ledger_type_for_sheet, required_headers_for


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
