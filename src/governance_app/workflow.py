from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

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
        "from": {"imported", "audited"},
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


def list_issues(
    config: AppConfig,
    batch_id: int,
    filters: dict[str, str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]] | dict[str, Any]:
    filters = filters or {}
    where = ["batch_id = ?"]
    params: list[Any] = [batch_id]
    for key, column in {
        "city": "coalesce(city, '未填地市')",
        "ledger_type": "ledger_type",
        "severity": "severity",
        "status": "status",
        "rule_id": "rule_id",
    }.items():
        value = filters.get(key)
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    base_where = " and ".join(where)
    sql = f"""
        select issue_code, coalesce(city, '未填地市') as city, district, telecom_site_code,
               telecom_site_name, ledger_type, rule_id, severity, status, message, suggestion,
               correction_value, correction_note, updated_at
          from issues
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
            item["rule_name"] = metadata.name
            item["explanation"] = _issue_explanation(item, metadata)
            item["review_suggestion"] = _review_suggestion(item)
            issues.append(item)
        if limit is None:
            return issues
        return {"issues": issues, "total": total, "limit": safe_limit, "offset": safe_offset}


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
                   sum(case when status = 'not_required' then 1 else 0 end) as not_required_count
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
            },
        )
        for key in ("total_count", "pending_count", "returned_count", "review_count", "still_invalid_count", "closed_count", "not_required_count"):
            target[key] += int(item[key] or 0)
    progress: list[dict[str, Any]] = []
    for item in merged.values():
        closed = int(item["closed_count"] or 0) + int(item["not_required_count"] or 0)
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
    if status in {"closed", "not_required"}:
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
            select i.batch_id, b.is_archived
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
