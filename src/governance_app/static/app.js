const views = {
  dashboard: "专项工作台",
  batches: "批次管理",
  import: "数据导入",
  audit: "稽核结果",
  export: "问题包导出",
  corrections: "整改回传",
  reports: "分析报表",
};

const state = {
  batchId: null,
  batches: [],
};

const pageTitle = document.querySelector("#page-title");
const mainContent = document.querySelector("#main-content");
const statusPill = document.querySelector("#service-status");
const navButtons = Array.from(document.querySelectorAll(".nav-button"));

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    const entities = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return entities[character];
  });
}

function setStatus(stateName, text) {
  statusPill.textContent = text;
  statusPill.className = `status status-${stateName}`;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

async function postJson(url, payload) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function refreshBatches() {
  const data = await fetchJson("/api/batches");
  state.batches = data.batches || [];
  const current = state.batches.find((batch) => batch.is_current) || state.batches[0];
  if (current) {
    state.batchId = current.id;
  } else {
    state.batchId = null;
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
    .map((batch) => `<option value="${batch.id}" ${batch.id === state.batchId ? "selected" : ""}>#${batch.id} ${escapeHtml(batch.name)}</option>`)
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

function metricCard(label, value, note) {
  return `
    <article class="metric-card">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${formatNumber(value)}</p>
      <p class="metric-note">${escapeHtml(note)}</p>
    </article>
  `;
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

function setOperationResult(stateName, content) {
  const result = document.querySelector("#operation-result");
  result.className = `result-box result-${stateName}`;
  if (typeof content === "string") {
    result.textContent = content;
  } else {
    result.innerHTML = content;
  }
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
      ${shellHeader(resultTitle, "Result")}
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
      ${shellHeader("没有可用批次", "Batch")}
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
    <section class="card">
      ${shellHeader("专项治理流程", batch ? `Batch #${batch.id}` : "Batch", renderBatchSelector())}
      <div id="workflow-area" class="workflow-area">正在加载流程</div>
    </section>
    <section class="card">
      ${shellHeader("专项概览", "Summary")}
      <div id="metric-grid" class="metric-grid">
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
      </div>
    </section>
    <section class="card">
      ${shellHeader("地市整改进度", "City Progress")}
      <div class="table-wrap"><table><thead><tr><th>地市</th><th>问题</th><th>待整改</th><th>待复核</th><th>已关闭</th><th>完成率</th></tr></thead><tbody id="city-progress-table"><tr><td colspan="6">正在加载</td></tr></tbody></table></div>
    </section>
  `;
  bindBatchSelector(loadDashboard);
  try {
    const [workflow, summary, progress] = await Promise.all([
      fetchJson(`/api/workflow?batch_id=${state.batchId}`),
      fetchJson(`/api/dashboard?batch_id=${state.batchId}`),
      fetchJson(`/api/city-progress?batch_id=${state.batchId}`),
    ]);
    renderWorkflow(workflow);
    renderMetrics(summary);
    renderCityProgress(progress.cities || []);
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
    ].join("");
    renderCityProgress([]);
  }
}

function renderEmptyDashboard() {
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("专项治理流程", "Batch")}
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
    <section class="card">
      ${shellHeader("专项概览", "Summary")}
      <div class="metric-grid">
        ${metricCard("台账记录", 0, "等待导入台账")}
        ${metricCard("问题总数", 0, "等待执行稽核")}
        ${metricCard("涉及地市", 0, "等待生成问题")}
        ${metricCard("命中规则", 0, "等待执行稽核")}
      </div>
    </section>
    <section class="card">
      ${shellHeader("地市整改进度", "City Progress")}
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
      ${shellHeader("批次管理", "Batches")}
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
      ${shellHeader("历史批次", "History")}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>批次</th>
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
    tbody.innerHTML = '<tr><td colspan="6">暂无批次，可以先新建批次或直接导入台账。</td></tr>';
    return;
  }
  tbody.innerHTML = state.batches
    .map(
      (batch) => `
        <tr>
          <td>#${batch.id} ${escapeHtml(batch.name)}</td>
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
  document.querySelector("#workflow-area").innerHTML = `
    <div class="workflow-steps">
      ${workflow.steps
        .map((step) => `<div class="workflow-step step-${step.state}"><span>${escapeHtml(step.label)}</span></div>`)
        .join("")}
    </div>
    <div class="next-action">下一步：<strong>${escapeHtml(workflow.next_action)}</strong></div>
    <div class="operation-log">
      ${(workflow.operations || []).map((item) => `<p><strong>${escapeHtml(item.operation)}</strong> ${escapeHtml(item.message)} <span>${escapeHtml(item.created_at)}</span></p>`).join("") || "<p>暂无操作记录</p>"}
    </div>
  `;
}

function renderMetrics(data) {
  const ledgerCounts = data.ledger_counts || {};
  const cityRows = data.issues_by_city || [];
  const ruleRows = data.issues_by_rule || [];
  const totalLedgers = Object.values(ledgerCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const totalIssues = cityRows.reduce((sum, row) => sum + Number(row.count || 0), 0);
  document.querySelector("#metric-grid").innerHTML = [
    metricCard("台账记录", totalLedgers, "当前批次导入记录总量"),
    metricCard("问题总数", totalIssues, "稽核规则命中的问题数量"),
    metricCard("涉及地市", cityRows.length, `问题最多：${cityRows[0]?.city || "暂无"}`),
    metricCard("命中规则", ruleRows.length, `最高频规则：${ruleRows[0]?.rule_id || "暂无"}`),
  ].join("");
}

function renderCityProgress(rows) {
  const tbody = document.querySelector("#city-progress-table");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6">当前批次暂无整改进度</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.city)}</td>
          <td>${formatNumber(row.total_count)}</td>
          <td>${formatNumber(row.pending_count)}</td>
          <td>${formatNumber(row.review_count)}</td>
          <td>${formatNumber(Number(row.closed_count || 0) + Number(row.not_required_count || 0))}</td>
          <td><span class="progress-pill">${escapeHtml(row.completion_rate)}%</span></td>
        </tr>
      `,
    )
    .join("");
}

async function renderImport() {
  await refreshBatches().catch(() => []);
  operationCard({
    title: "数据导入",
    eyebrow: "Workbook Import",
    description: "输入本机 Excel 台账文件完整路径，系统会校验四类台账模板并写入本地数据库，同时自动设为当前批次。",
    fields: [{ id: "workbook-path", label: "台账文件路径", placeholder: "/Users/.../附件：基站电费、租费基础数据治理台账模板.xlsx" }],
    buttonText: "导入台账",
    resultTitle: "导入结果",
  });
  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const path = fieldValue("workbook-path");
    if (!path) return setOperationResult("error", "请填写台账文件路径");
    setOperationResult("pending", "正在导入...");
    try {
      const data = await postJson("/api/import", { path });
      state.batchId = data.batch_id;
      await refreshBatches();
      const counts = data.ledger_counts || {};
      setOperationResult("success", `<p>导入成功，批次号：<strong>${escapeHtml(data.batch_id)}</strong></p><div class="mini-grid"><span>站址 ${formatNumber(counts.site)}</span><span>铁塔租费 ${formatNumber(counts.tower_rent)}</span><span>电费 ${formatNumber(counts.electricity)}</span><span>发电费 ${formatNumber(counts.generator)}</span></div>`);
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
}

async function renderAudit() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法执行稽核。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("稽核结果", "Issues", renderBatchSelector())}
      <div class="filter-grid">
        <input id="filter-city" placeholder="地市">
        <select id="filter-ledger"><option value="">全部台账</option><option value="site">站址</option><option value="tower_rent">铁塔租费</option><option value="electricity">电费</option><option value="generator">发电费</option></select>
        <select id="filter-status"><option value="">全部状态</option><option value="pending_export">待导出</option><option value="pending_correction">待整改</option><option value="needs_review">待人工复核</option><option value="closed">已关闭</option><option value="not_required">无需整改</option></select>
        <button id="run-audit" class="primary-button" type="button">执行稽核</button>
        <button id="load-issues" class="secondary-button" type="button">查询问题</button>
      </div>
      <div class="table-wrap"><table><thead><tr><th>问题编号</th><th>地市</th><th>台账</th><th>规则</th><th>风险</th><th>状态</th><th>说明</th><th>操作</th></tr></thead><tbody id="issue-table"><tr><td colspan="8">点击查询问题</td></tr></tbody></table></div>
    </section>
  `;
  bindBatchSelector(renderAudit);
  document.querySelector("#run-audit").addEventListener("click", async () => {
    await postJson("/api/audit", { batch_id: state.batchId });
    await loadIssues();
  });
  document.querySelector("#load-issues").addEventListener("click", loadIssues);
  await loadIssues();
}

async function loadIssues() {
  if (!state.batchId) {
    renderNoBatchPrompt("没有批次时无法查询问题。");
    return;
  }
  const params = new URLSearchParams({ batch_id: state.batchId });
  const city = fieldValue("filter-city");
  const ledger = document.querySelector("#filter-ledger").value;
  const status = document.querySelector("#filter-status").value;
  if (city) params.set("city", city);
  if (ledger) params.set("ledger_type", ledger);
  if (status) params.set("status", status);
  const data = await fetchJson(`/api/issues?${params.toString()}`);
  renderIssueRows(data.issues || []);
}

function renderIssueRows(issues) {
  const tbody = document.querySelector("#issue-table");
  if (!issues.length) {
    tbody.innerHTML = '<tr><td colspan="8">当前筛选条件下暂无问题</td></tr>';
    return;
  }
  tbody.innerHTML = issues
    .map(
      (issue) => `
        <tr>
          <td>${escapeHtml(issue.issue_code)}</td>
          <td>${escapeHtml(issue.city)}</td>
          <td>${escapeHtml(issue.ledger_type)}</td>
          <td>${escapeHtml(issue.rule_id)}</td>
          <td>${escapeHtml(issue.severity)}</td>
          <td>${escapeHtml(issue.status)}</td>
          <td>${escapeHtml(issue.message)}</td>
          <td><button class="text-button" data-close="${escapeHtml(issue.issue_code)}" type="button">关闭</button></td>
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
}

async function renderExport() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法导出整改包。");
    return;
  }
  operationCard({
    title: "问题包导出",
    eyebrow: "Export",
    description: "按当前批次生成各地市整改包，并将相关问题状态更新为待整改。",
    fields: [{ id: "export-batch-id", label: "批次号", type: "number", value: String(state.batchId), inputmode: "numeric" }],
    buttonText: "导出整改包",
    resultTitle: "导出路径",
  });
  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const batchId = Number(fieldValue("export-batch-id"));
    if (!Number.isInteger(batchId) || batchId <= 0) return setOperationResult("error", "请填写有效批次号");
    setOperationResult("pending", "正在导出...");
    try {
      const data = await postJson("/api/export", { batch_id: batchId });
      setOperationResult("success", data.paths?.length ? resultList(data.paths) : "当前批次没有可导出的问题");
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
}

async function renderCorrections() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法导入整改回传。");
    return;
  }
  operationCard({
    title: "整改回传",
    eyebrow: "Correction Return",
    description: "导入地市回传的整改问题清单后，可在下方查看地市进度和待复核问题。",
    fields: [{ id: "correction-path", label: "回传文件路径", placeholder: "/Users/.../杭州_整改问题清单_批次1.xlsx" }],
    buttonText: "导入回传",
    resultTitle: "回传结果",
  });
  mainContent.insertAdjacentHTML("beforeend", `<section class="card">${shellHeader("地市整改进度", "Progress")}<div class="table-wrap"><table><thead><tr><th>地市</th><th>问题</th><th>待整改</th><th>待复核</th><th>已关闭</th><th>完成率</th></tr></thead><tbody id="city-progress-table"></tbody></table></div></section>`);
  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const path = fieldValue("correction-path");
    if (!path) return setOperationResult("error", "请填写回传文件路径");
    setOperationResult("pending", "正在导入回传...");
    try {
      const data = await postJson("/api/corrections", { path });
      setOperationResult("success", `匹配问题数：${formatNumber(data.matched_count)}${data.errors?.length ? `；错误：${data.errors.map(escapeHtml).join("；")}` : ""}`);
      const progress = await fetchJson(`/api/city-progress?batch_id=${state.batchId}`);
      renderCityProgress(progress.cities || []);
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
  const progress = await fetchJson(`/api/city-progress?batch_id=${state.batchId}`).catch(() => ({ cities: [] }));
  renderCityProgress(progress.cities || []);
}

async function renderReports() {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法生成归档汇总。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("专项归档导出", "Archive", renderBatchSelector())}
      <div class="operation-panel">
        <p>归档会生成当前批次的汇总 Excel，包括归档总览、地市整改进度和问题清单，并将批次标记为已归档。</p>
        <button id="archive-batch" class="primary-button" type="button">生成归档汇总</button>
      </div>
      <div id="operation-result" class="result-box">等待操作</div>
    </section>
  `;
  bindBatchSelector(renderReports);
  document.querySelector("#archive-batch").addEventListener("click", async () => {
    setOperationResult("pending", "正在生成归档汇总...");
    try {
      const data = await postJson("/api/archive", { batch_id: state.batchId });
      setOperationResult("success", resultList([data.path]));
    } catch (error) {
      setOperationResult("error", error.message);
    }
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
  if (view === "audit") return renderAudit();
  if (view === "export") return renderExport();
  if (view === "corrections") return renderCorrections();
  if (view === "reports") return renderReports();
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});

checkHealth();
activateView("dashboard");
