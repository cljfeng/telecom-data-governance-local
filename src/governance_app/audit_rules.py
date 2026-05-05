import json
from dataclasses import dataclass
from typing import Any, Callable

from governance_app.models import LedgerType, Severity


@dataclass(frozen=True)
class RuleFinding:
    rule_id: str
    severity: Severity
    field_name: str | None
    message: str
    suggestion: str


@dataclass(frozen=True)
class AuditRule:
    rule_id: str
    ledger_type: LedgerType
    severity: Severity
    evaluate: Callable[[dict[str, Any]], RuleFinding | None]


def parse_row(row_json: str) -> dict[str, Any]:
    return json.loads(row_json)


def all_rules() -> list[AuditRule]:
    return [
        AuditRule("required_site_code", "site", "high", _required("电信站址编码", "站址编码为空", "补充电信站址编码")),
        AuditRule("required_city", "site", "medium", _required("地市", "地市为空", "补充地市")),
        AuditRule(
            "electricity_price_range",
            "electricity",
            "high",
            _number_range("电费单价", 0, 2, "电费单价超出 0-2 元合理范围", "核实电费单价或转供电合同"),
        ),
        AuditRule(
            "electricity_share_percent",
            "electricity",
            "medium",
            _number_range("分摊比例(%)", 0, 100, "分摊比例不在 0-100 范围", "核实共享情况和分摊比例"),
        ),
        AuditRule(
            "generator_duration_positive",
            "generator",
            "high",
            _greater_than_zero("发电时长", "发电时长小于等于 0", "核实发电起止时间和时长"),
        ),
    ]


def _required(field_name: str, message: str, suggestion: str):
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        if row.get(field_name) in (None, ""):
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate


def _number_range(field_name: str, minimum: float, maximum: float, message: str, suggestion: str):
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        value = row.get(field_name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return RuleFinding("", "high", field_name, f"{field_name}不是有效数字", suggestion)
        if number < minimum or number > maximum:
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate


def _greater_than_zero(field_name: str, message: str, suggestion: str):
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        value = row.get(field_name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return RuleFinding("", "high", field_name, f"{field_name}不是有效数字", suggestion)
        if number <= 0:
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate
