import json
from dataclasses import dataclass
from statistics import median
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


@dataclass(frozen=True)
class RuleMetadata:
    rule_id: str
    name: str
    ledger_type: LedgerType | str
    severity: Severity | str
    description: str
    default_suggestion: str


@dataclass(frozen=True)
class RuleThresholds:
    electricity_price_min: float = 0
    electricity_price_max: float = 2
    share_percent_min: float = 0
    share_percent_max: float = 100


@dataclass(frozen=True)
class AuditLedgerRow:
    ledger_row_id: int
    ledger_type: LedgerType
    city: str | None
    district: str | None
    telecom_site_code: str | None
    telecom_site_name: str | None
    row: dict[str, Any]


@dataclass(frozen=True)
class BatchRuleFinding:
    ledger_row_id: int
    field_name: str | None
    message: str
    suggestion: str


@dataclass(frozen=True)
class BatchAuditRule:
    rule_id: str
    ledger_type: LedgerType | str
    severity: Severity
    evaluate: Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]


def parse_row(row_json: str) -> dict[str, Any]:
    return json.loads(row_json)


RULE_CATALOG: dict[str, RuleMetadata] = {
    "required_site_code": RuleMetadata("required_site_code", "站址编码必填", "site", "high", "检查站址台账电信站址编码是否为空。", "补充电信站址编码"),
    "required_city": RuleMetadata("required_city", "地市信息必填", "site", "medium", "检查站址台账地市字段是否为空。", "补充地市"),
    "electricity_price_range": RuleMetadata("electricity_price_range", "电费单价合理性", "electricity", "high", "检查电费单价是否超出默认合理区间。", "核实电费单价或转供电合同"),
    "electricity_share_percent": RuleMetadata("electricity_share_percent", "电费分摊比例范围", "electricity", "medium", "检查电费分摊比例是否在 0-100 范围内。", "核实共享情况和分摊比例"),
    "generator_duration_positive": RuleMetadata("generator_duration_positive", "发电时长有效性", "generator", "high", "检查发电时长是否为正数。", "核实发电起止时间和时长"),
    "electricity_price_benchmark": RuleMetadata("electricity_price_benchmark", "电费单价同类基准偏离", "electricity", "high", "按供电方式比较电费单价，识别明显高于同类基准的记录。", "核实电价依据、合同和报账口径"),
    "electricity_contract_share_variance": RuleMetadata("electricity_contract_share_variance", "合同分摊比例偏差", "electricity", "medium", "比较实际分摊比例与合同约定分摊比例。", "核对合同约定和实际分摊比例"),
    "electricity_duplicate_payment": RuleMetadata("electricity_duplicate_payment", "电费重复报账", "electricity", "high", "识别同站址、同电表、同账期的重复电费记录。", "核实同账期是否重复报账"),
    "electricity_usage_spike_drop": RuleMetadata("electricity_usage_spike_drop", "用电量异常波动", "electricity", "high", "识别同站址电表用电量较历史记录的异常上升或下降。", "核实抄表数据、设备变化和报账周期"),
    "electricity_capacity_mismatch": RuleMetadata("electricity_capacity_mismatch", "合同容量与实际容量不一致", "electricity", "medium", "比较合同申报容量与实际用电容量。", "核对合同容量和现场实际容量"),
    "tower_mount_height_exceeds_tower_height": RuleMetadata("tower_mount_height_exceeds_tower_height", "挂高超过塔高", "tower_rent", "high", "检查设备挂高是否超过铁塔塔高。", "核对设备挂高和塔高基础属性"),
    "tower_site_height_inconsistent": RuleMetadata("tower_site_height_inconsistent", "同站址塔高不一致", "tower_rent", "medium", "识别同一电信站址多订单塔高不一致。", "核对铁塔站址基础属性和订单塔高"),
    "tower_confirmation_product_changed": RuleMetadata("tower_confirmation_product_changed", "业务确认单产品不一致", "tower_rent", "medium", "识别同一业务确认单前后账期铁塔产品不一致。", "核对业务确认单产品变更依据"),
    "tower_product_shared_users_inconsistent": RuleMetadata("tower_product_shared_users_inconsistent", "铁塔共享用户数不一致", "tower_rent", "medium", "识别同站址同铁塔产品共享用户数不一致。", "核对同站址多订单的铁塔共享用户数"),
    "tower_room_shared_users_inconsistent": RuleMetadata("tower_room_shared_users_inconsistent", "机房共享用户数不一致", "tower_rent", "medium", "识别同站址同机房产品共享用户数不一致。", "核对同站址多订单的机房共享用户数"),
    "tower_duplicate_product_service_fee": RuleMetadata("tower_duplicate_product_service_fee", "产品服务费重复计费", "tower_rent", "high", "识别同账期同站址产品服务费多次计费。", "核实产品服务费是否重复计费"),
    "tower_duplicate_maintenance_fee": RuleMetadata("tower_duplicate_maintenance_fee", "维护费重复计费", "tower_rent", "high", "识别同账期同站址维护费多次计费。", "核实维护费是否重复计费"),
    "tower_duplicate_site_fee": RuleMetadata("tower_duplicate_site_fee", "场地费重复计费", "tower_rent", "high", "识别同账期同站址场地费多次计费。", "核实场地费是否重复计费"),
    "tower_duplicate_power_intro_fee": RuleMetadata("tower_duplicate_power_intro_fee", "电力引入费重复计费", "tower_rent", "high", "识别同账期同站址电力引入费多次计费。", "核实电力引入费是否重复计费"),
    "tower_product_units_zero_fee_nonzero": RuleMetadata("tower_product_units_zero_fee_nonzero", "产品单元为零但费用非零", "tower_rent", "high", "检查产品单元数为零时是否仍产生产品服务费。", "核对产品配置和费用生成口径"),
    "tower_maintenance_discount_not_lowest": RuleMetadata("tower_maintenance_discount_not_lowest", "维护费共享折扣非最优惠", "tower_rent", "medium", "检查共享场景下维护费共享折扣是否异常。", "核对共享折扣政策和适用用户数"),
    "tower_original_owner_power_intro_fee_nonzero": RuleMetadata("tower_original_owner_power_intro_fee_nonzero", "原产权方电力引入费非零", "tower_rent", "high", "检查原产权方站址是否仍收取电力引入费。", "核实站址产权属性和电力引入费依据"),
    "site_code_missing_in_master": RuleMetadata("site_code_missing_in_master", "站址编码跨表不存在", "all", "high", "检查费用台账中的电信站址编码是否存在于站址台账。", "补充站址主数据或核对费用台账站址编码"),
    "site_name_mismatch_across_ledgers": RuleMetadata("site_name_mismatch_across_ledgers", "站址名称跨表不一致", "all", "medium", "检查同一站址编码在费用台账与站址台账中的名称是否一致。", "统一站址名称或核对站址编码"),
    "tower_stopped_site_still_charged": RuleMetadata("tower_stopped_site_still_charged", "停租后仍产生租费", "tower_rent", "high", "检查停租日期已填写但仍产生租费的记录。", "核对停租状态、账期和费用生成口径"),
    "electricity_lump_sum_still_reimbursed": RuleMetadata("electricity_lump_sum_still_reimbursed", "包干站址仍重复报账", "electricity", "high", "检查包干站址是否仍标记为报账。", "核对包干电费和报账口径，避免重复报账"),
    "electricity_transfer_without_contract": RuleMetadata("electricity_transfer_without_contract", "转供电无合同", "electricity", "high", "检查转供电站址是否缺少转供电合同。", "补充转供电合同或核实供电方式"),
    "generator_missing_responsible_party": RuleMetadata("generator_missing_responsible_party", "发电责任方缺失", "all", "medium", "检查存在发电费但站址发电责任方缺失的记录。", "补充站址发电责任方并核对发电费用口径"),
}

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
            _number_range(
                "电费单价",
                thresholds.electricity_price_min,
                thresholds.electricity_price_max,
                f"电费单价超出 {thresholds.electricity_price_min:g}-{thresholds.electricity_price_max:g} 元合理范围",
                "核实电费单价或转供电合同",
            ),
        ),
        AuditRule(
            "electricity_share_percent",
            "electricity",
            "medium",
            _number_range(
                "分摊比例(%)",
                thresholds.share_percent_min,
                thresholds.share_percent_max,
                f"分摊比例不在 {thresholds.share_percent_min:g}-{thresholds.share_percent_max:g} 范围",
                "核实共享情况和分摊比例",
            ),
        ),
        AuditRule(
            "generator_duration_positive",
            "generator",
            "high",
            _greater_than_zero("发电时长", "发电时长小于等于 0", "核实发电起止时间和时长"),
        ),
    ]


def all_batch_rules() -> list[BatchAuditRule]:
    return [
        BatchAuditRule(
            "electricity_price_benchmark",
            "electricity",
            "high",
            _electricity_price_benchmark,
        ),
        BatchAuditRule(
            "electricity_contract_share_variance",
            "electricity",
            "medium",
            _electricity_contract_share_variance,
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
            _electricity_usage_spike_drop,
        ),
        BatchAuditRule(
            "electricity_capacity_mismatch",
            "electricity",
            "medium",
            _electricity_capacity_mismatch,
        ),
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
        BatchAuditRule("site_code_missing_in_master", "all", "high", _site_code_missing_in_master),
        BatchAuditRule("site_name_mismatch_across_ledgers", "all", "medium", _site_name_mismatch_across_ledgers),
        BatchAuditRule("tower_stopped_site_still_charged", "tower_rent", "high", _tower_stopped_site_still_charged),
        BatchAuditRule("electricity_lump_sum_still_reimbursed", "electricity", "high", _electricity_lump_sum_still_reimbursed),
        BatchAuditRule("electricity_transfer_without_contract", "electricity", "high", _electricity_transfer_without_contract),
        BatchAuditRule("generator_missing_responsible_party", "all", "medium", _generator_missing_responsible_party),
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


_PRICE_FIELDS = ("电费单价", "单价", "电价")
_SUPPLY_FIELDS = ("供电方式", "供电类型")
_PERIOD_FIELDS = ("报账周期", "账期", "账单月份", "计费账期")
_USAGE_FIELDS = ("用电量", "本期用电量", "用电量(kWh)", "用电量（kWh）")
_CONTRACT_SHARE_FIELDS = ("合同约定分摊比例(%)", "合同分摊比例(%)", "合同约定分摊比例", "合同分摊比例")
_CONTRACT_CAPACITY_FIELDS = ("合同申报容量", "合同容量", "申报容量")
_ACTUAL_CAPACITY_FIELDS = ("实际用电容量", "实际容量", "用电容量")
_TOWER_HEIGHT_FIELDS = ("塔高", "铁塔塔高")
_MOUNT_HEIGHT_FIELDS = ("挂高", "天线挂高", "设备挂高")
_TOWER_TYPE_FIELDS = ("塔桅类型", "铁塔类型", "铁塔产品", "铁塔产品类型")
_POWER_INTRO_FEE_FIELDS = ("电力引入费(元/年)", "电力引入费", "电力引入费（元/年）")
_PRODUCT_SERVICE_FEE_FIELDS = ("产品服务费合计（元/年）（不含税）", "产品服务费合计", "产品服务费")
_TOWER_FEE_FIELDS = _PRODUCT_SERVICE_FEE_FIELDS + ("维护费(元/年)", "场地费(元/年)", "电力引入费(元/年)")
_UNIT_COUNT_FIELDS = ("铁塔产品单元数", "机房产品单元数", "配套产品单元数", "产品单元数")
_MAINTENANCE_DISCOUNT_FIELDS = ("维护费共享折扣", "维护费最低折扣", "维护费折扣", "维护费折扣系数")
_SHARING_INFO_FIELDS = ("站址共享信息", "铁塔共享信息", "共享信息")
_TRANSFER_CONTRACT_FIELDS = ("转供电合同情况", "转供电合同", "合同情况")
_GENERATOR_AMOUNT_FIELDS = ("最终分摊金额", "分摊金额", "非5G金额", "5G金额")


def _electricity_price_benchmark(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    grouped: dict[tuple[str, str], list[tuple[AuditLedgerRow, float]]] = {}
    for ledger_row in rows:
        price = _number(_first_value(ledger_row.row, _PRICE_FIELDS))
        supply = _text(_first_value(ledger_row.row, _SUPPLY_FIELDS))
        district = _text(ledger_row.district or ledger_row.row.get("区县"))
        if price is None or not supply or not district:
            continue
        grouped.setdefault((district, supply), []).append((ledger_row, price))

    findings: list[BatchRuleFinding] = []
    for (district, supply), peers in grouped.items():
        if len(peers) < 2:
            continue
        benchmark = median(price for _, price in peers)
        if benchmark == 0:
            continue
        for ledger_row, price in peers:
            variance = (price - benchmark) / benchmark
            if abs(variance) > 0.05:
                findings.append(
                    BatchRuleFinding(
                        ledger_row.ledger_row_id,
                        "电费单价",
                        f"同区县同供电方式电费单价偏差超过±5%：{district}/{supply}基准{benchmark:g}，当前{price:g}",
                        "核实供电合同、电价依据及报账单价",
                    )
                )
    return findings


def _electricity_contract_share_variance(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        actual = _number(ledger_row.row.get("分摊比例(%)"))
        contract = _number(_first_value(ledger_row.row, _CONTRACT_SHARE_FIELDS))
        if actual is None or contract is None:
            continue
        if abs(actual - contract) > 3:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "分摊比例(%)",
                    f"铁塔单站分摊比例与合同约定偏差超过±3个百分点：合同{contract:g}%，当前{actual:g}%",
                    "按合同约定核对分摊比例",
                )
            )
    return findings


def _electricity_usage_spike_drop(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
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
        for (_, previous_row, previous_usage), (_, current_row, current_usage) in zip(values, values[1:], strict=False):
            if previous_usage == 0:
                continue
            change = (current_usage - previous_usage) / previous_usage
            if abs(change) > 0.3:
                findings.append(
                    BatchRuleFinding(
                        current_row.ledger_row_id,
                        "用电量",
                        f"用电量较上一账期突变超过30%：上一期{previous_usage:g}，本期{current_usage:g}",
                        "核对抄表读数、倍率和异常能耗原因",
                    )
                )
    return findings


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


def _site_code_missing_in_master(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    site_codes = {
        _text(row.telecom_site_code or row.row.get("电信站址编码"))
        for row in rows
        if row.ledger_type == "site"
    }
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type == "site":
            continue
        site_code = _text(ledger_row.telecom_site_code or ledger_row.row.get("电信站址编码"))
        if site_code and site_code not in site_codes:
            findings.append(
                BatchRuleFinding(
                    ledger_row.ledger_row_id,
                    "电信站址编码",
                    f"费用台账站址编码未在站址台账中找到：{site_code}",
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


def _first_value(row: dict[str, Any], field_names: tuple[str, ...]) -> Any:
    for field_name in field_names:
        value = row.get(field_name)
        if value not in (None, ""):
            return value
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if text.endswith("%"):
        text = text[:-1]
    for unit in ("元/年", "元", "kWh", "KWH", "度", "米", "m", "M"):
        text = text.replace(unit, "")
    try:
        return float(text)
    except ValueError:
        return None


def _period_key(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    return text.replace("年", "-").replace("月", "").replace("/", "-").strip()
