from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, cast

from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.models import IssueStatus
from governance_app.ports.database import (
    Database,
    PersistenceError,
    ReviewRecord,
    UnitOfWork,
)
from governance_app.workflow import (
    transition_batch_in_unit_of_work,
)

ROUTE_TO_STORAGE_DOMAIN = {
    "electricity-analysis": "electricity",
    "tower-rent-analysis": "tower_rent",
}

ONLINE_REVIEW_STATUSES = {"needs_review", "still_invalid", "closed", "not_required"}
MAX_BATCH_REVIEW_ITEMS = 200


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
def save_opportunity_review(
    config: AppConfig,
    batch_id: int,
    route_domain: str,
    payload: dict[str, Any],
    *,
    database: Database | None = None,
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
    realized = optional_nonnegative_amount(
        payload.get("realized_saving_amount"), "实际落实金额"
    )
    note = str(payload.get("review_note") or "").strip()
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        saved = _save_opportunity_review_in_unit_of_work(
            unit_of_work,
            batch_id,
            route_domain,
            opportunity_code,
            cast(IssueStatus, status),
            verified,
            realized,
            note,
        )
        opportunity = saved["opportunity"]
        if opportunity["batch_status"] in {"distributed", "returning"}:
            transition_batch_in_unit_of_work(
                unit_of_work,
                batch_id,
                "correction_return",
            )
        return saved["review"]


def match_opportunity(
    unit_of_work: UnitOfWork,
    opportunity_code: str,
    *,
    batch_id: int | None = None,
    route_domain: str | None = None,
    expected_issue_code: str | None = None,
) -> ReviewRecord:
    row = unit_of_work.reviews.get_opportunity(opportunity_code)
    storage_domain = (
        ROUTE_TO_STORAGE_DOMAIN.get(route_domain) if route_domain is not None else None
    )
    if (
        row is None
        or (batch_id is not None and row["batch_id"] != batch_id)
        or (route_domain is not None and storage_domain != row["domain"])
    ):
        raise ValueError("机会不存在或不属于当前批次专题")
    if not row["source_issue_code"] or row["issue_code"] is None:
        raise ValueError("旧版专题机会缺少来源问题，请先重新运行专题分析")
    if (
        expected_issue_code is not None
        and row["source_issue_code"] != expected_issue_code
    ):
        raise ValueError("专题机会与问题编号不匹配")
    if row["domain"] != row["issue_ledger_type"]:
        raise ValueError("专题机会领域与来源问题不匹配")
    if row["is_archived"]:
        raise ValueError("批次已归档，不能修改专题核查结果")
    return row


def _save_opportunity_review_in_unit_of_work(
    unit_of_work: UnitOfWork,
    batch_id: int,
    route_domain: str,
    opportunity_code: str,
    status: IssueStatus,
    verified: float | None,
    realized: float | None,
    note: str,
) -> dict[str, Any]:
    opportunity = match_opportunity(
        unit_of_work,
        opportunity_code,
        batch_id=batch_id,
        route_domain=route_domain,
    )
    issue = unit_of_work.issues.get_with_batch(
        str(opportunity["source_issue_code"])
    )
    if issue is None:
        raise ValueError("issue not found")
    unit_of_work.issues.update_status(
        issue,
        status,
        source="analysis_review",
        event_note=f"保存专题核查：{opportunity_code}",
        correction_note=note,
        update_correction_note=True,
    )
    unit_of_work.reviews.upsert(opportunity, verified, realized, note)
    review = unit_of_work.reviews.load_payload(opportunity_code)
    if review is None:
        raise ValueError("机会不存在或不属于当前批次专题")
    return {
        "opportunity": opportunity,
        "review": {
            "opportunity_code": review["opportunity_code"],
            **review_payload_fields(review),
        },
    }


def preview_batch_opportunity_reviews(
    config: AppConfig,
    batch_id: int,
    route_domain: str,
    payload: dict[str, Any],
    *,
    database: Database | None = None,
) -> dict[str, Any]:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        return _batch_review_preview(
            unit_of_work,
            batch_id,
            route_domain,
            payload,
        )


def _batch_review_preview(
    unit_of_work: UnitOfWork,
    batch_id: int,
    route_domain: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    status = payload.get("status")
    if status not in ONLINE_REVIEW_STATUSES:
        raise ValueError("批量核查状态无效")
    note = str(payload.get("review_note") or "").strip()
    if not note:
        raise ValueError("批量核查说明不能为空")
    if len(note) > 500:
        raise ValueError("批量核查说明不能超过 500 字")
    raw_codes = payload.get("opportunity_codes")
    if not isinstance(raw_codes, list):
        raise ValueError("请选择需要批量处理的专题记录")
    codes = list(
        dict.fromkeys(
            str(code or "").strip() for code in raw_codes if str(code or "").strip()
        )
    )
    if not codes:
        raise ValueError("请选择需要批量处理的专题记录")
    if len(codes) > MAX_BATCH_REVIEW_ITEMS:
        raise ValueError(f"单次最多批量处理 {MAX_BATCH_REVIEW_ITEMS} 条记录")
    batch = unit_of_work.batches.get(batch_id)
    if batch is None:
        raise ValueError("批次不存在")
    if batch["is_archived"]:
        raise ValueError("批次已归档，不能批量修改专题核查结果")
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for code in codes:
        try:
            opportunity = match_opportunity(
                unit_of_work,
                code,
                batch_id=batch_id,
                route_domain=route_domain,
            )
        except ValueError as exc:
            blocked.append({"opportunity_code": code, "error": str(exc)})
            continue
        eligible.append(
            {
                "opportunity_code": code,
                "issue_code": opportunity["source_issue_code"],
                "current_status": opportunity["issue_status"],
            }
        )
    signature_payload = {
        "batch_id": batch_id,
        "route_domain": route_domain,
        "status": status,
        "review_note": note,
        "eligible": eligible,
        "blocked": blocked,
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "selected_count": len(codes),
        "eligible_count": len(eligible),
        "blocked_count": len(blocked),
        "target_status": status,
        "eligible": eligible,
        "blocked": blocked,
        "preview_signature": signature,
    }


def save_batch_opportunity_reviews(
    config: AppConfig,
    batch_id: int,
    route_domain: str,
    payload: dict[str, Any],
    *,
    database: Database | None = None,
) -> dict[str, Any]:
    if payload.get("confirmed") is not True:
        raise ValueError("请先预览影响并确认批量操作")
    supplied_signature = str(payload.get("preview_signature") or "")
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        preview = _batch_review_preview(
            unit_of_work,
            batch_id,
            route_domain,
            payload,
        )
        if not supplied_signature or supplied_signature != preview["preview_signature"]:
            raise ValueError("所选记录或状态已变化，请重新预览后确认")
        if not preview["eligible_count"]:
            raise ValueError("所选记录均无法批量处理")

        status = cast(IssueStatus, payload["status"])
        note = str(payload.get("review_note") or "").strip()
        succeeded: list[dict[str, Any]] = []
        failed = list(preview["blocked"])
        for item in preview["eligible"]:
            try:
                with unit_of_work.reviews.savepoint():
                    saved = _save_opportunity_review_in_unit_of_work(
                        unit_of_work,
                        batch_id,
                        route_domain,
                        item["opportunity_code"],
                        status,
                        None,
                        None,
                        note,
                    )
                succeeded.append(saved["review"])
            except (ValueError, PersistenceError) as exc:
                failed.append(
                    {"opportunity_code": item["opportunity_code"], "error": str(exc)}
                )

        if succeeded:
            batch = unit_of_work.batches.get(batch_id)
            assert batch is not None
            batch_status = batch["status"]
            if batch_status in {"distributed", "returning"}:
                transition_batch_in_unit_of_work(
                    unit_of_work,
                    batch_id,
                    "correction_return",
                )
        failure_excerpt = "；".join(
            f"{item['opportunity_code']}（{item['error']}）" for item in failed[:10]
        )
        message = (
            f"批量专题核查：目标状态 {status}，选择 {preview['selected_count']} 条，"
            f"成功 {len(succeeded)} 条，失败 {len(failed)} 条"
        )
        if failure_excerpt:
            message += f"；失败明细：{failure_excerpt}"
        unit_of_work.batches.add_operation(
            batch_id,
            "batch_analysis_review",
            message,
        )
        return {
            "selected_count": preview["selected_count"],
            "success_count": len(succeeded),
            "failed_count": len(failed),
            "succeeded": succeeded,
            "failed": failed,
        }
