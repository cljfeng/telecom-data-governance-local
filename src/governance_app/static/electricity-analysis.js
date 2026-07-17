import { fetchJson, postJson } from "/api.js?v=20260712-1";
import { batchReviewToolbar, bindBatchReview, bindReviewForms, reviewForm } from "/analysis-review.js?v=20260717-3";
import { issueStatusLabel } from "/issue-status.js?v=20260717-1";
import {
  bindSpecialistMetricFilters,
  initialSpecialistView,
  rememberSpecialistView,
  specialistFilterControls,
  specialistFilterQuery,
  specialistPagination,
  specialistSummary,
} from "/specialist-analysis-ui.js?v=20260718-1";

const PAGE_SIZE = 24;
let electricityOffset = 0;

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function confidenceLabel(value) {
  return { high: "高", medium: "中", low: "低" }[value] || value || "未知";
}

function optionRows(rows, field, label) {
  const values = [...new Set(rows.map((row) => row[field]).filter(Boolean))];
  return [`<option value="">全部${label}</option>`, ...values.map((value) => `<option value="${value}">${field === "confidence" ? confidenceLabel(value) : value}</option>`)].join("");
}

export async function renderElectricityAnalysis(ctx) {
  await ctx.refreshBatches().catch(() => []);
  const batch = ctx.currentBatch();
  if (!batch) {
    ctx.renderNoBatchPrompt("还没有可分析的批次。");
    return;
  }
  const initialView = initialSpecialistView(ctx, "electricity");
  ctx.mainContent.innerHTML = `
    <section class="card">
      ${ctx.shellHeader("电费压降分析", `${batch.batch_code || `#${batch.id}`} ${batch.name}`, ctx.renderBatchSelector())}
      <div class="button-row">
        <button id="run-electricity-analysis" class="primary-button" type="button" disabled aria-describedby="electricity-analysis-gate">生成分析</button>
        <button id="export-electricity-analysis" class="secondary-button" type="button" disabled aria-describedby="electricity-analysis-gate">导出 Excel</button>
      </div>
      <div id="electricity-analysis-result" class="result-box">正在检查当前批次是否可以生成分析。</div>
      <p id="electricity-analysis-gate" class="field-hint">正在检查前置条件。</p>
    </section>
    <section class="card metric-section">
      <div id="electricity-summary" class="metric-grid"></div>
    </section>
    <section class="card">
      <div id="electricity-queue-heading">${ctx.shellHeader("待处理清单", "电费机会")}</div>
      ${specialistFilterControls("electricity", initialView)}
      ${batchReviewToolbar("electricity")}
      <div id="electricity-opportunity-list" class="analysis-review-list"><div class="empty-state">正在加载</div></div>
      <div id="electricity-pagination" class="pagination-bar" aria-label="电费机会分页"></div>
    </section>
    <div class="dashboard-grid specialist-breakdowns">
      <section class="card">${ctx.shellHeader("异常分类", "分类")}<div id="electricity-type-breakdown" class="risk-summary"></div></section>
      <section class="card">${ctx.shellHeader("地市排行", "地市")}<div id="electricity-city-ranking" class="risk-summary"></div></section>
    </div>
  `;
  ctx.bindBatchSelector(() => renderElectricityAnalysis(ctx));
  document.querySelector("#run-electricity-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "生成中...", async () => {
      const result = document.querySelector("#electricity-analysis-result");
      result.className = "result-box result-pending";
      result.textContent = "正在生成电费压降机会...";
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/run`, {});
        result.className = "result-box result-success";
        result.textContent = `已生成 ${ctx.formatNumber(data.opportunity_count)} 条电费压降机会。`;
        await loadElectricityAnalysisData(ctx);
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#export-electricity-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "导出中...", async () => {
      const result = document.querySelector("#electricity-analysis-result");
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/export`, {});
        result.className = "result-box result-success";
        result.textContent = `已导出：${data.path}`;
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#apply-electricity-filters").addEventListener("click", () => {
    electricityOffset = 0;
    loadElectricityAnalysisData(ctx);
  });
  await loadElectricityAnalysisData(ctx);
}

async function loadElectricityAnalysisData(ctx) {
  rememberSpecialistView(ctx, "electricity", "electricity");
  const query = specialistFilterQuery("electricity", { limit: PAGE_SIZE, offset: electricityOffset });
  try {
    const [summary, list] = await Promise.all([
      fetchJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/summary`),
      fetchJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/opportunities${query.toString() ? `?${query}` : ""}`),
    ]);
    const visibleSummary = summary.analysis_stale
      ? { ...summary, opportunity_count: 0, abnormal_site_count: 0, recoverable_amount: 0, saving_opportunity_amount: 0, high_risk_count: 0, pending_count: 0, returned_count: 0, needs_review_count: 0, closed_count: 0, verified_recoverable_amount: 0, realized_saving_amount: 0, type_breakdown: [], city_rankings: [] }
      : summary;
    renderSummary(ctx, visibleSummary);
    renderBreakdown(ctx, visibleSummary);
    renderRows(ctx, summary.analysis_stale ? [] : list.opportunities || []);
    bindSpecialistMetricFilters("electricity", () => {
      electricityOffset = 0;
      return loadElectricityAnalysisData(ctx);
    });
    specialistPagination("electricity", list.total, list.limit, list.offset, (nextOffset) => {
      electricityOffset = nextOffset;
      loadElectricityAnalysisData(ctx).then(() => document.querySelector("#electricity-queue-heading")?.scrollIntoView({ block: "start" }));
    });
    updateElectricityActions(ctx, summary);
  } catch (error) {
    document.querySelector("#electricity-summary").innerHTML = [
      ctx.metricCard("电费总额", 0, "等待分析", "info"),
      ctx.metricCard("异常站址", 0, "等待分析", "warning"),
      ctx.metricCard("可追回金额", 0, "等待分析", "danger"),
      ctx.metricCard("压降机会金额", 0, "等待分析", "success"),
      ctx.metricCard("待处理", 0, "等待分析", "warning"),
      ctx.metricCard("待人工复核", 0, "等待分析", "review"),
      ctx.metricCard("已确认闭环", 0, "等待分析", "success"),
      ctx.metricCard("核实可追回", 0, "等待分析", "danger"),
      ctx.metricCard("实际落实", 0, "等待分析", "success"),
    ].join("");
    document.querySelector("#electricity-opportunity-list").innerHTML = `<div class="empty-state">${ctx.escapeHtml(error.message)}</div>`;
  }
}

function updateElectricityActions(ctx, summary) {
  const batch = ctx.currentBatch();
  const runButton = document.querySelector("#run-electricity-analysis");
  const exportButton = document.querySelector("#export-electricity-analysis");
  const hint = document.querySelector("#electricity-analysis-gate");
  const result = document.querySelector("#electricity-analysis-result");
  const allowedStatus = ["audited", "distributed", "returning"].includes(batch?.status);
  const canRun = Boolean(batch && !batch.is_archived && allowedStatus && Number(summary.ledger_row_count || 0) > 0);
  runButton.disabled = !canRun;
  exportButton.disabled = !canRun || !summary.analysis_generated;
  if (summary.analysis_stale) hint.textContent = "历史分析与当前台账不一致，已停止展示；请重新导入台账后生成分析。";
  else if (batch?.is_archived) hint.textContent = "当前批次已归档，不能重新生成或导出专题分析。";
  else if (!allowedStatus) hint.textContent = "请先完成台账导入和稽核，再生成电费压降分析。";
  else if (!Number(summary.ledger_row_count || 0)) hint.textContent = "当前批次没有电费台账，请先导入电费台账。";
  else if (!summary.analysis_generated) hint.textContent = "可以生成分析；生成完成后才能导出 Excel。";
  else hint.textContent = "分析已生成，可以重新生成或导出 Excel。";
  if (result.className === "result-box") {
    result.textContent = summary.analysis_generated ? "电费压降分析已生成。" : hint.textContent;
  }
  if (!summary.analysis_generated && !canRun) {
    document.querySelector("#electricity-opportunity-list").innerHTML = `<div class="empty-state">${ctx.escapeHtml(hint.textContent)}</div>`;
  }
}

function renderSummary(ctx, summary) {
  document.querySelector("#electricity-summary").className = "specialist-summary";
  document.querySelector("#electricity-summary").innerHTML = specialistSummary(
    [
      { label: "压降机会", value: ctx.formatNumber(summary.opportunity_count), note: "查看全部机会", tone: "info", view: "all" },
      { label: "待人工复核", value: ctx.formatNumber(summary.needs_review_count), note: "优先完成省级复核", tone: "review", view: "needs_review" },
      { label: "核实可追回", value: money(summary.verified_recoverable_amount), note: "查看已核实金额", tone: "danger", view: "verified" },
      { label: "实际落实", value: money(summary.realized_saving_amount), note: "查看已落实成果", tone: "success", view: "realized" },
    ],
    [
      { html: ctx.metricCard("电费总额", summary.total_electricity_amount, `电费记录 ${ctx.formatNumber(summary.ledger_row_count)}`, "info") },
      { html: ctx.metricCard("异常站址", summary.abnormal_site_count, `机会 ${ctx.formatNumber(summary.opportunity_count)} 条`, "warning") },
      { html: ctx.metricCard("测算可追回", summary.recoverable_amount, "相对确定问题", "danger") },
      { html: ctx.metricCard("压降机会金额", summary.saving_opportunity_amount, `高风险 ${ctx.formatNumber(summary.high_risk_count)} 条`, "success") },
      { html: ctx.metricCard("待处理", summary.pending_count, "等待整改或再次核验", "warning") },
      { html: ctx.metricCard("已回传待确认", summary.returned_count, "地市已提交整改结果", "review") },
      { html: ctx.metricCard("已确认闭环", summary.closed_count, "含无需整改与复审解决", "success") },
    ],
  );
}

function renderBreakdown(ctx, summary) {
  document.querySelector("#electricity-type-breakdown").innerHTML =
    (summary.type_breakdown || [])
      .map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.opportunity_type)}</strong><span>${ctx.formatNumber(row.opportunity_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 压降 ${money(row.saving_opportunity_amount)}</span></div>`)
      .join("") || '<div class="empty-state">暂无异常分类</div>';
  document.querySelector("#electricity-city-ranking").innerHTML =
    (summary.city_rankings || [])
      .map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.city)}</strong><span>${ctx.formatNumber(row.opportunity_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 压降 ${money(row.saving_opportunity_amount)}</span></div>`)
      .join("") || '<div class="empty-state">暂无地市排行</div>';
}

function renderRows(ctx, rows) {
  const typeFilter = document.querySelector("#electricity-type-filter");
  const confidenceFilter = document.querySelector("#electricity-confidence-filter");
  if (typeFilter && typeFilter.options.length <= 1) typeFilter.innerHTML = optionRows(rows, "opportunity_type", "类型");
  if (confidenceFilter && confidenceFilter.options.length <= 1) confidenceFilter.innerHTML = optionRows(rows, "confidence", "置信度");
  const list = document.querySelector("#electricity-opportunity-list");
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state">暂无电费压降机会。可以先点击“生成分析”或调整筛选条件。</div>';
    return;
  }
  list.innerHTML = rows
    .map(
      (row) => `
        <article class="analysis-review-card">
          <div class="analysis-review-context">
            <div class="analysis-review-card-head">
              <div>
                <span class="analysis-review-kicker">${ctx.escapeHtml(row.opportunity_type || "电费机会")}</span>
                <h3>${ctx.escapeHtml(row.telecom_site_name || row.telecom_site_code || "未命名站址")}</h3>
              </div>
              <span class="analysis-status analysis-status-${ctx.escapeHtml(row.issue_status || "legacy")}">${ctx.escapeHtml(issueStatusLabel(row.issue_status))}</span>
            </div>
            <dl class="analysis-review-meta">
              <div><dt>问题编号</dt><dd>${ctx.escapeHtml(row.issue_code || "未关联来源问题")}</dd></div>
              <div><dt>机会编号</dt><dd>${ctx.escapeHtml(row.opportunity_code || "")}</dd></div>
              <div><dt>地市 / 账期</dt><dd>${ctx.escapeHtml(row.city || "未填地市")} · ${ctx.escapeHtml(row.period || "未填账期")}</dd></div>
              <div><dt>置信度</dt><dd>${ctx.escapeHtml(confidenceLabel(row.confidence))}</dd></div>
            </dl>
            <div class="analysis-review-amounts">
              <div><span>当前金额</span><strong>${money(row.current_amount)}</strong></div>
              <div><span>测算可追回</span><strong>${money(row.recoverable_amount)}</strong></div>
              <div><span>测算压降机会</span><strong>${money(row.saving_opportunity_amount)}</strong></div>
            </div>
            <p class="analysis-review-suggestion"><strong>建议动作</strong>${ctx.escapeHtml(row.suggestion || "请结合原始账单进行核查。")}</p>
          </div>
          ${reviewForm(ctx, row)}
        </article>
      `,
    )
    .join("");
  bindReviewForms(ctx, "electricity-analysis", () => loadElectricityAnalysisData(ctx));
  bindBatchReview(ctx, "electricity-analysis", "electricity", () => loadElectricityAnalysisData(ctx));
}
