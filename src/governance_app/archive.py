from pathlib import Path

from openpyxl import Workbook

from governance_app.analytics import dashboard_summary
from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.workflow import city_progress

CLOSED_STATUSES = {"closed", "not_required"}


def archive_precheck(config: AppConfig, batch_id: int) -> dict:
    with connect(config) as conn:
        batch = conn.execute("select status, is_archived from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("batch not found")
        status_rows = conn.execute(
            """
            select status, count(*) as count
              from issues
             where batch_id = ?
             group by status
            """,
            (batch_id,),
        ).fetchall()
        status_counts = {row["status"]: row["count"] for row in status_rows}
        open_issue_count = sum(count for status, count in status_counts.items() if status not in CLOSED_STATUSES)
    blockers = []
    if batch["is_archived"]:
        blockers.append({"type": "archived", "message": "批次已归档，不能重复归档"})
    if batch["status"] != "returning":
        blockers.append({"type": "workflow_status", "message": "批次需要完成导出和回传后再归档"})
    if open_issue_count:
        blockers.append({"type": "open_issues", "message": f"仍有 {open_issue_count} 条问题未闭环"})
    return {
        "ready": not blockers,
        "batch_status": batch["status"],
        "is_archived": bool(batch["is_archived"]),
        "open_issue_count": open_issue_count,
        "status_counts": status_counts,
        "blockers": blockers,
    }


def archive_batch(config: AppConfig, batch_id: int) -> Path:
    with connect(config) as conn:
        batch = conn.execute("select status, is_archived from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("batch not found")
        if batch["is_archived"]:
            raise ValueError("batch is archived")
        if batch["status"] != "returning":
            raise ValueError("batch must be ready for archive")

    archive_dir = config.export_dir / f"archive_batch_{batch_id}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"批次{batch_id}_专项治理归档汇总.xlsx"

    summary = dashboard_summary(config, batch_id)
    progress = city_progress(config, batch_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "归档总览"
    ws.append(["指标", "值"])
    ws.append(["批次号", batch_id])
    ws.append(["台账记录数", sum(int(value or 0) for value in summary["ledger_counts"].values())])
    ws.append(["问题总数", sum(int(row["count"] or 0) for row in summary["issues_by_city"])])
    ws.append(["涉及地市", len(summary["issues_by_city"])])
    ws.append(["闭环率", summary["closure_rate"]])
    ws.append(["未闭环问题数", summary["open_issue_count"]])
    ws.append(["待复核问题数", summary["status_counts"].get("needs_review", 0)])
    ws.append(["仍异常问题数", summary["status_counts"].get("still_invalid", 0)])
    ws.append(["无需整改问题数", summary["status_counts"].get("not_required", 0)])

    ws = wb.create_sheet("规则命中排行")
    ws.append(["规则编号", "规则名称", "命中数"])
    for row in summary["issues_by_rule"]:
        ws.append([row["rule_id"], row["rule_name"], row["count"]])

    ws = wb.create_sheet("风险等级分布")
    ws.append(["风险等级", "问题数量"])
    with connect(config) as conn:
        for row in conn.execute(
            """
            select severity, count(*) as count
              from issues
             where batch_id = ?
             group by severity
             order by count desc, severity
            """,
            (batch_id,),
        ):
            ws.append([row["severity"], row["count"]])

    ws = wb.create_sheet("地市整改进度")
    ws.append(["地市", "问题总数", "待整改", "已回传", "待人工复核", "仍异常", "已关闭", "无需整改", "完成率"])
    for row in progress:
        ws.append(
            [
                row["city"],
                row["total_count"],
                row["pending_count"],
                row["returned_count"],
                row["review_count"],
                row["still_invalid_count"],
                row["closed_count"],
                row["not_required_count"],
                row["completion_rate"],
            ]
        )

    ws = wb.create_sheet("问题清单")
    ws.append(["问题编号", "地市", "区县", "站址编码", "站址名称", "台账类型", "规则编号", "规则名称", "风险", "状态", "问题说明", "整改说明"])
    with connect(config) as conn:
        for issue in conn.execute(
            """
            select issue_code, coalesce(city, '未填地市') as city, district, telecom_site_code,
                   telecom_site_name, ledger_type, rule_id, severity, status, message, correction_note
              from issues
             where batch_id = ?
             order by city, issue_code
            """,
            (batch_id,),
        ):
            ws.append(
                [
                    issue["issue_code"],
                    issue["city"],
                    issue["district"],
                    issue["telecom_site_code"],
                    issue["telecom_site_name"],
                    issue["ledger_type"],
                    issue["rule_id"],
                    rule_metadata(issue["rule_id"]).name,
                    issue["severity"],
                    issue["status"],
                    issue["message"],
                    issue["correction_note"],
                ]
            )

        ws = wb.create_sheet("操作日志")
        ws.append(["操作", "说明", "时间"])
        for log in conn.execute(
            """
            select operation, message, created_at
              from operation_logs
             where batch_id = ?
             order by id
            """,
            (batch_id,),
        ):
            ws.append([log["operation"], log["message"], log["created_at"]])

        ws = wb.create_sheet("未闭环问题")
        ws.append(["问题编号", "地市", "站址编码", "台账类型", "规则名称", "风险", "状态", "问题说明"])
        for issue in conn.execute(
            """
            select issue_code, coalesce(city, '未填地市') as city, telecom_site_code,
                   ledger_type, rule_id, severity, status, message
              from issues
             where batch_id = ?
               and status not in ('closed', 'not_required')
             order by city, issue_code
            """,
            (batch_id,),
        ):
            ws.append(
                [
                    issue["issue_code"],
                    issue["city"],
                    issue["telecom_site_code"],
                    issue["ledger_type"],
                    rule_metadata(issue["rule_id"]).name,
                    issue["severity"],
                    issue["status"],
                    issue["message"],
                ]
            )

    wb.save(path)
    with connect(config) as conn:
        conn.execute(
            "update import_batches set status = 'archived', is_archived = 1, archived_at = current_timestamp where id = ?",
            (batch_id,),
        )
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "archive", f"生成归档汇总：{path.name}"),
        )
    return path
