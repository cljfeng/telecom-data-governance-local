import { fetchJson, postJson, postFormData } from "/api.js?v=20260517-1";
import { state } from "/state.js?v=20260517-1";
import { escapeHtml, formatNumber, withBusy } from "/ui.js?v=20260517-1";
import { renderLedgerData } from "/ledger-data.js?v=20260517-1";
import { renderRules } from "/rules.js?v=20260517-1";
import { renderSettings } from "/settings.js?v=20260517-1";
import { renderAnalytics } from "/analytics.js?v=20260517-1";
import { renderElectricityAnalysis } from "/electricity-analysis.js?v=20260708-1";

const views = {
  dashboard: "专项工作台",
  batches: "批次管理",
  import: "数据导入",
  ledgerData: "数据整理",
  rules: "规则设置",
  audit: "稽核结果",
  export: "问题包导出",
  corrections: "整改回传",
  electricityAnalysis: "电费压降分析",
  reports: "分析报表",
  settings: "本地设置",
};

const pageTitle = document.querySelector("#page-title");
const mainContent = document.querySelector("#main-content");
const statusPill = document.querySelector("#service-status");
const headerBatch = document.querySelector("#header-batch");
const lastSync = document.querySelector("#last-sync");
const navButtons = Array.from(document.querySelectorAll(".nav-button"));

function setStatus(stateName, text) {
  statusPill.textContent = text;
  statusPill.className = `status status-${stateName}`;
  if (stateName === "online") {
    const now = new Date();
    lastSync.textContent = `最近同步 ${now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
  }
}

async function refreshBatches() {
  const data = await fetchJson("/api/batches");
  state.batches = data.batches || [];
  const current = state.batches.find((batch) => batch.is_current) || state.batches[0];
  if (current) {
    state.batchId = current.id;
    headerBatch.textContent = `${current.batch_code || `#${current.id}`} 当前批次`;
  } else {
    state.batchId = null;
    headerBatch.textContent = "暂无批次";
  }
  return state.batches;
}

function currentBatch() {
  return state.batches.find((batch) => batch.id === state.batchId) || state.batches[0];
}

function batchOptions() {
  if (!state.batches.length) {
    return '<option value="">暂无批次</option>';
  }
  return state.batches
    .map((batch) => `<option value="${batch.id}" ${batch.id === state.batchId ? "selected" : ""}>${escapeHtml(batch.batch_code || `#${batch.id}`)} ${escapeHtml(batch.name)}</option>`)
    .join("");
}

function shellHeader(title, eyebrow, actionHtml = "") {
  return `
    <div class="section-header">
      <div>
        <p class="eyebrow">${escapeHtml(eyebrow)}</p>
        <h2>${escapeHtml(title)}</h2>
      </div>
      ${actionHtml}
    </div>
  `;
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

function percentValue(value) {
  const parsed = Number.parseFloat(value);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(100, parsed));
}

function statusLabel(status) {
  const labels = {
    pending_export: "待导出",
    pending_correction: "待整改",
    returned: "已回传",
    still_invalid: "仍异常",
    needs_review: "待复核",
    closed: "已关闭",
    not_required: "无需整改",
  };
  return labels[status] || status || "未开始";
}

function ledgerLabel(type) {
  const labels = {
    site: "站址",
    tower_rent: "铁塔租费",
    electricity: "电费",
    generator: "发电费",
  };
  return labels[type] || type || "未知";
}

function severityLabel(severity) {
  const labels = {
    high: "高",
    medium: "中",
    low: "低",
  };
  return labels[severity] || severity || "未知";
}

function severityTone(severity) {
  if (severity === "high" || severity === "严重" || severity === "高") return "danger";
  if (severity === "medium" || severity === "中") return "warning";
  return "success";
}

function statusTone(status) {
  if (status === "still_invalid") return "danger";
  if (status === "needs_review" || status === "returned") return "warning";
  if (status === "closed" || status === "not_required") return "success";
  return "info";
}

function cityProgressTone(row) {
  const rate = percentValue(row.completion_rate);
  const review = Number(row.review_count || 0);
  const pending = Number(row.pending_count || 0);
  if (review > 0) return { text: "需复核", tone: "warning" };
  if (rate >= 90) return { text: "完成", tone: "success" };
  if (rate < 35 && pending > 0) return { text: "滞后", tone: "danger" };
  return { text: "正常", tone: "info" };
}

function actionForWorkflow(workflow) {
  const action = workflow?.next_action || "";
  if (action.includes("导入")) return { label: "导入台账", view: "import", secondary: "批次管理", secondaryView: "batches" };
  if (action.includes("稽核")) return { label: "执行稽核", view: "audit", secondary: "查看问题", secondaryView: "audit" };
  if (action.includes("导出")) return { label: "导出整改包", view: "export", secondary: "查看问题", secondaryView: "audit" };
  if (action.includes("回传")) return { label: "导入回传", view: "corrections", secondary: "查看进度", secondaryView: "corrections" };
  if (action.includes("归档")) return { label: "生成归档", view: "reports", secondary: "分析报表", secondaryView: "reports" };
  return { label: "查看工作台", view: "dashboard", secondary: "数据导入", secondaryView: "import" };
}

function resultList(items) {
  if (!items?.length) {
    return "<p>无返回明细</p>";
  }
  return `<ul class="path-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function fieldValue(id) {
  return document.querySelector(`#${id}`).value.trim();
}

function selectedWorkbookFile() {
  return document.querySelector("#workbook-file")?.files?.[0] || null;
}

function workbookFormData(extraFields = {}) {
  const file = selectedWorkbookFile();
  if (!file) return null;
  const formData = new FormData();
  formData.append("file", file);
  Object.entries(extraFields).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      formData.append(key, value);
    }
  });
  return formData;
}

function setOperationResult(stateName, content) {
  const result = document.querySelector("#operation-result");
  result.className = `result-box result-${stateName}`;
  result.textContent = content;
}

function setOperationHtml(stateName, content) {
  const result = document.querySelector("#operation-result");
  result.className = `result-box result-${stateName}`;
  result.innerHTML = content;
}

function operationCard({ title, eyebrow, description, fields, buttonText, resultTitle }) {
  const fieldHtml = fields
    .map(
      (field) => `
        <label class="form-field">
          <span>${escapeHtml(field.label)}</span>
          <input id="${escapeHtml(field.id)}" type="${escapeHtml(field.type || "text")}" value="${escapeHtml(field.value || "")}" placeholder="${escapeHtml(field.placeholder || "")}" inputmode="${escapeHtml(field.inputmode || "text")}">
        </label>
      `,
    )
    .join("");

  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader(title, eyebrow)}
      <div class="operation-panel">
        <p>${escapeHtml(description)}</p>
        <div class="form-grid">${fieldHtml}</div>
        <button id="operation-submit" class="primary-button" type="button">${escapeHtml(buttonText)}</button>
      </div>
    </section>
    <section class="card">
      ${shellHeader(resultTitle, "处理结果")}
      <div id="operation-result" class="result-box">等待操作</div>
    </section>
  `;
}

function renderBatchSelector() {
  return `
    <div class="toolbar">
      <label class="compact-field">
        <span>当前批次</span>
        <select id="batch-selector">${batchOptions()}</select>
      </label>
      <button id="select-batch" class="secondary-button" type="button">切换</button>
    </div>
  `;
}

function bindBatchSelector(afterSelect) {
  const selector = document.querySelector("#batch-selector");
  const button = document.querySelector("#select-batch");
  if (!selector || !button) return;
  button.addEventListener("click", async () => {
    if (!selector.value) {
      renderNoBatchPrompt("还没有可切换的专项批次。");
      return;
    }
    state.batchId = Number(selector.value);
    await postJson("/api/batches/current", { batch_id: state.batchId });
    await refreshBatches();
    afterSelect();
  });
}

function renderNoBatchPrompt(message = "当前还没有专项批次。") {
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("没有可用批次", "批次")}
      <div class="operation-panel">
        <p>${escapeHtml(message)}可以先新建一个批次，再导入全省台账；也可以直接到“数据导入”页面导入模板，系统会自动生成批次。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>批次名称</span>
            <input id="new-batch-name" value="2026年基站电费租费基础数据核查" placeholder="请输入专项批次名称">
          </label>
        </div>
        <button id="create-batch" class="primary-button" type="button">新建批次</button>
      </div>
    </section>
  `;
  document.querySelector("#create-batch").addEventListener("click", async () => {
    const name = fieldValue("new-batch-name");
    if (!name) return;
    await postJson("/api/batches", { name });
    await refreshBatches();
    await loadDashboard();
  });
}

async function loadDashboard() {
  await refreshBatches().catch(() => []);
  const batch = currentBatch();
  if (!batch) {
    renderEmptyDashboard();
    return;
  }
  mainContent.innerHTML = `
    <section class="card command-card">
      ${shellHeader("专项治理闭环", batch ? `${batch.batch_code || `#${batch.id}`} ${batch.name}` : "批次", renderBatchSelector())}
      <div id="workflow-area" class="workflow-area">正在加载流程</div>
    </section>
    <section class="card metric-section">
      <div id="metric-grid" class="metric-grid">
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
      </div>
    </section>
    <div class="dashboard-grid">
      <section class="card">
        ${shellHeader("地市整改进度", "地市进度")}
        <div class="table-wrap"><table><thead><tr><th>地市</th><th>问题</th><th>待整改</th><th>待复核</th><th>已关闭</th><th>高频规则</th><th>完成率</th><th>状态</th></tr></thead><tbody id="city-progress-table"><tr><td colspan="8">正在加载</td></tr></tbody></table></div>
      </section>
      <aside class="side-stack">
        <section class="card">
          ${shellHeader("风险摘要", "风险")}
          <div id="risk-summary" class="risk-summary">正在加载</div>
        </section>
        <section class="card">
          ${shellHeader("最近操作", "操作记录")}
          <div id="operation-log" class="operation-log operation-log-panel">正在加载</div>
        </section>
      </aside>
    </div>
  `;
  bindBatchSelector(loadDashboard);
  try {
    const [workflow, summary, progress] = await Promise.all([
      fetchJson(`/api/workflow?batch_id=${state.batchId}`),
      fetchJson(`/api/dashboard?batch_id=${state.batchId}`),
      fetchJson(`/api/city-progress?batch_id=${state.batchId}`),
    ]);
    renderWorkflow(workflow);
    renderMetrics(summary, progress.cities || []);
    renderCityProgress(progress.cities || []);
    renderRiskSummary(summary.issues_by_rule || []);
    renderOperationLog(workflow.operations || []);
  } catch (error) {
    if (error.message === "batch not found") {
      await refreshBatches().catch(() => []);
      renderEmptyDashboard();
      return;
    }
    document.querySelector("#workflow-area").textContent = `流程加载失败：${error.message}`;
    document.querySelector("#metric-grid").innerHTML = [
      metricCard("台账记录", 0, "概览加载失败"),
      metricCard("问题总数", 0, "概览加载失败"),
      metricCard("涉及地市", 0, "概览加载失败"),
      metricCard("命中规则", 0, "概览加载失败"),
      metricCard("完成率", 0, "概览加载失败"),
    ].join("");
    renderCityProgress([]);
    renderRiskSummary([]);
    renderOperationLog([]);
  }
}

function renderEmptyDashboard() {
  mainContent.innerHTML = `
    <section class="card command-card">
      ${shellHeader("专项治理闭环", "批次")}
      <div class="operation-panel">
        <p>当前还没有专项批次。可以先新建一个批次，再导入全省台账；也可以直接到“数据导入”页面导入模板，系统会自动生成批次。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>批次名称</span>
            <input id="new-batch-name" value="2026年基站电费租费基础数据核查" placeholder="请输入专项批次名称">
          </label>
        </div>
        <button id="create-batch" class="primary-button" type="button">新建批次</button>
      </div>
    </section>
    <section class="card metric-section">
      <div class="metric-grid">
        ${metricCard("台账记录", 0, "等待导入台账")}
        ${metricCard("问题总数", 0, "等待执行稽核")}
        ${metricCard("待整改", 0, "等待导出问题包")}
        ${metricCard("待复核", 0, "等待地市回传")}
        ${metricCard("完成率", 0, "等待闭环")}
      </div>
    </section>
    <section class="card">
      ${shellHeader("地市整改进度", "地市进度")}
      <div class="empty-state">暂无批次数据</div>
    </section>
  `;
  document.querySelector("#create-batch").addEventListener("click", async () => {
    const name = fieldValue("new-batch-name");
    if (!name) return;
    await postJson("/api/batches", { name });
    await loadDashboard();
  });
}

async function renderBatches() {
  await refreshBatches().catch(() => []);
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("批次管理", "批次")}
      <div class="operation-panel">
        <p>批次用于管理每一轮专项治理。新建批次后可以导入台账、执行稽核、导出整改包、导入回传并归档。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>新批次名称</span>
            <input id="batch-name" value="2026年基站电费租费基础数据核查" placeholder="请输入专项批次名称">
          </label>
        </div>
        <button id="create-batch-page" class="primary-button" type="button">新建批次</button>
      </div>
    </section>
    <section class="card">
      ${shellHeader("历史批次", "历史")}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>批次编码</th>
              <th>批次名称</th>
              <th>状态</th>
              <th>创建时间</th>
              <th>来源文件</th>
              <th>当前</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="batch-table"></tbody>
        </table>
      </div>
    </section>
  `;
  document.querySelector("#create-batch-page").addEventListener("click", async () => {
    const name = fieldValue("batch-name");
    if (!name) return;
    await postJson("/api/batches", { name });
    await renderBatches();
  });
  renderBatchRows();
}

function renderBatchRows() {
  const tbody = document.querySelector("#batch-table");
  if (!state.batches.length) {
    tbody.innerHTML = '<tr><td colspan="7">暂无批次，可以先新建批次或直接导入台账。</td></tr>';
    return;
  }
  tbody.innerHTML = state.batches
    .map(
      (batch) => `
        <tr>
          <td>${escapeHtml(batch.batch_code || `#${batch.id}`)}</td>
          <td>${escapeHtml(batch.name)}</td>
          <td>${escapeHtml(batch.status)}${batch.is_archived ? " / 已归档" : ""}</td>
          <td>${escapeHtml(batch.created_at)}</td>
          <td>${escapeHtml(batch.source_file || "手动创建")}</td>
          <td>${batch.is_current ? '<span class="progress-pill">当前</span>' : ""}</td>
          <td><button class="text-button" data-select-batch="${batch.id}" type="button">设为当前</button></td>
        </tr>
      `,
    )
    .join("");
  tbody.querySelectorAll("[data-select-batch]").forEach((button) => {
    button.addEventListener("click", async () => {
      await postJson("/api/batches/current", { batch_id: Number(button.dataset.selectBatch) });
      await renderBatches();
    });
  });
}

function renderWorkflow(workflow) {
  const action = actionForWorkflow(workflow);
  const guidance = workflow.guidance || {};
  const todo = workflow.todo_summary || {};
  document.querySelector("#workflow-area").innerHTML = `
    <div class="workflow-layout">
      <div class="workflow-steps">
        ${workflow.steps
          .map(
            (step, index) => `
              <div class="workflow-step step-${step.state}">
                <span class="step-index">${step.state === "done" ? "✓" : index + 1}</span>
                <span>${escapeHtml(step.label)}</span>
                ${step.blocked_reason ? `<small>${escapeHtml(step.blocked_reason)}</small>` : ""}
              </div>
            `,
          )
          .join("")}
      </div>
      <div class="next-action">
        <p class="eyebrow">下一步动作</p>
        <h3>${escapeHtml(guidance.title || workflow.next_action)}</h3>
        <p>${escapeHtml(guidance.reason || "系统会按当前批次状态引导完成导入、稽核、导出、回传和归档。")}</p>
        <div class="todo-strip">
          <span>台账 ${formatNumber(todo.ledger_count || 0)}</span>
          <span>未闭环 ${formatNumber(todo.open_issue_count || 0)}</span>
          <span>待复核 ${formatNumber(todo.review_count || 0)}</span>
          <span>仍异常 ${formatNumber(todo.still_invalid_count || 0)}</span>
        </div>
        <div class="button-row">
          <button class="primary-button" type="button" data-next-view="${escapeHtml(guidance.primary_view || workflow.steps.find((step) => step.can_operate)?.primary_action?.view || action.view)}">${escapeHtml(guidance.primary_label || workflow.steps.find((step) => step.can_operate)?.primary_action?.label || action.label)}</button>
          <button class="secondary-button" type="button" data-next-view="${escapeHtml(action.secondaryView)}">${escapeHtml(action.secondary)}</button>
        </div>
      </div>
    </div>
  `;
  document.querySelectorAll("[data-next-view]").forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.nextView));
  });
}

function renderMetrics(data, progressRows = []) {
  const ledgerCounts = data.ledger_counts || {};
  const cityRows = data.issues_by_city || [];
  const ruleRows = data.issues_by_rule || [];
  const totalLedgers = Object.values(ledgerCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const totalIssues = cityRows.reduce((sum, row) => sum + Number(row.count || 0), 0);
  const pending = progressRows.reduce((sum, row) => sum + Number(row.pending_count || 0), 0) || totalIssues;
  const review = progressRows.reduce((sum, row) => sum + Number(row.review_count || 0), 0);
  const closed = progressRows.reduce((sum, row) => sum + Number(row.closed_count || 0) + Number(row.not_required_count || 0), 0);
  const completionRate = totalIssues ? ((closed / totalIssues) * 100).toFixed(1) : "0.0";
  document.querySelector("#metric-grid").innerHTML = [
    metricCard("台账记录", totalLedgers, "当前批次导入记录总量", "info"),
    metricCard("问题总数", totalIssues, `涉及地市 ${cityRows.length} 个`, "danger"),
    metricCard("待整改", pending, `问题最多：${cityRows[0]?.city || "暂无"}`, "warning"),
    metricCard("待复核", review, "等待省公司人工确认", "review"),
    metricCard("完成率", completionRate, `命中规则 ${ruleRows.length} 条`, "success"),
  ].join("");
}

function renderCityProgress(rows) {
  const tbody = document.querySelector("#city-progress-table");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8">当前批次暂无整改进度</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .map((row) => {
      const completion = percentValue(row.completion_rate);
      const status = cityProgressTone(row);
      return `
        <tr>
          <td><strong>${escapeHtml(row.city)}</strong></td>
          <td>${formatNumber(row.total_count)}</td>
          <td>${formatNumber(row.pending_count)}</td>
          <td>${formatNumber(row.review_count)}</td>
          <td>${formatNumber(Number(row.closed_count || 0) + Number(row.not_required_count || 0))}</td>
          <td>${(row.top_rules || []).slice(0, 2).map((rule) => `<span class="mini-chip">${escapeHtml(rule.rule_name)}</span>`).join("") || "暂无"}</td>
          <td>
            <div class="progress-cell">
              <span class="progress-track"><span style="width: ${completion}%"></span></span>
              <span>${escapeHtml(row.completion_rate)}%</span>
            </div>
          </td>
          <td><span class="chip chip-${status.tone}">${status.text}</span></td>
        </tr>
      `;
    })
    .join("");
}

function renderRiskSummary(rows) {
  const container = document.querySelector("#risk-summary");
  if (!container) return;
  if (!rows.length) {
    container.innerHTML = '<div class="empty-state">暂无风险摘要</div>';
    return;
  }
  const max = Math.max(...rows.map((row) => Number(row.count || 0)), 1);
  container.innerHTML = rows
    .slice(0, 5)
    .map((row) => {
      const count = Number(row.count || 0);
      return `
        <div class="risk-row">
          <div>
            <strong>${escapeHtml(row.rule_name || row.rule_id)}</strong>
            <span>${formatNumber(count)} 个问题</span>
          </div>
          <span class="risk-bar"><span style="width: ${(count / max) * 100}%"></span></span>
        </div>
      `;
    })
    .join("");
}

function renderOperationLog(items) {
  const container = document.querySelector("#operation-log");
  if (!container) return;
  if (!items.length) {
    container.innerHTML = "<p>暂无操作记录</p>";
    return;
  }
  container.innerHTML = items
    .slice(0, 4)
    .map((item) => `<p><strong>${escapeHtml(item.operation)}</strong><span>${escapeHtml(item.created_at)}</span>${escapeHtml(item.message)}</p>`)
    .join("");
}

async function renderImport() {
  await refreshBatches().catch(() => []);
  const recent = await fetchJson("/api/import/recent").catch(() => ({ files: [] }));
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("数据导入", "台账导入")}
      <div class="operation-panel">
        <p>选择本机 Excel 台账文件，先进行模板预检。预检通过后再正式入库，系统会自动生成并选中当前批次。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>台账文件</span>
            <input id="workbook-file" type="file" accept=".xlsx,.xlsm,.xltx,.xltm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12">
            <input id="workbook-path" type="hidden">
            <small id="selected-workbook-name" class="field-hint">尚未选择文件</small>
          </label>
          <label class="form-field">
            <span>导入策略</span>
            <select id="import-strategy">
              <option value="new">新建批次</option>
              <option value="append">追加到当前批次</option>
              <option value="replace">覆盖当前批次</option>
            </select>
          </label>
        </div>
        <div class="button-row">
          <button id="preview-import" class="secondary-button" type="button">预检模板</button>
          <button id="operation-submit" class="primary-button" type="button">导入台账</button>
        </div>
      </div>
    </section>
    <section class="card">
      ${shellHeader("预检与导入结果", "处理结果")}
      <div id="operation-result" class="result-box">请先选择文件并执行预检</div>
    </section>
    <section class="card">
      ${shellHeader("最近文件", "文件记录")}
      <div id="recent-files" class="recent-files"></div>
    </section>
  `;
  renderRecentFiles(recent.files || []);
  document.querySelector("#workbook-file").addEventListener("change", () => {
    const file = selectedWorkbookFile();
    document.querySelector("#workbook-path").value = "";
    document.querySelector("#selected-workbook-name").textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "尚未选择文件";
  });
  document.querySelector("#preview-import").addEventListener("click", async (event) => {
    const path = fieldValue("workbook-path");
    const formData = workbookFormData();
    if (!formData && !path) return setOperationResult("error", "请选择台账文件");
    await withBusy(event.currentTarget, "预检中...", async () => {
      setOperationResult("pending", "正在预检模板...");
      try {
        const data = formData ? await postFormData("/api/import/preview/upload", formData) : await postJson("/api/import/preview", { path });
        setOperationHtml("success", renderImportPreviewResult(data, "预检通过，可以正式导入"));
        const recentData = await fetchJson("/api/import/recent").catch(() => ({ files: [] }));
        renderRecentFiles(recentData.files || []);
      } catch (error) {
        if (error.data) {
          setOperationHtml("error", renderImportPreviewResult(error.data, "预检未通过，请按错误明细修正后重试"));
        } else {
          setOperationResult("error", error.message);
        }
        const recentData = await fetchJson("/api/import/recent").catch(() => ({ files: [] }));
        renderRecentFiles(recentData.files || []);
      }
    });
  });
  document.querySelector("#operation-submit").addEventListener("click", async (event) => {
    const path = fieldValue("workbook-path");
    const file = selectedWorkbookFile();
    if (!file && !path) return setOperationResult("error", "请选择台账文件");
    await withBusy(event.currentTarget, "导入中...", async () => {
      setOperationResult("pending", "正在导入...");
      try {
        const strategy = document.querySelector("#import-strategy").value;
        const payload = { path, strategy };
        if (strategy !== "new") payload.batch_id = state.batchId;
        const formData = workbookFormData({ strategy, batch_id: strategy !== "new" ? state.batchId : "" });
        const data = formData ? await postFormData("/api/import/upload", formData) : await postJson("/api/import", payload);
        state.batchId = data.batch_id;
        await refreshBatches();
        const counts = data.ledger_counts || {};
        setOperationHtml("success", `<p>导入成功，批次号：<strong>${escapeHtml(data.batch_id)}</strong>。即将进入专项工作台，下一步执行稽核。</p><div class="mini-grid"><span>站址 ${formatNumber(counts.site)}</span><span>铁塔租费 ${formatNumber(counts.tower_rent)}</span><span>电费 ${formatNumber(counts.electricity)}</span><span>发电费 ${formatNumber(counts.generator)}</span></div>`);
        const recentData = await fetchJson("/api/import/recent").catch(() => ({ files: [] }));
        renderRecentFiles(recentData.files || []);
        window.setTimeout(() => activateView("dashboard"), 800);
      } catch (error) {
        if (error.data?.errors?.length) {
          setOperationHtml("error", renderImportPreviewResult(error.data, "导入未通过，请按错误明细修正后重试"));
        } else {
          setOperationResult("error", error.message);
        }
      }
    });
  });
}

function renderImportPreviewResult(data, message) {
  const counts = data.ledger_counts || {};
  const errors = data.errors || [];
  const summary = data.error_summary || { blocker: errors.length, warning: 0 };
  const blockerErrors = errors.filter((error) => (error.severity || "blocker") === "blocker");
  const warningErrors = errors.filter((error) => error.severity === "warning");
  const detailMessage = data.error && data.error !== message ? `<p>${escapeHtml(data.error)}</p>` : "";
  return `
    <p><strong>${escapeHtml(message)}</strong></p>
    ${detailMessage}
    <p>建议批次名称：${escapeHtml(data.batch_name || "未命名批次")}</p>
    <div class="mini-grid">
      <span>站址 ${formatNumber(counts.site)}</span>
      <span>铁塔租费 ${formatNumber(counts.tower_rent)}</span>
      <span>电费 ${formatNumber(counts.electricity)}</span>
      <span>发电费 ${formatNumber(counts.generator)}</span>
    </div>
    ${
      errors.length
        ? `<div class="preview-summary"><span class="chip chip-danger">必须修复 ${formatNumber(summary.blocker || 0)}</span><span class="chip chip-warning">建议修复 ${formatNumber(summary.warning || 0)}</span></div>`
        : ""
    }
    ${
      blockerErrors.length
        ? `<div class="error-group"><strong>必须修复</strong><ul class="path-list">${blockerErrors.map((error) => `<li>${escapeHtml(error.field_name)}：${escapeHtml(error.message)}。${escapeHtml(error.action || "")}</li>`).join("")}</ul></div>`
        : ""
    }
    ${warningErrors.length ? `<div class="error-group"><strong>建议修复</strong><ul class="path-list">${warningErrors.map((error) => `<li>第 ${escapeHtml(error.row_number)} 行 ${escapeHtml(error.field_name)}：${escapeHtml(error.message)}。${escapeHtml(error.action || "")}</li>`).join("")}</ul></div>` : ""}
    ${data.error_export_path ? `<p>错误明细：${escapeHtml(data.error_export_path)}</p>` : ""}
  `;
}

function renderRecentFiles(files) {
  const container = document.querySelector("#recent-files");
  if (!container) return;
  if (!files.length) {
    container.innerHTML = '<div class="empty-state">暂无最近文件</div>';
    return;
  }
  container.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>文件路径</th>
            <th>结果</th>
            <th>台账记录</th>
            <th>错误</th>
            <th>时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${files
            .map((file) => {
              const counts = file.ledger_counts || {};
              const total = Number(counts.site || 0) + Number(counts.tower_rent || 0) + Number(counts.electricity || 0) + Number(counts.generator || 0);
              return `
                <tr>
                  <td>${escapeHtml(file.path)}</td>
                  <td>${file.ok ? '<span class="progress-pill">通过</span>' : '<span class="risk-pill">异常</span>'}</td>
                  <td>${formatNumber(total)}</td>
                  <td>${formatNumber(file.error_count)}</td>
                  <td>${escapeHtml(file.last_used_at)}</td>
                  <td><button class="text-button" data-use-path="${escapeHtml(file.path)}" type="button">填入</button></td>
                </tr>
              `;
            })
            .join("")}
        </tbody>
      </table>
    </div>
  `;
  container.querySelectorAll("[data-use-path]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector("#workbook-path").value = button.dataset.usePath;
      document.querySelector("#workbook-file").value = "";
      document.querySelector("#selected-workbook-name").textContent = `使用最近文件：${button.dataset.usePath}`;
    });
  });
}

async function renderAudit() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法执行稽核。");
    return;
  }
  state.issueLimit = state.issueLimit || 50;
  state.issueOffset = 0;
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("稽核结果", "问题清单", renderBatchSelector())}
      <div id="issue-summary" class="issue-summary-panel">正在加载问题摘要</div>
      <div class="quick-filter-bar">
        <button class="segmented-button is-active" type="button" data-issue-view="list">明细视图</button>
        <button class="segmented-button" type="button" data-issue-view="groups">聚合视图</button>
      </div>
      <div class="quick-filter-bar">
        <button class="segmented-button is-active" type="button" data-quick-filter="all">全部问题</button>
        <button class="segmented-button" type="button" data-quick-filter="high">高风险优先</button>
        <button class="segmented-button" type="button" data-quick-filter="needs_review">待复核</button>
        <button class="segmented-button" type="button" data-quick-filter="open">未闭环</button>
        <button class="segmented-button" type="button" data-quick-filter="closed">已闭环</button>
      </div>
      <div class="filter-grid audit-filter-grid">
        <input id="filter-city" placeholder="地市">
        <select id="filter-ledger"><option value="">全部台账</option><option value="site">站址</option><option value="tower_rent">铁塔租费</option><option value="electricity">电费</option><option value="generator">发电费</option></select>
        <select id="filter-rule"><option value="">全部规则</option></select>
        <select id="filter-status"><option value="">全部状态</option><option value="pending_export">待导出</option><option value="pending_correction">待整改</option><option value="returned">已回传</option><option value="still_invalid">仍异常</option><option value="needs_review">待人工复核</option><option value="closed">已关闭</option><option value="not_required">无需整改</option></select>
        <button id="run-audit" class="primary-button" type="button">执行稽核</button>
        <button id="load-issues" class="secondary-button" type="button">查询问题</button>
      </div>
      <div id="issue-table-wrap" class="table-wrap issue-table-wrap"></div>
      <div class="pagination-bar">
        <button id="issue-prev-page" class="secondary-button" type="button">上一页</button>
        <span id="issue-page-info">第 1 页</span>
        <button id="issue-next-page" class="secondary-button" type="button">下一页</button>
      </div>
    </section>
  `;
  bindBatchSelector(renderAudit);
  state.issueView = state.issueView || "list";
  renderIssueTableShell();
  document.querySelector("#run-audit").addEventListener("click", async (event) => {
    await withBusy(event.currentTarget, "稽核中...", async () => {
      await postJson("/api/audit", { batch_id: state.batchId });
      await loadIssues();
    });
  });
  document.querySelector("#load-issues").addEventListener("click", async () => {
    state.issueOffset = 0;
    await loadIssues();
  });
  document.querySelectorAll("[data-quick-filter]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.issueQuickFilter = button.dataset.quickFilter;
      document.querySelectorAll("[data-quick-filter]").forEach((item) => item.classList.toggle("is-active", item === button));
      applyQuickIssueFilter(state.issueQuickFilter);
      state.issueOffset = 0;
      await loadIssues();
    });
  });
  document.querySelectorAll("[data-issue-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.issueView = button.dataset.issueView;
      document.querySelectorAll("[data-issue-view]").forEach((item) => item.classList.toggle("is-active", item === button));
      renderIssueTableShell();
      state.issueOffset = 0;
      await loadIssues();
    });
  });
  ["#filter-city", "#filter-ledger", "#filter-rule", "#filter-status"].forEach((selector) => {
    const control = document.querySelector(selector);
    if (!control) return;
    const eventName = selector === "#filter-city" ? "change" : "change";
    control.addEventListener(eventName, async () => {
      state.issueOffset = 0;
      await loadIssues();
    });
  });
  document.querySelector("#issue-prev-page").addEventListener("click", async () => {
    state.issueOffset = Math.max(0, Number(state.issueOffset || 0) - Number(state.issueLimit || 50));
    await loadIssues();
  });
  document.querySelector("#issue-next-page").addEventListener("click", async () => {
    state.issueOffset = Number(state.issueOffset || 0) + Number(state.issueLimit || 50);
    await loadIssues();
  });
  await loadIssues();
}

async function loadIssues() {
  if (!state.batchId) {
    renderNoBatchPrompt("没有批次时无法查询问题。");
    return;
  }
  const limit = Number(state.issueLimit || 50);
  const offset = Number(state.issueOffset || 0);
  const params = new URLSearchParams({ batch_id: state.batchId, limit, offset });
  const city = fieldValue("filter-city");
  const ledger = document.querySelector("#filter-ledger").value;
  const rule = document.querySelector("#filter-rule").value;
  const status = document.querySelector("#filter-status").value;
  const severity = state.issueQuickFilter === "high" ? "high" : "";
  const closure = ["open", "closed"].includes(state.issueQuickFilter) ? state.issueQuickFilter : "";
  if (city) params.set("city", city);
  if (ledger) params.set("ledger_type", ledger);
  if (rule) params.set("rule_id", rule);
  if (status) params.set("status", status);
  if (severity) params.set("severity", severity);
  if (closure) params.set("closure", closure);
  if (state.issueView === "groups") {
    const groupData = await fetchJson(`/api/issue-groups?${params.toString()}`);
    renderIssueGroupSummary(groupData.groups || []);
    renderIssueGroupRows(groupData.groups || []);
    renderIssuePagination(0, limit, 0);
    return;
  }
  const data = await fetchJson(`/api/issues?${params.toString()}`);
  renderIssueRuleOptions(data.rules || [], rule);
  renderIssueSummary(data.issues || [], data.total || 0);
  renderIssueRows(data.issues || []);
  renderIssuePagination(data.total || 0, data.limit || limit, data.offset || offset);
}

function renderIssueTableShell() {
  const wrap = document.querySelector("#issue-table-wrap");
  if (!wrap) return;
  if (state.issueView === "groups") {
    wrap.innerHTML = `<table class="issue-table"><thead><tr><th>地市</th><th>站址</th><th>台账</th><th>规则</th><th>风险</th><th>问题数</th><th>未闭环</th><th>状态构成</th><th>操作</th></tr></thead><tbody id="issue-table"><tr><td colspan="9">正在加载聚合问题</td></tr></tbody></table>`;
  } else {
    wrap.innerHTML = `<table class="issue-table"><thead><tr><th>问题编号</th><th>地市</th><th>台账</th><th>规则</th><th>风险</th><th>可信度</th><th>状态</th><th>说明</th><th>操作</th></tr></thead><tbody id="issue-table"><tr><td colspan="9">点击查询问题</td></tr></tbody></table>`;
  }
}

function renderIssueGroupSummary(groups) {
  const total = groups.reduce((sum, group) => sum + Number(group.issue_count || 0), 0);
  const open = groups.reduce((sum, group) => sum + Number(group.open_count || 0), 0);
  const review = groups.reduce((sum, group) => sum + Number(group.review_count || 0), 0);
  const container = document.querySelector("#issue-summary");
  if (!container) return;
  container.innerHTML = `
    <span>聚合组 ${formatNumber(groups.length)}</span>
    <span>问题总数 ${formatNumber(total)}</span>
    <span>未闭环 ${formatNumber(open)}</span>
    <span>待复核 ${formatNumber(review)}</span>
  `;
}

function renderIssueGroupRows(groups) {
  const tbody = document.querySelector("#issue-table");
  if (!groups.length) {
    tbody.innerHTML = '<tr><td colspan="9">当前筛选条件下暂无聚合问题</td></tr>';
    return;
  }
  tbody.innerHTML = groups
    .map((group) => `
      <tr>
        <td>${escapeHtml(group.city)}</td>
        <td><strong>${escapeHtml(group.telecom_site_code || "未填编码")}</strong><p class="table-note">${escapeHtml(group.telecom_site_name || "")}</p></td>
        <td>${escapeHtml(ledgerLabel(group.ledger_type))}</td>
        <td class="rule-cell"><strong>${escapeHtml(group.rule_name || group.rule_id)}</strong><span>${escapeHtml(group.rule_id)}</span></td>
        <td><span class="chip chip-${severityTone(group.severity)}">${escapeHtml(severityLabel(group.severity))}</span></td>
        <td>${formatNumber(group.issue_count)}</td>
        <td>${formatNumber(group.open_count)}</td>
        <td>
          <span class="mini-chip">待复核 ${formatNumber(group.review_count)}</span>
          <span class="mini-chip">仍异常 ${formatNumber(group.still_invalid_count)}</span>
          <span class="mini-chip">已关闭 ${formatNumber(group.closed_count)}</span>
          <span class="mini-chip">无需整改 ${formatNumber(group.not_required_count)}</span>
        </td>
        <td>
          <button class="text-button" data-group-status="closed" data-group='${escapeHtml(JSON.stringify(groupStatusPayload(group)))}' type="button">批量关闭</button>
          <button class="text-button" data-group-status="not_required" data-group='${escapeHtml(JSON.stringify(groupStatusPayload(group)))}' type="button">批量无需整改</button>
        </td>
      </tr>
    `)
    .join("");
  tbody.querySelectorAll("[data-group-status]").forEach((button) => {
    button.addEventListener("click", async () => {
      const group = JSON.parse(button.dataset.group);
      await postJson("/api/issues/group-status", { batch_id: state.batchId, group, status: button.dataset.groupStatus });
      await loadIssues();
    });
  });
}

function groupStatusPayload(group) {
  return {
    city: group.city,
    ledger_type: group.ledger_type,
    rule_id: group.rule_id,
    telecom_site_code: group.telecom_site_code || "",
  };
}

function applyQuickIssueFilter(filter) {
  const status = document.querySelector("#filter-status");
  if (!status) return;
  if (filter === "needs_review") status.value = "needs_review";
  if (filter === "all" || filter === "high" || filter === "open" || filter === "closed") status.value = "";
}

function renderIssueSummary(issues, total) {
  const container = document.querySelector("#issue-summary");
  if (!container) return;
  const high = issues.filter((issue) => issue.severity === "high").length;
  const review = issues.filter((issue) => issue.status === "needs_review").length;
  const open = issues.filter((issue) => !["closed", "not_required"].includes(issue.status)).length;
  container.innerHTML = `
    <span>当前筛选共 ${formatNumber(total)} 条</span>
    <span>本页高风险 ${formatNumber(high)}</span>
    <span>本页未闭环 ${formatNumber(open)}</span>
    <span>本页待复核 ${formatNumber(review)}</span>
  `;
}

function renderIssueRuleOptions(rules, selectedRule) {
  const select = document.querySelector("#filter-rule");
  if (!select) return;
  const options = ['<option value="">全部规则</option>'].concat(
    rules.map((rule) => {
      const selected = rule.rule_id === selectedRule ? "selected" : "";
      const label = `${rule.rule_name || rule.rule_id} (${formatNumber(rule.issue_count)})`;
      return `<option value="${escapeHtml(rule.rule_id)}" ${selected}>${escapeHtml(label)}</option>`;
    }),
  );
  select.innerHTML = options.join("");
}

function renderIssuePagination(total, limit, offset) {
  const info = document.querySelector("#issue-page-info");
  const prev = document.querySelector("#issue-prev-page");
  const next = document.querySelector("#issue-next-page");
  if (!info || !prev || !next) return;
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  info.textContent = `第 ${currentPage} / ${totalPages} 页，共 ${formatNumber(total)} 条`;
  prev.disabled = offset <= 0;
  next.disabled = offset + limit >= total;
}

function renderIssueRows(issues) {
  const tbody = document.querySelector("#issue-table");
  if (!issues.length) {
    tbody.innerHTML = '<tr><td colspan="9">当前筛选条件下暂无问题</td></tr>';
    return;
  }
  tbody.innerHTML = issues
    .map(
      (issue) => `
        <tr>
          <td>${escapeHtml(issue.issue_code)}</td>
          <td>${escapeHtml(issue.city)}</td>
          <td>${escapeHtml(ledgerLabel(issue.ledger_type))}</td>
          <td class="rule-cell"><strong>${escapeHtml(issue.rule_name || issue.rule_id)}</strong><span>${escapeHtml(issue.rule_id)}</span></td>
          <td><span class="chip chip-${severityTone(issue.severity)}">${escapeHtml(severityLabel(issue.severity))}</span></td>
          <td><span class="chip chip-${confidenceTone(issue.confidence)}">${escapeHtml(issue.confidence_label || "疑似问题")}</span></td>
          <td><span class="chip chip-${statusTone(issue.status)}">${escapeHtml(statusLabel(issue.status))}</span></td>
          <td class="message-cell">
            <strong>${escapeHtml(issue.explanation?.what_happened || issue.message)}</strong>
            <span>${escapeHtml(issue.explanation?.judgement_basis || "")}</span>
            <span>证据：${escapeHtml(issue.evidence?.field || "规则命中")} ${issue.evidence?.value !== undefined && issue.evidence?.value !== null ? `= ${escapeHtml(String(issue.evidence.value))}` : ""}</span>
            <span>${escapeHtml(issue.group?.label || "单条问题")}${Number(issue.group?.same_site_rule_count || 0) > 1 ? ` · ${formatNumber(issue.group.same_site_rule_count)} 条` : ""}</span>
            <span>建议：${escapeHtml(issue.explanation?.recommended_action || issue.suggestion || "")}</span>
            <span>复核：${escapeHtml(issue.review_suggestion?.decision || "")} · ${escapeHtml(issue.review_suggestion?.reason || "")}</span>
          </td>
          <td>
            <button class="text-button" data-close="${escapeHtml(issue.issue_code)}" type="button">关闭</button>
            <button class="text-button" data-not-required="${escapeHtml(issue.issue_code)}" type="button">无需整改</button>
          </td>
        </tr>
      `,
    )
    .join("");
  tbody.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", async () => {
      await postJson("/api/issues/status", { issue_code: button.dataset.close, status: "closed" });
      await loadIssues();
    });
  });
  tbody.querySelectorAll("[data-not-required]").forEach((button) => {
    button.addEventListener("click", async () => {
      await postJson("/api/issues/status", { issue_code: button.dataset.notRequired, status: "not_required" });
      await loadIssues();
    });
  });
}

function confidenceTone(confidence) {
  if (confidence === "high") return "danger";
  if (confidence === "low") return "info";
  return "warning";
}

async function renderExport() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法导出整改包。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("问题包导出", "整改包")}
      <div class="operation-panel">
        <p>按当前批次生成整改问题清单。导出整改包会更新问题状态为待整改，请确认已完成稽核且问题清单无误。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>批次号</span>
            <input id="export-batch-id" type="number" value="${escapeHtml(String(state.batchId))}" inputmode="numeric">
          </label>
          <label class="form-field">
            <span>导出方式</span>
            <select id="export-mode">
              <option value="city">分地市单独文件</option>
              <option value="province">全省汇总一个文件</option>
            </select>
          </label>
        </div>
        <button id="operation-submit" class="primary-button" type="button">导出整改包</button>
      </div>
    </section>
    <section class="card">
      ${shellHeader("导出路径", "处理结果")}
      <div id="operation-result" class="result-box">等待操作</div>
    </section>
  `;
  document.querySelector("#operation-submit").addEventListener("click", async (event) => {
    const batchId = Number(fieldValue("export-batch-id"));
    if (!Number.isInteger(batchId) || batchId <= 0) return setOperationResult("error", "请填写有效批次号");
    if (!window.confirm("导出整改包会更新问题状态为待整改，确认继续？")) return;
    const mode = document.querySelector("#export-mode").value;
    await withBusy(event.currentTarget, "导出中...", async () => {
      setOperationResult("pending", "正在导出...");
      try {
        const data = await postJson("/api/export", { batch_id: batchId, mode });
        if (data.paths?.length) {
          setOperationHtml("success", resultList(data.paths));
        } else {
          setOperationResult("success", "当前批次没有可导出的问题");
        }
      } catch (error) {
        setOperationResult("error", error.message);
      }
    });
  });
}

async function renderCorrections() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法导入整改回传。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("整改回传", "回传导入")}
      <div class="operation-panel">
        <p>选择地市回传的整改问题清单 Excel，系统会按问题编号匹配并更新整改状态。</p>
        <div class="form-grid">
          <label class="form-field">
            <span>回传文件</span>
            <input id="correction-file" type="file" accept=".xlsx,.xlsm,.xltx,.xltm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12">
            <small id="selected-correction-name" class="field-hint">尚未选择文件</small>
          </label>
        </div>
        <button id="operation-submit" class="primary-button" type="button">导入回传</button>
      </div>
    </section>
    <section class="card">
      ${shellHeader("回传结果", "处理结果")}
      <div id="operation-result" class="result-box">请选择文件后导入</div>
    </section>
    <section class="card">
      ${shellHeader("地市整改进度", "地市进度")}
      <div class="table-wrap"><table><thead><tr><th>地市</th><th>问题</th><th>待整改</th><th>待复核</th><th>已关闭</th><th>高频规则</th><th>完成率</th><th>状态</th></tr></thead><tbody id="city-progress-table"></tbody></table></div>
    </section>
  `;
  document.querySelector("#correction-file").addEventListener("change", () => {
    const file = document.querySelector("#correction-file")?.files?.[0] || null;
    document.querySelector("#selected-correction-name").textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "尚未选择文件";
  });
  document.querySelector("#operation-submit").addEventListener("click", async (event) => {
    const file = document.querySelector("#correction-file")?.files?.[0] || null;
    if (!file) return setOperationResult("error", "请选择整改回传 Excel 文件");
    await withBusy(event.currentTarget, "导入中...", async () => {
      setOperationResult("pending", "正在导入回传...");
      try {
        const formData = new FormData();
        formData.append("file", file);
        const data = await postFormData("/api/corrections/upload", formData);
        setOperationHtml("success", renderCorrectionImportResult(data));
        const progress = await fetchJson(`/api/city-progress?batch_id=${state.batchId}`);
        renderCityProgress(progress.cities || []);
      } catch (error) {
        setOperationResult("error", error.message);
      }
    });
  });
  const progress = await fetchJson(`/api/city-progress?batch_id=${state.batchId}`).catch(() => ({ cities: [] }));
  renderCityProgress(progress.cities || []);
}

function renderCorrectionImportResult(data) {
  const errors = data.errors || [];
  const warnings = data.review_warnings || [];
  const review = data.auto_review || {};
  return `
    <p><strong>匹配问题数：${formatNumber(data.matched_count)}</strong></p>
    <div class="mini-grid">
      <span>待复核 ${formatNumber(review.needs_review || 0)}</span>
      <span>仍异常 ${formatNumber(review.still_invalid || 0)}</span>
      <span>无需整改 ${formatNumber(review.not_required || 0)}</span>
    </div>
    ${warnings.length ? `<div class="error-group"><strong>复核提醒</strong><ul class="path-list">${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
    ${errors.length ? `<div class="error-group"><strong>导入错误</strong><ul class="path-list">${errors.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
    ${!warnings.length && !errors.length ? "<p>回传已导入，问题已进入待复核。</p>" : ""}
  `;
}

async function renderReports() {
  return renderAnalytics({
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
  });
}

async function checkHealth() {
  setStatus("pending", "连接中");
  try {
    await fetchJson("/api/health");
    setStatus("online", "本地服务正常");
  } catch {
    setStatus("offline", "本地服务异常");
  }
}

function activateView(view) {
  pageTitle.textContent = views[view];
  navButtons.forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
  if (view === "dashboard") return loadDashboard();
  if (view === "batches") return renderBatches();
  if (view === "import") return renderImport();
  if (view === "ledgerData")
    return renderLedgerData({
      mainContent,
      refreshBatches,
      currentBatch,
      renderNoBatchPrompt,
      shellHeader,
      renderBatchSelector,
      bindBatchSelector,
      fieldValue,
      ledgerLabel,
    });
  if (view === "rules") return renderRules({ mainContent, shellHeader, state, refreshBatches });
  if (view === "audit") return renderAudit();
  if (view === "export") return renderExport();
  if (view === "corrections") return renderCorrections();
  if (view === "electricityAnalysis")
    return renderElectricityAnalysis({
      mainContent,
      state,
      refreshBatches,
      currentBatch,
      renderNoBatchPrompt,
      renderBatchSelector,
      bindBatchSelector,
      shellHeader,
      metricCard,
      escapeHtml,
      formatNumber,
      withBusy,
    });
  if (view === "reports") return renderReports();
  if (view === "settings") return renderSettings({ mainContent, shellHeader });
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});

checkHealth();
activateView("dashboard");
