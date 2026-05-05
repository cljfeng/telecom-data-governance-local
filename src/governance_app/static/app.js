const views = {
  dashboard: "专项工作台",
  import: "数据导入",
  audit: "稽核结果",
  export: "问题包导出",
  corrections: "整改回传",
  reports: "分析报表",
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
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

function setStatus(state, text) {
  statusPill.textContent = text;
  statusPill.className = `status status-${state}`;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
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

function metricCard(label, value, note) {
  return `
    <article class="metric-card">
      <p class="metric-label">${escapeHtml(label)}</p>
      <p class="metric-value">${formatNumber(value)}</p>
      <p class="metric-note">${escapeHtml(note)}</p>
    </article>
  `;
}

function renderDashboardShell() {
  mainContent.innerHTML = `
    <section class="card">
      <div class="section-header">
        <div>
          <p class="eyebrow">Batch #1</p>
          <h2>专项概览</h2>
        </div>
      </div>
      <div id="metric-grid" class="metric-grid">
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
        <div class="metric-card skeleton"></div>
      </div>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <p class="eyebrow">Issues</p>
          <h2>地市问题分布</h2>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>地市</th>
              <th>问题数量</th>
              <th>占比</th>
            </tr>
          </thead>
          <tbody id="city-issue-table">
            <tr>
              <td colspan="3">正在加载工作台数据</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function renderDashboard(data) {
  const ledgerCounts = data.ledger_counts || {};
  const cityRows = data.issues_by_city || [];
  const ruleRows = data.issues_by_rule || [];
  const totalLedgers = Object.values(ledgerCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const totalIssues = cityRows.reduce((sum, row) => sum + Number(row.count || 0), 0);
  const topCity = cityRows[0]?.city || "暂无";
  const topRule = ruleRows[0]?.rule_id || "暂无";

  document.querySelector("#metric-grid").innerHTML = [
    metricCard("台账记录", totalLedgers, "当前批次导入记录总量"),
    metricCard("问题总数", totalIssues, "稽核规则命中的问题数量"),
    metricCard("涉及地市", cityRows.length, `问题最多：${topCity}`),
    metricCard("命中规则", ruleRows.length, `最高频规则：${topRule}`),
  ].join("");

  const tbody = document.querySelector("#city-issue-table");
  if (!cityRows.length) {
    tbody.innerHTML = '<tr><td colspan="3">当前批次暂无问题分布数据</td></tr>';
    return;
  }

  tbody.innerHTML = cityRows
    .map((row) => {
      const count = Number(row.count || 0);
      const ratio = totalIssues ? `${((count / totalIssues) * 100).toFixed(1)}%` : "0.0%";
      return `
        <tr>
          <td>${escapeHtml(row.city || "未填地市")}</td>
          <td>${formatNumber(count)}</td>
          <td>${ratio}</td>
        </tr>
      `;
    })
    .join("");
}

function renderPlaceholder(view) {
  mainContent.innerHTML = `
    <section class="card placeholder">
      <p class="eyebrow">Local Module</p>
      <h2>${views[view]}</h2>
      <p>该模块将在本地服务中承载对应流程。当前版本先提供静态入口，便于专项治理人员在同一界面中切换工作区。</p>
    </section>
  `;
}

function operationCard({ title, eyebrow, description, fields, buttonText, resultTitle }) {
  const fieldHtml = fields
    .map(
      (field) => `
        <label class="form-field">
          <span>${escapeHtml(field.label)}</span>
          <input
            id="${escapeHtml(field.id)}"
            type="${escapeHtml(field.type || "text")}"
            value="${escapeHtml(field.value || "")}"
            placeholder="${escapeHtml(field.placeholder || "")}"
            inputmode="${escapeHtml(field.inputmode || "text")}"
          >
        </label>
      `,
    )
    .join("");

  mainContent.innerHTML = `
    <section class="card">
      <div class="section-header">
        <div>
          <p class="eyebrow">${escapeHtml(eyebrow)}</p>
          <h2>${escapeHtml(title)}</h2>
        </div>
      </div>
      <div class="operation-panel">
        <p>${escapeHtml(description)}</p>
        <div class="form-grid">${fieldHtml}</div>
        <button id="operation-submit" class="primary-button" type="button">${escapeHtml(buttonText)}</button>
      </div>
    </section>

    <section class="card">
      <div class="section-header">
        <div>
          <p class="eyebrow">Result</p>
          <h2>${escapeHtml(resultTitle)}</h2>
        </div>
      </div>
      <div id="operation-result" class="result-box">等待操作</div>
    </section>
  `;
}

function fieldValue(id) {
  return document.querySelector(`#${id}`).value.trim();
}

function setOperationResult(state, content) {
  const result = document.querySelector("#operation-result");
  result.className = `result-box result-${state}`;
  if (typeof content === "string") {
    result.textContent = content;
    return;
  }
  result.innerHTML = content;
}

function resultList(items) {
  if (!items?.length) {
    return "<p>无返回明细</p>";
  }
  return `
    <ul class="path-list">
      ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ul>
  `;
}

function renderImport() {
  operationCard({
    title: "数据导入",
    eyebrow: "Workbook Import",
    description: "输入本机 Excel 台账文件完整路径，系统会校验四类台账模板并写入本地数据库。",
    fields: [
      {
        id: "workbook-path",
        label: "台账文件路径",
        placeholder: "/Users/.../附件：基站电费、租费基础数据治理台账模板.xlsx",
      },
    ],
    buttonText: "导入台账",
    resultTitle: "导入结果",
  });

  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const path = fieldValue("workbook-path");
    if (!path) {
      setOperationResult("error", "请填写台账文件路径");
      return;
    }
    setOperationResult("pending", "正在导入...");
    try {
      const data = await postJson("/api/import", { path });
      const counts = data.ledger_counts || {};
      setOperationResult(
        "success",
        `
          <p>导入成功，批次号：<strong>${escapeHtml(data.batch_id)}</strong></p>
          <div class="mini-grid">
            <span>站址 ${formatNumber(counts.site)}</span>
            <span>铁塔租费 ${formatNumber(counts.tower_rent)}</span>
            <span>电费 ${formatNumber(counts.electricity)}</span>
            <span>发电费 ${formatNumber(counts.generator)}</span>
          </div>
        `,
      );
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
}

function renderAudit() {
  operationCard({
    title: "执行稽核",
    eyebrow: "Audit",
    description: "输入导入批次号，执行内置基础质量和费用异常规则，生成问题清单。",
    fields: [{ id: "audit-batch-id", label: "批次号", type: "number", value: "1", inputmode: "numeric" }],
    buttonText: "执行稽核",
    resultTitle: "稽核结果",
  });

  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const batchId = Number(fieldValue("audit-batch-id"));
    if (!Number.isInteger(batchId) || batchId <= 0) {
      setOperationResult("error", "请填写有效批次号");
      return;
    }
    setOperationResult("pending", "正在执行稽核...");
    try {
      const data = await postJson("/api/audit", { batch_id: batchId });
      setOperationResult("success", `稽核完成，运行号：${data.audit_run_id}，规则命中：${formatNumber(data.issue_count)}`);
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
}

function renderExport() {
  operationCard({
    title: "问题包导出",
    eyebrow: "Export",
    description: "输入批次号，按地市生成整改问题清单 Excel 文件。",
    fields: [{ id: "export-batch-id", label: "批次号", type: "number", value: "1", inputmode: "numeric" }],
    buttonText: "导出整改包",
    resultTitle: "导出路径",
  });

  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const batchId = Number(fieldValue("export-batch-id"));
    if (!Number.isInteger(batchId) || batchId <= 0) {
      setOperationResult("error", "请填写有效批次号");
      return;
    }
    setOperationResult("pending", "正在导出...");
    try {
      const data = await postJson("/api/export", { batch_id: batchId });
      setOperationResult("success", data.paths?.length ? resultList(data.paths) : "当前批次没有可导出的问题");
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
}

function renderCorrections() {
  operationCard({
    title: "整改回传",
    eyebrow: "Correction Return",
    description: "输入地市回传的整改问题清单路径，系统会按问题编号匹配并更新状态。",
    fields: [{ id: "correction-path", label: "回传文件路径", placeholder: "/Users/.../杭州_整改问题清单_批次1.xlsx" }],
    buttonText: "导入回传",
    resultTitle: "回传结果",
  });

  document.querySelector("#operation-submit").addEventListener("click", async () => {
    const path = fieldValue("correction-path");
    if (!path) {
      setOperationResult("error", "请填写回传文件路径");
      return;
    }
    setOperationResult("pending", "正在导入回传...");
    try {
      const data = await postJson("/api/corrections", { path });
      setOperationResult("success", `匹配问题数：${formatNumber(data.matched_count)}${data.errors?.length ? `；错误：${data.errors.map(escapeHtml).join("；")}` : ""}`);
    } catch (error) {
      setOperationResult("error", error.message);
    }
  });
}

async function loadDashboard() {
  renderDashboardShell();
  try {
    const data = await fetchJson("/api/dashboard?batch_id=1");
    renderDashboard(data);
  } catch (error) {
    document.querySelector("#metric-grid").innerHTML = `
      <div class="empty-state">工作台数据加载失败：${error.message}</div>
    `;
    document.querySelector("#city-issue-table").innerHTML = `
      <tr><td colspan="3">无法读取地市问题分布</td></tr>
    `;
  }
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
  navButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === view);
  });

  if (view === "dashboard") {
    loadDashboard();
    return;
  }
  if (view === "import") {
    renderImport();
    return;
  }
  if (view === "audit") {
    renderAudit();
    return;
  }
  if (view === "export") {
    renderExport();
    return;
  }
  if (view === "reports") {
    loadDashboard();
    return;
  }
  if (view === "corrections") {
    renderCorrections();
    return;
  }
  renderPlaceholder(view);
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});

checkHealth();
activateView("dashboard");
