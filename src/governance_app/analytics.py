from typing import Any

from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect


def dashboard_summary(config: AppConfig, batch_id: int) -> dict[str, Any]:
    with connect(config) as conn:
        ledger_counts = {
            row["ledger_type"]: row["count"]
            for row in conn.execute(
                "select ledger_type, count(*) as count from ledger_rows where batch_id = ? group by ledger_type",
                (batch_id,),
            )
        }
        issues_by_city = [
            dict(row)
            for row in conn.execute(
                "select coalesce(city, '未填地市') as city, count(*) as count from issues where batch_id = ? group by coalesce(city, '未填地市') order by count desc",
                (batch_id,),
            )
        ]
        issues_by_rule = []
        for row in conn.execute(
            "select rule_id, count(*) as count from issues where batch_id = ? group by rule_id order by count desc",
            (batch_id,),
        ):
            item = dict(row)
            item["rule_name"] = rule_metadata(item["rule_id"]).name
            issues_by_rule.append(item)
        issues_by_severity = [
            dict(row)
            for row in conn.execute(
                "select severity, count(*) as count from issues where batch_id = ? group by severity order by count desc, severity",
                (batch_id,),
            )
        ]
        status_counts = {
            row["status"]: row["count"]
            for row in conn.execute(
                "select status, count(*) as count from issues where batch_id = ? group by status",
                (batch_id,),
            )
        }
        total_issues = sum(status_counts.values())
        closed_count = int(status_counts.get("closed", 0) or 0) + int(status_counts.get("not_required", 0) or 0)
        open_issue_count = total_issues - closed_count
        return {
            "batch_id": batch_id,
            "ledger_counts": ledger_counts,
            "issues_by_city": issues_by_city,
            "issues_by_rule": issues_by_rule,
            "issues_by_severity": issues_by_severity,
            "status_counts": status_counts,
            "open_issue_count": open_issue_count,
            "closure_rate": round((closed_count / total_issues) * 100, 1) if total_issues else 0.0,
        }
