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
    ledger_type: LedgerType
    severity: Severity
    evaluate: Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]


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
_UNIT_COUNT_FIELDS = ("铁塔产品单元数", "机房产品单元数", "配套产品单元数", "产品单元数")
_MAINTENANCE_DISCOUNT_FIELDS = ("维护费共享折扣", "维护费最低折扣", "维护费折扣", "维护费折扣系数")
_SHARING_INFO_FIELDS = ("站址共享信息", "铁塔共享信息", "共享信息")


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
