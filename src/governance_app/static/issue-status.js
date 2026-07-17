export const ISSUE_STATUS_OPTIONS = [
  ["pending_export", "待导出"],
  ["pending_correction", "待整改"],
  ["returned", "已回传待确认"],
  ["needs_review", "待人工复核"],
  ["still_invalid", "仍需整改"],
  ["closed", "已确认闭环"],
  ["not_required", "无需整改"],
  ["resolved_by_reaudit", "复审已解决"],
];

const ISSUE_STATUS_LABELS = new Map(ISSUE_STATUS_OPTIONS);

export function issueStatusLabel(status, fallback = "待处理") {
  return ISSUE_STATUS_LABELS.get(status) || fallback;
}

export function issueStatusOptions(selected = "", includeAll = true) {
  return [
    ...(includeAll ? ['<option value="">全部状态</option>'] : []),
    ...ISSUE_STATUS_OPTIONS.map(
      ([value, label]) =>
        `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`,
    ),
  ].join("");
}
