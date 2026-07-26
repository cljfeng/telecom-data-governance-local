from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from governance_app.audit_quality import (
    confidence_for,
    confidence_label,
    parse_result_payload,
)
from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.db import connect
from governance_app.geo import normalize_city
from governance_app.models import IssueStatus
from governance_app.ports.database import (
    Database,
    IssueGroupQuery,
    IssueGroupSelector,
    IssueQuery,
    LedgerQuery,
    UnitOfWork,
)
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


def create_batch(config: AppConfig, name: str, *, database: Database | None = None) -> int:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("batch name is required")
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        batch_id = unit_of_work.batches.create(name=cleaned, batch_code=_new_batch_code())
        unit_of_work.batches.set_current(batch_id)
        unit_of_work.batches.add_operation(
            batch_id,
            "create_batch",
            f"创建专项批次：{cleaned}",
        )
        return batch_id


def transition_batch(
    config: AppConfig,
    batch_id: int,
    event: str,
    *,
    database: Database | None = None,
) -> str:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        return transition_batch_in_unit_of_work(unit_of_work, batch_id, event)


def transition_batch_in_unit_of_work(
    unit_of_work: UnitOfWork,
    batch_id: int,
    event: str,
) -> str:
    batch = unit_of_work.batches.get(batch_id)
    if batch is None:
        raise ValueError("batch not found")
    target, archive = _batch_transition_target(
        status=str(batch["status"]),
        is_archived=bool(batch["is_archived"]),
        event=event,
    )
    unit_of_work.batches.update_status(batch_id, target, archive=archive)
    return target


def transition_batch_in_conn(conn, batch_id: int, event: str) -> str:
    row = conn.execute("select status, is_archived from import_batches where id = ?", (batch_id,)).fetchone()
    if row is None:
        raise ValueError("batch not found")
    target, archive = _batch_transition_target(
        status=str(row["status"]),
        is_archived=bool(row["is_archived"]),
        event=event,
    )
    if archive:
        conn.execute(
            "update import_batches set status = ?, is_archived = 1, archived_at = current_timestamp where id = ?",
            (target, batch_id),
        )
    else:
        conn.execute("update import_batches set status = ? where id = ?", (target, batch_id))
    return str(target)


def _batch_transition_target(
    *,
    status: str,
    is_archived: bool,
    event: str,
) -> tuple[str, bool]:
    rule = BATCH_TRANSITIONS.get(event)
    if rule is None:
        raise ValueError("invalid batch transition")
    if is_archived:
        raise ValueError("batch is archived")
    if status not in rule["from"]:
        raise ValueError(f"invalid batch transition: {status} -> {event}")
    return str(rule["to"]), event == "archive"


def set_current_batch(
    config: AppConfig,
    batch_id: int,
    *,
    database: Database | None = None,
) -> None:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        if unit_of_work.batches.get(batch_id) is None:
            raise ValueError("batch not found")
        unit_of_work.batches.set_current(batch_id)
        unit_of_work.batches.add_operation(
            batch_id,
            "select_batch",
            "切换当前工作批次",
        )


def list_batches(
    config: AppConfig,
    *,
    database: Database | None = None,
) -> list[dict[str, Any]]:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        current = unit_of_work.batches.current_id()
        rows = unit_of_work.batches.list_all()
        return [
            {
                **_batch_dict(row),
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
    *,
    database: Database | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    filters = filters or {}
    if limit is not None:
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
    else:
        safe_limit = 500
        safe_offset = 0
    query = IssueQuery(
        batch_id=batch_id,
        city=filters.get("city"),
        ledger_type=filters.get("ledger_type"),
        severity=filters.get("severity"),
        status=filters.get("status"),
        rule_id=filters.get("rule_id"),
        closure=filters.get("closure"),
        limit=safe_limit,
        offset=safe_offset,
    )
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        rows, total = unit_of_work.issues.query(query)
        issues = []
        for row in rows:
            item = dict(row)
            metadata = rule_metadata(item["rule_id"])
            result = parse_result_payload(item.pop("result_json", None))
            same_site_rule_count = int(item.pop("same_site_rule_count", 0) or 0)
            confidence = result.get("confidence") or confidence_for(metadata.category, item["severity"])
            item["rule_name"] = metadata.name
            item["confidence"] = confidence
            item["confidence_label"] = result.get("confidence_label") or confidence_label(confidence)
            item["evidence"] = result.get("evidence") or _fallback_evidence(item)
            item["group"] = {
                "same_site_rule_count": same_site_rule_count,
                "label": "同站址同规则聚合" if same_site_rule_count > 1 else "单条问题",
            }
            item["explanation"] = _issue_explanation(item, metadata)
            item["review_suggestion"] = _review_suggestion(item)
            issues.append(item)
        if limit is None:
            return issues
        return {"issues": issues, "total": total, "limit": safe_limit, "offset": safe_offset}


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


def list_issue_rules(
    config: AppConfig,
    batch_id: int,
    *,
    database: Database | None = None,
) -> list[dict[str, Any]]:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        rows = unit_of_work.issues.rule_counts(batch_id)
    return [
        {
            "rule_id": row["rule_id"],
            "rule_name": rule_metadata(row["rule_id"]).name,
            "issue_count": row["issue_count"],
        }
        for row in rows
    ]


def list_issue_groups(
    config: AppConfig,
    batch_id: int,
    filters: dict[str, str] | None = None,
    *,
    database: Database | None = None,
) -> list[dict[str, Any]]:
    filters = filters or {}
    query = IssueGroupQuery(
        batch_id=batch_id,
        city=filters.get("city"),
        ledger_type=filters.get("ledger_type"),
        rule_id=filters.get("rule_id"),
        closure=filters.get("closure"),
    )
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        rows = unit_of_work.issues.groups(query)
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


def update_issue_group_status(
    config: AppConfig,
    batch_id: int,
    group: dict[str, Any],
    status: IssueStatus,
    *,
    database: Database | None = None,
) -> int:
    if status not in ISSUE_STATUSES:
        raise ValueError("invalid issue status")
    selector = IssueGroupSelector(
        batch_id=batch_id,
        rule_id=str(group.get("rule_id", "")),
        ledger_type=str(group.get("ledger_type", "")),
        city=str(group.get("city", "")),
        telecom_site_code=str(group.get("telecom_site_code", "")),
    )
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        batch = unit_of_work.batches.get(batch_id)
        if batch is None:
            raise ValueError("batch not found")
        if batch["is_archived"]:
            raise ValueError("batch is archived")
        event_note = f"批量更新问题组：{selector.rule_id}"
        updated_count = unit_of_work.issues.update_group_status(
            selector,
            status,
            source="manual_group",
            event_note=event_note,
        )
        if updated_count:
            unit_of_work.batches.add_operation(
                batch_id,
                "update_issue_group_status",
                f"批量更新问题组：{selector.rule_id} -> {status}，{updated_count} 条",
            )
        return updated_count


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


def list_ledger_rows(
    config: AppConfig,
    batch_id: int,
    filters: dict[str, str] | None = None,
    *,
    limit: int = 500,
    offset: int = 0,
    database: Database | None = None,
) -> list[dict[str, Any]]:
    filters = filters or {}
    query = LedgerQuery(
        batch_id=batch_id,
        ledger_type=filters.get("ledger_type"),
        city=filters.get("city"),
        district=filters.get("district"),
        site_code=filters.get("site_code"),
        limit=max(1, min(limit, 500)),
        offset=max(offset, 0),
    )
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        rows = unit_of_work.ledgers.query(query)
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


def count_ledger_rows(
    config: AppConfig,
    batch_id: int,
    filters: dict[str, str] | None = None,
    *,
    database: Database | None = None,
) -> int:
    filters = filters or {}
    query = LedgerQuery(
        batch_id=batch_id,
        ledger_type=filters.get("ledger_type"),
        city=filters.get("city"),
        district=filters.get("district"),
        site_code=filters.get("site_code"),
    )
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        return unit_of_work.ledgers.count(query)


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


def update_issue_status_in_conn(
    conn: sqlite3.Connection,
    issue_code: str,
    status: IssueStatus,
    *,
    source: str,
    event_note: str,
    correction_value: str | None = None,
    correction_note: str | None = None,
    update_correction_value: bool = False,
    update_correction_note: bool = False,
) -> sqlite3.Row:
    if status not in ISSUE_STATUSES:
        raise ValueError("invalid issue status")
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
    assignments = ["status = ?"]
    params: list[object] = [status]
    if update_correction_value:
        assignments.append("correction_value = ?")
        params.append(correction_value)
    if update_correction_note:
        assignments.append("correction_note = ?")
        params.append(correction_note)
    assignments.append("updated_at = current_timestamp")
    params.append(issue_code)
    conn.execute(
        f"update issues set {', '.join(assignments)} where issue_code = ?",
        params,
    )
    conn.execute(
        """
        insert into issue_events(issue_id, from_status, to_status, source, note)
        values (?, ?, ?, ?, ?)
        """,
        (row["id"], row["status"], status, source, event_note),
    )
    return row


def update_issue_status(
    config: AppConfig,
    issue_code: str,
    status: IssueStatus,
    *,
    database: Database | None = None,
) -> None:
    if status not in ISSUE_STATUSES:
        raise ValueError("invalid issue status")
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        issue = unit_of_work.issues.get_with_batch(issue_code)
        if issue is None:
            raise ValueError("issue not found")
        if issue["is_archived"]:
            raise ValueError("batch is archived")
        unit_of_work.issues.update_status(
            issue,
            status,
            source="manual",
            event_note=f"人工更新问题状态：{issue_code}",
        )
        unit_of_work.batches.add_operation(
            int(issue["batch_id"]),
            "update_issue_status",
            f"更新问题状态：{issue_code} -> {status}",
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
