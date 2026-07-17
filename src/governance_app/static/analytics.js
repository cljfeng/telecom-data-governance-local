import { fetchJson, postJson } from "/api.js?v=20260517-1";
import { escapeHtml, formatNumber, withBusy } from "/ui.js?v=20260517-1";

const LEDGER_SECTIONS = [
  { type: "data_quality", label: "基础数据质量", tone: "review" },
  { type: "tower_rent", label: "租费风险", ledgerType: "tower_rent", tone: "review" },
  { type: "electricity", label: "电费风险", ledgerType: "electricity", tone: "warning" },
  { type: "generator", label: "发电费风险", ledgerType: "generator", tone: "danger" },
];

function batchStatusLabel(status) {
  return {
    created: "待导入",
    imported: "待稽核",
    audited: "已稽核",
    distributed: "整改中",
    returning: "回传复核中",
    archived: "已归档",
  }[status] || status || "未知状态";
}

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
    renderNoBatchPrompt("没有批次时无法生成分析报表。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card analytics-command-card">
      ${shellHeader("稽核问题分析", "分析报表", renderBatchSelector())}
      <div class="analytics-toolbar">
        <button id="export-notice-report" class="primary-button" type="button">导出通报</button>
        <button id="archive-precheck" class="secondary-button" type="button">归档检查</button>
        <button id="archive-batch" class="secondary-button" type="button">生成归档汇总</button>
      </div>
      <div id="analytics-summary" class="metric-grid analytics-kpi-grid">
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
      </div>
    </section>
    <section class="analytics-overview-grid">
      <div class="card analytics-panel">
        <header class="panel-header">
          <h3 class="panel-title">风险等级分布</h3>
          <span class="panel-kicker">高风险优先闭环</span>
        </header>
        <div id="severity-stack" class="severity-stack">正在加载</div>
        <div id="ledger-share-table" class="legend-grid">正在加载</div>
      </div>
      <div class="card analytics-panel">
        <header class="panel-header">
          <h3 class="panel-title">地市问题排名</h3>
          <span class="panel-kicker">Top 10</span>
        </header>
        <div id="city-bars" class="bar-chart">正在加载</div>
      </div>
      <div class="card analytics-panel">
        <header class="panel-header">
          <h3 class="panel-title">规则命中排行</h3>
          <span class="panel-kicker">Top 10</span>
        </header>
        <div id="rule-bars" class="bar-chart">正在加载</div>
      </div>
    </section>
    <section class="card analytics-panel">
      <header class="panel-header">
        <div>
          <h3 class="panel-title">问题类型矩阵</h3>
          <p class="panel-description">按地市交叉展示高频规则，颜色越深表示问题越集中。</p>
        </div>
      </header>
      <div id="issue-heatmap" class="issue-heatmap">正在加载</div>
    </section>
    <section class="card analytics-panel">
      <header class="panel-header">
        <div>
          <h3 class="panel-title">专题分析</h3>
          <p class="panel-description">基础质量问题和费用风险分开看，避免字段缺失掩盖多付、错付、重复付风险。</p>
        </div>
      </header>
      <div id="ledger-tabs" class="analytics-tabs"></div>
      <div id="ledger-analysis-sections"></div>
    </section>
    <section class="card analytics-panel">
      <header class="panel-header">
        <div>
          <h3 class="panel-title">规则效果复盘</h3>
          <p class="panel-description">按规则查看命中、未闭环、无需整改和闭环率，用来判断规则是否过宽、过严或需要优先复核。</p>
        </div>
      </header>
      <div id="rule-effectiveness" class="table-wrap">正在加载</div>
    </section>
    <section class="analytics-overview-grid analytics-ops-grid">
      <div class="card analytics-panel">
        ${shellHeader("稽核问题通报", "通报导出")}
        <div class="operation-panel">
          <p>导出当前批次的稽核问题统计 Excel，包含通报总览、地市问题统计、分类统计和问题明细。</p>
        </div>
        <div id="notice-result" class="result-box">等待操作</div>
      </div>
      <div class="card analytics-panel">
        ${shellHeader("专项归档导出", "归档")}
        <div class="operation-panel">
          <p>归档会生成当前批次的汇总 Excel，包括归档总览、地市整改进度、规则命中排行、风险等级分布和未闭环问题。</p>
        </div>
        <div id="operation-result" class="result-box">等待操作</div>
      </div>
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
      const riskItems = data.risk_items || [];
      const details = [
        data.ready ? "当前批次满足归档条件。" : "当前批次暂不满足归档条件。",
        `未闭环 ${formatNumber(data.open_issue_count)}，批次状态 ${batchStatusLabel(data.batch_status)}`,
      ]
        .concat(blockers.map((item) => `阻断：${item.message}`))
        .concat(riskItems.map((item) => `风险：${item.message}`));
      setOperationResult(
        data.ready ? "success" : "error",
        details.join("\n"),
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
  const highRisk = severityCount(data, "high");
  const cityCount = (data.issues_by_city || []).filter((row) => Number(row.count || 0) > 0).length;
  document.querySelector("#analytics-summary").innerHTML = [
    metricCard("问题总数", totalIssues, `未闭环 ${formatNumber(data.open_issue_count)}`, "danger"),
    metricCard("高风险", highRisk, "优先核实多付、错付、重复付", "danger"),
    metricCard("未闭环", data.open_issue_count || 0, "待地市整改或复核", "warning"),
    metricCard("闭环率", `${formatNumber(data.closure_rate)}%`, "已关闭和无需整改占比", "success"),
    metricCard("高频规则", topRule?.count || 0, topRule?.rule_name || "暂无", "warning"),
    metricCard("涉及地市", cityCount, "存在问题的地市数量", "review"),
  ].join("");
  renderSeverityStack(data.issues_by_severity || [], totalIssues);
  renderBars("#city-bars", data.issues_by_city || [], "city");
  renderBars("#rule-bars", data.issues_by_rule || [], "rule_name");
  renderLedgerShareRows(data.issues_by_ledger_type || [], totalIssues);
  renderHeatmap(data.city_rule_matrix || []);
  renderLedgerSections(data);
  renderRuleEffectiveness(data.rule_effectiveness || []);
}

function renderRuleEffectiveness(rows) {
  const container = document.querySelector("#rule-effectiveness");
  if (!container) return;
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无规则效果数据</div>';
    return;
  }
  container.innerHTML = `
    <table>
      <thead><tr><th>规则</th><th>可信度</th><th>问题数</th><th>未闭环</th><th>无需整改</th><th>仍异常</th><th>闭环率</th></tr></thead>
      <tbody>
        ${rows.slice(0, 12).map((row) => `
          <tr>
            <td><strong>${escapeHtml(row.rule_name || row.rule_id)}</strong><p class="table-note">${escapeHtml(row.category_label || "")}</p></td>
            <td><span class="chip chip-${confidenceTone(row.confidence)}">${escapeHtml(row.confidence_label || "疑似问题")}</span></td>
            <td>${formatNumber(row.total_count)}</td>
            <td>${formatNumber(row.open_count)}</td>
            <td>${formatNumber(row.not_required_count)}</td>
            <td>${formatNumber(row.still_invalid_count)}</td>
            <td>${formatNumber(row.closure_rate)}%</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function confidenceTone(confidence) {
  if (confidence === "high") return "danger";
  if (confidence === "low") return "info";
  return "warning";
}

function renderLedgerSections(data) {
  renderTabs();
  const container = document.querySelector("#ledger-analysis-sections");
  container.innerHTML = LEDGER_SECTIONS.map((section, index) => {
    const categories = section.type === "data_quality"
      ? (data.issue_categories || []).filter((row) => row.category === "data_quality")
      : (data.issue_categories || []).filter((row) => row.ledger_type === section.ledgerType && row.category !== "data_quality");
    const cityRows = section.type === "data_quality"
      ? mergeRowsByCity((data.city_rule_matrix || []).filter((row) => row.category === "data_quality"))
      : (data.city_ledger_matrix || []).filter((row) => row.ledger_type === section.ledgerType);
    const ruleRows = section.type === "data_quality"
      ? (data.city_rule_matrix || []).filter((row) => row.category === "data_quality")
      : (data.city_rule_matrix || []).filter((row) => row.ledger_type === section.ledgerType && row.category !== "data_quality");
    const total = categories.reduce((sum, row) => sum + Number(row.count || 0), 0);
    const topRule = categories[0];
    return `
      <section class="ledger-analysis-card ${index === 0 ? "is-active" : ""}" data-ledger-section="${section.type}">
        <div class="metric-grid compact-metrics">
          ${metricCard("问题数", total, "当前专题命中问题", section.tone)}
          ${metricCard("主要规则", topRule?.count || 0, topRule?.rule_name || "暂无", "warning")}
          ${metricCard("涉及地市", uniqueCount(cityRows, "city"), "存在问题的地市数量", "review")}
        </div>
        <div class="analytics-grid analytics-grid-wide">
          <div>
            <h3 class="panel-title">${section.label}地市分布</h3>
            <div class="bar-chart">${renderBarsHtml(cityRows, "city")}</div>
          </div>
          <div>
            <h3 class="panel-title">${section.label}规则构成</h3>
            <div class="table-wrap">${renderCategoryTable(categories)}</div>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>地市</th><th>问题类型</th><th>问题数</th></tr></thead>
            <tbody>${renderRuleMatrixRows(ruleRows)}</tbody>
          </table>
        </div>
      </section>
    `;
  }).join("");
  bindTabs();
}

function renderBars(selector, rows, labelField) {
  document.querySelector(selector).innerHTML = renderBarsHtml(rows, labelField);
}

function renderBarsHtml(rows, labelField) {
  if (!rows.length) {
    return '<div class="empty-state">暂无数据</div>';
  }
  const topRows = rows.slice(0, 10);
  const max = Math.max(...topRows.map((row) => Number(row.count || 0)), 1);
  return topRows
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

function renderLedgerShareRows(rows, totalIssues) {
  const container = document.querySelector("#ledger-share-table");
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无台账分类统计</div>';
    return;
  }
  container.innerHTML = rows
    .map((row) => {
      const count = Number(row.count || 0);
      const share = totalIssues ? (count / totalIssues) * 100 : 0;
      return `
        <div class="legend-item">
          <span>${escapeHtml(row.ledger_label || ledgerLabel(row.ledger_type))}</span>
          <strong>${formatNumber(count)}</strong>
          <em>${formatNumber(share)}%</em>
        </div>
      `;
    })
    .join("");
}

function renderCategoryTable(rows) {
  if (!rows.length) {
    return '<div class="empty-state">暂无规则命中</div>';
  }
  return `
    <table>
      <thead><tr><th>规则</th><th>风险</th><th>问题数</th></tr></thead>
      <tbody>
        ${rows.slice(0, 8).map((row) => `
          <tr>
            <td><strong>${escapeHtml(row.rule_name || row.rule_id)}</strong></td>
            <td>${escapeHtml(row.severity_label || severityLabel(row.severity))}</td>
            <td>${formatNumber(row.count)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderSeverityStack(rows, totalIssues) {
  const container = document.querySelector("#severity-stack");
  const ordered = ["high", "medium", "low"].map((severity) => {
    const row = rows.find((item) => item.severity === severity);
    return { severity, label: severityLabel(severity), count: Number(row?.count || 0) };
  });
  if (!totalIssues) {
    container.innerHTML = '<div class="empty-state">暂无风险等级数据</div>';
    return;
  }
  container.innerHTML = `
    <div class="stack-track">
      ${ordered.map((row) => `<span class="stack-${row.severity}" style="width:${(row.count / totalIssues) * 100}%"></span>`).join("")}
    </div>
    <div class="severity-list">
      ${ordered.map((row) => `
        <div class="severity-item severity-${row.severity}">
          <span>${escapeHtml(row.label)}</span>
          <strong>${formatNumber(row.count)}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function renderHeatmap(rows) {
  const container = document.querySelector("#issue-heatmap");
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无矩阵数据</div>';
    return;
  }
  const cities = [...new Set(rows.map((row) => row.city).filter(Boolean))].slice(0, 10);
  const rules = topByCount(rows, "rule_name", 8);
  const max = Math.max(...rows.map((row) => Number(row.count || 0)), 1);
  container.innerHTML = `
    <div class="heatmap-grid" style="grid-template-columns: 96px repeat(${rules.length}, minmax(70px, 1fr));">
      <span class="heatmap-corner">地市/规则</span>
      ${rules.map((rule) => `<span class="heatmap-head">${escapeHtml(rule)}</span>`).join("")}
      ${cities.map((city) => `
        <span class="heatmap-city">${escapeHtml(city)}</span>
        ${rules.map((rule) => {
          const match = rows.find((row) => row.city === city && row.rule_name === rule);
          const count = Number(match?.count || 0);
          const alpha = count ? 0.16 + (count / max) * 0.62 : 0;
          return `<span class="heatmap-cell" style="--heat:${alpha};">${count ? formatNumber(count) : ""}</span>`;
        }).join("")}
      `).join("")}
    </div>
  `;
}

function renderTabs() {
  document.querySelector("#ledger-tabs").innerHTML = LEDGER_SECTIONS.map((section, index) => `
    <button class="analytics-tab ${index === 0 ? "is-active" : ""}" type="button" data-ledger-tab="${section.type}">${escapeHtml(section.label)}</button>
  `).join("");
}

function bindTabs() {
  document.querySelectorAll("[data-ledger-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.ledgerTab;
      document.querySelectorAll("[data-ledger-tab]").forEach((item) => item.classList.toggle("is-active", item === button));
      document.querySelectorAll("[data-ledger-section]").forEach((section) => {
        section.classList.toggle("is-active", section.dataset.ledgerSection === target);
      });
    });
  });
}

function renderRuleMatrixRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="3">暂无地市规则交叉数据</td></tr>';
  }
  return rows
    .slice(0, 80)
    .map((row) => `<tr><td>${escapeHtml(row.city)}</td><td>${escapeHtml(row.rule_name || row.rule_id)}</td><td>${formatNumber(row.count)}</td></tr>`)
    .join("");
}

function severityCount(data, severity) {
  return Number((data.issues_by_severity || []).find((row) => row.severity === severity)?.count || 0);
}

function topByCount(rows, labelField, limit) {
  const counts = new Map();
  rows.forEach((row) => {
    const label = row[labelField] || row.rule_id || "未分类";
    counts.set(label, (counts.get(label) || 0) + Number(row.count || 0));
  });
  return [...counts.entries()]
    .sort((first, second) => second[1] - first[1] || first[0].localeCompare(second[0], "zh-CN"))
    .slice(0, limit)
    .map(([label]) => label);
}

function mergeRowsByCity(rows) {
  const counts = new Map();
  rows.forEach((row) => counts.set(row.city, (counts.get(row.city) || 0) + Number(row.count || 0)));
  return [...counts.entries()]
    .map(([city, count]) => ({ city, count }))
    .sort((a, b) => b.count - a.count || a.city.localeCompare(b.city, "zh-CN"));
}

function uniqueCount(rows, field) {
  return new Set(rows.map((row) => row[field]).filter(Boolean)).size;
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
  const renderedValue = typeof value === "number" ? formatNumber(value) : value;
  return `
    <article class="metric-card metric-${escapeHtml(tone)}">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${escapeHtml(renderedValue)}</p>
      <p class="metric-note">${escapeHtml(note)}</p>
      <span class="metric-spark" aria-hidden="true"></span>
    </article>
  `;
}
