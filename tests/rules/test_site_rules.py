from governance_app.audit_rules import DEFAULT_THRESHOLDS
from governance_app.rules.site import site_batch_rules, site_rules


def test_site_module_owns_only_site_rules():
    assert [rule.rule_id for rule in site_rules(DEFAULT_THRESHOLDS)] == ["required_site_code", "required_city"]
    assert [rule.rule_id for rule in site_batch_rules(DEFAULT_THRESHOLDS)] == [
        "missing_site_code_duplicate_name",
        "site_code_missing_in_master",
    ]
