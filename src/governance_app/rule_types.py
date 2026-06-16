from dataclasses import dataclass
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
class RuleThresholds:
    electricity_price_min: float = 0
    electricity_price_max: float = 0.9
    share_percent_min: float = 0
    share_percent_max: float = 100
    generator_duration_max_hours: float = 24
    contract_share_variance_points: float = 3
    usage_change_ratio: float = 0.3
    fee_period_change_ratio: float = 1
    city_supply_price_deviation_ratio: float = 0.2
    generator_duration_mismatch_hours: float = 0.25
    generator_cost_per_hour_multiplier: float = 1.5
    generator_cost_per_hour_min: float = 300
    electricity_amount_variance_ratio: float = 0.1
    electricity_amount_variance_min: float = 100
    electricity_usage_mismatch_ratio: float = 0.1
    electricity_usage_mismatch_min: float = 10
    electricity_commercial_price_min: float = 0.3
    electricity_commercial_price_max: float = 1.5


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
    ledger_type: LedgerType | str
    severity: Severity
    evaluate: Callable[[list[AuditLedgerRow]], list[BatchRuleFinding]]
