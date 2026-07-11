from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from governance_app.audit_quality import (
    confidence_for,
    confidence_label,
    parse_result_payload,
)
from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.geo import normalize_city
from governance_app.models import IssueStatus
from governance_app.templates import FIELD_GROUPS

ISSUE_STATUSES = {
    "pending_export",
    "pending_correction",
    "returned",
    "still_invalid",
    "needs_review",
    "closed",
    "not_required",
    "resolved_by_reaudit",
}


WORKFLOW_STEPS = [
    ("created", "创建批次"),
    ("imported", "导入台账"),
    ("audited", "执行稽核"),
    ("distributed", "导出整改包"),
    ("returning", "导入回传"),
    ("archived", "归档"),
]

NEXT_ACTIONS = {
    "created": "导入台账",
    "imported": "执行稽核",
    "audited": "导出整改包",
    "distributed": "导入回传",
    "returning": "复核并归档",
    "archived": "已归档",
}

STEP_ACTIONS = {
    "created": {"label": "新建批次", "view": "batches"},
    "imported": {"label": "导入台账", "view": "import"},
    "audited": {"label": "执行稽核", "view": "audit"},
    "distributed": {"label": "导出整改包", "view": "export"},
    "returning": {"label": "导入回传", "view": "corrections"},
    "archived": {"label": "生成归档", "view": "reports"},
}

NEXT_STEP_ACTIONS = {
    "created": {"label": "导入台账", "view": "import"},
    "imported": {"label": "执行稽核", "view": "audit"},
    "audited": {"label": "导出整改包", "view": "export"},
    "distributed": {"label": "导入回传", "view": "corrections"},
    "returning": {"label": "复核并归档", "view": "reports"},
    "archived": {"label": "已归档", "view": "reports"},
}

BATCH_TRANSITIONS = {
    "import": {
        "from": {"created", "imported", "audited", "distributed", "returning"},
        "to": "imported",
    },
    "audit": {
        "from": {"imported", "audited", "distributed", "returning"},
        "to": "audited",
    },
    "export": {
        "from": {"audited"},
        "to": "distributed",
    },
    "export_empty": {
        "from": {"audited"},
        "to": "returning",
    },
    "correction_return": {
        "from": {"distributed", "returning"},
        "to": "returning",
    },
    "archive": {
        "from": {"returning"},
        "to": "archived",
    },
}


def create_batch(config: AppConfig, name: str) -> int:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("batch name is required")
    with connect(config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, batch_code, status) values (?, ?, ?, ?)",
            ("", cleaned, _new_batch_code(), "created"),
        ).lastrowid
        conn.execute(
            "insert into settings(key, value_json) values ('current_batch_id', ?) "
            "on conflict(key) do update set value_json = excluded.value_json",
            (str(batch_id),),
        )
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "create_batch", f"创建专项批次：{cleaned}"),
        )
        return int(batch_id)


def transition_batch(config: AppConfig, batch_id: int, event: str) -> str:
    with connect(config) as conn:
        return transition_batch_in_conn(conn, batch_id, event)


def transition_batch_in_conn(conn, batch_id: int, event: str) -> str:
    rule = BATCH_TRANSITIONS.get(event)
    if rule is None:
        raise ValueError("invalid batch transition")
    row = conn.execute("select status, is_archived from import_batches where id = ?", (batch_id,)).fetchone()
    if row is None:
        raise ValueError("batch not found")
    if row["is_archived"]:
        raise ValueError("batch is archived")
    current = row["status"]
    if current not in rule["from"]:
        raise ValueError(f"invalid batch transition: {current} -> {event}")
    target = rule["to"]
    if event == "archive":
        conn.execute(
            "update import_batches set status = ?, is_archived = 1, archived_at = current_timestamp where id = ?",
            (target, batch_id),
        )
    else:
        conn.execute("update import_batches set status = ? where id = ?", (target, batch_id))
    return str(target)


def set_current_batch(config: AppConfig, batch_id: int) -> None:
    with connect(config) as conn:
        _require_batch(conn, batch_id)
        conn.execute(
            "insert into settings(key, value_json) values ('current_batch_id', ?) "
            "on conflict(key) do update set value_json = excluded.value_json",
            (str(batch_id),),
        )
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "select_batch", "切换当前工作批次"),
        )


def list_batches(config: AppConfig) -> list[dict[str, Any]]:
    with connect(config) as conn:
        current = _current_batch_id(conn)
        rows = conn.execute(
            """
            select id, coalesce(name, source_file, '未命名批次') as name, batch_code, source_file, template_version,
                   created_at, status, is_archived, archived_at
              from import_batches
             order by id desc
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": _display_batch_name(row["name"]),
                "batch_code": row["batch_code"] or _code_from_created_at(row["created_at"], row["id"]),
                "source_file": row["source_file"],
                "template_version": row["template_version"],
                "created_at": row["created_at"],
                "status": row["status"],
                "is_archived": bool(row["is_archived"]),
                "archived_at": row["archived_at"],
                "is_current": row["id"] == current,
            }
            for row in rows
        ]


def get_batch_workflow(config: AppConfig, batch_id: int) -> dict[str, Any]:
    with connect(config) as conn:
        batch = _batch_dict(_require_batch(conn, batch_id))
        status = "archived" if batch["is_archived"] else batch["status"]
        active_index = _step_index(status)
        todo_summary = _todo_summary(conn, batch_id)
        operations = [
            dict(row)
            for row in conn.execute(
                """
                select operation, message, created_at
                  from operation_logs
                 where batch_id = ?
                 order by id desc
                 limit 10
                """,
                (batch_id,),
            )
        ]
        return {
            "batch": batch,
            "next_action": NEXT_ACTIONS.get(status, "继续处理"),
            "guidance": _workflow_guidance(status, todo_summary),
            "todo_summary": todo_summary,
            "steps": [
                {
                    "key": key,
                    "label": label,
                    "state": _step_state(index, active_index, status),
                    "can_operate": index == active_index and status != "archived",
                    "blocked_reason": "" if index <= active_index else f"请先完成{NEXT_ACTIONS.get(status, WORKFLOW_STEPS[active_index][1])}",
                    "primary_action": NEXT_STEP_ACTIONS.get(status, STEP_ACTIONS.get(key, {"label": label, "view": "dashboard"})) if index == active_index else STEP_ACTIONS.get(key, {"label": label, "view": "dashboard"}),
                }
                for index, (key, label) in enumerate(WORKFLOW_STEPS)
            ],
            "operations": operations,
        }


def _todo_summary(conn, batch_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        select count(*) as total_issue_count,
               sum(case when status not in ('closed', 'not_required', 'resolved_by_reaudit') then 1 else 0 end) as open_issue_count,
               sum(case when status = 'pending_correction' then 1 else 0 end) as pending_count,
               sum(case when status = 'needs_review' then 1 else 0 end) as review_count,
               sum(case when status = 'still_invalid' then 1 else 0 end) as still_invalid_count
          from issues
         where batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    ledger_count = conn.execute(
        "select count(*) as count from ledger_rows where batch_id = ?",
        (batch_id,),
    ).fetchone()["count"]
    return {
        "ledger_count": int(ledger_count or 0),
        "total_issue_count": int(row["total_issue_count"] or 0),
        "open_issue_count": int(row["open_issue_count"] or 0),
        "pending_count": int(row["pending_count"] or 0),
        "review_count": int(row["review_count"] or 0),
        "still_invalid_count": int(row["still_invalid_count"] or 0),
    }


def _workflow_guidance(status: str, todo_summary: dict[str, Any]) -> dict[str, str]:
    guidance = {
        "created": {
            "title": "下一步：导入台账",
            "reason": "当前批次还没有台账数据，先导入省公司模板后才能执行稽核。",
            "primary_label": "导入台账",
            "primary_view": "import",
        },
        "imported": {
            "title": "下一步：执行稽核",
            "reason": "台账已导入，当前批次还没有形成可整改的问题清单。",
            "primary_label": "执行稽核",
            "primary_view": "audit",
        },
        "audited": {
            "title": "下一步：导出整改包",
            "reason": f"已生成 {todo_summary['total_issue_count']} 条问题，请确认后下发地市整改。",
            "primary_label": "导出整改包",
            "primary_view": "export",
        },
        "distributed": {
            "title": "下一步：导入回传",
            "reason": f"仍有 {todo_summary['pending_count']} 条问题等待地市回传整改结果。",
            "primary_label": "导入回传",
            "primary_view": "corrections",
        },
        "returning": {
            "title": "下一步：复核并归档",
            "reason": f"待复核 {todo_summary['review_count']} 条，仍异常 {todo_summary['still_invalid_count']} 条，闭环后可归档。",
            "primary_label": "查看分析报表",
            "primary_view": "reports",
        },
        "archived": {
            "title": "已归档",
            "reason": "当前批次已经锁定归档，可查看归档汇总和分析报表。",
            "primary_label": "查看归档",
            "primary_view": "reports",
        },
    }
    return guidance.get(
        status,
        {
            "title": "下一步：继续处理",
            "reason": "请按当前流程继续完成专项治理。",
            "primary_label": "查看工作台",
            "primary_view": "dashboard",
        },
    )


def list_issues(
    config: AppConfig,
    batch_id: int,
    filters: dict[str, str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]] | dict[str, Any]:
    filters = filters or {}
    where = ["issues.batch_id = ?"]
    params: list[Any] = [batch_id]
    for key, column in {
        "city": "coalesce(issues.city, '未填地市')",
        "ledger_type": "issues.ledger_type",
        "severity": "issues.severity",
        "status": "issues.status",
        "rule_id": "issues.rule_id",
    }.items():
        value = filters.get(key)
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    closure = filters.get("closure")
    if closure == "open":
        where.append("issues.status not in ('closed', 'not_required', 'resolved_by_reaudit')")
    elif closure == "closed":
        where.append("issues.status in ('closed', 'not_required', 'resolved_by_reaudit')")
    base_where = " and ".join(where)
    sql = f"""
        select issues.issue_code, coalesce(issues.city, '未填地市') as city, issues.district,
               issues.telecom_site_code, issues.telecom_site_name, issues.ledger_type,
               issues.rule_id, issues.severity, issues.status, issues.message, issues.suggestion,
               issues.correction_value, issues.correction_note, issues.updated_at, ar.result_json
          from issues
          left join audit_results ar on ar.id = issues.audit_result_id
         where {base_where}
         order by updated_at desc, issue_code
    """
    if limit is not None:
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        sql = f"{sql} limit ? offset ?"
        query_params = params + [safe_limit, safe_offset]
    else:
        safe_limit = 500
        safe_offset = 0
        sql = f"{sql} limit 500"
        query_params = params
    with connect(config) as conn:
        total = conn.execute(f"select count(*) as count from issues where {base_where}", params).fetchone()["count"]
        issues = []
        for row in conn.execute(sql, query_params):
            item = dict(row)
            metadata = rule_metadata(item["rule_id"])
            result = parse_result_payload(item.pop("result_json", None))
            confidence = result.get("confidence") or confidence_for(metadata.category, item["severity"])
            item["rule_name"] = metadata.name
            item["confidence"] = confidence
            item["confidence_label"] = result.get("confidence_label") or confidence_label(confidence)
            item["evidence"] = result.get("evidence") or _fallback_evidence(item)
            item["group"] = _issue_group(conn, batch_id, item)
            item["explanation"] = _issue_explanation(item, metadata)
            item["review_suggestion"] = _review_suggestion(item)
            issues.append(item)
        if limit is None:
            return issues
        return {"issues": issues, "total": total, "limit": safe_limit, "offset": safe_offset}


def _issue_group(conn, batch_id: int, issue: dict[str, Any]) -> dict[str, Any]:
    count = conn.execute(
        """
        select count(*) as count
          from issues
         where batch_id = ?
           and rule_id = ?
           and coalesce(telecom_site_code, '') = coalesce(?, '')
        """,
        (batch_id, issue["rule_id"], issue.get("telecom_site_code")),
    ).fetchone()["count"]
    return {
        "same_site_rule_count": int(count or 0),
        "label": "同站址同规则聚合" if int(count or 0) > 1 else "单条问题",
    }


def _fallback_evidence(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "field": None,
        "value": None,
        "message": issue.get("message"),
        "ledger_type": issue.get("ledger_type"),
        "city": issue.get("city"),
        "district": issue.get("district"),
        "site_code": issue.get("telecom_site_code"),
        "site_name": issue.get("telecom_site_name"),
    }


def list_issue_rules(config: AppConfig, batch_id: int) -> list[dict[str, Any]]:
    with connect(config) as conn:
        rows = conn.execute(
            """
            select rule_id, count(*) as issue_count
              from issues
             where batch_id = ?
             group by rule_id
             order by issue_count desc, rule_id
            """,
            (batch_id,),
        ).fetchall()
    return [
        {
            "rule_id": row["rule_id"],
            "rule_name": rule_metadata(row["rule_id"]).name,
            "issue_count": row["issue_count"],
        }
        for row in rows
    ]


def list_issue_groups(config: AppConfig, batch_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    where = ["batch_id = ?"]
    params: list[Any] = [batch_id]
    for key, column in {
        "city": "coalesce(city, '未填地市')",
        "ledger_type": "ledger_type",
        "rule_id": "rule_id",
    }.items():
        value = filters.get(key)
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    closure = filters.get("closure")
    if closure == "open":
        where.append("status not in ('closed', 'not_required', 'resolved_by_reaudit')")
    elif closure == "closed":
        where.append("status in ('closed', 'not_required', 'resolved_by_reaudit')")
    sql = f"""
        select coalesce(city, '未填地市') as city,
               ledger_type,
               rule_id,
               severity,
               coalesce(telecom_site_code, '') as telecom_site_code,
               max(telecom_site_name) as telecom_site_name,
               count(*) as issue_count,
               sum(case when status not in ('closed', 'not_required', 'resolved_by_reaudit') then 1 else 0 end) as open_count,
               sum(case when status = 'needs_review' then 1 else 0 end) as review_count,
               sum(case when status = 'still_invalid' then 1 else 0 end) as still_invalid_count,
               sum(case when status = 'closed' then 1 else 0 end) as closed_count,
               sum(case when status = 'not_required' then 1 else 0 end) as not_required_count,
               min(issue_code) as representative_issue_code,
               max(updated_at) as updated_at
          from issues
         where {" and ".join(where)}
         group by coalesce(city, '未填地市'), ledger_type, rule_id, severity, coalesce(telecom_site_code, '')
         order by open_count desc, issue_count desc, city, telecom_site_code, rule_id
         limit 200
    """
    with connect(config) as conn:
        rows = conn.execute(sql, params).fetchall()
    groups = []
    for row in rows:
        metadata = rule_metadata(row["rule_id"])
        groups.append(
            {
                "city": normalize_city(row["city"]),
                "ledger_type": row["ledger_type"],
                "rule_id": row["rule_id"],
                "rule_name": metadata.name,
                "severity": row["severity"],
                "telecom_site_code": row["telecom_site_code"],
                "telecom_site_name": row["telecom_site_name"],
                "issue_count": int(row["issue_count"] or 0),
                "open_count": int(row["open_count"] or 0),
                "review_count": int(row["review_count"] or 0),
                "still_invalid_count": int(row["still_invalid_count"] or 0),
                "closed_count": int(row["closed_count"] or 0),
                "not_required_count": int(row["not_required_count"] or 0),
                "representative_issue_code": row["representative_issue_code"],
                "updated_at": row["updated_at"],
            }
        )
    return groups


def update_issue_group_status(config: AppConfig, batch_id: int, group: dict[str, Any], status: IssueStatus) -> int:
    if status not in ISSUE_STATUSES:
        raise ValueError("invalid issue status")
    where = ["batch_id = ?", "rule_id = ?", "ledger_type = ?", "coalesce(city, '未填地市') = ?", "coalesce(telecom_site_code, '') = ?"]
    params: list[Any] = [
        batch_id,
        group.get("rule_id", ""),
        group.get("ledger_type", ""),
        group.get("city", ""),
        group.get("telecom_site_code", ""),
    ]
    with connect(config) as conn:
        batch = conn.execute("select is_archived from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("batch not found")
        if batch["is_archived"]:
            raise ValueError("batch is archived")
        affected = conn.execute(
            f"select id, status from issues where {' and '.join(where)}",
            params,
        ).fetchall()
        cursor = conn.execute(
            f"update issues set status = ?, updated_at = current_timestamp where {' and '.join(where)}",
            [status] + params,
        )
        if cursor.rowcount:
            conn.executemany(
                """
                insert into issue_events(issue_id, from_status, to_status, source, note)
                values (?, ?, ?, 'manual_group', ?)
                """,
                [
                    (row["id"], row["status"], status, f"批量更新问题组：{group.get('rule_id')}")
                    for row in affected
                ],
            )
            conn.execute(
                "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
                (batch_id, "update_issue_group_status", f"批量更新问题组：{group.get('rule_id')} -> {status}，{cursor.rowcount} 条"),
            )
        return int(cursor.rowcount or 0)


def city_progress(config: AppConfig, batch_id: int) -> list[dict[str, Any]]:
    with connect(config) as conn:
        top_rules = _top_rules_by_city(conn, batch_id)
        rows = conn.execute(
            """
            select coalesce(city, '未填地市') as city,
                   count(*) as total_count,
                   sum(case when status = 'pending_correction' then 1 else 0 end) as pending_count,
                   sum(case when status = 'returned' then 1 else 0 end) as returned_count,
                   sum(case when status = 'needs_review' then 1 else 0 end) as review_count,
                   sum(case when status = 'still_invalid' then 1 else 0 end) as still_invalid_count,
                   sum(case when status = 'closed' then 1 else 0 end) as closed_count,
                   sum(case when status = 'not_required' then 1 else 0 end) as not_required_count,
                   sum(case when status = 'resolved_by_reaudit' then 1 else 0 end) as resolved_count
              from issues
             where batch_id = ?
             group by coalesce(city, '未填地市')
             order by total_count desc, city
            """,
            (batch_id,),
        ).fetchall()
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        city = normalize_city(item["city"])
        target = merged.setdefault(
            city,
            {
                "city": city,
                "total_count": 0,
                "pending_count": 0,
                "returned_count": 0,
                "review_count": 0,
                "still_invalid_count": 0,
                "closed_count": 0,
                "not_required_count": 0,
                "resolved_count": 0,
            },
        )
        for key in ("total_count", "pending_count", "returned_count", "review_count", "still_invalid_count", "closed_count", "not_required_count", "resolved_count"):
            target[key] += int(item[key] or 0)
    progress: list[dict[str, Any]] = []
    for item in merged.values():
        closed = (
            int(item["closed_count"] or 0)
            + int(item["not_required_count"] or 0)
            + int(item["resolved_count"] or 0)
        )
        total = int(item["total_count"] or 0)
        item["completion_rate"] = round((closed / total) * 100, 1) if total else 0.0
        item["top_rules"] = top_rules.get(item["city"], [])
        progress.append(item)
    return sorted(progress, key=lambda item: (-int(item["total_count"] or 0), item["city"]))


def _top_rules_by_city(conn, batch_id: int) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        select coalesce(city, '未填地市') as city, rule_id, severity, count(*) as count
          from issues
         where batch_id = ?
         group by coalesce(city, '未填地市'), rule_id, severity
         order by count desc,
                  case severity when 'high' then 0 when 'medium' then 1 else 2 end,
                  rule_id
        """,
        (batch_id,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        city = normalize_city(row["city"])
        grouped.setdefault(city, []).append(
            {
                "rule_id": row["rule_id"],
                "rule_name": rule_metadata(row["rule_id"]).name,
                "severity": row["severity"],
                "count": row["count"],
            }
        )
    return {city: rules[:5] for city, rules in grouped.items()}


def list_ledger_rows(config: AppConfig, batch_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    where = ["lr.batch_id = ?"]
    params: list[Any] = [batch_id]
    for key, column in {
        "ledger_type": "lr.ledger_type",
        "city": "coalesce(lr.city, '未填地市')",
        "district": "coalesce(lr.district, '')",
        "site_code": "coalesce(lr.telecom_site_code, '')",
    }.items():
        value = filters.get(key)
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    sql = f"""
        select lr.id, lr.ledger_type, coalesce(lr.city, '未填地市') as city, lr.district,
               lr.telecom_site_code, lr.telecom_site_name, lr.tower_site_code, lr.tower_site_name,
               case
                   when lr.row_json is not null and lr.row_json <> '{{}}' then lr.row_json
                   else coalesce(rr.row_json, lr.row_json)
               end as row_json
          from ledger_rows lr
          left join raw_rows rr on rr.id = lr.raw_row_id
         where {" and ".join(where)}
         order by lr.ledger_type, lr.city, lr.telecom_site_code, lr.id
         limit 500
    """
    with connect(config) as conn:
        rows = conn.execute(sql, params).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        raw = json.loads(row["row_json"])
        ledger_type = row["ledger_type"]
        result.append(
            {
                "id": row["id"],
                "ledger_type": ledger_type,
                "city": row["city"],
                "district": row["district"],
                "telecom_site_code": row["telecom_site_code"],
                "telecom_site_name": row["telecom_site_name"],
                "tower_site_code": row["tower_site_code"],
                "tower_site_name": row["tower_site_name"],
                "field_groups": _field_groups(ledger_type, raw),
                "raw": raw,
            }
        )
    return result


def _issue_explanation(issue: dict[str, Any], metadata) -> dict[str, str]:
    return {
        "rule_id": issue["rule_id"],
        "rule_name": metadata.name,
        "risk": _severity_label(issue["severity"]),
        "what_happened": issue["message"],
        "judgement_basis": metadata.description,
        "recommended_action": issue["suggestion"] or metadata.default_suggestion,
        "requires_attachment": "高风险问题建议补充合同、发票、现场或系统截图等佐证材料" if issue["severity"] == "high" else "必要时补充说明或佐证材料",
    }


def _review_suggestion(issue: dict[str, Any]) -> dict[str, str]:
    status = issue["status"]
    note = (issue.get("correction_note") or "").strip()
    value = (issue.get("correction_value") or "").strip()
    if status == "pending_correction":
        return {"decision": "等待整改", "reason": "问题已导出，等待地市填写整改结果和说明"}
    if status == "needs_review":
        if not note and not value:
            return {"decision": "建议退回", "reason": "回传缺少整改说明和整改后值"}
        if issue["severity"] == "high" and not note:
            return {"decision": "需要人工判断", "reason": "高风险问题缺少整改说明"}
        return {"decision": "建议复核", "reason": "已回传整改信息，请核对佐证和原始台账"}
    if status in {"closed", "not_required", "resolved_by_reaudit"}:
        return {"decision": "已闭环", "reason": "当前状态已计入闭环"}
    if status == "still_invalid":
        return {"decision": "建议退回", "reason": "回传后仍异常，需要地市继续整改"}
    return {"decision": "待处理", "reason": "请按当前流程继续处理"}


def _severity_label(value: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value, value)


def update_issue_status(config: AppConfig, issue_code: str, status: IssueStatus) -> None:
    if status not in ISSUE_STATUSES:
        raise ValueError("invalid issue status")
    with connect(config) as conn:
        row = conn.execute(
            """
            select i.id, i.batch_id, i.status, b.is_archived
              from issues i
              join import_batches b on b.id = i.batch_id
             where i.issue_code = ?
            """,
            (issue_code,),
        ).fetchone()
        if row is None:
            raise ValueError("issue not found")
        if row["is_archived"]:
            raise ValueError("batch is archived")
        conn.execute(
            "update issues set status = ?, updated_at = current_timestamp where issue_code = ?",
            (status, issue_code),
        )
        conn.execute(
            """
            insert into issue_events(issue_id, from_status, to_status, source, note)
            values (?, ?, ?, 'manual', ?)
            """,
            (row["id"], row["status"], status, f"人工更新问题状态：{issue_code}"),
        )
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (row["batch_id"], "update_issue_status", f"更新问题状态：{issue_code} -> {status}"),
        )


def _field_groups(ledger_type: str, raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    configured = FIELD_GROUPS.get(ledger_type, {})
    groups: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for group_name, fields in configured.items():
        values = {field: raw[field] for field in fields if field in raw}
        if values:
            groups[group_name] = values
            used.update(values)
    remaining = {field: value for field, value in raw.items() if field not in used}
    if remaining:
        groups["其他字段"] = remaining
    return groups


def record_operation(config: AppConfig, batch_id: int, operation: str, message: str, status: str | None = None) -> None:
    with connect(config) as conn:
        _require_batch(conn, batch_id)
        if status is not None:
            conn.execute("update import_batches set status = ? where id = ?", (status, batch_id))
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, operation, message),
        )


def _new_batch_code() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _code_from_created_at(created_at: str | None, batch_id: int) -> str:
    if created_at:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(created_at[:19], fmt).strftime("%Y%m%d-%H%M%S")
            except ValueError:
                continue
    return f"批次{batch_id}"


def _require_batch(conn, batch_id: int):
    row = conn.execute(
        """
        select id, coalesce(name, source_file, '未命名批次') as name, source_file, template_version,
               batch_code, created_at, status, is_archived, archived_at
          from import_batches
         where id = ?
        """,
        (batch_id,),
    ).fetchone()
    if row is None:
        raise ValueError("batch not found")
    return row


def _batch_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": _display_batch_name(row["name"]),
        "batch_code": row["batch_code"] or _code_from_created_at(row["created_at"], row["id"]),
        "source_file": row["source_file"],
        "template_version": row["template_version"],
        "created_at": row["created_at"],
        "status": row["status"],
        "is_archived": bool(row["is_archived"]),
        "archived_at": row["archived_at"],
    }


def _current_batch_id(conn) -> int | None:
    row = conn.execute("select value_json from settings where key = 'current_batch_id'").fetchone()
    if row is None:
        return None
    try:
        return int(row["value_json"])
    except ValueError:
        return None


def _display_batch_name(value: str | None) -> str:
    text = str(value or "未命名批次").strip()
    return re.sub(r"^[0-9a-fA-F]{32}-", "", text).strip() or text


def _step_index(status: str) -> int:
    keys = [key for key, _label in WORKFLOW_STEPS]
    return keys.index(status) if status in keys else 0


def _step_state(index: int, active_index: int, status: str) -> str:
    if status == "archived":
        return "done"
    if index < active_index:
        return "done"
    if index == active_index:
        return "current"
    return "pending"
