from typing import Callable

from governance_app.rule_fields import (
    AMOUNT_FIELD_KEYWORDS,
    FEE_SPIKE_FIELDS,
    PERIOD_FIELDS,
)
from governance_app.rule_helpers import (
    _first_value,
    _is_placeholder,
    _number,
    _period_key,
    _positive_fee_field,
    _text,
)
from governance_app.rule_types import (
    AuditLedgerRow,
    BatchAuditRule,
    BatchRuleFinding,
    RuleThresholds,
)


def cross_ledger_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]:
    return [
        BatchAuditRule("amount_negative", "all", "high", _amount_negative),
        BatchAuditRule("fee_amount_period_spike", "all", "high", _fee_amount_period_spike(thresholds.fee_period_change_ratio)),
        BatchAuditRule("fee_paid_without_master_site", "all", "high", _fee_paid_without_master_site),
        BatchAuditRule("site_name_mismatch_across_ledgers", "all", "medium", _site_name_mismatch_across_ledgers),
    ]


def _amount_negative(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        for field_name, value in ledger_row.row.items():
            if not any(keyword in field_name for keyword in AMOUNT_FIELD_KEYWORDS):
                continue
            number = _number(value)
            if number is not None and number < 0:
                findings.append(BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    field_name,
                    f"{field_name}为负数：{number:g}",
                    "核实是否为冲销、退费或录入错误，必要时补充说明",
                ))
    return findings


def _fee_amount_period_spike(change_ratio_threshold: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, str, str], list[tuple[str, AuditLedgerRow, float]]] = {}
        for ledger_row in rows:
            site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
            period = _period_key(_first_value(ledger_row.row, PERIOD_FIELDS))
            if not site_code or not period:
                continue
            for field_name in FEE_SPIKE_FIELDS:
                amount = _number(ledger_row.row.get(field_name))
                if amount is None or amount < 0:
                    continue
                grouped.setdefault((ledger_row.ledger_type, site_code, field_name), []).append((period, ledger_row, amount))
        findings: list[BatchRuleFinding] = []
        for (_, _, field_name), values in grouped.items():
            values.sort(key=lambda item: item[0])
            for (_, _previous_row, previous), (_, current_row, current) in zip(
                values, values[1:], strict=False
            ):
                if previous <= 0:
                    continue
                change = (current - previous) / previous
                if abs(change) >= change_ratio_threshold:
                    findings.append(BatchRuleFinding(
                        current_row.ledger_row_id,
                        field_name,
                        f"{field_name}较上一账期变动超过{change_ratio_threshold * 100:g}%：上一期{previous:g}，本期{current:g}",
                        "核对是否存在调账、冲销、漏录或重复计费",
                    ))
        return findings

    return evaluate


def _fee_paid_without_master_site(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    site_codes = {
        _text(row.telecom_site_code or row.row.get("电信站址编码"))
        for row in rows
        if row.ledger_type == "site"
    }
    site_codes = {site_code for site_code in site_codes if site_code and not _is_placeholder(site_code)}
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type == "site":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        if not site_code or _is_placeholder(site_code) or site_code in site_codes:
            continue
        field_name, amount = _positive_fee_field(ledger_row.row)
        if not field_name:
            continue
        findings.append(BatchRuleFinding(
            ledger_row.ledger_row_id,
            field_name,
            f"站址台账不存在该站址编码“{site_code}”，但{field_name}存在正向费用{amount:g}",
            "暂停支付并核实站址主数据、费用依据和报账归属",
        ))
    return findings


def _site_name_mismatch_across_ledgers(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    master_names = {
        _text(row.telecom_site_code or row.row.get("电信站址编码")): _text(row.telecom_site_name or row.row.get("电信站址名称"))
        for row in rows
        if row.ledger_type == "site"
    }
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type == "site":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        master_name = master_names.get(site_code)
        current_name = _text(ledger_row.telecom_site_name or ledger_row.row.get("电信站址名称"))
        if master_name and current_name and master_name != current_name:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id,
                "电信站址名称",
                f"同一站址编码名称不一致：站址台账“{master_name}”，当前台账“{current_name}”",
                "统一站址名称或核对站址编码",
            ))
    return findings
