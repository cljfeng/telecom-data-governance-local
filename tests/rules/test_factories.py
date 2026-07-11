from governance_app.rule_types import AuditLedgerRow
from governance_app.rules.factories import (
    duplicate_positive_fee,
    greater_than_zero,
    inconsistent_in_group,
    number_above,
    number_range,
    optional_number_range,
    required,
)


def _row(row_id: int, values: dict) -> AuditLedgerRow:
    return AuditLedgerRow(row_id, "tower_rent", None, None, values.get("电信站址编码"), None, values)


def test_scalar_factories_preserve_empty_and_boundary_behavior():
    assert required("编码", "缺失", "补充")({"编码": ""}).message == "缺失"
    assert required("编码", "缺失", "补充")({"编码": "A"}) is None
    assert number_range("比例", 0, 100, "超限", "核对")({"比例": 100}) is None
    assert number_range("比例", 0, 100, "超限", "核对")({"比例": "x"}).message == "比例不是有效数字"
    assert optional_number_range("比例", 0, 100, "超限", "核对")({"比例": ""}) is None
    assert optional_number_range("比例", 0, 100, "超限", "核对")({"比例": 101}).message == "超限"
    assert number_above("时长", 24, "超时", "核对")({"时长": 24}) is None
    assert number_above("时长", 24, "超时", "核对")({"时长": 25}).message == "超时"
    assert greater_than_zero("金额", "非正", "核对")({"金额": 0}).message == "非正"


def test_duplicate_positive_fee_marks_every_duplicate_row():
    evaluate = duplicate_positive_fee("维护费(元/年)", "维护费", "重复计费")
    rows = [
        _row(1, {"电信站址编码": "S1", "账期": "2026-01", "维护费(元/年)": 10}),
        _row(2, {"电信站址编码": "S1", "账期": "2026-01", "维护费(元/年)": 20}),
    ]

    findings = evaluate(rows)

    assert [finding.ledger_row_id for finding in findings] == [1, 2]
    assert {finding.message for finding in findings} == {"重复计费"}


def test_inconsistent_in_group_marks_every_inconsistent_row():
    evaluate = inconsistent_in_group(("电信站址编码",), ("塔高",), "塔高", "高度不一致", "核对")
    rows = [
        _row(1, {"电信站址编码": "S1", "塔高": 20}),
        _row(2, {"电信站址编码": "S1", "塔高": 30}),
    ]

    findings = evaluate(rows)

    assert [finding.ledger_row_id for finding in findings] == [1, 2]
    assert {finding.field_name for finding in findings} == {"塔高"}
