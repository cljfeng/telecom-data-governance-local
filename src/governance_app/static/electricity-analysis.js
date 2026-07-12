import { fetchJson, postJson } from "/api.js?v=20260712-1";
import { bindReviewForms, reviewForm, statusOptions } from "/analysis-review.js?v=20260712-1";

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function optionRows(rows, field, label) {
  const values = [...new Set(rows.map((row) => row[field]).filter(Boolean))];
  return [`<option value="">全部${label}</option>`, ...values.map((value) => `<option value="${value}">${value}</option>`)].join("");
}

function statusLabel(status) {
  return {
    pending_export: "待导出",
    pending_correction: "待整改",
    returned: "待复核",
    needs_review: "待复核",
    still_invalid: "仍不合规",
    closed: "已闭环",
    not_required: "无需整改",
    resolved_by_reaudit: "复审已解决",
  }[status] || "待处理";
}

export async function renderElectricityAnalysis(ctx) {
  await ctx.refreshBatches().catch(() => []);
  const batch = ctx.currentBatch();
  if (!batch) {
    ctx.renderNoBatchPrompt("还没有可分析的批次。");
    return;
  }
  ctx.mainContent.innerHTML = `
    <section class="card">
      ${ctx.shellHeader("电费压降分析", `${batch.batch_code || `#${batch.id}`} ${batch.name}`, ctx.renderBatchSelector())}
      <div class="button-row">
        <button id="run-electricity-analysis" class="primary-button" type="button">生成分析</button>
        <button id="export-electricity-analysis" class="secondary-button" type="button">导出 Excel</button>
      </div>
      <div id="electricity-analysis-result" class="result-box">可先生成分析，再查看压降机会清单。</div>
    </section>
    <section class="card metric-section">
      <div id="electricity-summary" class="metric-grid"></div>
    </section>
    <div class="dashboard-grid">
      <section class="card">
        ${ctx.shellHeader("异常分类", "分类")}
        <div id="electricity-type-breakdown" class="risk-summary"></div>
      </section>
      <section class="card">
        ${ctx.shellHeader("地市排行", "地市")}
        <div id="electricity-city-ranking" class="risk-summary"></div>
      </section>
    </div>
    <section class="card">
      ${ctx.shellHeader("压降机会清单", "机会")}
      <div class="toolbar">
        <label class="compact-field"><span>异常类型</span><select id="electricity-type-filter"><option value="">全部类型</option></select></label>
        <label class="compact-field"><span>置信度</span><select id="electricity-confidence-filter"><option value="">全部置信度</option></select></label>
        <label class="compact-field"><span>闭环状态</span><select id="electricity-status-filter">${statusOptions()}</select></label>
        <button id="apply-electricity-filters" class="secondary-button" type="button">筛选</button>
      </div>
      <div id="electricity-opportunity-list" class="analysis-review-list"><div class="empty-state">正在加载</div></div>
    </section>
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
  document.querySelector("#apply-electricity-filters").addEventListener("click", () => loadElectricityAnalysisData(ctx));
  await loadElectricityAnalysisData(ctx);
}

async function loadElectricityAnalysisData(ctx) {
  const type = document.querySelector("#electricity-type-filter")?.value || "";
  const confidence = document.querySelector("#electricity-confidence-filter")?.value || "";
  const status = document.querySelector("#electricity-status-filter")?.value || "";
  const query = new URLSearchParams();
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  if (status) query.set("status", status);
  try {
    const [summary, list] = await Promise.all([
      fetchJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/summary`),
      fetchJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/opportunities${query.toString() ? `?${query}` : ""}`),
    ]);
    renderSummary(ctx, summary);
    renderBreakdown(ctx, summary);
    renderRows(ctx, list.opportunities || []);
  } catch (error) {
    document.querySelector("#electricity-summary").innerHTML = [
      ctx.metricCard("电费总额", 0, "等待分析", "info"),
      ctx.metricCard("异常站址", 0, "等待分析", "warning"),
      ctx.metricCard("可追回金额", 0, "等待分析", "danger"),
      ctx.metricCard("压降机会金额", 0, "等待分析", "success"),
      ctx.metricCard("待处理", 0, "等待分析", "warning"),
      ctx.metricCard("待复核", 0, "等待分析", "review"),
      ctx.metricCard("已闭环", 0, "等待分析", "success"),
      ctx.metricCard("核实可追回", 0, "等待分析", "danger"),
      ctx.metricCard("实际落实", 0, "等待分析", "success"),
    ].join("");
    document.querySelector("#electricity-opportunity-list").innerHTML = `<div class="empty-state">${ctx.escapeHtml(error.message)}</div>`;
  }
}

function renderSummary(ctx, summary) {
  document.querySelector("#electricity-summary").innerHTML = [
    ctx.metricCard("电费总额", summary.total_electricity_amount, `电费记录 ${ctx.formatNumber(summary.ledger_row_count)}`, "info"),
    ctx.metricCard("异常站址", summary.abnormal_site_count, `机会 ${ctx.formatNumber(summary.opportunity_count)} 条`, "warning"),
    ctx.metricCard("可追回金额", summary.recoverable_amount, "相对确定问题", "danger"),
    ctx.metricCard("压降机会金额", summary.saving_opportunity_amount, `高风险 ${ctx.formatNumber(summary.high_risk_count)} 条`, "success"),
    ctx.metricCard("待处理", summary.pending_count, "等待整改或再次核验", "warning"),
    ctx.metricCard("待复核", summary.review_count, "已返回核查结果", "review"),
    ctx.metricCard("已闭环", summary.closed_count, "含无需整改与复审解决", "success"),
    ctx.metricCard("核实可追回", summary.verified_recoverable_amount, "以核查认定为准", "danger"),
    ctx.metricCard("实际落实", summary.realized_saving_amount, "已退款或已完成优化", "success"),
  ].join("");
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
              <span class="analysis-status analysis-status-${ctx.escapeHtml(row.issue_status || "legacy")}">${ctx.escapeHtml(statusLabel(row.issue_status))}</span>
            </div>
            <dl class="analysis-review-meta">
              <div><dt>问题编号</dt><dd>${ctx.escapeHtml(row.issue_code || "未关联来源问题")}</dd></div>
              <div><dt>机会编号</dt><dd>${ctx.escapeHtml(row.opportunity_code || "")}</dd></div>
              <div><dt>地市 / 账期</dt><dd>${ctx.escapeHtml(row.city || "未填地市")} · ${ctx.escapeHtml(row.period || "未填账期")}</dd></div>
              <div><dt>置信度</dt><dd>${ctx.escapeHtml(row.confidence || "-")}</dd></div>
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
}
