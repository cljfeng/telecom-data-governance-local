from governance_app.rule_fields import (
    MAINTENANCE_DISCOUNT_FIELDS,
    MOUNT_HEIGHT_FIELDS,
    PERIOD_FIELDS,
    POWER_INTRO_FEE_FIELDS,
    PRODUCT_SERVICE_FEE_FIELDS,
    SHARING_INFO_FIELDS,
    TOWER_FEE_FIELDS,
    TOWER_HEIGHT_FIELDS,
    TOWER_TYPE_FIELDS,
    UNIT_COUNT_FIELDS,
)
from governance_app.rule_helpers import (
    _first_value,
    _month_key,
    _number,
    _period_key,
    _text,
)
from governance_app.rule_types import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    RuleThresholds,
)
from governance_app.rules.factories import duplicate_positive_fee, inconsistent_in_group


def tower_rent_rules(thresholds: RuleThresholds) -> list[AuditRule]:
    del thresholds
    return []


def tower_rent_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]:
    del thresholds
    return [
        BatchAuditRule("tower_mount_height_exceeds_tower_height", "tower_rent", "high", _tower_mount_height_exceeds_tower_height),
        BatchAuditRule(
            "tower_site_height_inconsistent", "tower_rent", "medium",
            inconsistent_in_group(("电信站址编码",), TOWER_HEIGHT_FIELDS, "塔高", "同站址多订单塔高不一致", "核对铁塔站址基础属性和订单塔高"),
        ),
        BatchAuditRule(
            "tower_confirmation_product_changed", "tower_rent", "medium",
            inconsistent_in_group(("业务确认单号",), ("铁塔产品", "铁塔产品类型"), "铁塔产品", "同业务确认单前后账期铁塔产品不一致", "核对业务确认单产品变更依据"),
        ),
        BatchAuditRule(
            "tower_product_shared_users_inconsistent", "tower_rent", "medium",
            inconsistent_in_group(("电信站址编码", "铁塔产品"), ("铁塔共享用户数", "铁塔共享用户数量", "共享用户数"), "铁塔共享用户数", "同站址同铁塔产品共享用户数不一致", "核对同站址多订单的铁塔共享用户数"),
        ),
        BatchAuditRule(
            "tower_room_shared_users_inconsistent", "tower_rent", "medium",
            inconsistent_in_group(("电信站址编码", "机房产品"), ("机房共享用户数", "机房共享用户数量"), "机房共享用户数", "同站址同机房产品机房共享用户数不一致", "核对同站址多订单的机房共享用户数"),
        ),
        BatchAuditRule("tower_duplicate_product_service_fee", "tower_rent", "high", duplicate_positive_fee("产品服务费合计（元/年）（不含税）", "产品服务费", "同账期同站址产品服务费多次计费")),
        BatchAuditRule("tower_duplicate_maintenance_fee", "tower_rent", "high", duplicate_positive_fee("维护费(元/年)", "维护费", "同账期同站址维护费多次计费")),
        BatchAuditRule("tower_duplicate_site_fee", "tower_rent", "high", duplicate_positive_fee("场地费(元/年)", "场地费", "同账期同站址场地费多次计费")),
        BatchAuditRule("tower_duplicate_power_intro_fee", "tower_rent", "high", duplicate_positive_fee("电力引入费(元/年)", "电力引入费", "同账期同站址电力引入费多次计费")),
        BatchAuditRule("tower_product_units_zero_fee_nonzero", "tower_rent", "high", _tower_product_units_zero_fee_nonzero),
        BatchAuditRule("tower_maintenance_discount_not_lowest", "tower_rent", "medium", _tower_maintenance_discount_not_lowest),
        BatchAuditRule("tower_original_owner_power_intro_fee_nonzero", "tower_rent", "high", _tower_original_owner_power_intro_fee_nonzero),
        BatchAuditRule("tower_stopped_site_still_charged", "tower_rent", "medium", _tower_stopped_site_still_charged),
        BatchAuditRule("tower_charged_after_stop_period", "tower_rent", "high", _tower_charged_after_stop_period),
    ]


def _tower_mount_height_exceeds_tower_height(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        tower_type = _text(_first_value(ledger_row.row, TOWER_TYPE_FIELDS))
        if "普通地面塔" not in tower_type and "景观塔" not in tower_type:
            continue
        mount_height = _number(_first_value(ledger_row.row, MOUNT_HEIGHT_FIELDS))
        tower_height = _number(_first_value(ledger_row.row, TOWER_HEIGHT_FIELDS))
        if mount_height is not None and tower_height is not None and mount_height > tower_height:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "挂高", f"挂高大于塔高：挂高{mount_height:g}，塔高{tower_height:g}",
                "仅针对普通地面塔和景观塔核对挂高、塔高录入",
            ))
    return findings


def _tower_product_units_zero_fee_nonzero(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        unit_values = [_number(ledger_row.row.get(field)) for field in UNIT_COUNT_FIELDS if field in ledger_row.row]
        if not unit_values:
            continue
        fee = _number(_first_value(ledger_row.row, PRODUCT_SERVICE_FEE_FIELDS))
        if sum(value or 0 for value in unit_values) == 0 and fee is not None and fee != 0:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "产品服务费合计（元/年）（不含税）", "产品单元数和为0，但产品服务费不为0",
                "核对产品单元数和产品服务费计费依据",
            ))
    return findings


def _tower_maintenance_discount_not_lowest(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    discounted: list[tuple[AuditLedgerRow, float]] = []
    for ledger_row in rows:
        sharing_info = " ".join(_text(ledger_row.row.get(field)) for field in SHARING_INFO_FIELDS)
        discount = _number(_first_value(ledger_row.row, MAINTENANCE_DISCOUNT_FIELDS))
        if "共享" in sharing_info and discount is not None:
            discounted.append((ledger_row, discount))
    if len(discounted) < 2:
        return []
    lowest = min(discount for _, discount in discounted)
    return [
        BatchRuleFinding(
            ledger_row.ledger_row_id, "维护费共享折扣", f"维护费共享未享受最低折扣：最低折扣{lowest:g}，当前{discount:g}",
            "核对共享维护费折扣政策并按最低折扣计费",
        )
        for ledger_row, discount in discounted if discount > lowest
    ]


def _tower_original_owner_power_intro_fee_nonzero(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        sharing_info = " ".join(_text(ledger_row.row.get(field)) for field in SHARING_INFO_FIELDS)
        fee = _number(_first_value(ledger_row.row, POWER_INTRO_FEE_FIELDS))
        if "原产权方" in sharing_info and fee is not None and fee != 0:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "电力引入费(元/年)", "站址共享信息为原产权方，电力引入费不为0",
                "按原产权方共享规则核对电力引入费是否应减免",
            ))
    return findings


def _tower_stopped_site_still_charged(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        stop_date = _text(ledger_row.row.get("停租日期"))
        fee = sum(_number(ledger_row.row.get(field)) or 0 for field in TOWER_FEE_FIELDS)
        if stop_date and fee > 0:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "停租日期", f"停租日期已填写但仍存在费用，费用合计{fee:g}",
                "核对停租日期、账期和费用生成口径",
            ))
    return findings


def _tower_charged_after_stop_period(rows: list[AuditLedgerRow]) -> list[BatchRuleFinding]:
    findings: list[BatchRuleFinding] = []
    for ledger_row in rows:
        if ledger_row.ledger_type != "tower_rent":
            continue
        stop_month = _month_key(ledger_row.row.get("停租日期"))
        period = _period_key(_first_value(ledger_row.row, PERIOD_FIELDS))
        fee = sum(_number(ledger_row.row.get(field)) or 0 for field in TOWER_FEE_FIELDS)
        if stop_month and period and period > stop_month and fee > 0:
            findings.append(BatchRuleFinding(
                ledger_row.ledger_row_id, "账期", f"账期{period}晚于停租月份{stop_month}，但仍存在费用{fee:g}",
                "核对停租日期、生效账期和费用终止口径",
            ))
    return findings
