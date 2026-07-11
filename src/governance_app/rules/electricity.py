from datetime import datetime
from typing import Callable

from governance_app.geo import normalize_city
from governance_app.rule_fields import (
    ACTUAL_CAPACITY_FIELDS as _ACTUAL_CAPACITY_FIELDS,
    CONTRACT_CAPACITY_FIELDS as _CONTRACT_CAPACITY_FIELDS,
    CONTRACT_SHARE_FIELDS as _CONTRACT_SHARE_FIELDS,
    CURRENT_READING_FIELDS as _CURRENT_READING_FIELDS,
    ELECTRICITY_AMOUNT_FIELDS as _ELECTRICITY_AMOUNT_FIELDS,
    METER_MULTIPLIER_FIELDS as _METER_MULTIPLIER_FIELDS,
    METER_PERIOD_END_FIELDS as _METER_PERIOD_END_FIELDS,
    METER_PERIOD_START_FIELDS as _METER_PERIOD_START_FIELDS,
    PERIOD_FIELDS as _PERIOD_FIELDS,
    PREVIOUS_READING_FIELDS as _PREVIOUS_READING_FIELDS,
    PRICE_FIELDS as _PRICE_FIELDS,
    SUPPLY_FIELDS as _SUPPLY_FIELDS,
    TRANSFER_CONTRACT_FIELDS as _TRANSFER_CONTRACT_FIELDS,
    USAGE_FIELDS as _USAGE_FIELDS,
)
from governance_app.rule_helpers import (
    _datetime_value,
    _first_value,
    _median,
    _number,
    _period_key,
    _positive_field,
    _positive_or_zero_field,
    _text,
)
from governance_app.rule_types import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    RuleThresholds,
)
from governance_app.rules.factories import number_above, optional_number_range


def electricity_rules(thresholds: RuleThresholds) -> list[AuditRule]:
    return [
        AuditRule(
            "electricity_price_range",
            "electricity",
            "high",
            number_above(
                "电费单价",
                thresholds.electricity_price_max,
                f"电费单价超过 {thresholds.electricity_price_max:g} 元",
                "核实电费单价、电价依据或转供电合同",
            ),
        ),
        AuditRule(
            "electricity_share_percent",
            "electricity",
            "medium",
            optional_number_range(
                "分摊比例(%)",
                thresholds.share_percent_min,
                thresholds.share_percent_max,
                f"分摊比例不在 {thresholds.share_percent_min:g}-{thresholds.share_percent_max:g} 范围",
                "核实共享情况和分摊比例",
            ),
        ),
    ]


def electricity_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]:
    return [
        BatchAuditRule(
            "electricity_contract_share_variance",
            "electricity",
            "medium",
            _electricity_contract_share_variance(thresholds.contract_share_variance_points),
        ),
        BatchAuditRule(
            "electricity_duplicate_payment",
            "electricity",
            "high",
            _electricity_duplicate_payment,
        ),
        BatchAuditRule(
            "electricity_usage_spike_drop",
            "electricity",
            "high",
            _electricity_usage_spike_drop(thresholds.usage_change_ratio),
        ),
        BatchAuditRule(
            "electricity_capacity_mismatch",
            "electricity",
            "medium",
            _electricity_capacity_mismatch,
        ),
        BatchAuditRule("electricity_meter_reading_reverse", "electricity", "medium", _electricity_meter_reading_reverse),
        BatchAuditRule(
            "electricity_reading_usage_mismatch",
            "electricity",
            "medium",
            _electricity_reading_usage_mismatch(
                thresholds.electricity_usage_mismatch_ratio,
                thresholds.electricity_usage_mismatch_min,
            ),
        ),
        BatchAuditRule("electricity_zero_usage_positive_fee", "electricity", "high", _electricity_zero_usage_positive_fee),
        BatchAuditRule(
            "electricity_amount_calculation_mismatch",
            "electricity",
            "high",
            _electricity_amount_calculation_mismatch(
                thresholds.electricity_amount_variance_ratio,
                thresholds.electricity_amount_variance_min,
            ),
        ),
        BatchAuditRule("electricity_period_overlap", "electricity", "high", _electricity_period_overlap),
        BatchAuditRule(
            "electricity_price_commercial_range",
            "electricity",
            "medium",
            _electricity_price_commercial_range(
                thresholds.electricity_commercial_price_min,
                thresholds.electricity_commercial_price_max,
            ),
        ),
        BatchAuditRule(
            "electricity_price_city_supply_outlier",
            "electricity",
            "medium",
            _electricity_price_city_supply_outlier(thresholds.city_supply_price_deviation_ratio),
        ),
        BatchAuditRule("electricity_lump_sum_still_reimbursed", "electricity", "high", _electricity_lump_sum_still_reimbursed),
        BatchAuditRule("electricity_transfer_without_contract", "electricity", "high", _electricity_transfer_without_contract),
    ]


def _electricity_price_city_supply_outlier(deviation_ratio: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, str, str], list[tuple[AuditLedgerRow, float]]] = {}
        for ledger_row in rows:
            if ledger_row.ledger_type != "electricity":
                continue
            price = _number(_first_value(ledger_row.row, _PRICE_FIELDS))
            city = normalize_city(ledger_row.city or ledger_row.row.get("地市"))
            district = _text(ledger_row.district or ledger_row.row.get("区县"))
            supply = _text(_first_value(ledger_row.row, _SUPPLY_FIELDS))
            if price is None or not city or not district or not supply:
                continue
            grouped.setdefault((city, district, supply), []).append((ledger_row, price))
        findings: list[BatchRuleFinding] = []
        for (city, district, supply), values in grouped.items():
            if len(values) < 4:
                continue
            median = _median([price for _, price in values])
            if median <= 0:
                continue
            for ledger_row, price in values:
                if abs(price - median) / median > deviation_ratio:
                    findings.append(
                        BatchRuleFinding(
                            ledger_row.ledger_row_id,
                            "电费单价",
                            f"{city}{district}{supply}电费单价偏离中位数超过{deviation_ratio * 100:g}%：中位数{median:g}，当前{price:g}",
                            "核实电价依据、供电方式和转供电合同",
                        )
                    )
        return findings

    return evaluate


def _electricity_contract_share_variance(variance_points: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        findings: list[BatchRuleFinding] = []
        for ledger_row in rows:
            actual = _number(ledger_row.row.get("分摊比例(%)"))
            contract = _number(_first_value(ledger_row.row, _CONTRACT_SHARE_FIELDS))
            if actual is None or contract is None:
                continue
            if abs(actual - contract) > variance_points:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        "分摊比例(%)",
                        f"铁塔单站分摊比例与合同约定偏差超过±{variance_points:g}个百分点：合同{contract:g}%，当前{actual:g}%",
                        "按合同约定核对分摊比例",
                    )
                )
        return findings

    return evaluate


def _electricity_usage_spike_drop(change_ratio_threshold: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, str], list[tuple[str, AuditLedgerRow, float]]] = {}
        for ledger_row in rows:
            site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
            meter = _text(ledger_row.row.get("电表户号")) or ""
            period = _period_key(_first_value(ledger_row.row, _PERIOD_FIELDS))
            usage = _number(_first_value(ledger_row.row, _USAGE_FIELDS))
            if not site_code or not period or usage is None:
                continue
            grouped.setdefault((site_code, meter), []).append((period, ledger_row, usage))

        findings: list[BatchRuleFinding] = []
        for values in grouped.values():
            values.sort(key=lambda item: item[0])
            for (_, _previous_row, previous_usage), (_, current_row, current_usage) in zip(
                values, values[1:], strict=False
            ):
                if previous_usage == 0:
                    continue
                change = (current_usage - previous_usage) / previous_usage
                if abs(change) > change_ratio_threshold:
                    findings.append(
                        BatchRuleFinding(
                            current_row.ledger_row_id,
                            "用电量",
                            f"用电量较上一账期突变超过{change_ratio_threshold * 100:g}%：上一期{previous_usage:g}，本期{current_usage:g}",
                            "核对抄表读数、倍率和异常能耗原因",
                        )
                    )
        return findings

    return evaluate


def _electricity_duplicate_payment(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    grouped: dict[tuple[str, str], list[AuditLedgerRow]] = {}
    for ledger_row in rows:
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        period = _period_key(_first_value(ledger_row.row, _PERIOD_FIELDS))
        if not site_code or not period:
            continue
        grouped.setdefault((site_code, period), []).append(ledger_row)
    return [
        BatchRuleFinding(
            ledger_row.ledger_row_id,
            "报账周期",
            "同一站址同一账期出现多笔缴费记录",
            "核实是否重复报账或重复缴费",
        )
        for group in grouped.values()
        if len(group) > 1
        for ledger_row in group
    ]


def _electricity_capacity_mismatch(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        contract = _number(_first_value(ledger_row.row, _CONTRACT_CAPACITY_FIELDS))
        actual = _number(_first_value(ledger_row.row, _ACTUAL_CAPACITY_FIELDS))
        if contract is None or actual is None:
            continue
        if abs(contract - actual) > 0.01:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "实际用电容量",
                    f"合同申报容量与实际用电容量不匹配：合同{contract:g}，实际{actual:g}",
                    "核对合同申报容量、现场设备容量和计费容量",
                )
            )
    return findings


def _electricity_meter_reading_reverse(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        previous = _number(_first_value(ledger_row.row, _PREVIOUS_READING_FIELDS))
        current = _number(_first_value(ledger_row.row, _CURRENT_READING_FIELDS))
        if previous is None or current is None:
            continue
        if current < previous:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "本次抄表数",
                    f"本次抄表数小于上次抄表数：上次{previous:g}，本次{current:g}",
                    "核实是否换表、倍率调整或抄表读数录入错误",
                )
            )
    return findings


def _electricity_reading_usage_mismatch(
    variance_ratio: float,
    variance_min: float,
) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        findings: list[BatchRuleFinding] = []
        for ledger_row in rows:
            previous = _number(_first_value(ledger_row.row, _PREVIOUS_READING_FIELDS))
            current = _number(_first_value(ledger_row.row, _CURRENT_READING_FIELDS))
            usage = _number(_first_value(ledger_row.row, _USAGE_FIELDS))
            multiplier = _number(_first_value(ledger_row.row, _METER_MULTIPLIER_FIELDS)) or 1
            if previous is None or current is None or usage is None:
                continue
            expected = (current - previous) * multiplier
            if expected < 0:
                continue
            allowed = max(abs(expected) * variance_ratio, variance_min)
            if abs(usage - expected) > allowed:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        "用电量",
                        f"用电量与抄表读数差值不匹配：读数计算{expected:g}，填报{usage:g}",
                        "核实抄表读数、倍率和用电量计算口径",
                    )
                )
        return findings

    return evaluate


def _electricity_zero_usage_positive_fee(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        usage = _number(_first_value(ledger_row.row, _USAGE_FIELDS))
        field_name, amount = _positive_field(ledger_row.row, _ELECTRICITY_AMOUNT_FIELDS)
        if usage == 0 and field_name:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    field_name,
                    f"用电量为0但{field_name}存在正向费用{amount:g}",
                    "核实是否固定费用、录入错误或异常报账",
                )
            )
    return findings


def _electricity_amount_calculation_mismatch(
    variance_ratio: float,
    variance_min: float,
) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        findings: list[BatchRuleFinding] = []
        for ledger_row in rows:
            usage = _number(_first_value(ledger_row.row, _USAGE_FIELDS))
            price = _number(_first_value(ledger_row.row, _PRICE_FIELDS))
            amount_field, amount = _positive_or_zero_field(ledger_row.row, _ELECTRICITY_AMOUNT_FIELDS)
            if usage is None or price is None or amount_field is None:
                continue
            share = _number(ledger_row.row.get("分摊比例(%)"))
            share_factor = (share / 100) if share is not None else 1
            expected = usage * price * share_factor
            allowed = max(abs(expected) * variance_ratio, variance_min)
            if abs(amount - expected) > allowed:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        amount_field,
                        f"{amount_field}与用电量、电价计算值偏差超过阈值：计算{expected:g}，填报{amount:g}",
                        "核实电量、电价、分摊比例和支付金额",
                    )
                )
        return findings

    return evaluate


def _electricity_period_overlap(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    grouped: dict[tuple[str, str], list[tuple[datetime, datetime, AuditLedgerRow]]] = {}
    for ledger_row in rows:
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        meter = _text(ledger_row.row.get("电表户号")) or ""
        start = _datetime_value(_first_value(ledger_row.row, _METER_PERIOD_START_FIELDS))
        end = _datetime_value(_first_value(ledger_row.row, _METER_PERIOD_END_FIELDS))
        if not site_code or start is None or end is None or end < start:
            continue
        grouped.setdefault((site_code, meter), []).append((start, end, ledger_row))
    findings: list[BatchRuleFinding] = []
    seen: set[int] = set()
    for values in grouped.values():
        values.sort(key=lambda item: item[0])
        for previous, current in zip(values, values[1:], strict=False):
            previous_start, previous_end, previous_row = previous
            current_start, current_end, current_row = current
            if current_start <= previous_end and current_end >= previous_start:
                for row in (previous_row, current_row):
                    if row.ledger_row_id in seen:
                        continue
                    seen.add(row.ledger_row_id)
                    findings.append(
                        BatchRuleFinding(
                            row.ledger_row_id,
                            "抄表开始日期",
                            "同站址同电表抄表区间存在交叉重叠，疑似重复计费",
                            "核实抄表起止日期和同一时段是否重复报账",
                        )
                    )
    return findings


def _electricity_price_commercial_range(
    minimum: float,
    maximum: float,
) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        findings: list[BatchRuleFinding] = []
        for ledger_row in rows:
            price = _number(_first_value(ledger_row.row, _PRICE_FIELDS))
            if price is None:
                continue
            if price < minimum or price > maximum:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        "电费单价",
                        f"电价{price:g}元/度超出{minimum:g}-{maximum:g}元/度参考范围",
                        "核实电价依据、供电方式和转供电加价",
                    )
                )
        return findings

    return evaluate



def _electricity_lump_sum_still_reimbursed(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        lump_sum = _text(ledger_row.row.get("是否包干站址"))
        reimbursed = _text(ledger_row.row.get("是否报账"))
        if lump_sum == "是" and reimbursed == "是":
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "是否报账",
                    "包干站址仍标记为报账，存在重复报账风险",
                    "核对包干电费和报账口径，避免重复报账",
                )
            )
    return findings


def _electricity_transfer_without_contract(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        supply = _text(_first_value(ledger_row.row, _SUPPLY_FIELDS))
        contract = _text(_first_value(ledger_row.row, _TRANSFER_CONTRACT_FIELDS))
        if "转供" in supply and (not contract or contract in {"无", "否", "未签订"}):
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "转供电合同情况",
                    "供电方式为转供电但缺少有效合同信息",
                    "补充转供电合同或核实供电方式",
                )
            )
    return findings
