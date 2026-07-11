from typing import Callable

from governance_app.rule_fields import (
    GENERATOR_AMOUNT_FIELDS,
    GENERATOR_DATE_FIELDS,
    GENERATOR_END_FIELDS,
    GENERATOR_START_FIELDS,
)
from governance_app.rule_helpers import _datetime_value, _first_value, _median, _number, _text
from governance_app.rule_types import AuditLedgerRow, AuditRule, BatchAuditRule, BatchRuleFinding, RuleThresholds
from governance_app.rules.factories import number_above


def generator_rules(thresholds: RuleThresholds) -> list[AuditRule]:
    return [
        AuditRule(
            "generator_duration_over_24h",
            "generator",
            "high",
            number_above(
                "发电时长",
                thresholds.generator_duration_max_hours,
                f"发电时长超过 {thresholds.generator_duration_max_hours:g} 小时",
                "核实发电开始时间、结束时间和工单时长",
            ),
        ),
    ]


def generator_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]:
    return [
        BatchAuditRule("generator_missing_responsible_party", "all", "medium", _generator_missing_responsible_party),
        BatchAuditRule("generator_missing_date_with_cost", "generator", "high", _generator_missing_date_with_cost),
        BatchAuditRule("generator_duplicate_work_order", "generator", "high", _generator_duplicate_work_order),
        BatchAuditRule("generator_duration_mismatch", "generator", "medium", _generator_duration_mismatch(thresholds.generator_duration_mismatch_hours)),
        BatchAuditRule(
            "generator_cost_per_hour_outlier",
            "generator",
            "high",
            _generator_cost_per_hour_outlier(
                thresholds.generator_cost_per_hour_multiplier,
                thresholds.generator_cost_per_hour_min,
            ),
        ),
    ]


def _generator_missing_responsible_party(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    responsible_by_site = {
        _text(row.telecom_site_code or row.row.get("电信站址编码")): _text(row.row.get("站址发电责任方"))
        for row in rows
        if row.ledger_type == "site" and "站址发电责任方" in row.row
    }
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type != "generator":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        has_generator_cost = (_number(ledger_row.row.get("发电时长")) or 0) > 0 or any(
            (_number(ledger_row.row.get(field)) or 0) > 0 for field in GENERATOR_AMOUNT_FIELDS
        )
        if site_code in responsible_by_site and not responsible_by_site[site_code] and has_generator_cost:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "站址发电责任方", "站址台账发电责任方缺失，但发电费台账存在费用或时长",
                "补充站址发电责任方并核对发电费用口径",
            ))
    return findings


def _generator_missing_date_with_cost(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type != "generator":
            continue
        has_cost_or_duration = (_number(ledger_row.row.get("发电时长")) or 0) > 0 or any(
            (_number(ledger_row.row.get(field)) or 0) > 0 for field in GENERATOR_AMOUNT_FIELDS
        )
        has_date = any(_text(ledger_row.row.get(field)) for field in GENERATOR_DATE_FIELDS)
        if has_cost_or_duration and not has_date:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "发电日期", "发电费台账存在发电时长或金额，但发电日期为空",
                "补充发电日期并核对运维工单",
            ))
    return findings


def _generator_duplicate_work_order(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    grouped: dict[str, list[AuditLedgerRow]] = {}
    for ledger_row in rows:
        if ledger_row.ledger_type != "generator":
            continue
        work_order = _text(ledger_row.row.get("运维系统工单号"))
        if work_order:
            grouped.setdefault(work_order, []).append(ledger_row)
    return [
        BatchRuleFinding(
            ledger_row.ledger_row_id, "运维系统工单号", f"同一运维系统工单号重复出现：{work_order}",
            "核实该工单是否重复填报或重复报账",
        )
        for work_order, group in grouped.items() if len(group) > 1 for ledger_row in group
    ]


def _generator_duration_mismatch(allowed_hours: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        findings: list[BatchRuleFinding] = []
        for ledger_row in rows:
            if ledger_row.ledger_type != "generator":
                continue
            start = _datetime_value(_first_value(ledger_row.row, GENERATOR_START_FIELDS))
            end = _datetime_value(_first_value(ledger_row.row, GENERATOR_END_FIELDS))
            reported = _number(ledger_row.row.get("发电时长"))
            if start is None or end is None or reported is None:
                continue
            calculated = (end - start).total_seconds() / 3600
            if calculated < 0:
                continue
            if abs(calculated - reported) > allowed_hours:
                findings.append(BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "发电时长",
                    f"发电起止时间计算时长{calculated:g}小时，填报发电时长{reported:g}小时，偏差超过{allowed_hours:g}小时",
                    "核对发电开始时间、结束时间和填报时长",
                ))
        return findings

    return evaluate


def _generator_cost_per_hour_outlier(
    multiplier: float,
    minimum_rate: float,
) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        values: list[tuple[AuditLedgerRow, float]] = []
        for ledger_row in rows:
            if ledger_row.ledger_type != "generator":
                continue
            duration = _number(ledger_row.row.get("发电时长"))
            amount = sum(_number(ledger_row.row.get(field)) or 0 for field in GENERATOR_AMOUNT_FIELDS)
            if duration is None or duration <= 0 or amount <= 0:
                continue
            values.append((ledger_row, amount / duration))
        if len(values) < 2:
            return []
        median = _median([rate for _, rate in values])
        if median <= 0:
            return []
        findings: list[BatchRuleFinding] = []
        for ledger_row, rate in values:
            if rate > median * multiplier and rate > minimum_rate:
                findings.append(BatchRuleFinding(
                    ledger_row.ledger_row_id, "最终分摊金额",
                    f"发电小时单价异常：同批次中位数{median:g}元/小时，当前{rate:g}元/小时",
                    "核实发电时长、分摊金额和结算标准",
                ))
        return findings

    return evaluate
