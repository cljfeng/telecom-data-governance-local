"""Compatibility facade for the modular audit rule registry."""

import json
from typing import Any, Iterable, TypeVar

from governance_app.rule_catalog import RULE_CATALOG, RuleMetadata
from governance_app.rule_types import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    RuleFinding,
    RuleThresholds,
)
from governance_app.rules.cross_ledger import cross_ledger_batch_rules
from governance_app.rules.electricity import electricity_batch_rules, electricity_rules
from governance_app.rules.generator import generator_batch_rules, generator_rules
from governance_app.rules.site import site_batch_rules, site_rules
from governance_app.rules.tower_rent import tower_rent_batch_rules, tower_rent_rules


DEFAULT_THRESHOLDS = RuleThresholds()

_ROW_RULE_ORDER = (
    "required_site_code",
    "required_city",
    "electricity_price_range",
    "electricity_share_percent",
    "generator_duration_over_24h",
)

_BATCH_RULE_ORDER = (
    "electricity_contract_share_variance",
    "electricity_duplicate_payment",
    "electricity_usage_spike_drop",
    "electricity_capacity_mismatch",
    "electricity_meter_reading_reverse",
    "electricity_reading_usage_mismatch",
    "electricity_zero_usage_positive_fee",
    "electricity_amount_calculation_mismatch",
    "electricity_period_overlap",
    "electricity_price_commercial_range",
    "amount_negative",
    "fee_amount_period_spike",
    "fee_paid_without_master_site",
    "electricity_price_city_supply_outlier",
    "tower_mount_height_exceeds_tower_height",
    "tower_site_height_inconsistent",
    "tower_confirmation_product_changed",
    "tower_product_shared_users_inconsistent",
    "tower_room_shared_users_inconsistent",
    "tower_duplicate_product_service_fee",
    "tower_duplicate_maintenance_fee",
    "tower_duplicate_site_fee",
    "tower_duplicate_power_intro_fee",
    "tower_product_units_zero_fee_nonzero",
    "tower_maintenance_discount_not_lowest",
    "tower_original_owner_power_intro_fee_nonzero",
    "missing_site_code_duplicate_name",
    "site_code_missing_in_master",
    "site_name_mismatch_across_ledgers",
    "tower_stopped_site_still_charged",
    "tower_charged_after_stop_period",
    "electricity_lump_sum_still_reimbursed",
    "electricity_transfer_without_contract",
    "generator_missing_responsible_party",
    "generator_missing_date_with_cost",
    "generator_duplicate_work_order",
    "generator_duration_mismatch",
    "generator_cost_per_hour_outlier",
)

_Rule = TypeVar("_Rule", AuditRule, BatchAuditRule)


def parse_row(row_json: str) -> dict[str, Any]:
    return json.loads(row_json)


def rule_metadata(rule_id: str) -> RuleMetadata:
    return RULE_CATALOG.get(
        rule_id,
        RuleMetadata(rule_id, rule_id, "unknown", "medium", f"未登记规则：{rule_id}", "按规则编号核实问题明细"),
    )


def all_rules(thresholds: RuleThresholds = DEFAULT_THRESHOLDS) -> list[AuditRule]:
    return _ordered_rules(
        (
            *site_rules(thresholds),
            *electricity_rules(thresholds),
            *tower_rent_rules(thresholds),
            *generator_rules(thresholds),
        ),
        _ROW_RULE_ORDER,
    )


def all_batch_rules(thresholds: RuleThresholds = DEFAULT_THRESHOLDS) -> list[BatchAuditRule]:
    return _ordered_rules(
        (
            *electricity_batch_rules(thresholds),
            *cross_ledger_batch_rules(thresholds),
            *tower_rent_batch_rules(thresholds),
            *site_batch_rules(thresholds),
            *generator_batch_rules(thresholds),
        ),
        _BATCH_RULE_ORDER,
    )


def _ordered_rules(rules: Iterable[_Rule], order: tuple[str, ...]) -> list[_Rule]:
    by_id: dict[str, _Rule] = {}
    for rule in rules:
        if rule.rule_id in by_id:
            raise ValueError(f"duplicate audit rule: {rule.rule_id}")
        by_id[rule.rule_id] = rule
    expected = set(order)
    actual = set(by_id)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(f"audit rule registry mismatch: missing={missing}, unexpected={unexpected}")
    return [by_id[rule_id] for rule_id in order]


__all__ = [
    "AuditLedgerRow",
    "AuditRule",
    "BatchAuditRule",
    "BatchRuleFinding",
    "DEFAULT_THRESHOLDS",
    "RuleFinding",
    "RuleThresholds",
    "all_batch_rules",
    "all_rules",
    "parse_row",
    "rule_metadata",
]
