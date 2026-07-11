from governance_app.audit_rules import DEFAULT_THRESHOLDS
from governance_app.rules.electricity import electricity_rules
from governance_app.rules.generator import generator_batch_rules, generator_rules


def test_generator_module_owns_generator_rules():
    assert [rule.rule_id for rule in generator_rules(DEFAULT_THRESHOLDS)] == ["generator_duration_over_24h"]
    assert [rule.rule_id for rule in generator_batch_rules(DEFAULT_THRESHOLDS)] == [
        "generator_missing_responsible_party",
        "generator_missing_date_with_cost",
        "generator_duplicate_work_order",
        "generator_duration_mismatch",
        "generator_cost_per_hour_outlier",
    ]


def test_electricity_module_owns_electricity_row_rules():
    assert [rule.rule_id for rule in electricity_rules(DEFAULT_THRESHOLDS)] == [
        "electricity_price_range",
        "electricity_share_percent",
    ]
