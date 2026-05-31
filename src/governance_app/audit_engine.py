import hashlib
import json
from dataclasses import dataclass
from time import perf_counter

from governance_app.audit_rules import (
    AuditLedgerRow,
    BatchRuleFinding,
    RuleFinding,
    RuleThresholds,
    all_batch_rules,
    all_rules,
    parse_row,
)
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.rule_settings import RuleSetting, load_rule_settings


@dataclass(frozen=True)
class AuditRunResult:
    audit_run_id: int
    issue_count: int


def run_audit(config: AppConfig, batch_id: int) -> AuditRunResult:
    started_at = perf_counter()
    rule_settings = load_rule_settings(config)
    rules = _enabled_rules(all_rules(_thresholds_from_settings(rule_settings)), rule_settings)
    batch_rules = _enabled_rules(all_batch_rules(), rule_settings)
    with connect(config) as conn:
        audit_run_id = conn.execute(
            "insert into audit_runs(batch_id, rule_count) values (?, ?)",
            (batch_id, len(rules) + len(batch_rules)),
        ).lastrowid
        rows = conn.execute(
            "select * from ledger_rows where batch_id = ? order by id",
            (batch_id,),
        ).fetchall()
        issue_count = 0
        audit_rows = [
            AuditLedgerRow(
                ledger_row_id=ledger_row["id"],
                ledger_type=ledger_row["ledger_type"],
                city=ledger_row["city"],
                district=ledger_row["district"],
                telecom_site_code=ledger_row["telecom_site_code"],
                telecom_site_name=ledger_row["telecom_site_name"],
                row=parse_row(ledger_row["row_json"]),
            )
            for ledger_row in rows
        ]
        for ledger_row in rows:
            row_data = parse_row(ledger_row["row_json"])
            for rule in rules:
                if rule.ledger_type != ledger_row["ledger_type"]:
                    continue
                finding = rule.evaluate(row_data)
                if finding is None:
                    continue
                _insert_finding(
                    conn,
                    audit_run_id,
                    batch_id,
                    ledger_row,
                    rule.rule_id,
                    rule.severity,
                    finding,
                )
                issue_count += 1
        ledger_rows_by_id = {ledger_row["id"]: ledger_row for ledger_row in rows}
        for rule in batch_rules:
            matching_rows = audit_rows if rule.ledger_type == "all" else [row for row in audit_rows if row.ledger_type == rule.ledger_type]
            for finding in rule.evaluate(matching_rows):
                ledger_row = ledger_rows_by_id[finding.ledger_row_id]
                _insert_finding(
                    conn,
                    audit_run_id,
                    batch_id,
                    ledger_row,
                    rule.rule_id,
                    rule.severity,
                    finding,
                )
                issue_count += 1
        conn.execute("update import_batches set status = 'audited' where id = ?", (batch_id,))
        elapsed = perf_counter() - started_at
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "audit", f"执行稽核，生成问题 {issue_count} 条，规则 {len(rules) + len(batch_rules)} 条，耗时 {elapsed:.2f} 秒"),
        )
        return AuditRunResult(audit_run_id=audit_run_id, issue_count=issue_count)


def _enabled_rules(rules, settings: dict[str, RuleSetting]):
    return [rule for rule in rules if settings.get(rule.rule_id, RuleSetting(rule.rule_id)).enabled]


def _thresholds_from_settings(settings: dict[str, RuleSetting]) -> RuleThresholds:
    price_range = settings.get("electricity_price_range")
    config = price_range.config if price_range else {}
    return RuleThresholds(
        electricity_price_min=float(config.get("min", 0)),
        electricity_price_max=float(config.get("max", 2)),
    )


def _issue_code(batch_id: int, ledger_row_id: int, rule_id: str) -> str:
    digest = hashlib.sha1(f"{batch_id}:{ledger_row_id}:{rule_id}".encode("utf-8")).hexdigest()[:10]
    return f"ISS-{batch_id}-{digest}"


def _insert_finding(
    conn,
    audit_run_id: int,
    batch_id: int,
    ledger_row,
    rule_id: str,
    severity: str,
    finding: RuleFinding | BatchRuleFinding,
) -> None:
    result_json = json.dumps({"field": finding.field_name, "message": finding.message}, ensure_ascii=False)
    audit_result_id = conn.execute(
        """
        insert into audit_results(audit_run_id, ledger_row_id, rule_id, severity, message, field_name, result_json)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_run_id,
            ledger_row["id"],
            rule_id,
            severity,
            finding.message,
            finding.field_name,
            result_json,
        ),
    ).lastrowid
    issue_code = _issue_code(batch_id, ledger_row["id"], rule_id)
    conn.execute(
        """
        insert or ignore into issues(
            issue_code, audit_result_id, batch_id, city, district, telecom_site_code, telecom_site_name,
            ledger_type, rule_id, severity, message, suggestion
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            issue_code,
            audit_result_id,
            batch_id,
            ledger_row["city"],
            ledger_row["district"],
            ledger_row["telecom_site_code"],
            ledger_row["telecom_site_name"],
            ledger_row["ledger_type"],
            rule_id,
            severity,
            finding.message,
            finding.suggestion,
        ),
    )
