from governance_app.audit_rules import all_batch_rules, all_rules


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
