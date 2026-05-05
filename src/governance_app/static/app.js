const views = {
  dashboard: "专项工作台",
  import: "数据导入",
  audit: "稽核结果",
  export: "问题包导出",
  reports: "分析报表",
  settings: "本地设置",
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

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
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
  renderPlaceholder(view);
}

navButtons.forEach((button) => {
  button.addEventListener("click", () => activateView(button.dataset.view));
});

checkHealth();
activateView("dashboard");
