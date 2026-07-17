import { fetchJson, postJson } from "/api.js?v=20260712-1";
import { bindReviewForms, reviewForm, statusOptions } from "/analysis-review.js?v=20260712-1";
import { issueStatusLabel } from "/issue-status.js?v=20260717-1";

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function optionRows(rows, field, label) {
  const values = [...new Set(rows.map((row) => row[field]).filter(Boolean))];
  return [`<option value="">全部${label}</option>`, ...values.map((value) => `<option value="${value}">${value}</option>`)].join("");
}

export async function renderTowerRentAnalysis(ctx) {
  await ctx.refreshBatches().catch(() => []);
  const batch = ctx.currentBatch();
  if (!batch) {
    ctx.renderNoBatchPrompt("还没有可分析的批次。");
    return;
  }
  ctx.mainContent.innerHTML = `
    <section class="card">
      ${ctx.shellHeader("租费异常分析", `${batch.batch_code || `#${batch.id}`} ${batch.name}`, ctx.renderBatchSelector())}
      <div class="button-row">
        <button id="run-tower-rent-analysis" class="primary-button" type="button" disabled aria-describedby="tower-rent-analysis-gate">生成分析</button>
        <button id="export-tower-rent-analysis" class="secondary-button" type="button" disabled aria-describedby="tower-rent-analysis-gate">导出 Excel</button>
      </div>
      <div id="tower-rent-analysis-result" class="result-box">正在检查当前批次是否可以生成分析。</div>
      <p id="tower-rent-analysis-gate" class="field-hint">正在检查前置条件。</p>
    </section>
    <section class="card metric-section">
      <div id="tower-rent-summary" class="metric-grid"></div>
    </section>
    <div class="dashboard-grid">
      <section class="card">${ctx.shellHeader("异常分类", "分类")}<div id="tower-rent-type-breakdown" class="risk-summary"></div></section>
      <section class="card">${ctx.shellHeader("地市排行", "地市")}<div id="tower-rent-city-ranking" class="risk-summary"></div></section>
    </div>
    <section class="card">
      ${ctx.shellHeader("异常线索清单", "线索")}
      <div class="toolbar">
        <label class="compact-field"><span>异常类型</span><select id="tower-rent-type-filter"><option value="">全部类型</option></select></label>
        <label class="compact-field"><span>置信度</span><select id="tower-rent-confidence-filter"><option value="">全部置信度</option></select></label>
        <label class="compact-field"><span>闭环状态</span><select id="tower-rent-status-filter">${statusOptions()}</select></label>
        <button id="apply-tower-rent-filters" class="secondary-button" type="button">筛选</button>
      </div>
      <div id="tower-rent-clue-list" class="analysis-review-list"><div class="empty-state">正在加载</div></div>
    </section>
  `;
  ctx.bindBatchSelector(() => renderTowerRentAnalysis(ctx));
  document.querySelector("#run-tower-rent-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "生成中...", async () => {
      const result = document.querySelector("#tower-rent-analysis-result");
      result.className = "result-box result-pending";
      result.textContent = "正在生成租费异常线索...";
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/run`, {});
        result.className = "result-box result-success";
        result.textContent = `已生成 ${ctx.formatNumber(data.clue_count)} 条租费异常线索。`;
        await loadTowerRentAnalysisData(ctx);
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#export-tower-rent-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "导出中...", async () => {
      const result = document.querySelector("#tower-rent-analysis-result");
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/export`, {});
        result.className = "result-box result-success";
        result.textContent = `已导出：${data.path}`;
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#apply-tower-rent-filters").addEventListener("click", () => loadTowerRentAnalysisData(ctx));
  await loadTowerRentAnalysisData(ctx);
}

async function loadTowerRentAnalysisData(ctx) {
  const type = document.querySelector("#tower-rent-type-filter")?.value || "";
  const confidence = document.querySelector("#tower-rent-confidence-filter")?.value || "";
  const status = document.querySelector("#tower-rent-status-filter")?.value || "";
  const query = new URLSearchParams();
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  if (status) query.set("status", status);
  try {
    const [summary, list] = await Promise.all([
      fetchJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/summary`),
      fetchJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/opportunities${query.toString() ? `?${query}` : ""}`),
    ]);
    renderSummary(ctx, summary);
    renderBreakdown(ctx, summary);
    renderRows(ctx, list.opportunities || []);
    updateTowerRentActions(ctx, summary);
  } catch (error) {
    document.querySelector("#tower-rent-summary").innerHTML = [
      ctx.metricCard("租费总额", 0, "等待分析", "info"),
      ctx.metricCard("异常站址", 0, "等待分析", "warning"),
      ctx.metricCard("预计可追回金额", 0, "等待分析", "danger"),
      ctx.metricCard("优惠落实金额", 0, "等待分析", "success"),
      ctx.metricCard("待核查金额", 0, "等待分析", "review"),
      ctx.metricCard("待处理", 0, "等待分析", "warning"),
      ctx.metricCard("待人工复核", 0, "等待分析", "review"),
      ctx.metricCard("已确认闭环", 0, "等待分析", "success"),
      ctx.metricCard("核实可追回", 0, "等待分析", "danger"),
      ctx.metricCard("实际落实", 0, "等待分析", "success"),
    ].join("");
    document.querySelector("#tower-rent-clue-list").innerHTML = `<div class="empty-state">${ctx.escapeHtml(error.message)}</div>`;
  }
}

function updateTowerRentActions(ctx, summary) {
  const batch = ctx.currentBatch();
  const runButton = document.querySelector("#run-tower-rent-analysis");
  const exportButton = document.querySelector("#export-tower-rent-analysis");
  const hint = document.querySelector("#tower-rent-analysis-gate");
  const result = document.querySelector("#tower-rent-analysis-result");
  const allowedStatus = ["audited", "distributed", "returning"].includes(batch?.status);
  const canRun = Boolean(batch && !batch.is_archived && allowedStatus && Number(summary.ledger_row_count || 0) > 0);
  runButton.disabled = !canRun;
  exportButton.disabled = !canRun || !summary.analysis_generated;
  if (batch?.is_archived) hint.textContent = "当前批次已归档，不能重新生成或导出专题分析。";
  else if (!allowedStatus) hint.textContent = "请先完成台账导入和稽核，再生成租费异常分析。";
  else if (!Number(summary.ledger_row_count || 0)) hint.textContent = "当前批次没有铁塔租费台账，请先导入铁塔租费台账。";
  else if (!summary.analysis_generated) hint.textContent = "可以生成分析；生成完成后才能导出 Excel。";
  else hint.textContent = "分析已生成，可以重新生成或导出 Excel。";
  if (result.className === "result-box") {
    result.textContent = summary.analysis_generated ? "租费异常分析已生成。" : hint.textContent;
  }
  if (!summary.analysis_generated && !canRun) {
    document.querySelector("#tower-rent-clue-list").innerHTML = `<div class="empty-state">${ctx.escapeHtml(hint.textContent)}</div>`;
  }
}

function renderSummary(ctx, summary) {
  document.querySelector("#tower-rent-summary").innerHTML = [
    ctx.metricCard("租费总额", summary.total_rent_amount, `租费记录 ${ctx.formatNumber(summary.ledger_row_count)}`, "info"),
    ctx.metricCard("异常站址", summary.abnormal_site_count, `线索 ${ctx.formatNumber(summary.clue_count)} 条`, "warning"),
    ctx.metricCard("预计可追回金额", summary.recoverable_amount, "相对确定问题", "danger"),
    ctx.metricCard("优惠落实金额", summary.discount_realization_amount, "共享折扣等优惠线索", "success"),
    ctx.metricCard("待核查金额", summary.review_amount, `高风险 ${ctx.formatNumber(summary.high_risk_count)} 条`, "review"),
    ctx.metricCard("待处理", summary.pending_count, "等待整改或再次核验", "warning"),
    ctx.metricCard("待人工复核", summary.review_count, "已填写核查信息", "review"),
    ctx.metricCard("已确认闭环", summary.closed_count, "含无需整改与复审解决", "success"),
    ctx.metricCard("核实可追回", summary.verified_recoverable_amount, "以核查认定为准", "danger"),
    ctx.metricCard("实际落实", summary.realized_saving_amount, "已退款或已完成优化", "success"),
  ].join("");
}

function renderBreakdown(ctx, summary) {
  document.querySelector("#tower-rent-type-breakdown").innerHTML =
    (summary.type_breakdown || [])
      .map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.opportunity_type)}</strong><span>${ctx.formatNumber(row.clue_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 优惠 ${money(row.discount_realization_amount)} / 待核查 ${money(row.review_amount)}</span></div>`)
      .join("") || '<div class="empty-state">暂无异常分类</div>';
  document.querySelector("#tower-rent-city-ranking").innerHTML =
    (summary.city_rankings || [])
      .map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.city)}</strong><span>${ctx.formatNumber(row.clue_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 优惠 ${money(row.discount_realization_amount)} / 待核查 ${money(row.review_amount)}</span></div>`)
      .join("") || '<div class="empty-state">暂无地市排行</div>';
}

function renderRows(ctx, rows) {
  const typeFilter = document.querySelector("#tower-rent-type-filter");
  const confidenceFilter = document.querySelector("#tower-rent-confidence-filter");
  if (typeFilter && typeFilter.options.length <= 1) typeFilter.innerHTML = optionRows(rows, "opportunity_type", "类型");
  if (confidenceFilter && confidenceFilter.options.length <= 1) confidenceFilter.innerHTML = optionRows(rows, "confidence", "置信度");
  const list = document.querySelector("#tower-rent-clue-list");
  if (!rows.length) {
    list.innerHTML = '<div class="empty-state">暂无租费异常线索。可以先点击“生成分析”或调整筛选条件。</div>';
    return;
  }
  list.innerHTML = rows
    .map(
      (row) => `
        <article class="analysis-review-card">
          <div class="analysis-review-context">
            <div class="analysis-review-card-head">
              <div>
                <span class="analysis-review-kicker">${ctx.escapeHtml(row.opportunity_type || "租费线索")}</span>
                <h3>${ctx.escapeHtml(row.telecom_site_name || row.telecom_site_code || "未命名站址")}</h3>
              </div>
              <span class="analysis-status analysis-status-${ctx.escapeHtml(row.issue_status || "legacy")}">${ctx.escapeHtml(issueStatusLabel(row.issue_status))}</span>
            </div>
            <dl class="analysis-review-meta">
              <div><dt>问题编号</dt><dd>${ctx.escapeHtml(row.issue_code || "未关联来源问题")}</dd></div>
              <div><dt>线索编号</dt><dd>${ctx.escapeHtml(row.opportunity_code || "")}</dd></div>
              <div><dt>地市 / 账期</dt><dd>${ctx.escapeHtml(row.city || "未填地市")} · ${ctx.escapeHtml(row.period || "未填账期")}</dd></div>
              <div><dt>置信度</dt><dd>${ctx.escapeHtml(row.confidence || "-")}</dd></div>
            </dl>
            <div class="analysis-review-amounts">
              <div><span>当前金额</span><strong>${money(row.current_amount)}</strong></div>
              <div><span>预计可追回</span><strong>${money(row.recoverable_amount)}</strong></div>
              <div><span>优惠落实机会</span><strong>${money(row.discount_realization_amount)}</strong></div>
              <div><span>待核查范围</span><strong>${money(row.review_amount)}</strong></div>
            </div>
            <p class="analysis-review-suggestion"><strong>建议动作</strong>${ctx.escapeHtml(row.suggestion || "请结合合同与计费资料进行核查。")}</p>
          </div>
          ${reviewForm(ctx, row)}
        </article>
      `,
    )
    .join("");
  bindReviewForms(ctx, "tower-rent-analysis", () => loadTowerRentAnalysisData(ctx));
}
