from governance_app.audit_rules import all_batch_rules, all_rules
from governance_app.rule_types import AuditLedgerRow, BatchRuleFinding, RuleFinding, RuleThresholds


ROW_RULES = [
    ("required_site_code", "site", "high"),
    ("required_city", "site", "medium"),
    ("electricity_price_range", "electricity", "high"),
    ("electricity_share_percent", "electricity", "medium"),
    ("generator_duration_over_24h", "generator", "high"),
]

BATCH_RULES = [
    ("electricity_contract_share_variance", "electricity", "medium"),
    ("electricity_duplicate_payment", "electricity", "high"),
    ("electricity_usage_spike_drop", "electricity", "high"),
    ("electricity_capacity_mismatch", "electricity", "medium"),
    ("electricity_meter_reading_reverse", "electricity", "medium"),
    ("electricity_reading_usage_mismatch", "electricity", "medium"),
    ("electricity_zero_usage_positive_fee", "electricity", "high"),
    ("electricity_amount_calculation_mismatch", "electricity", "high"),
    ("electricity_period_overlap", "electricity", "high"),
    ("electricity_price_commercial_range", "electricity", "medium"),
    ("amount_negative", "all", "high"),
    ("fee_amount_period_spike", "all", "high"),
    ("fee_paid_without_master_site", "all", "high"),
    ("electricity_price_city_supply_outlier", "electricity", "medium"),
    ("tower_mount_height_exceeds_tower_height", "tower_rent", "high"),
    ("tower_site_height_inconsistent", "tower_rent", "medium"),
    ("tower_confirmation_product_changed", "tower_rent", "medium"),
    ("tower_product_shared_users_inconsistent", "tower_rent", "medium"),
    ("tower_room_shared_users_inconsistent", "tower_rent", "medium"),
    ("tower_duplicate_product_service_fee", "tower_rent", "high"),
    ("tower_duplicate_maintenance_fee", "tower_rent", "high"),
    ("tower_duplicate_site_fee", "tower_rent", "high"),
    ("tower_duplicate_power_intro_fee", "tower_rent", "high"),
    ("tower_product_units_zero_fee_nonzero", "tower_rent", "high"),
    ("tower_maintenance_discount_not_lowest", "tower_rent", "medium"),
    ("tower_original_owner_power_intro_fee_nonzero", "tower_rent", "high"),
    ("missing_site_code_duplicate_name", "site", "high"),
    ("site_code_missing_in_master", "all", "high"),
    ("site_name_mismatch_across_ledgers", "all", "medium"),
    ("tower_stopped_site_still_charged", "tower_rent", "medium"),
    ("tower_charged_after_stop_period", "tower_rent", "high"),
    ("electricity_lump_sum_still_reimbursed", "electricity", "high"),
    ("electricity_transfer_without_contract", "electricity", "high"),
    ("generator_missing_responsible_party", "all", "medium"),
    ("generator_missing_date_with_cost", "generator", "high"),
    ("generator_duplicate_work_order", "generator", "high"),
    ("generator_duration_mismatch", "generator", "medium"),
    ("generator_cost_per_hour_outlier", "generator", "high"),
]


def test_row_rule_snapshot():
    assert [(rule.rule_id, rule.ledger_type, rule.severity) for rule in all_rules()] == ROW_RULES


def test_batch_rule_snapshot():
    rules = all_batch_rules()
    assert [(rule.rule_id, rule.ledger_type, rule.severity) for rule in rules] == BATCH_RULES
    assert len({rule.rule_id for rule in rules}) == len(rules)


def test_custom_threshold_rule_snapshot():
    thresholds = _custom_thresholds()

    assert [(rule.rule_id, rule.ledger_type, rule.severity) for rule in all_rules(thresholds)] == ROW_RULES
    assert [(rule.rule_id, rule.ledger_type, rule.severity) for rule in all_batch_rules(thresholds)] == BATCH_RULES

    row_rules = {rule.rule_id: rule for rule in all_rules(thresholds)}
    assert row_rules["electricity_price_range"].evaluate({"电费单价": 0.1}) is None
    assert row_rules["electricity_price_range"].evaluate({"电费单价": 1.22}) is None
    assert row_rules["electricity_price_range"].evaluate({"电费单价": 1.24}) is not None
    assert row_rules["electricity_share_percent"].evaluate({"分摊比例(%)": 11}) is not None
    assert row_rules["electricity_share_percent"].evaluate({"分摊比例(%)": 88}) is None
    assert row_rules["generator_duration_over_24h"].evaluate({"发电时长": 8}) is not None


def test_custom_batch_thresholds_are_forwarded_to_rule_closures():
    thresholds = _custom_thresholds()

    assert not _findings(
        "electricity_contract_share_variance",
        thresholds,
        [_ledger_row(1, "electricity", {"分摊比例(%)": 3.5, "合同约定分摊比例(%)": 0})],
    )
    assert "±4个百分点" in _findings(
        "electricity_contract_share_variance",
        thresholds,
        [_ledger_row(1, "electricity", {"分摊比例(%)": 6, "合同约定分摊比例(%)": 0})],
    )[0].message
    assert not _findings(
        "electricity_usage_spike_drop",
        thresholds,
        [
            _ledger_row(1, "electricity", {"电信站址编码": "S1", "账期": "2026-01", "用电量": 100}),
            _ledger_row(2, "electricity", {"电信站址编码": "S1", "账期": "2026-02", "用电量": 140}),
        ],
    )
    assert "超过42%" in _findings(
        "electricity_usage_spike_drop",
        thresholds,
        [
            _ledger_row(1, "electricity", {"电信站址编码": "S1", "账期": "2026-01", "用电量": 100}),
            _ledger_row(2, "electricity", {"电信站址编码": "S1", "账期": "2026-02", "用电量": 150}),
        ],
    )[0].message
    assert "超过73%" in _findings(
        "fee_amount_period_spike",
        thresholds,
        [
            _ledger_row(1, "electricity", {"电信站址编码": "S1", "账期": "2026-01", "电费金额": 100}),
            _ledger_row(2, "electricity", {"电信站址编码": "S1", "账期": "2026-02", "电费金额": 180}),
        ],
    )[0].message
    assert not _findings(
        "electricity_reading_usage_mismatch",
        thresholds,
        [_ledger_row(1, "electricity", {"上次抄表数": 0, "本次抄表数": 100, "用电量": 120})],
    )
    assert not _findings(
        "electricity_reading_usage_mismatch",
        thresholds,
        [_ledger_row(1, "electricity", {"上次抄表数": 0, "本次抄表数": 200, "用电量": 230})],
    )
    assert _findings(
        "electricity_reading_usage_mismatch",
        thresholds,
        [_ledger_row(1, "electricity", {"上次抄表数": 0, "本次抄表数": 100, "用电量": 150})],
    )
    assert not _findings(
        "electricity_amount_calculation_mismatch",
        thresholds,
        [_ledger_row(1, "electricity", {"用电量": 100, "电费单价": 1, "电费金额": 300})],
    )
    assert not _findings(
        "electricity_amount_calculation_mismatch",
        thresholds,
        [_ledger_row(1, "electricity", {"用电量": 2000, "电费单价": 1, "电费金额": 2300})],
    )
    assert _findings(
        "electricity_amount_calculation_mismatch",
        thresholds,
        [_ledger_row(1, "electricity", {"用电量": 100, "电费单价": 1, "电费金额": 350})],
    )
    assert "0.41-1.37" in _findings(
        "electricity_price_commercial_range",
        thresholds,
        [_ledger_row(1, "electricity", {"电费单价": 0.4})],
    )[0].message
    assert "0.41-1.37" in _findings(
        "electricity_price_commercial_range",
        thresholds,
        [_ledger_row(1, "electricity", {"电费单价": 1.38})],
    )[0].message
    assert not _findings(
        "electricity_price_city_supply_outlier",
        thresholds,
        [
            _ledger_row(row_id, "electricity", {"地市": "杭州", "区县": "上城", "供电方式": "直供", "电费单价": price})
            for row_id, price in enumerate((1, 1, 1, 1.3), start=1)
        ],
    )
    assert "超过31%" in _findings(
        "electricity_price_city_supply_outlier",
        thresholds,
        [
            _ledger_row(row_id, "electricity", {"地市": "杭州", "区县": "上城", "供电方式": "直供", "电费单价": price})
            for row_id, price in enumerate((1, 1, 1, 1.4), start=1)
        ],
    )[0].message
    assert not _findings(
        "generator_duration_mismatch",
        thresholds,
        [_ledger_row(1, "generator", {"发电开始时间": "2026-01-01 00:00:00", "发电结束时间": "2026-01-01 08:00:00", "发电时长": 7.5})],
    )
    assert "超过0.6小时" in _findings(
        "generator_duration_mismatch",
        thresholds,
        [_ledger_row(1, "generator", {"发电开始时间": "2026-01-01 00:00:00", "发电结束时间": "2026-01-01 08:00:00", "发电时长": 7})],
    )[0].message
    assert not _findings(
        "generator_cost_per_hour_outlier",
        thresholds,
        [
            _ledger_row(row_id, "generator", {"发电时长": 1, "最终分摊金额": amount})
            for row_id, amount in enumerate((100, 100, 300), start=1)
        ],
    )
    assert not _findings(
        "generator_cost_per_hour_outlier",
        thresholds,
        [
            _ledger_row(row_id, "generator", {"发电时长": 1, "最终分摊金额": amount})
            for row_id, amount in enumerate((300, 300, 600), start=1)
        ],
    )
    assert "当前700元/小时" in _findings(
        "generator_cost_per_hour_outlier",
        thresholds,
        [
            _ledger_row(row_id, "generator", {"发电时长": 1, "最终分摊金额": amount})
            for row_id, amount in enumerate((100, 100, 700), start=1)
        ],
    )[0].message


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


def _findings(rule_id, thresholds, rows):
    rules = {rule.rule_id: rule for rule in all_batch_rules(thresholds)}
    return rules[rule_id].evaluate(rows)


def _custom_thresholds():
    return RuleThresholds(
        electricity_price_min=0.27,
        electricity_price_max=1.23,
        share_percent_min=12,
        share_percent_max=88,
        generator_duration_max_hours=7,
        contract_share_variance_points=4,
        usage_change_ratio=0.42,
        fee_period_change_ratio=0.73,
        city_supply_price_deviation_ratio=0.31,
        generator_duration_mismatch_hours=0.6,
        generator_cost_per_hour_multiplier=2.2,
        generator_cost_per_hour_min=456,
        electricity_amount_variance_ratio=0.17,
        electricity_amount_variance_min=234,
        electricity_usage_mismatch_ratio=0.19,
        electricity_usage_mismatch_min=23,
        electricity_commercial_price_min=0.41,
        electricity_commercial_price_max=1.37,
    )


def _row_finding(severity, field_name, message, suggestion):
    return RuleFinding("", severity, field_name, message, suggestion)


def _batch_finding(row_id, field_name, message, suggestion):
    return BatchRuleFinding(row_id, field_name, message, suggestion)
