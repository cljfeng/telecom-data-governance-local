from typing import Any, Callable

from governance_app.rule_fields import PERIOD_FIELDS
from governance_app.rule_helpers import _first_value, _number, _period_key, _text
from governance_app.rule_types import AuditLedgerRow, BatchRuleFinding, RuleFinding


def required(field_name: str, message: str, suggestion: str) -> Callable[[dict[str, Any]], RuleFinding | None]:
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        if row.get(field_name) in (None, ""):
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate


def number_range(
    field_name: str,
    minimum: float,
    maximum: float,
    message: str,
    suggestion: str,
) -> Callable[[dict[str, Any]], RuleFinding | None]:
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


def optional_number_range(
    field_name: str,
    minimum: float,
    maximum: float,
    message: str,
    suggestion: str,
) -> Callable[[dict[str, Any]], RuleFinding | None]:
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        value = row.get(field_name)
        if value in (None, ""):
            return None
        number = _number(value)
        if number is None:
            return RuleFinding(
                "",
                "high",
                field_name,
                f"{field_name}格式异常，应填写0-100之间的百分比数值",
                suggestion,
            )
        if number < minimum or number > maximum:
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate


def number_above(
    field_name: str,
    maximum: float,
    message: str,
    suggestion: str,
) -> Callable[[dict[str, Any]], RuleFinding | None]:
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        number = _number(row.get(field_name))
        if number is not None and number > maximum:
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate


def greater_than_zero(
    field_name: str,
    message: str,
    suggestion: str,
) -> Callable[[dict[str, Any]], RuleFinding | None]:
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


def duplicate_positive_fee(
    field_name: str,
    short_name: str,
    message: str,
) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, str], list[AuditLedgerRow]] = {}
        for ledger_row in rows:
            site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
            period = _period_key(_first_value(ledger_row.row, PERIOD_FIELDS))
            fee = _number(ledger_row.row.get(field_name))
            if not site_code or not period or fee is None or fee <= 0:
                continue
            grouped.setdefault((site_code, period), []).append(ledger_row)
        return [
            BatchRuleFinding(
                ledger_row.ledger_row_id,
                field_name,
                message,
                f"核对同账期同站址{short_name}是否重复计费",
            )
            for group in grouped.values()
            if len(group) > 1
            for ledger_row in group
        ]

    return evaluate


def inconsistent_in_group(
    group_fields: tuple[str, ...],
    value_fields: tuple[str, ...],
    field_name: str,
    message: str,
    suggestion: str,
) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, ...], list[tuple[AuditLedgerRow, str]]] = {}
        for ledger_row in rows:
            key = tuple(_text(ledger_row.row.get(field)) for field in group_fields)
            value = _text(_first_value(ledger_row.row, value_fields))
            if any(not item for item in key) or not value:
                continue
            grouped.setdefault(key, []).append((ledger_row, value))
        findings: list[BatchRuleFinding] = []
        for group in grouped.values():
            if len({value for _, value in group}) <= 1:
                continue
            findings.extend(
                BatchRuleFinding(ledger_row.ledger_row_id, field_name, message, suggestion)
                for ledger_row, _ in group
            )
        return findings

    return evaluate
