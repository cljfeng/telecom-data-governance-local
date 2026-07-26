from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook

from governance_app.analytics import dashboard_summary
from governance_app.audit_rules import rule_metadata
from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.exporter import excel_safe
from governance_app.file_storage_runtime import file_storage_for
from governance_app.issue_status import issue_status_label
from governance_app.ports.database import Database, UnitOfWork
from governance_app.ports.file_storage import FileStorage
from governance_app.rule_settings import load_rule_settings
from governance_app.version import version_payload
from governance_app.workflow import city_progress, transition_batch_in_unit_of_work

CLOSED_STATUSES = {"closed", "not_required", "resolved_by_reaudit"}


@dataclass(frozen=True)
class ArchiveEligibility:
    batch_status: str
    is_archived: bool
    status_counts: dict[str, int]
    open_issue_count: int
    audited_reviewed_closure: bool
    blockers: list[dict[str, str]]


def _archive_eligibility(
    unit_of_work: UnitOfWork,
    batch_id: int,
) -> ArchiveEligibility:
    data = unit_of_work.archives.eligibility(batch_id)
    status_counts = dict(data["status_counts"])
    open_issue_count = sum(
        count
        for status, count in status_counts.items()
        if status not in CLOSED_STATUSES
    )
    audited_reviewed_closure = data["status"] == "audited" and bool(
        data["audited_reviewed_closure"]
    )
    blockers = []
    if data["is_archived"]:
        blockers.append({"type": "archived", "message": "批次已归档，不能重复归档"})
    if data["status"] != "returning" and not audited_reviewed_closure:
        blockers.append(
            {"type": "workflow_status", "message": "批次需要完成导出和回传后再归档"}
        )
    if open_issue_count:
        blockers.append(
            {"type": "open_issues", "message": f"仍有 {open_issue_count} 条问题未闭环"}
        )
    return ArchiveEligibility(
        batch_status=str(data["status"]),
        is_archived=bool(data["is_archived"]),
        status_counts=status_counts,
        open_issue_count=open_issue_count,
        audited_reviewed_closure=audited_reviewed_closure,
        blockers=blockers,
    )


def archive_precheck(
    config: AppConfig,
    batch_id: int,
    *,
    database: Database | None = None,
) -> dict:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        eligibility = _archive_eligibility(unit_of_work, batch_id)
        data = unit_of_work.archives.eligibility(batch_id)
        high_risk_open = int(data["high_risk_open"])
        review_count = eligibility.status_counts.get("needs_review", 0)
    risk_items = []
    if high_risk_open:
        risk_items.append({"type": "high_risk_open", "message": f"仍有 {high_risk_open} 条高风险问题未闭环"})
    if review_count:
        risk_items.append({"type": "needs_review", "message": f"仍有 {review_count} 条问题等待省公司复核"})
    return {
        "ready": not eligibility.blockers,
        "batch_status": eligibility.batch_status,
        "is_archived": eligibility.is_archived,
        "open_issue_count": eligibility.open_issue_count,
        "status_counts": eligibility.status_counts,
        "risk_items": risk_items,
        "blockers": eligibility.blockers,
    }


def archive_batch(
    config: AppConfig,
    batch_id: int,
    *,
    database: Database | None = None,
    storage: FileStorage | None = None,
) -> Path:
    selected_database = database or database_for(config)
    selected_storage = storage or file_storage_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        eligibility = _archive_eligibility(unit_of_work, batch_id)
        if eligibility.is_archived:
            raise ValueError("batch is archived")
        if eligibility.blockers:
            raise ValueError("batch must be ready for archive")
        severity_counts = unit_of_work.archives.severity_counts(batch_id)
        issues = unit_of_work.archives.issue_snapshot(batch_id)
        specialist_reviews = unit_of_work.archives.specialist_reviews(batch_id)
        operation_logs = unit_of_work.archives.operation_logs(batch_id)
        rule_counts = unit_of_work.archives.rule_counts(batch_id)
        open_issues = unit_of_work.archives.issue_snapshot(
            batch_id,
            open_only=True,
        )

    path = selected_storage.prepare_export(
        f"archive_batch_{batch_id}/批次{batch_id}_专项治理归档汇总.xlsx"
    )

    summary = dashboard_summary(
        config,
        batch_id,
        database=selected_database,
    )
    progress = city_progress(config, batch_id, database=selected_database)

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
    ws.append(["规则分类", "规则编号", "规则名称", "命中数"])
    for row in summary["issues_by_rule"]:
        metadata = rule_metadata(row["rule_id"])
        ws.append([_rule_category_label(metadata.category), row["rule_id"], row["rule_name"], row["count"]])

    ws = wb.create_sheet("风险等级分布")
    ws.append(["风险等级", "问题数量"])
    for row in severity_counts:
        ws.append([_severity_label(row["severity"]), row["count"]])

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
    ws.append(["问题编号", "地市", "区县", "站址编码", "站址名称", "台账类型", "规则分类", "规则编号", "规则名称", "风险", "状态", "问题说明", "整改说明"])
    for issue in issues:
            metadata = rule_metadata(issue["rule_id"])
            ws.append(
                [
                    issue["issue_code"],
                    issue["city"],
                    issue["district"],
                    issue["telecom_site_code"],
                    issue["telecom_site_name"],
                    _ledger_label(issue["ledger_type"]),
                    _rule_category_label(metadata.category),
                    issue["rule_id"],
                    metadata.name,
                    _severity_label(issue["severity"]),
                    _status_label(issue["status"]),
                    issue["message"],
                    issue["correction_note"],
                ]
            )

    ws = wb.create_sheet("专题核查成果")
    specialist_headers = [
            "批次",
            "专题领域",
            "机会编号",
            "机会类型",
            "来源问题编号",
            "最终问题状态",
            "地市",
            "站址编码",
            "站址名称",
            "测算可追回金额",
            "测算压降/优惠金额",
            "核实可追回金额",
            "实际落实金额",
            "核查说明",
            "更新时间",
    ]
    ws.append([excel_safe(value) for value in specialist_headers])
    for review in specialist_reviews:
            ws.append(
                [
                    review["batch_id"],
                    excel_safe(
                        {"electricity": "电费压降", "tower_rent": "铁塔租费"}.get(
                            review["domain"], review["domain"]
                        )
                    ),
                    excel_safe(review["opportunity_code"]),
                    excel_safe(review["opportunity_type"]),
                    excel_safe(review["source_issue_code"]),
                    excel_safe(_status_label(review["issue_status"])),
                    excel_safe(review["city"]),
                    excel_safe(review["telecom_site_code"]),
                    excel_safe(review["telecom_site_name"]),
                    review["estimated_recoverable_amount"],
                    review["estimated_saving_amount"],
                    review["verified_recoverable_amount"],
                    review["realized_saving_amount"],
                    excel_safe(review["review_note"]),
                    excel_safe(review["updated_at"]),
                ]
            )

    ws = wb.create_sheet("操作日志")
    ws.append(["操作", "说明", "时间"])
    for log in operation_logs:
        ws.append([log["operation"], log["message"], log["created_at"]])

    ws = wb.create_sheet("版本与规则快照")
    ws.append(["项目", "值"])
    for key, value in version_payload().items():
        ws.append([key, value])
    ws.append([])
    ws.append(["规则编号", "规则名称", "规则分类", "风险等级", "是否启用", "阈值配置"])
    settings = load_rule_settings(config, database=selected_database)
    for row in rule_counts:
            metadata = rule_metadata(row["rule_id"])
            setting = settings.get(row["rule_id"])
            ws.append(
                [
                    row["rule_id"],
                    metadata.name,
                    _rule_category_label(metadata.category),
                    _severity_label(row["severity"]),
                    "是" if setting is None or setting.enabled else "否",
                    "" if setting is None else str(setting.config),
                ]
            )

    ws = wb.create_sheet("未闭环问题")
    ws.append(["问题编号", "地市", "站址编码", "台账类型", "规则分类", "规则名称", "风险", "状态", "问题说明"])
    for issue in open_issues:
            metadata = rule_metadata(issue["rule_id"])
            ws.append(
                [
                    issue["issue_code"],
                    issue["city"],
                    issue["telecom_site_code"],
                    _ledger_label(issue["ledger_type"]),
                    _rule_category_label(metadata.category),
                    metadata.name,
                    _severity_label(issue["severity"]),
                    _status_label(issue["status"]),
                    issue["message"],
                ]
            )

    ws = wb.create_sheet("专项复盘")
    ws.append(["复盘项", "值", "建议"])
    ws.append(["闭环率", summary["closure_rate"], "低于100%时不得正式归档"])
    ws.append(["未闭环问题数", summary["open_issue_count"], "优先处理高风险和仍异常问题"])
    ws.append(["待复核问题数", summary["status_counts"].get("needs_review", 0), "复核通过后更新为已关闭或无需整改"])
    ws.append(["仍异常问题数", summary["status_counts"].get("still_invalid", 0), "退回地市继续整改"])
    ws.append([])
    ws.append(["规则编号", "规则名称", "可信度", "命中数", "未闭环", "无需整改", "仍异常", "闭环率"])
    for row in summary.get("rule_effectiveness", []):
        ws.append(
                [
                    row["rule_id"],
                    row["rule_name"],
                    row.get("confidence_label", ""),
                    row["total_count"],
                    row["open_count"],
                    row["not_required_count"],
                    row["still_invalid_count"],
                    row["closure_rate"],
                ]
        )

    wb.save(path)
    with selected_database.unit_of_work() as unit_of_work:
        latest = _archive_eligibility(unit_of_work, batch_id)
        if latest.blockers and not latest.audited_reviewed_closure:
            raise ValueError("batch must be ready for archive")
        if eligibility.audited_reviewed_closure:
            unit_of_work.batches.update_status(batch_id, "returning")
        transition_batch_in_unit_of_work(unit_of_work, batch_id, "archive")
        unit_of_work.batches.add_operation(
            batch_id,
            "archive",
            f"生成归档汇总：{path.name}",
        )
    return path


def export_notice_report(
    config: AppConfig,
    batch_id: int,
    *,
    database: Database | None = None,
    storage: FileStorage | None = None,
) -> Path:
    selected_database = database or database_for(config)
    selected_storage = storage or file_storage_for(config)
    summary = dashboard_summary(
        config,
        batch_id,
        database=selected_database,
    )
    progress = city_progress(config, batch_id, database=selected_database)
    with selected_database.unit_of_work() as unit_of_work:
        batch = unit_of_work.batches.get(batch_id)
        if batch is None:
            raise ValueError("batch not found")
        batch_code = batch["batch_code"] or f"批次{batch_id}"
        issues = unit_of_work.archives.issue_snapshot(batch_id)
    path = selected_storage.prepare_export(
        f"稽核问题通报_{batch_code}.xlsx"
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "通报总览"
    total_issues = sum(int(row["count"] or 0) for row in summary["issues_by_city"])
    ws.append(["指标", "值"])
    ws.append(["批次编码", batch_code])
    ws.append(["问题总数", total_issues])
    ws.append(["涉及地市", len(summary["issues_by_city"])])
    ws.append(["未闭环问题", summary["open_issue_count"]])
    ws.append(["闭环率", summary["closure_rate"]])

    ws = wb.create_sheet("地市问题统计")
    ws.append(["地市", "问题总数", "待整改", "已回传", "待复核", "仍异常", "已关闭", "无需整改", "完成率"])
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

    ws = wb.create_sheet("分类统计")
    ws.append(["分类维度", "分类项", "规则编号", "规则名称", "风险等级", "问题数量"])
    for row in summary["issues_by_ledger_type"]:
        ws.append(["台账类型", row.get("ledger_label") or _ledger_label(row["ledger_type"]), "", "", "", row["count"]])
    for row in summary["issues_by_severity"]:
        label = row.get("severity_label") or _severity_label(row["severity"])
        ws.append(["风险等级", label, "", "", label, row["count"]])
    for row in summary["issue_categories"]:
        ws.append([
            "规则分类",
            row.get("category_label") or _rule_category_label(rule_metadata(row["rule_id"]).category),
            row["rule_id"],
            row["rule_name"],
            row.get("severity_label") or _severity_label(row["severity"]),
            row["count"],
        ])

    ws = wb.create_sheet("问题明细")
    ws.append(["问题编号", "地市", "区县", "站址编码", "站址名称", "台账类型", "规则分类", "规则编号", "规则名称", "风险", "状态", "问题说明", "建议整改方向"])
    for issue in issues:
        metadata = rule_metadata(issue["rule_id"])
        ws.append(
                [
                    issue["issue_code"],
                    issue["city"],
                    issue["district"],
                    issue["telecom_site_code"],
                    issue["telecom_site_name"],
                    _ledger_label(issue["ledger_type"]),
                    _rule_category_label(metadata.category),
                    issue["rule_id"],
                    metadata.name,
                    _severity_label(issue["severity"]),
                    _status_label(issue["status"]),
                    issue["message"],
                    issue["suggestion"],
                ]
        )

    wb.save(path)
    with selected_database.unit_of_work() as unit_of_work:
        unit_of_work.batches.add_operation(
            batch_id,
            "notice_report",
            f"导出稽核问题通报：{path.name}",
        )
    return path


def _ledger_label(value: str | None) -> str:
    return {
        "site": "站址",
        "tower_rent": "铁塔租费",
        "electricity": "电费",
        "generator": "发电费",
        "all": "跨台账",
    }.get(str(value or ""), str(value or "未知"))


def _severity_label(value: str | None) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(str(value or ""), str(value or "未知"))


def _rule_category_label(value: str | None) -> str:
    return {"data_quality": "基础数据质量", "problem_audit": "问题稽核"}.get(str(value or ""), str(value or "未知"))


def _status_label(value: str | None) -> str:
    return issue_status_label(value)
