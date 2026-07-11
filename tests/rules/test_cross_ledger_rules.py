from governance_app.audit_rules import DEFAULT_THRESHOLDS
from governance_app.rules.cross_ledger import cross_ledger_batch_rules


def test_cross_ledger_module_owns_only_cross_ledger_rules():
    assert [rule.rule_id for rule in cross_ledger_batch_rules(DEFAULT_THRESHOLDS)] == [
        "amount_negative",
        "fee_amount_period_spike",
        "fee_paid_without_master_site",
        "site_name_mismatch_across_ledgers",
    ]
