from typing import Any

from governance_app.audit_quality import confidence_for, confidence_label
from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.geo import normalize_city


def dashboard_summary(config: AppConfig, batch_id: int) -> dict[str, Any]:
    with connect(config) as conn:
        ledger_counts = {
            row["ledger_type"]: row["count"]
            for row in conn.execute(
                "select ledger_type, count(*) as count from ledger_rows where batch_id = ? group by ledger_type",
                (batch_id,),
            )
        }
        issues_by_city = _city_counts(
            conn.execute("select city, count(*) as count from issues where batch_id = ? group by city", (batch_id,))
        )
        issues_by_rule = []
        for row in conn.execute(
            "select rule_id, count(*) as count from issues where batch_id = ? group by rule_id order by count desc",
            (batch_id,),
        ):
            item = dict(row)
            item["rule_name"] = rule_metadata(item["rule_id"]).name
            issues_by_rule.append(item)
        issues_by_severity = [
            {**dict(row), "severity_label": _severity_label(row["severity"])}
            for row in conn.execute(
                "select severity, count(*) as count from issues where batch_id = ? group by severity order by count desc, severity",
                (batch_id,),
            )
        ]
        issues_by_ledger_type = [
            {**dict(row), "ledger_label": _ledger_label(row["ledger_type"])}
            for row in conn.execute(
                "select ledger_type, count(*) as count from issues where batch_id = ? group by ledger_type order by count desc, ledger_type",
                (batch_id,),
            )
        ]
        issue_categories = []
        for row in conn.execute(
            """
            select ledger_type, rule_id, severity, count(*) as count
              from issues
             where batch_id = ?
             group by ledger_type, rule_id, severity
             order by count desc, ledger_type, rule_id
            """,
            (batch_id,),
        ):
            item = dict(row)
            metadata = rule_metadata(item["rule_id"])
            item["rule_name"] = metadata.name
            item["category"] = metadata.category
            item["category_label"] = _rule_category_label(metadata.category)
            item["ledger_label"] = _ledger_label(item["ledger_type"])
            item["severity_label"] = _severity_label(item["severity"])
            issue_categories.append(item)
        city_rule_matrix = []
        for row in conn.execute(
            """
            select coalesce(city, '未填地市') as city, ledger_type, rule_id, count(*) as count
              from issues
             where batch_id = ?
             group by coalesce(city, '未填地市'), ledger_type, rule_id
             order by city, ledger_type, count desc, rule_id
            """,
            (batch_id,),
        ):
            item = dict(row)
            item["city"] = normalize_city(item["city"])
            metadata = rule_metadata(item["rule_id"])
            item["rule_name"] = metadata.name
            item["category"] = metadata.category
            item["category_label"] = _rule_category_label(metadata.category)
            item["ledger_label"] = _ledger_label(item["ledger_type"])
            city_rule_matrix.append(item)
        city_rule_matrix = _merge_matrix(city_rule_matrix, ("city", "ledger_type", "rule_id"), label_field="ledger_label", enrich_rule=True)
        city_ledger_matrix = [
            {**dict(row), "city": normalize_city(row["city"]), "ledger_label": _ledger_label(row["ledger_type"])}
            for row in conn.execute(
                """
                select coalesce(city, '未填地市') as city, ledger_type, count(*) as count
                  from issues
                 where batch_id = ?
                 group by coalesce(city, '未填地市'), ledger_type
                 order by city, count desc, ledger_type
                """,
                (batch_id,),
            )
        ]
        city_ledger_matrix = _merge_matrix(city_ledger_matrix, ("city", "ledger_type"), label_field="ledger_label")
        city_severity_matrix = [
            {**dict(row), "city": normalize_city(row["city"]), "severity_label": _severity_label(row["severity"])}
            for row in conn.execute(
                """
                select coalesce(city, '未填地市') as city, severity, count(*) as count
                  from issues
                 where batch_id = ?
                 group by coalesce(city, '未填地市'), severity
                 order by city, count desc, severity
                """,
                (batch_id,),
            )
        ]
        city_severity_matrix = _merge_matrix(city_severity_matrix, ("city", "severity"), label_field="severity_label")
        status_counts = {
            row["status"]: row["count"]
            for row in conn.execute(
                "select status, count(*) as count from issues where batch_id = ? group by status",
                (batch_id,),
            )
        }
        total_issues = sum(status_counts.values())
        closed_count = (
            int(status_counts.get("closed", 0) or 0)
            + int(status_counts.get("not_required", 0) or 0)
            + int(status_counts.get("resolved_by_reaudit", 0) or 0)
        )
        open_issue_count = total_issues - closed_count
        rule_effectiveness = []
        for row in conn.execute(
            """
            select rule_id, severity,
                   count(*) as total_count,
                   sum(case when status not in ('closed', 'not_required', 'resolved_by_reaudit') then 1 else 0 end) as open_count,
                   sum(case when status in ('closed', 'not_required', 'resolved_by_reaudit') then 1 else 0 end) as closed_count,
                   sum(case when status = 'not_required' then 1 else 0 end) as not_required_count,
                   sum(case when status = 'still_invalid' then 1 else 0 end) as still_invalid_count
              from issues
             where batch_id = ?
             group by rule_id, severity
             order by case severity when 'high' then 0 when 'medium' then 1 else 2 end,
                      total_count desc,
                      rule_id
            """,
            (batch_id,),
        ):
            item = dict(row)
            metadata = rule_metadata(item["rule_id"])
            confidence = confidence_for(metadata.category, item["severity"])
            total = int(item["total_count"] or 0)
            closed = int(item["closed_count"] or 0)
            item["rule_name"] = metadata.name
            item["category"] = metadata.category
            item["category_label"] = _rule_category_label(metadata.category)
            item["confidence"] = confidence
            item["confidence_label"] = confidence_label(confidence)
            item["closure_rate"] = round((closed / total) * 100, 1) if total else 0.0
            rule_effectiveness.append(item)
        return {
            "batch_id": batch_id,
            "ledger_counts": ledger_counts,
            "issues_by_city": issues_by_city,
            "issues_by_rule": issues_by_rule,
            "issues_by_severity": issues_by_severity,
            "issues_by_ledger_type": issues_by_ledger_type,
            "issue_categories": issue_categories,
            "city_rule_matrix": city_rule_matrix,
            "city_ledger_matrix": city_ledger_matrix,
            "city_severity_matrix": city_severity_matrix,
            "status_counts": status_counts,
            "rule_effectiveness": rule_effectiveness,
            "open_issue_count": open_issue_count,
            "closure_rate": round((closed_count / total_issues) * 100, 1) if total_issues else 0.0,
        }


def _ledger_label(value: str) -> str:
    return {
        "site": "站址",
        "tower_rent": "铁塔租费",
        "electricity": "电费",
        "generator": "发电费",
    }.get(value, value)


def _severity_label(value: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value, value)


def _rule_category_label(value: str) -> str:
    return {"data_quality": "基础数据质量", "problem_audit": "问题稽核"}.get(value, value)


def _city_counts(rows) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        city = normalize_city(row["city"])
        counts[city] = counts.get(city, 0) + int(row["count"] or 0)
    return [
        {"city": city, "count": count}
        for city, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _merge_matrix(rows: list[dict[str, Any]], keys: tuple[str, ...], label_field: str | None = None, enrich_rule: bool = False) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row[field] for field in keys)
        if key not in merged:
            merged[key] = {field: row[field] for field in keys}
            merged[key]["count"] = 0
            if label_field:
                merged[key][label_field] = row[label_field]
            if enrich_rule:
                merged[key]["rule_name"] = row["rule_name"]
                merged[key]["category"] = row.get("category")
                merged[key]["category_label"] = row.get("category_label")
        merged[key]["count"] += int(row["count"] or 0)
    return sorted(merged.values(), key=lambda item: (item["city"], -item["count"], str(tuple(item.values()))))
