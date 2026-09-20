import hashlib
import json
from dataclasses import dataclass
from time import perf_counter

from governance_app.audit_quality import result_payload
from governance_app.audit_rules import (
    AuditLedgerRow,
    BatchRuleFinding,
    RuleFinding,
    RuleThresholds,
    all_batch_rules,
    all_rules,
    parse_row,
    rule_metadata,
)
from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.ports.database import AuditFindingRecord, Database, UnitOfWork
from governance_app.rule_settings import RuleSetting, load_rule_settings
from governance_app.workflow import transition_batch_in_unit_of_work


@dataclass(frozen=True)
class AuditRunResult:
    audit_run_id: int
    issue_count: int


def run_audit(
    config: AppConfig,
    batch_id: int,
    *,
    database: Database | None = None,
) -> AuditRunResult:
    started_at = perf_counter()
    selected_database = database or database_for(config)
    rule_settings = load_rule_settings(config, database=selected_database)
    thresholds = _thresholds_from_settings(rule_settings)
    rules = _enabled_rules(all_rules(thresholds), rule_settings)
    batch_rules = _enabled_rules(all_batch_rules(thresholds), rule_settings)
    with selected_database.unit_of_work() as unit_of_work:
        batch = unit_of_work.batches.get(batch_id)
        if batch is None:
            raise ValueError("batch not found")
        if batch["is_archived"]:
            raise ValueError("batch is archived")
        rows = unit_of_work.ledgers.audit_rows(batch_id)
        if not rows:
            raise ValueError("当前批次没有台账数据，请先导入台账再执行稽核")
        audit_run_id = unit_of_work.audits.create_run(
            batch_id,
            len(rules) + len(batch_rules),
        )
        issue_count = 0
        seen_issue_codes: set[str] = set()
        audit_rows = [
            AuditLedgerRow(
                ledger_row_id=ledger_row["id"],
                ledger_type=ledger_row["ledger_type"],
                city=ledger_row["city"],
                district=ledger_row["district"],
                telecom_site_code=ledger_row["telecom_site_code"],
                telecom_site_name=ledger_row["telecom_site_name"],
                row=parse_row(ledger_row["effective_row_json"]),
            )
            for ledger_row in rows
        ]
        for ledger_row in rows:
            row_data = parse_row(ledger_row["effective_row_json"])
            for rule in rules:
                if rule.ledger_type != ledger_row["ledger_type"]:
                    continue
                finding = rule.evaluate(row_data)
                if finding is None:
                    continue
                issue_code = _save_finding(
                    unit_of_work,
                    audit_run_id,
                    batch_id,
                    ledger_row,
                    rule.rule_id,
                    rule.severity,
                    finding,
                )
                seen_issue_codes.add(issue_code)
                issue_count += 1
        ledger_rows_by_id = {ledger_row["id"]: ledger_row for ledger_row in rows}
        for rule in batch_rules:
            matching_rows = audit_rows if rule.ledger_type == "all" else [row for row in audit_rows if row.ledger_type == rule.ledger_type]
            for finding in rule.evaluate(matching_rows):
                ledger_row = ledger_rows_by_id[finding.ledger_row_id]
                issue_code = _save_finding(
                    unit_of_work,
                    audit_run_id,
                    batch_id,
                    ledger_row,
                    rule.rule_id,
                    rule.severity,
                    finding,
                )
                seen_issue_codes.add(issue_code)
                issue_count += 1
        unit_of_work.audits.resolve_missing(
            batch_id,
            audit_run_id,
            seen_issue_codes,
        )
        unit_of_work.audits.clear_analysis_opportunities(batch_id)
        transition_batch_in_unit_of_work(unit_of_work, batch_id, "audit")
        elapsed = perf_counter() - started_at
        unit_of_work.batches.add_operation(
            batch_id,
            "audit",
            f"执行稽核，生成问题 {issue_count} 条，规则 {len(rules) + len(batch_rules)} 条，耗时 {elapsed:.2f} 秒",
        )
        return AuditRunResult(audit_run_id=audit_run_id, issue_count=issue_count)


def _enabled_rules(rules, settings: dict[str, RuleSetting]):
    return [rule for rule in rules if settings.get(rule.rule_id, RuleSetting(rule.rule_id)).enabled]


def _thresholds_from_settings(settings: dict[str, RuleSetting]) -> RuleThresholds:
    def number(rule_id: str, key: str, default: float) -> float:
        setting = settings.get(rule_id)
        if not setting:
            return default
        try:
            return float(setting.config.get(key, default))
        except (TypeError, ValueError):
            return default

    return RuleThresholds(
        electricity_price_min=number("electricity_price_range", "min", 0),
        electricity_price_max=number("electricity_price_range", "max", 0.9),
        share_percent_min=number("electricity_share_percent", "min", 0),
        share_percent_max=number("electricity_share_percent", "max", 100),
        generator_duration_max_hours=number("generator_duration_over_24h", "max_hours", 24),
        contract_share_variance_points=number("electricity_contract_share_variance", "max_points", 3),
        usage_change_ratio=number("electricity_usage_spike_drop", "change_ratio", 0.3),
        fee_period_change_ratio=number("fee_amount_period_spike", "change_ratio", 1),
        city_supply_price_deviation_ratio=number("electricity_price_city_supply_outlier", "deviation_ratio", 0.2),
        generator_duration_mismatch_hours=number("generator_duration_mismatch", "allowed_hours", 0.25),
        generator_cost_per_hour_multiplier=number("generator_cost_per_hour_outlier", "multiplier", 1.5),
        generator_cost_per_hour_min=number("generator_cost_per_hour_outlier", "min_rate", 300),
        electricity_amount_variance_ratio=number("electricity_amount_calculation_mismatch", "variance_ratio", 0.1),
        electricity_amount_variance_min=number("electricity_amount_calculation_mismatch", "variance_min", 100),
        electricity_usage_mismatch_ratio=number("electricity_reading_usage_mismatch", "variance_ratio", 0.1),
        electricity_usage_mismatch_min=number("electricity_reading_usage_mismatch", "variance_min", 10),
        electricity_commercial_price_min=number("electricity_price_commercial_range", "min", 0.3),
        electricity_commercial_price_max=number("electricity_price_commercial_range", "max", 1.5),
    )


def _issue_code(batch_id: int, ledger_row_id: int, rule_id: str) -> str:
    digest = hashlib.sha1(f"{batch_id}:{ledger_row_id}:{rule_id}".encode("utf-8")).hexdigest()[:10]
    return f"ISS-{batch_id}-{digest}"


def _save_finding(
    unit_of_work: UnitOfWork,
    audit_run_id: int,
    batch_id: int,
    ledger_row,
    rule_id: str,
    severity: str,
    finding: RuleFinding | BatchRuleFinding,
) -> str:
    metadata = rule_metadata(rule_id)
    row_data = parse_row(ledger_row["effective_row_json"])
    result_json = json.dumps(
        result_payload(
            rule_id=rule_id,
            rule_name=metadata.name,
            category=metadata.category,
            severity=severity,
            field_name=finding.field_name,
            message=finding.message,
            ledger_type=ledger_row["ledger_type"],
            city=ledger_row["city"],
            district=ledger_row["district"],
            site_code=ledger_row["telecom_site_code"],
            site_name=ledger_row["telecom_site_name"],
            row=row_data,
        ),
        ensure_ascii=False,
    )
    issue_code = _issue_code(batch_id, ledger_row["id"], rule_id)
    unit_of_work.audits.save_finding(
        AuditFindingRecord(
            audit_run_id,
            batch_id,
            ledger_row["id"],
            ledger_row["ledger_type"],
            ledger_row["city"],
            ledger_row["district"],
            ledger_row["telecom_site_code"],
            ledger_row["telecom_site_name"],
            rule_id,
            severity,
            finding.message,
            finding.suggestion,
            finding.field_name,
            result_json,
            issue_code,
        )
    )
    return issue_code
