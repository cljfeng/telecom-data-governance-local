from __future__ import annotations

import json
from typing import Any

from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
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


def create_batch(config: AppConfig, name: str) -> int:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("batch name is required")
    with connect(config) as conn:
        batch_id = conn.execute(
            "insert into import_batches(source_file, name, status) values (?, ?, ?)",
            ("", cleaned, "created"),
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
            select id, coalesce(name, source_file, '未命名批次') as name, source_file, template_version,
                   created_at, status, is_archived, archived_at
              from import_batches
             order by id desc
            """
        ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
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
                }
                for index, (key, label) in enumerate(WORKFLOW_STEPS)
            ],
            "operations": operations,
        }


def list_issues(config: AppConfig, batch_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
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
    sql = f"""
        select issue_code, coalesce(city, '未填地市') as city, district, telecom_site_code,
               telecom_site_name, ledger_type, rule_id, severity, status, message, suggestion,
               correction_value, correction_note, updated_at
          from issues
         where {" and ".join(where)}
         order by updated_at desc, issue_code
         limit 500
    """
    with connect(config) as conn:
        issues = []
        for row in conn.execute(sql, params):
            item = dict(row)
            item["rule_name"] = rule_metadata(item["rule_id"]).name
            issues.append(item)
        return issues


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
    progress: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        closed = int(item["closed_count"] or 0) + int(item["not_required_count"] or 0)
        total = int(item["total_count"] or 0)
        item["completion_rate"] = round((closed / total) * 100, 1) if total else 0.0
        progress.append(item)
    return progress


def list_ledger_rows(config: AppConfig, batch_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    where = ["batch_id = ?"]
    params: list[Any] = [batch_id]
    for key, column in {
        "ledger_type": "ledger_type",
        "city": "coalesce(city, '未填地市')",
        "district": "coalesce(district, '')",
        "site_code": "coalesce(telecom_site_code, '')",
    }.items():
        value = filters.get(key)
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    sql = f"""
        select id, ledger_type, coalesce(city, '未填地市') as city, district,
               telecom_site_code, telecom_site_name, tower_site_code, tower_site_name, row_json
          from ledger_rows
         where {" and ".join(where)}
         order by ledger_type, city, telecom_site_code, id
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


def _require_batch(conn, batch_id: int):
    row = conn.execute(
        """
        select id, coalesce(name, source_file, '未命名批次') as name, source_file, template_version,
               created_at, status, is_archived, archived_at
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
        "name": row["name"],
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
