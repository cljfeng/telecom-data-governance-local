from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping
from typing import Any, cast

from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.models import IssueStatus
from governance_app.workflow import update_issue_status_in_conn

ROUTE_TO_STORAGE_DOMAIN = {
    "electricity-analysis": "electricity",
    "tower-rent-analysis": "tower_rent",
}

ONLINE_REVIEW_STATUSES = {"needs_review", "still_invalid", "closed", "not_required"}


def optional_nonnegative_amount(value: object, label: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是非负数字")
    try:
        number = float(str(value).replace(",", "").replace("，", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是非负数字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label}必须是非负数字")
    return round(number, 2)


def match_opportunity_in_conn(
    conn: sqlite3.Connection,
    opportunity_code: str,
    *,
    batch_id: int | None = None,
    route_domain: str | None = None,
    expected_issue_code: str | None = None,
) -> sqlite3.Row:
    row = conn.execute(
        """
        select ao.*,
               i.issue_code,
               i.ledger_type as issue_ledger_type,
               i.status as issue_status,
               i.correction_value,
               i.correction_note,
               b.is_archived
          from analysis_opportunities ao
          join import_batches b on b.id = ao.batch_id
          left join issues i on i.issue_code = ao.source_issue_code
         where ao.opportunity_code = ?
        """,
        (opportunity_code,),
    ).fetchone()
    storage_domain = ROUTE_TO_STORAGE_DOMAIN.get(route_domain) if route_domain is not None else None
    if (
        row is None
        or (batch_id is not None and row["batch_id"] != batch_id)
        or (route_domain is not None and storage_domain != row["domain"])
    ):
        raise ValueError("机会不存在或不属于当前批次专题")
    if not row["source_issue_code"] or row["issue_code"] is None:
        raise ValueError("旧版专题机会缺少来源问题，请先重新运行专题分析")
    if expected_issue_code is not None and row["source_issue_code"] != expected_issue_code:
        raise ValueError("专题机会与问题编号不匹配")
    if row["domain"] != row["issue_ledger_type"]:
        raise ValueError("专题机会领域与来源问题不匹配")
    if row["is_archived"]:
        raise ValueError("批次已归档，不能修改专题核查结果")
    return row


def upsert_review_in_conn(
    conn: sqlite3.Connection,
    opportunity: Mapping[str, Any],
    verified: float | None,
    realized: float | None,
    note: str,
) -> None:
    conn.execute(
        """
        insert into analysis_opportunity_reviews(
            batch_id, domain, opportunity_code, opportunity_type, source_issue_code,
            estimated_recoverable_amount, estimated_saving_amount,
            verified_recoverable_amount, realized_saving_amount, review_note
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(opportunity_code) do update set
            verified_recoverable_amount = coalesce(
                excluded.verified_recoverable_amount,
                analysis_opportunity_reviews.verified_recoverable_amount
            ),
            realized_saving_amount = coalesce(
                excluded.realized_saving_amount,
                analysis_opportunity_reviews.realized_saving_amount
            ),
            review_note = excluded.review_note,
            updated_at = current_timestamp
        """,
        (
            opportunity["batch_id"],
            opportunity["domain"],
            opportunity["opportunity_code"],
            opportunity["opportunity_type"],
            opportunity["source_issue_code"],
            opportunity["recoverable_amount"],
            opportunity["saving_opportunity_amount"],
            verified,
            realized,
            note,
        ),
    )


def sync_existing_review_note_in_conn(
    conn: sqlite3.Connection, issue_code: str, note: str
) -> None:
    conn.execute(
        """
        update analysis_opportunity_reviews
           set review_note = ?, updated_at = current_timestamp
         where source_issue_code = ?
        """,
        (note, issue_code),
    )


def review_payload_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "issue_code": row["issue_code"],
        "issue_status": row["issue_status"],
        "correction_value": row["correction_value"],
        "correction_note": row["correction_note"],
        "verified_recoverable_amount": row["verified_recoverable_amount"],
        "realized_saving_amount": row["realized_saving_amount"],
        "review_note": row["review_note"],
        "reviewed_at": row["reviewed_at"],
    }


def load_review_payload_in_conn(
    conn: sqlite3.Connection, opportunity_code: str
) -> dict[str, Any]:
    row = conn.execute(
        """
        select ao.opportunity_code,
               i.issue_code,
               i.status as issue_status,
               i.correction_value,
               i.correction_note,
               r.verified_recoverable_amount,
               r.realized_saving_amount,
               r.review_note,
               r.updated_at as reviewed_at
          from analysis_opportunities ao
          left join issues i on i.issue_code = ao.source_issue_code
          left join analysis_opportunity_reviews r
            on r.opportunity_code = ao.opportunity_code
         where ao.opportunity_code = ?
        """,
        (opportunity_code,),
    ).fetchone()
    if row is None:
        raise ValueError("机会不存在或不属于当前批次专题")
    return {"opportunity_code": row["opportunity_code"], **review_payload_fields(row)}


def review_summary_in_conn(
    conn: sqlite3.Connection, batch_id: int, storage_domain: str
) -> dict[str, int | float]:
    row = conn.execute(
        """
        select sum(case when i.status in ('pending_export', 'pending_correction', 'still_invalid')
                        then 1 else 0 end) as pending_count,
               sum(case when i.status in ('returned', 'needs_review')
                        then 1 else 0 end) as review_count,
               sum(case when i.status in ('closed', 'not_required', 'resolved_by_reaudit')
                        then 1 else 0 end) as closed_count,
               coalesce(sum(r.verified_recoverable_amount), 0) as verified_recoverable_amount,
               coalesce(sum(r.realized_saving_amount), 0) as realized_saving_amount
          from analysis_opportunities ao
          left join issues i on i.issue_code = ao.source_issue_code
          left join analysis_opportunity_reviews r
            on r.opportunity_code = ao.opportunity_code
         where ao.batch_id = ? and ao.domain = ?
        """,
        (batch_id, storage_domain),
    ).fetchone()
    return {
        "pending_count": int(row["pending_count"] or 0),
        "review_count": int(row["review_count"] or 0),
        "closed_count": int(row["closed_count"] or 0),
        "verified_recoverable_amount": round(float(row["verified_recoverable_amount"] or 0), 2),
        "realized_saving_amount": round(float(row["realized_saving_amount"] or 0), 2),
    }


def save_opportunity_review(
    config: AppConfig,
    batch_id: int,
    route_domain: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    status = payload.get("status")
    if status not in ONLINE_REVIEW_STATUSES:
        raise ValueError("专题核查状态无效")
    opportunity_code = str(payload.get("opportunity_code") or "").strip()
    if not opportunity_code:
        raise ValueError("机会编号不能为空")
    verified = optional_nonnegative_amount(
        payload.get("verified_recoverable_amount"), "核实可追回金额"
    )
    realized = optional_nonnegative_amount(payload.get("realized_saving_amount"), "实际落实金额")
    note = str(payload.get("review_note") or "").strip()
    with connect(config) as conn:
        opportunity = match_opportunity_in_conn(
            conn,
            opportunity_code,
            batch_id=batch_id,
            route_domain=route_domain,
        )
        update_issue_status_in_conn(
            conn,
            opportunity["source_issue_code"],
            cast(IssueStatus, status),
            source="analysis_review",
            event_note=f"保存专题核查：{opportunity_code}",
            correction_note=note,
            update_correction_fields=True,
        )
        upsert_review_in_conn(conn, opportunity, verified, realized, note)
        return load_review_payload_in_conn(conn, opportunity_code)
