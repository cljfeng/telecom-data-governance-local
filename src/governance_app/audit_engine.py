import hashlib
import json
from dataclasses import dataclass

from governance_app.audit_rules import all_rules, parse_row
from governance_app.config import AppConfig
from governance_app.db import connect


@dataclass(frozen=True)
class AuditRunResult:
    audit_run_id: int
    issue_count: int


def run_audit(config: AppConfig, batch_id: int) -> AuditRunResult:
    rules = all_rules()
    with connect(config) as conn:
        audit_run_id = conn.execute(
            "insert into audit_runs(batch_id, rule_count) values (?, ?)",
            (batch_id, len(rules)),
        ).lastrowid
        rows = conn.execute(
            "select * from ledger_rows where batch_id = ? order by id",
            (batch_id,),
        ).fetchall()
        issue_count = 0
        for ledger_row in rows:
            row_data = parse_row(ledger_row["row_json"])
            for rule in rules:
                if rule.ledger_type != ledger_row["ledger_type"]:
                    continue
                finding = rule.evaluate(row_data)
                if finding is None:
                    continue
                severity = rule.severity
                result_json = json.dumps({"field": finding.field_name, "message": finding.message}, ensure_ascii=False)
                audit_result_id = conn.execute(
                    """
                    insert into audit_results(audit_run_id, ledger_row_id, rule_id, severity, message, field_name, result_json)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_run_id,
                        ledger_row["id"],
                        rule.rule_id,
                        severity,
                        finding.message,
                        finding.field_name,
                        result_json,
                    ),
                ).lastrowid
                issue_code = _issue_code(batch_id, ledger_row["id"], rule.rule_id)
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
                        rule.rule_id,
                        severity,
                        finding.message,
                        finding.suggestion,
                    ),
                )
                issue_count += 1
        conn.execute("update import_batches set status = 'audited' where id = ?", (batch_id,))
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "audit", f"执行稽核，生成问题 {issue_count} 条"),
        )
        return AuditRunResult(audit_run_id=audit_run_id, issue_count=issue_count)


def _issue_code(batch_id: int, ledger_row_id: int, rule_id: str) -> str:
    digest = hashlib.sha1(f"{batch_id}:{ledger_row_id}:{rule_id}".encode("utf-8")).hexdigest()[:10]
    return f"ISS-{batch_id}-{digest}"
