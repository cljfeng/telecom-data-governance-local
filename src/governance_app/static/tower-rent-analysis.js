import { fetchJson, postJson } from "/api.js?v=20260517-1";

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
        <button id="run-tower-rent-analysis" class="primary-button" type="button">生成分析</button>
        <button id="export-tower-rent-analysis" class="secondary-button" type="button">导出 Excel</button>
      </div>
      <div id="tower-rent-analysis-result" class="result-box">可先生成分析，再查看异常线索清单。</div>
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
        <button id="apply-tower-rent-filters" class="secondary-button" type="button">筛选</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>地市</th>
              <th>站址</th>
              <th>账期</th>
              <th>类型</th>
              <th>当前金额</th>
              <th>预计可追回</th>
              <th>优惠落实</th>
              <th>待核查</th>
              <th>置信度</th>
              <th>建议动作</th>
            </tr>
          </thead>
          <tbody id="tower-rent-clue-table"><tr><td colspan="10">正在加载</td></tr></tbody>
        </table>
      </div>
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
  const query = new URLSearchParams();
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  try {
    const [summary, list] = await Promise.all([
      fetchJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/summary`),
      fetchJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/opportunities${query.toString() ? `?${query}` : ""}`),
    ]);
    renderSummary(ctx, summary);
    renderBreakdown(ctx, summary);
    renderRows(ctx, list.opportunities || []);
  } catch (error) {
    document.querySelector("#tower-rent-summary").innerHTML = [
      ctx.metricCard("租费总额", 0, "等待分析", "info"),
      ctx.metricCard("异常站址", 0, "等待分析", "warning"),
      ctx.metricCard("预计可追回金额", 0, "等待分析", "danger"),
      ctx.metricCard("优惠落实金额", 0, "等待分析", "success"),
      ctx.metricCard("待核查金额", 0, "等待分析", "review"),
    ].join("");
    document.querySelector("#tower-rent-clue-table").innerHTML = `<tr><td colspan="10">${ctx.escapeHtml(error.message)}</td></tr>`;
  }
}

function renderSummary(ctx, summary) {
  document.querySelector("#tower-rent-summary").innerHTML = [
    ctx.metricCard("租费总额", summary.total_rent_amount, `租费记录 ${ctx.formatNumber(summary.ledger_row_count)}`, "info"),
    ctx.metricCard("异常站址", summary.abnormal_site_count, `线索 ${ctx.formatNumber(summary.clue_count)} 条`, "warning"),
    ctx.metricCard("预计可追回金额", summary.recoverable_amount, "相对确定问题", "danger"),
    ctx.metricCard("优惠落实金额", summary.discount_realization_amount, "共享折扣等优惠线索", "success"),
    ctx.metricCard("待核查金额", summary.review_amount, `高风险 ${ctx.formatNumber(summary.high_risk_count)} 条`, "review"),
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
  const tbody = document.querySelector("#tower-rent-clue-table");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10">暂无租费异常线索。可以先点击“生成分析”。</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${ctx.escapeHtml(row.city || "未填地市")}</td>
          <td><strong>${ctx.escapeHtml(row.telecom_site_code || "")}</strong><br>${ctx.escapeHtml(row.telecom_site_name || "")}</td>
          <td>${ctx.escapeHtml(row.period || "")}</td>
          <td>${ctx.escapeHtml(row.opportunity_type)}</td>
          <td>${money(row.current_amount)}</td>
          <td>${money(row.recoverable_amount)}</td>
          <td>${money(row.discount_realization_amount)}</td>
          <td>${money(row.review_amount)}</td>
          <td>${ctx.escapeHtml(row.confidence)}</td>
          <td>${ctx.escapeHtml(row.suggestion)}</td>
        </tr>
      `,
    )
    .join("");
}
