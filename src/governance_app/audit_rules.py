import json
from datetime import datetime
from typing import Any, Callable

from governance_app.geo import normalize_city
from governance_app.models import LedgerType
from governance_app.rule_catalog import RULE_CATALOG, RuleMetadata
from governance_app.rule_fields import (
    PRICE_FIELDS as _PRICE_FIELDS,
    SUPPLY_FIELDS as _SUPPLY_FIELDS,
    PERIOD_FIELDS as _PERIOD_FIELDS,
    USAGE_FIELDS as _USAGE_FIELDS,
    PREVIOUS_READING_FIELDS as _PREVIOUS_READING_FIELDS,
    CURRENT_READING_FIELDS as _CURRENT_READING_FIELDS,
    METER_MULTIPLIER_FIELDS as _METER_MULTIPLIER_FIELDS,
    ELECTRICITY_AMOUNT_FIELDS as _ELECTRICITY_AMOUNT_FIELDS,
    METER_PERIOD_START_FIELDS as _METER_PERIOD_START_FIELDS,
    METER_PERIOD_END_FIELDS as _METER_PERIOD_END_FIELDS,
    CONTRACT_SHARE_FIELDS as _CONTRACT_SHARE_FIELDS,
    CONTRACT_CAPACITY_FIELDS as _CONTRACT_CAPACITY_FIELDS,
    ACTUAL_CAPACITY_FIELDS as _ACTUAL_CAPACITY_FIELDS,
    TOWER_HEIGHT_FIELDS as _TOWER_HEIGHT_FIELDS,
    MOUNT_HEIGHT_FIELDS as _MOUNT_HEIGHT_FIELDS,
    TOWER_TYPE_FIELDS as _TOWER_TYPE_FIELDS,
    POWER_INTRO_FEE_FIELDS as _POWER_INTRO_FEE_FIELDS,
    PRODUCT_SERVICE_FEE_FIELDS as _PRODUCT_SERVICE_FEE_FIELDS,
    TOWER_FEE_FIELDS as _TOWER_FEE_FIELDS,
    UNIT_COUNT_FIELDS as _UNIT_COUNT_FIELDS,
    MAINTENANCE_DISCOUNT_FIELDS as _MAINTENANCE_DISCOUNT_FIELDS,
    SHARING_INFO_FIELDS as _SHARING_INFO_FIELDS,
    TRANSFER_CONTRACT_FIELDS as _TRANSFER_CONTRACT_FIELDS,
    GENERATOR_AMOUNT_FIELDS as _GENERATOR_AMOUNT_FIELDS,
    AMOUNT_FIELD_KEYWORDS as _AMOUNT_FIELD_KEYWORDS,
    GENERATOR_DATE_FIELDS as _GENERATOR_DATE_FIELDS,
    GENERATOR_START_FIELDS as _GENERATOR_START_FIELDS,
    GENERATOR_END_FIELDS as _GENERATOR_END_FIELDS,
    FEE_SPIKE_FIELDS as _FEE_SPIKE_FIELDS,
)
from governance_app.rule_helpers import (
    _datetime_value,
    _first_value,
    _is_placeholder,
    _median,
    _month_key,
    _number,
    _period_key,
    _positive_fee_field,
    _positive_field,
    _positive_or_zero_field,
    _text,
)
from governance_app.rules.electricity import electricity_batch_rules
from governance_app.rule_types import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    RuleFinding,
    RuleThresholds,
)


def parse_row(row_json: str) -> dict[str, Any]:
    return json.loads(row_json)


DEFAULT_THRESHOLDS = RuleThresholds()


def rule_metadata(rule_id: str) -> RuleMetadata:
    return RULE_CATALOG.get(
        rule_id,
        RuleMetadata(rule_id, rule_id, "unknown", "medium", f"未登记规则：{rule_id}", "按规则编号核实问题明细"),
    )


def all_rules(thresholds: RuleThresholds = DEFAULT_THRESHOLDS) -> list[AuditRule]:
    return [
        AuditRule("required_site_code", "site", "high", _required("电信站址编码", "站址编码为空", "补充电信站址编码")),
        AuditRule("required_city", "site", "medium", _required("地市", "地市为空", "补充地市")),
        AuditRule(
            "electricity_price_range",
            "electricity",
            "high",
            _number_above(
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
            _optional_number_range(
                "分摊比例(%)",
                thresholds.share_percent_min,
                thresholds.share_percent_max,
                f"分摊比例不在 {thresholds.share_percent_min:g}-{thresholds.share_percent_max:g} 范围",
                "核实共享情况和分摊比例",
            ),
        ),
        AuditRule(
            "generator_duration_over_24h",
            "generator",
            "high",
            _number_above(
                "发电时长",
                thresholds.generator_duration_max_hours,
                f"发电时长超过 {thresholds.generator_duration_max_hours:g} 小时",
                "核实发电开始时间、结束时间和工单时长",
            ),
        ),
    ]


def all_batch_rules(thresholds: RuleThresholds = DEFAULT_THRESHOLDS) -> list[BatchAuditRule]:
    electricity_rules = {rule.rule_id: rule for rule in electricity_batch_rules(thresholds)}
    return [
        electricity_rules["electricity_contract_share_variance"],
        electricity_rules["electricity_duplicate_payment"],
        electricity_rules["electricity_usage_spike_drop"],
        electricity_rules["electricity_capacity_mismatch"],
        electricity_rules["electricity_meter_reading_reverse"],
        electricity_rules["electricity_reading_usage_mismatch"],
        electricity_rules["electricity_zero_usage_positive_fee"],
        electricity_rules["electricity_amount_calculation_mismatch"],
        electricity_rules["electricity_period_overlap"],
        electricity_rules["electricity_price_commercial_range"],
        BatchAuditRule("amount_negative", "all", "high", _amount_negative),
        BatchAuditRule("fee_amount_period_spike", "all", "high", _fee_amount_period_spike(thresholds.fee_period_change_ratio)),
        BatchAuditRule("fee_paid_without_master_site", "all", "high", _fee_paid_without_master_site),
        electricity_rules["electricity_price_city_supply_outlier"],
        BatchAuditRule(
            "tower_mount_height_exceeds_tower_height",
            "tower_rent",
            "high",
            _tower_mount_height_exceeds_tower_height,
        ),
        BatchAuditRule(
            "tower_site_height_inconsistent",
            "tower_rent",
            "medium",
            _inconsistent_in_group(
                ("电信站址编码",),
                _TOWER_HEIGHT_FIELDS,
                "塔高",
                "同站址多订单塔高不一致",
                "核对铁塔站址基础属性和订单塔高",
            ),
        ),
        BatchAuditRule(
            "tower_confirmation_product_changed",
            "tower_rent",
            "medium",
            _inconsistent_in_group(
                ("业务确认单号",),
                ("铁塔产品", "铁塔产品类型"),
                "铁塔产品",
                "同业务确认单前后账期铁塔产品不一致",
                "核对业务确认单产品变更依据",
            ),
        ),
        BatchAuditRule(
            "tower_product_shared_users_inconsistent",
            "tower_rent",
            "medium",
            _inconsistent_in_group(
                ("电信站址编码", "铁塔产品"),
                ("铁塔共享用户数", "铁塔共享用户数量", "共享用户数"),
                "铁塔共享用户数",
                "同站址同铁塔产品共享用户数不一致",
                "核对同站址多订单的铁塔共享用户数",
            ),
        ),
        BatchAuditRule(
            "tower_room_shared_users_inconsistent",
            "tower_rent",
            "medium",
            _inconsistent_in_group(
                ("电信站址编码", "机房产品"),
                ("机房共享用户数", "机房共享用户数量"),
                "机房共享用户数",
                "同站址同机房产品机房共享用户数不一致",
                "核对同站址多订单的机房共享用户数",
            ),
        ),
        BatchAuditRule(
            "tower_duplicate_product_service_fee",
            "tower_rent",
            "high",
            _duplicate_positive_fee("产品服务费合计（元/年）（不含税）", "产品服务费", "同账期同站址产品服务费多次计费"),
        ),
        BatchAuditRule(
            "tower_duplicate_maintenance_fee",
            "tower_rent",
            "high",
            _duplicate_positive_fee("维护费(元/年)", "维护费", "同账期同站址维护费多次计费"),
        ),
        BatchAuditRule(
            "tower_duplicate_site_fee",
            "tower_rent",
            "high",
            _duplicate_positive_fee("场地费(元/年)", "场地费", "同账期同站址场地费多次计费"),
        ),
        BatchAuditRule(
            "tower_duplicate_power_intro_fee",
            "tower_rent",
            "high",
            _duplicate_positive_fee("电力引入费(元/年)", "电力引入费", "同账期同站址电力引入费多次计费"),
        ),
        BatchAuditRule(
            "tower_product_units_zero_fee_nonzero",
            "tower_rent",
            "high",
            _tower_product_units_zero_fee_nonzero,
        ),
        BatchAuditRule(
            "tower_maintenance_discount_not_lowest",
            "tower_rent",
            "medium",
            _tower_maintenance_discount_not_lowest,
        ),
        BatchAuditRule(
            "tower_original_owner_power_intro_fee_nonzero",
            "tower_rent",
            "high",
            _tower_original_owner_power_intro_fee_nonzero,
        ),
        BatchAuditRule("missing_site_code_duplicate_name", "site", "high", _missing_site_code_duplicate_name),
        BatchAuditRule("site_code_missing_in_master", "all", "high", _site_code_missing_in_master),
        BatchAuditRule("site_name_mismatch_across_ledgers", "all", "medium", _site_name_mismatch_across_ledgers),
        BatchAuditRule("tower_stopped_site_still_charged", "tower_rent", "medium", _tower_stopped_site_still_charged),
        BatchAuditRule("tower_charged_after_stop_period", "tower_rent", "high", _tower_charged_after_stop_period),
        electricity_rules["electricity_lump_sum_still_reimbursed"],
        electricity_rules["electricity_transfer_without_contract"],
        BatchAuditRule("generator_missing_responsible_party", "all", "medium", _generator_missing_responsible_party),
        BatchAuditRule("generator_missing_date_with_cost", "generator", "high", _generator_missing_date_with_cost),
        BatchAuditRule("generator_duplicate_work_order", "generator", "high", _generator_duplicate_work_order),
        BatchAuditRule(
            "generator_duration_mismatch",
            "generator",
            "medium",
            _generator_duration_mismatch(thresholds.generator_duration_mismatch_hours),
        ),
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


def _optional_number_range(field_name: str, minimum: float, maximum: float, message: str, suggestion: str):
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        value = row.get(field_name)
        if value in (None, ""):
            return None
        number = _number(value)
        if number is None:
            return RuleFinding("", "high", field_name, f"{field_name}格式异常，应填写0-100之间的百分比数值", suggestion)
        if number < minimum or number > maximum:
            return RuleFinding("", "high", field_name, message, suggestion)
        return None

    return evaluate


def _number_above(field_name: str, maximum: float, message: str, suggestion: str):
    def evaluate(row: dict[str, Any]) -> RuleFinding | None:
        number = _number(row.get(field_name))
        if number is None:
            return None
        if number > maximum:
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


def _amount_negative(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        for field_name, value in ledger_row.row.items():
            if not any(keyword in field_name for keyword in _AMOUNT_FIELD_KEYWORDS):
                continue
            number = _number(value)
            if number is not None and number < 0:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        field_name,
                        f"{field_name}为负数：{number:g}",
                        "核实是否为冲销、退费或录入错误，必要时补充说明",
                    )
                )
    return findings


def _fee_amount_period_spike(change_ratio_threshold: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, str, str], list[tuple[str, AuditLedgerRow, float]]] = {}
        for ledger_row in rows:
            site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
            period = _period_key(_first_value(ledger_row.row, _PERIOD_FIELDS))
            if not site_code or not period:
                continue
            for field_name in _FEE_SPIKE_FIELDS:
                amount = _number(ledger_row.row.get(field_name))
                if amount is None or amount < 0:
                    continue
                grouped.setdefault((ledger_row.ledger_type, site_code, field_name), []).append((period, ledger_row, amount))
        findings: list[BatchRuleFinding] = []
        for (_, _, field_name), values in grouped.items():
            values.sort(key=lambda item: item[0])
            for (_, previous_row, previous), (_, current_row, current) in zip(values, values[1:], strict=False):
                if previous <= 0:
                    continue
                change = (current - previous) / previous
                if abs(change) >= change_ratio_threshold:
                    findings.append(
                        BatchRuleFinding(
                            current_row.ledger_row_id,
                            field_name,
                            f"{field_name}较上一账期变动超过{change_ratio_threshold * 100:g}%：上一期{previous:g}，本期{current:g}",
                            "核对是否存在调账、冲销、漏录或重复计费",
                        )
                    )
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
        findings.append(
            BatchRuleFinding(
                ledger_row.ledger_row_id,
                field_name,
                f"站址台账不存在该站址编码“{site_code}”，但{field_name}存在正向费用{amount:g}",
                "暂停支付并核实站址主数据、费用依据和报账归属",
            )
        )
    return findings


def _tower_mount_height_exceeds_tower_height(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        tower_type = _text(_first_value(ledger_row.row, _TOWER_TYPE_FIELDS))
        if "普通地面塔" not in tower_type and "景观塔" not in tower_type:
            continue
        mount_height = _number(_first_value(ledger_row.row, _MOUNT_HEIGHT_FIELDS))
        tower_height = _number(_first_value(ledger_row.row, _TOWER_HEIGHT_FIELDS))
        if mount_height is None or tower_height is None:
            continue
        if mount_height > tower_height:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "挂高",
                    f"挂高大于塔高：挂高{mount_height:g}，塔高{tower_height:g}",
                    "仅针对普通地面塔和景观塔核对挂高、塔高录入",
                )
            )
    return findings


def _tower_product_units_zero_fee_nonzero(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        unit_values = [_number(ledger_row.row.get(field)) for field in _UNIT_COUNT_FIELDS if field in ledger_row.row]
        if not unit_values:
            continue
        fee = _number(_first_value(ledger_row.row, _PRODUCT_SERVICE_FEE_FIELDS))
        if sum(value or 0 for value in unit_values) == 0 and fee is not None and fee != 0:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "产品服务费合计（元/年）（不含税）",
                    "产品单元数和为0，但产品服务费不为0",
                    "核对产品单元数和产品服务费计费依据",
                )
            )
    return findings


def _tower_maintenance_discount_not_lowest(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    discounted: list[tuple[AuditLedgerRow, float]] = []
    for ledger_row in rows:
        sharing_info = " ".join(_text(ledger_row.row.get(field)) for field in _SHARING_INFO_FIELDS)
        discount = _number(_first_value(ledger_row.row, _MAINTENANCE_DISCOUNT_FIELDS))
        if "共享" in sharing_info and discount is not None:
            discounted.append((ledger_row, discount))
    if len(discounted) < 2:
        return []
    lowest = min(discount for _, discount in discounted)
    return [
        BatchRuleFinding(
            ledger_row.ledger_row_id,
            "维护费共享折扣",
            f"维护费共享未享受最低折扣：最低折扣{lowest:g}，当前{discount:g}",
            "核对共享维护费折扣政策并按最低折扣计费",
        )
        for ledger_row, discount in discounted
        if discount > lowest
    ]


def _tower_original_owner_power_intro_fee_nonzero(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        sharing_info = " ".join(_text(ledger_row.row.get(field)) for field in _SHARING_INFO_FIELDS)
        fee = _number(_first_value(ledger_row.row, _POWER_INTRO_FEE_FIELDS))
        if "原产权方" in sharing_info and fee is not None and fee != 0:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电力引入费(元/年)",
                    "站址共享信息为原产权方，电力引入费不为0",
                    "按原产权方共享规则核对电力引入费是否应减免",
                )
            )
    return findings


def _missing_site_code_duplicate_name(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    grouped: dict[str, list[AuditLedgerRow]] = {}
    for ledger_row in rows:
        if ledger_row.ledger_type != "site":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        site_name = _text(ledger_row.telecom_site_name or ledger_row.row.get("电信站址名称"))
        if not site_code and site_name:
            grouped.setdefault(site_name, []).append(ledger_row)
    return [
        BatchRuleFinding(
            ledger_row.ledger_row_id,
            "电信站址编码",
            f"站址编码为空，且站址名称“{site_name}”重复出现",
            "补充电信站址编码，核对是否重复建档",
        )
        for site_name, group in grouped.items()
        if len(group) > 1
        for ledger_row in group
    ]


def _site_code_missing_in_master(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
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
        if not site_code or _is_placeholder(site_code):
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电信站址编码",
                    "费用台账电信站址编码为空或为占位值，无法与站址台账进行匹配",
                    "补充正确的电信站址编码后重新核对跨表一致性",
                )
            )
        elif site_code not in site_codes:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电信站址编码",
                    f"费用台账电信站址编码“{site_code}”未在站址台账中找到",
                    "补充站址主数据或核对费用台账站址编码",
                )
            )
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
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电信站址名称",
                    f"同一站址编码名称不一致：站址台账“{master_name}”，当前台账“{current_name}”",
                    "统一站址名称或核对站址编码",
                )
            )
    return findings


def _tower_stopped_site_still_charged(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        stop_date = _text(ledger_row.row.get("停租日期"))
        fee = sum(_number(ledger_row.row.get(field)) or 0 for field in _TOWER_FEE_FIELDS)
        if stop_date and fee > 0:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "停租日期",
                    f"停租日期已填写但仍存在费用，费用合计{fee:g}",
                    "核对停租日期、账期和费用生成口径",
                )
            )
    return findings


def _tower_charged_after_stop_period(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type != "tower_rent":
            continue
        stop_month = _month_key(ledger_row.row.get("停租日期"))
        period = _period_key(_first_value(ledger_row.row, _PERIOD_FIELDS))
        fee = sum(_number(ledger_row.row.get(field)) or 0 for field in _TOWER_FEE_FIELDS)
        if stop_month and period and period > stop_month and fee > 0:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "账期",
                    f"账期{period}晚于停租月份{stop_month}，但仍存在费用{fee:g}",
                    "核对停租日期、生效账期和费用终止口径",
                )
            )
    return findings


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
            (_number(ledger_row.row.get(field)) or 0) > 0 for field in _GENERATOR_AMOUNT_FIELDS
        )
        if site_code in responsible_by_site and not responsible_by_site[site_code] and has_generator_cost:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "站址发电责任方",
                    "站址台账发电责任方缺失，但发电费台账存在费用或时长",
                    "补充站址发电责任方并核对发电费用口径",
                )
            )
    return findings


def _generator_missing_date_with_cost(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type != "generator":
            continue
        has_cost_or_duration = (_number(ledger_row.row.get("发电时长")) or 0) > 0 or any(
            (_number(ledger_row.row.get(field)) or 0) > 0 for field in _GENERATOR_AMOUNT_FIELDS
        )
        has_date = any(_text(ledger_row.row.get(field)) for field in _GENERATOR_DATE_FIELDS)
        if has_cost_or_duration and not has_date:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "发电日期",
                    "发电费台账存在发电时长或金额，但发电日期为空",
                    "补充发电日期并核对运维工单",
                )
            )
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
            ledger_row.ledger_row_id,
            "运维系统工单号",
            f"同一运维系统工单号重复出现：{work_order}",
            "核实该工单是否重复填报或重复报账",
        )
        for work_order, group in grouped.items()
        if len(group) > 1
        for ledger_row in group
    ]


def _generator_duration_mismatch(allowed_hours: float) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        findings: list[BatchRuleFinding] = []
        for ledger_row in rows:
            if ledger_row.ledger_type != "generator":
                continue
            start = _datetime_value(_first_value(ledger_row.row, _GENERATOR_START_FIELDS))
            end = _datetime_value(_first_value(ledger_row.row, _GENERATOR_END_FIELDS))
            reported = _number(ledger_row.row.get("发电时长"))
            if start is None or end is None or reported is None:
                continue
            calculated = (end - start).total_seconds() / 3600
            if calculated < 0:
                continue
            if abs(calculated - reported) > allowed_hours:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        "发电时长",
                        f"发电起止时间计算时长{calculated:g}小时，填报发电时长{reported:g}小时，偏差超过{allowed_hours:g}小时",
                        "核对发电开始时间、结束时间和填报时长",
                    )
                )
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
            amount = sum(_number(ledger_row.row.get(field)) or 0 for field in _GENERATOR_AMOUNT_FIELDS)
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
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        "最终分摊金额",
                        f"发电小时单价异常：同批次中位数{median:g}元/小时，当前{rate:g}元/小时",
                        "核实发电时长、分摊金额和结算标准",
                    )
                )
        return findings

    return evaluate


def _duplicate_positive_fee(field_name: str, short_name: str, message: str) -> Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]:
    def evaluate(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
        grouped: dict[tuple[str, str], list[AuditLedgerRow]] = {}
        for ledger_row in rows:
            site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
            period = _period_key(_first_value(ledger_row.row, _PERIOD_FIELDS))
            fee = _number(ledger_row.row.get(field_name))
            if not site_code or not period or fee is None or fee <= 0:
                continue
            grouped.setdefault((site_code, period), []).append(ledger_row)
        return [
            BatchRuleFinding(ledger_row.ledger_row_id, field_name, message, f"核对同账期同站址{short_name}是否重复计费")
            for group in grouped.values()
            if len(group) > 1
            for ledger_row in group
        ]

    return evaluate


def _inconsistent_in_group(
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
            distinct = {value for _, value in group}
            if len(distinct) <= 1:
                continue
            findings.extend(BatchRuleFinding(row.ledger_row_id, field_name, message, suggestion) for row, _ in group)
        return findings

    return evaluate
