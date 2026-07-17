from __future__ import annotations

ISSUE_STATUS_LABELS = {
    "pending_export": "待导出",
    "pending_correction": "待整改",
    "returned": "已回传待确认",
    "needs_review": "待人工复核",
    "still_invalid": "仍需整改",
    "closed": "已确认闭环",
    "not_required": "无需整改",
    "resolved_by_reaudit": "复审已解决",
}


def issue_status_label(status: str | None) -> str:
    return ISSUE_STATUS_LABELS.get(status or "", status or "未知")
