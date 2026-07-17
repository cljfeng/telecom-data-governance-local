import { issueStatusOptions } from "/issue-status.js?v=20260717-1";

export function specialistFilterControls(prefix, initialView = "actionable") {
  return `
    <div class="toolbar specialist-filter-bar">
      <label class="compact-field"><span>异常类型</span><select id="${prefix}-type-filter"><option value="">全部类型</option></select></label>
      <label class="compact-field"><span>置信度</span><select id="${prefix}-confidence-filter"><option value="">全部置信度</option></select></label>
      <label class="compact-field"><span>处理视图</span><select id="${prefix}-view-filter">
        <option value="actionable"${initialView === "actionable" ? " selected" : ""}>待处理队列</option>
        <option value="all"${initialView === "all" ? " selected" : ""}>全部记录</option>
        <option value="needs_review"${initialView === "needs_review" ? " selected" : ""}>待人工复核</option>
        <option value="verified"${initialView === "verified" ? " selected" : ""}>已核实金额</option>
        <option value="realized"${initialView === "realized" ? " selected" : ""}>已实际落实</option>
      </select></label>
      <label class="compact-field"><span>问题状态</span><select id="${prefix}-status-filter">${issueStatusOptions()}</select></label>
      <button id="apply-${prefix}-filters" class="secondary-button" type="button">筛选</button>
    </div>
  `;
}

export function specialistFilterQuery(prefix, pagination = {}) {
  const query = new URLSearchParams();
  const type = document.querySelector(`#${prefix}-type-filter`)?.value || "";
  const confidence = document.querySelector(`#${prefix}-confidence-filter`)?.value || "";
  const view = document.querySelector(`#${prefix}-view-filter`)?.value || "actionable";
  const status = document.querySelector(`#${prefix}-status-filter`)?.value || "";
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  if (status) query.set("status", status);
  else if (view === "actionable") query.set("queue", "actionable");
  else if (view === "needs_review") query.set("status", "needs_review");
  else if (view === "verified") query.set("review", "verified");
  else if (view === "realized") query.set("review", "realized");
  if (pagination.limit) query.set("limit", String(pagination.limit));
  if (pagination.offset) query.set("offset", String(pagination.offset));
  return query;
}

export function specialistPagination(prefix, total, limit, offset, onPage) {
  const host = document.querySelector(`#${prefix}-pagination`);
  if (!host) return;
  const safeLimit = Math.max(Number(limit || 24), 1);
  const safeTotal = Math.max(Number(total || 0), 0);
  if (!safeTotal) {
    host.innerHTML = "";
    return;
  }
  const currentPage = Math.floor(Number(offset || 0) / safeLimit) + 1;
  const totalPages = Math.max(Math.ceil(safeTotal / safeLimit), 1);
  host.innerHTML = `
    <span>共 ${safeTotal.toLocaleString("zh-CN")} 条 · 第 ${currentPage}/${totalPages} 页</span>
    <div class="button-row">
      <button class="secondary-button" type="button" data-page="prev" ${currentPage <= 1 ? "disabled" : ""}>上一页</button>
      <button class="secondary-button" type="button" data-page="next" ${currentPage >= totalPages ? "disabled" : ""}>下一页</button>
    </div>
  `;
  host.querySelector('[data-page="prev"]')?.addEventListener("click", () => onPage(Math.max(Number(offset || 0) - safeLimit, 0)));
  host.querySelector('[data-page="next"]')?.addEventListener("click", () => onPage(Number(offset || 0) + safeLimit));
}

export function specialistSummary(primary, secondary) {
  const cards = primary
    .map(
      (item) => `
        <button class="metric-card metric-${item.tone || "neutral"} specialist-metric-button" type="button" data-specialist-view="${item.view}">
          <span class="metric-label">${item.label}</span>
          <strong class="metric-value">${item.value}</strong>
          <span class="metric-note">${item.note}</span>
          <span class="metric-spark" aria-hidden="true"></span>
        </button>
      `,
    )
    .join("");
  const details = secondary.map((item) => item.html).join("");
  return `
    <div class="metric-grid specialist-primary-metrics">${cards}</div>
    <details class="specialist-more-metrics">
      <summary>更多指标 <span>${secondary.length} 项</span></summary>
      <div class="metric-grid">${details}</div>
    </details>
  `;
}

export function specialistPrerequisite(ctx, { prefix, title, ledgerName, summary }) {
  const batch = ctx.currentBatch();
  const noLedger = Number(summary.ledger_row_count || 0) <= 0;
  const stale = Boolean(summary.analysis_stale);
  const archived = Boolean(batch?.is_archived);
  const allowedStatus = ["audited", "distributed", "returning"].includes(batch?.status);
  let eyebrow = "前置条件";
  let heading = `准备${title}`;
  let description = `完成前置步骤后，系统会生成${title}的机会清单、金额测算和地市排行。`;
  let actionLabel = "执行稽核";
  let action = "audit";
  if (stale) {
    eyebrow = "结果已失效";
    heading = "当前台账已变化，需要重新生成分析";
    description = "历史结果已安全隔离，不会继续计入当前待办和成果。请确认台账后重新生成。";
    actionLabel = allowedStatus ? "重新生成分析" : "检查台账与稽核";
    action = allowedStatus ? "run" : "audit";
  } else if (noLedger) {
    heading = `先导入${ledgerName}`;
    description = `当前批次没有可用于${title}的${ledgerName}。导入前会先完成模板预检。`;
    actionLabel = "去导入台账";
    action = "import";
  } else if (allowedStatus) {
    eyebrow = "可以开始";
    heading = `前置条件已完成，生成${title}`;
    description = "生成后可按异常类型、置信度和处理状态筛选，并逐条完成复核。";
    actionLabel = "生成分析";
    action = "run";
  } else if (archived) {
    eyebrow = "批次已归档";
    heading = "归档批次不能重新生成分析";
    description = "可以切换到其他进行中的批次，或从分析报表查看已沉淀成果。";
    actionLabel = "";
  }
  const summaryHost = document.querySelector(`#${prefix}-summary`);
  const queueCard = document.querySelector(`#${prefix}-queue-card`);
  const breakdowns = document.querySelector(`#${prefix}-breakdowns`);
  const runButton = document.querySelector(`#run-${prefix}-analysis`);
  const exportButton = document.querySelector(`#export-${prefix}-analysis`);
  const resultBox = document.querySelector(`#${prefix}-analysis-result`);
  const gateHint = document.querySelector(`#${prefix}-analysis-gate`);
  if (summaryHost) {
    summaryHost.className = "specialist-prerequisite";
    summaryHost.innerHTML = `<div class="rich-empty rich-empty-horizontal">
      <span class="rich-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5"></path><path d="M4 17v3h16v-3"></path></svg></span>
      <div><p class="eyebrow">${eyebrow}</p><h3>${heading}</h3><p>${description}</p></div>
      ${actionLabel ? `<button class="primary-button" type="button" data-prerequisite-action="${action}">${actionLabel}</button>` : ""}
    </div>`;
  }
  if (queueCard) queueCard.hidden = true;
  if (breakdowns) breakdowns.hidden = true;
  if (runButton) runButton.hidden = true;
  if (exportButton) exportButton.hidden = true;
  if (resultBox) resultBox.hidden = true;
  if (gateHint) gateHint.hidden = true;
  document.querySelector("[data-prerequisite-action]")?.addEventListener("click", () => {
    if (action === "run") document.querySelector(`#run-${prefix}-analysis`)?.click();
    else ctx.activateView(action);
  });
}

export function showSpecialistResults(prefix) {
  const queueCard = document.querySelector(`#${prefix}-queue-card`);
  const breakdowns = document.querySelector(`#${prefix}-breakdowns`);
  const runButton = document.querySelector(`#run-${prefix}-analysis`);
  const exportButton = document.querySelector(`#export-${prefix}-analysis`);
  const resultBox = document.querySelector(`#${prefix}-analysis-result`);
  const gateHint = document.querySelector(`#${prefix}-analysis-gate`);
  if (queueCard) queueCard.hidden = false;
  if (breakdowns) breakdowns.hidden = false;
  if (runButton) runButton.hidden = false;
  if (exportButton) exportButton.hidden = false;
  if (resultBox) resultBox.hidden = false;
  if (gateHint) gateHint.hidden = false;
}

export function bindSpecialistMetricFilters(prefix, reload) {
  document.querySelectorAll("[data-specialist-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      const view = document.querySelector(`#${prefix}-view-filter`);
      const status = document.querySelector(`#${prefix}-status-filter`);
      if (!view || !status) return;
      view.value = button.dataset.specialistView;
      status.value = "";
      await reload();
      document.querySelector(`#${prefix}-queue-heading`)?.scrollIntoView({ block: "start" });
    });
  });
}

export function initialSpecialistView(ctx, key) {
  ctx.state.specialistViews ||= {};
  const stored = window.sessionStorage.getItem(`specialist-view:${key}`);
  return ctx.state.specialistViews[key] || stored || "actionable";
}

export function rememberSpecialistView(ctx, key, prefix) {
  ctx.state.specialistViews ||= {};
  const view = document.querySelector(`#${prefix}-view-filter`)?.value || "actionable";
  ctx.state.specialistViews[key] = view;
  window.sessionStorage.setItem(`specialist-view:${key}`, view);
}
