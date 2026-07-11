from governance_app.audit_rules import all_batch_rules, all_rules
from governance_app.rule_types import AuditLedgerRow, BatchRuleFinding, RuleFinding


ROW_RULES = [
    ("required_site_code", "site", "high"),
    ("required_city", "site", "medium"),
    ("electricity_price_range", "electricity", "high"),
    ("electricity_share_percent", "electricity", "medium"),
    ("generator_duration_over_24h", "generator", "high"),
]

BATCH_RULE_IDS = [
    "electricity_contract_share_variance", "electricity_duplicate_payment", "electricity_usage_spike_drop",
    "electricity_capacity_mismatch", "electricity_meter_reading_reverse", "electricity_reading_usage_mismatch",
    "electricity_zero_usage_positive_fee", "electricity_amount_calculation_mismatch", "electricity_period_overlap",
    "electricity_price_commercial_range", "amount_negative", "fee_amount_period_spike",
    "fee_paid_without_master_site", "electricity_price_city_supply_outlier",
    "tower_mount_height_exceeds_tower_height", "tower_site_height_inconsistent",
    "tower_confirmation_product_changed", "tower_product_shared_users_inconsistent",
    "tower_room_shared_users_inconsistent", "tower_duplicate_product_service_fee",
    "tower_duplicate_maintenance_fee", "tower_duplicate_site_fee", "tower_duplicate_power_intro_fee",
    "tower_product_units_zero_fee_nonzero", "tower_maintenance_discount_not_lowest",
    "tower_original_owner_power_intro_fee_nonzero", "missing_site_code_duplicate_name",
    "site_code_missing_in_master", "site_name_mismatch_across_ledgers", "tower_stopped_site_still_charged",
    "tower_charged_after_stop_period", "electricity_lump_sum_still_reimbursed",
    "electricity_transfer_without_contract", "generator_missing_responsible_party",
    "generator_missing_date_with_cost", "generator_duplicate_work_order", "generator_duration_mismatch",
    "generator_cost_per_hour_outlier",
]


def test_row_rule_snapshot():
    assert [(rule.rule_id, rule.ledger_type, rule.severity) for rule in all_rules()] == ROW_RULES


def test_batch_rule_snapshot():
    rules = all_batch_rules()
    assert [rule.rule_id for rule in rules] == BATCH_RULE_IDS
    assert len({rule.rule_id for rule in rules}) == len(rules)


def test_representative_finding_payload_snapshot():
    row_rules = {rule.rule_id: rule for rule in all_rules()}
    batch_rules = {rule.rule_id: rule for rule in all_batch_rules()}

    assert row_rules["required_site_code"].evaluate({}) == (
        _row_finding("high", "电信站址编码", "站址编码为空", "补充电信站址编码")
    )
    assert row_rules["electricity_price_range"].evaluate({"电费单价": 1}) == (
        _row_finding("high", "电费单价", "电费单价超过 0.9 元", "核实电费单价、电价依据或转供电合同")
    )
    assert row_rules["generator_duration_over_24h"].evaluate({"发电时长": 25}) == (
        _row_finding("high", "发电时长", "发电时长超过 24 小时", "核实发电开始时间、结束时间和工单时长")
    )

    cross_row = _ledger_row(1, "electricity", {"电费金额": -5})
    assert batch_rules["amount_negative"].evaluate([cross_row]) == [
        _batch_finding(1, "电费金额", "电费金额为负数：-5", "核实是否为冲销、退费或录入错误，必要时补充说明")
    ]

    tower_row = _ledger_row(2, "tower_rent", {"铁塔产品": "普通地面塔", "挂高": 30, "塔高": 20})
    assert batch_rules["tower_mount_height_exceeds_tower_height"].evaluate([tower_row]) == [
        _batch_finding(2, "挂高", "挂高大于塔高：挂高30，塔高20", "仅针对普通地面塔和景观塔核对挂高、塔高录入")
    ]


def _ledger_row(row_id, ledger_type, values):
    return AuditLedgerRow(row_id, ledger_type, None, None, None, None, values)


def _row_finding(severity, field_name, message, suggestion):
    return RuleFinding("", severity, field_name, message, suggestion)


def _batch_finding(row_id, field_name, message, suggestion):
    return BatchRuleFinding(row_id, field_name, message, suggestion)
