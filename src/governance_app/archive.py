from pathlib import Path

from openpyxl import Workbook

from governance_app.analytics import dashboard_summary
from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.workflow import city_progress


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
