import { fetchJson, postJson } from "/api.js?v=20260517-1";
import { escapeHtml, formatNumber, withBusy } from "/ui.js?v=20260517-1";

export async function renderAnalytics({
  mainContent,
  state,
  refreshBatches,
  currentBatch,
  renderNoBatchPrompt,
  shellHeader,
  renderBatchSelector,
  bindBatchSelector,
  resultList,
  setOperationResult,
}) {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法生成归档汇总。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("治理成效分析", "Analytics", renderBatchSelector())}
      <div id="analytics-summary" class="metric-grid">
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
      </div>
    </section>
    <section class="card">
      ${shellHeader("地市问题分布", "City Distribution")}
      <div class="analytics-grid analytics-grid-wide">
        <div>
          <h3 class="panel-title">地市问题 Top 10</h3>
          <div id="city-bars" class="bar-chart">正在加载</div>
        </div>
        <div>
          <h3 class="panel-title">规则命中 Top 10</h3>
          <div id="rule-bars" class="bar-chart">正在加载</div>
        </div>
      </div>
    </section>
    <section class="card">
      ${shellHeader("分类交叉统计", "Cross Analysis")}
      <div class="analytics-grid">
        <div class="table-wrap"><table><thead><tr><th>地市</th><th>台账类型</th><th>问题数</th></tr></thead><tbody id="city-ledger-table"><tr><td colspan="3">正在加载</td></tr></tbody></table></div>
        <div class="table-wrap"><table><thead><tr><th>地市</th><th>风险等级</th><th>问题数</th></tr></thead><tbody id="city-severity-table"><tr><td colspan="3">正在加载</td></tr></tbody></table></div>
      </div>
      <div class="table-wrap"><table><thead><tr><th>地市</th><th>问题类型</th><th>问题数</th></tr></thead><tbody id="city-rule-table"><tr><td colspan="3">正在加载</td></tr></tbody></table></div>
    </section>
    <section class="card">
      ${shellHeader("问题类型明细", "Issue Categories")}
      <div class="table-wrap"><table><thead><tr><th>台账</th><th>规则</th><th>风险</th><th>问题数</th></tr></thead><tbody id="category-stat-table"><tr><td colspan="4">正在加载</td></tr></tbody></table></div>
    </section>
    <section class="card">
      ${shellHeader("稽核问题通报", "Notice")}
      <div class="operation-panel">
        <p>导出当前批次的稽核问题统计 Excel，包含通报总览、地市问题统计、分类统计和问题明细。</p>
        <button id="export-notice-report" class="primary-button" type="button">导出通报 Excel</button>
      </div>
      <div id="notice-result" class="result-box">等待操作</div>
    </section>
    <section class="card">
      ${shellHeader("专项归档导出", "Archive")}
      <div class="operation-panel">
        <p>归档会生成当前批次的汇总 Excel，包括归档总览、地市整改进度、规则命中排行、风险等级分布和未闭环问题。</p>
        <div class="button-row">
          <button id="archive-precheck" class="secondary-button" type="button">归档前检查</button>
          <button id="archive-batch" class="primary-button" type="button">生成归档汇总</button>
        </div>
      </div>
      <div id="operation-result" class="result-box">等待操作</div>
    </section>
  `;
  bindBatchSelector(() =>
    renderAnalytics({
      mainContent,
      state,
      refreshBatches,
      currentBatch,
      renderNoBatchPrompt,
      shellHeader,
      renderBatchSelector,
      bindBatchSelector,
      resultList,
      setOperationResult,
    }),
  );
  await loadAnalytics(state.batchId);
  document.querySelector("#export-notice-report").addEventListener("click", async (event) => {
    await withBusy(event.currentTarget, "导出中...", async () => {
      const result = document.querySelector("#notice-result");
      result.className = "result-box result-pending";
      result.textContent = "正在生成通报 Excel...";
      const data = await postJson("/api/reports/notice", { batch_id: state.batchId });
      result.className = "result-box result-success";
      result.textContent = `通报已导出：${data.path}`;
    });
  });
  document.querySelector("#archive-precheck").addEventListener("click", async (event) => {
    await withBusy(event.currentTarget, "检查中...", async () => {
      setOperationResult("pending", "正在检查归档条件...");
      const data = await fetchJson(`/api/archive/precheck?batch_id=${state.batchId}`);
      const blockers = data.blockers || [];
      setOperationResult(
        data.ready ? "success" : "error",
        `
          <p>${data.ready ? "当前批次满足归档条件。" : "当前批次暂不满足归档条件。"}</p>
          <div class="mini-grid">
            <span>未闭环 ${formatNumber(data.open_issue_count)}</span>
            <span>批次状态 ${escapeHtml(data.batch_status)}</span>
          </div>
          ${blockers.length ? `<ul class="path-list">${blockers.map((item) => `<li>${escapeHtml(item.message)}</li>`).join("")}</ul>` : ""}
        `,
      );
    });
  });
  document.querySelector("#archive-batch").addEventListener("click", async (event) => {
    await withBusy(event.currentTarget, "归档中...", async () => {
      setOperationResult("pending", "正在生成归档汇总...");
      const data = await postJson("/api/archive", { batch_id: state.batchId });
      setOperationResult("success", resultList([data.path]));
      await loadAnalytics(state.batchId);
    });
  });
}

async function loadAnalytics(batchId) {
  const data = await fetchJson(`/api/dashboard?batch_id=${batchId}`);
  const totalIssues = (data.issues_by_city || []).reduce((sum, row) => sum + Number(row.count || 0), 0);
  const topRule = (data.issues_by_rule || [])[0];
  const topSeverity = (data.issues_by_severity || [])[0];
  document.querySelector("#analytics-summary").innerHTML = [
    metricCard("问题总数", totalIssues, `未闭环 ${formatNumber(data.open_issue_count)}`, "danger"),
    metricCard("闭环率", data.closure_rate, "已关闭和无需整改占比", "success"),
    metricCard("高频规则", topRule?.count || 0, topRule?.rule_name || "暂无", "warning"),
    metricCard("主要风险", topSeverity?.count || 0, severityLabel(topSeverity?.severity) || "暂无", "review"),
  ].join("");
  renderBars("#city-bars", data.issues_by_city || [], "city");
  renderBars("#rule-bars", data.issues_by_rule || [], "rule_name");
  renderCityLedgerRows(data.city_ledger_matrix || []);
  renderCitySeverityRows(data.city_severity_matrix || []);
  renderCityRuleRows(data.city_rule_matrix || []);
  renderCategoryRows(data.issue_categories || []);
}

function renderBars(selector, rows, labelField) {
  const container = document.querySelector(selector);
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无数据</div>';
    return;
  }
  const topRows = rows.slice(0, 10);
  const max = Math.max(...topRows.map((row) => Number(row.count || 0)), 1);
  container.innerHTML = topRows
    .map((row) => {
      const count = Number(row.count || 0);
      return `
        <div class="bar-row">
          <span class="bar-label">${escapeHtml(row[labelField] || "未分类")}</span>
          <span class="bar-track"><span style="width: ${(count / max) * 100}%"></span></span>
          <strong>${formatNumber(count)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderCityLedgerRows(rows) {
  renderMatrixRows("#city-ledger-table", rows, (row) => [row.city, row.ledger_label || ledgerLabel(row.ledger_type), row.count], 3);
}

function renderCitySeverityRows(rows) {
  renderMatrixRows("#city-severity-table", rows, (row) => [row.city, row.severity_label || severityLabel(row.severity), row.count], 3);
}

function renderCityRuleRows(rows) {
  renderMatrixRows("#city-rule-table", rows.slice(0, 80), (row) => [row.city, row.rule_name || row.rule_id, row.count], 3);
}

function renderMatrixRows(selector, rows, toCells, colspan) {
  const tbody = document.querySelector(selector);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${colspan}">暂无数据</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map((row) => {
      const cells = toCells(row);
      return `<tr>${cells.map((cell, index) => `<td>${index === cells.length - 1 ? formatNumber(cell) : escapeHtml(cell)}</td>`).join("")}</tr>`;
    })
    .join("");
}

function renderCategoryRows(rows) {
  const tbody = document.querySelector("#category-stat-table");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4">暂无分类统计</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .slice(0, 20)
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(ledgerLabel(row.ledger_type))}</td>
          <td><strong>${escapeHtml(row.rule_name || row.rule_id)}</strong><br><span>${escapeHtml(row.rule_id)}</span></td>
          <td>${escapeHtml(severityLabel(row.severity))}</td>
          <td>${formatNumber(row.count)}</td>
        </tr>
      `,
    )
    .join("");
}

function ledgerLabel(type) {
  return {
    site: "站址",
    tower_rent: "铁塔租费",
    electricity: "电费",
    generator: "发电费",
  }[type] || type || "未知";
}

function severityLabel(severity) {
  return {
    high: "高",
    medium: "中",
    low: "低",
  }[severity] || severity || "";
}

function metricCard(label, value, note, tone = "neutral") {
  return `
    <article class="metric-card metric-${escapeHtml(tone)}">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${formatNumber(value)}</p>
      <p class="metric-note">${escapeHtml(note)}</p>
      <span class="metric-spark" aria-hidden="true"></span>
    </article>
  `;
}
