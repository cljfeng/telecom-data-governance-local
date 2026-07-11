from governance_app.audit_rules import DEFAULT_THRESHOLDS
from governance_app.rules.tower_rent import tower_rent_batch_rules, tower_rent_rules


def test_tower_rent_module_owns_only_tower_rent_rules():
    assert tower_rent_rules(DEFAULT_THRESHOLDS) == []
    rule_ids = [rule.rule_id for rule in tower_rent_batch_rules(DEFAULT_THRESHOLDS)]

    assert len(rule_ids) == 14
    assert all(rule_id.startswith("tower_") for rule_id in rule_ids)
    assert rule_ids[0] == "tower_mount_height_exceeds_tower_height"
    assert rule_ids[-1] == "tower_charged_after_stop_period"
