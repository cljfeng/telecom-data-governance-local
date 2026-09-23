import { fetchJson, postJson } from "/api.js?v=20260517-1";
import { state } from "/state.js?v=20260517-1";
import { escapeHtml } from "/ui.js?v=20260517-1";

const PAGE_SIZE = 24;
let ledgerOffset = 0;

export async function renderLedgerData({
  mainContent,
  refreshBatches,
  currentBatch,
  renderNoBatchPrompt,
  shellHeader,
  renderBatchSelector,
  bindBatchSelector,
  fieldValue,
  ledgerLabel,
}) {
  await refreshBatches().catch(() => []);
  if (!currentBatch()) {
    renderNoBatchPrompt("没有批次时无法查看台账数据。");
    return;
  }
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("数据整理", "台账明细", renderBatchSelector())}
      <div class="filter-grid">
        <select id="ledger-data-type">
          <option value="site">站址台账</option>
          <option value="tower_rent">铁塔租费台账</option>
          <option value="electricity">电费台账</option>
          <option value="generator">发电费台账</option>
        </select>
        <input id="ledger-data-city" placeholder="地市">
        <input id="ledger-data-site-code" placeholder="电信站址编码">
        <button id="load-ledger-data" class="primary-button" type="button">查询台账</button>
      </div>
      <div id="ledger-data-list" class="ledger-data-list empty-state">选择筛选条件后查询台账数据</div>
      <div id="ledger-data-pagination" class="pagination-bar" aria-label="台账分页"></div>
    </section>
  `;
  bindBatchSelector(() =>
    renderLedgerData({
      mainContent,
      refreshBatches,
      currentBatch,
      renderNoBatchPrompt,
      shellHeader,
      renderBatchSelector,
      bindBatchSelector,
      fieldValue,
      ledgerLabel,
    }),
  );
  document.querySelector("#load-ledger-data").addEventListener("click", () => {
    ledgerOffset = 0;
    loadLedgerData({ fieldValue, ledgerLabel });
  });
  await loadLedgerData({ fieldValue, ledgerLabel });
}

async function loadLedgerData({ fieldValue, ledgerLabel }) {
  const params = new URLSearchParams({ batch_id: state.batchId });
  const ledgerType = document.querySelector("#ledger-data-type").value;
  const city = fieldValue("ledger-data-city");
  const siteCode = fieldValue("ledger-data-site-code");
  if (ledgerType) params.set("ledger_type", ledgerType);
  if (city) params.set("city", city);
  if (siteCode) params.set("site_code", siteCode);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(ledgerOffset));
  const data = await fetchJson(`/api/ledger-rows?${params.toString()}`);
  renderLedgerDataRows(data.rows || [], ledgerLabel, data.total || 0);
  renderLedgerPagination(data, { fieldValue, ledgerLabel });
}

function renderLedgerDataRows(rows, ledgerLabel, total) {
  const container = document.querySelector("#ledger-data-list");
  if (!rows.length) {
    container.className = "ledger-data-list empty-state";
    container.textContent = "当前筛选条件下暂无台账记录";
    return;
  }
  container.className = "ledger-data-list";
  container.innerHTML = `
    <div class="data-count-bar">共 ${Number(total).toLocaleString("zh-CN")} 条台账记录，当前显示 ${rows.length} 条</div>
    ${rows
    .map(
      (row) => `
        <article class="ledger-row-card">
          <header>
            <div>
              <p class="eyebrow">${escapeHtml(ledgerLabel(row.ledger_type))}</p>
              <h3>${escapeHtml(row.telecom_site_name || "未命名站址")}</h3>
            </div>
            <span class="chip chip-info">${escapeHtml(row.city || "未填地市")}</span>
          </header>
          <div class="mini-grid">
            <span>站址编码 ${escapeHtml(row.telecom_site_code || "-")}</span>
            <span>区县 ${escapeHtml(row.district || "-")}</span>
            <span>铁塔编码 ${escapeHtml(row.tower_site_code || "-")}</span>
          </div>
          ${Object.entries(row.field_groups || {})
            .map(
              ([groupName, fields]) => `
                <details class="field-group" open>
                  <summary>${escapeHtml(groupName)} <span>${Object.keys(fields).length} 项</span></summary>
                  ${renderFieldTable(fields)}
                </details>
              `,
            )
            .join("")}
          ${state.runtimeMode === "local" && ["site", "tower_rent"].includes(row.ledger_type) ? `
            <button class="secondary-button" type="button" data-authority="${row.id}" data-authority-type="${row.ledger_type}">查看及维护权威值</button>
            <div class="site-authority-panel" data-authority-panel="${row.id}"></div>` : ""}
        </article>
      `,
    )
    .join("")}
  `;
  container.querySelectorAll("[data-authority]").forEach((button) => {
    button.addEventListener("click", () => showAuthority(button.dataset.authority, button.dataset.authorityType));
  });
}

async function showAuthority(rowId, type) {
  const panel = document.querySelector(`[data-authority-panel="${rowId}"]`);
  if (!panel) return;
  panel.textContent = "正在读取来源和版本…";
  try {
    const collection = type === "site" ? "sites" : "tower-rents";
    const detail = await fetchJson(`/api/local/${collection}/${rowId}?batch_id=${encodeURIComponent(state.batchId)}`);
    if (detail.identity_conflict) {
      panel.textContent = "站址编码缺失或归属冲突，请先由省公司核对记录身份。";
      return;
    }
    const fields = Object.keys(detail.current || {});
    panel.innerHTML = `
      <p>当前认可值：第 ${detail.version} 版${detail.version ? "（已留存更正依据）" : "（来源初始值，尚未单独核实）"}</p>
      <details><summary>导入来源值</summary>${renderFieldTable(detail.source)}</details>
      <details open><summary>当前值</summary>${renderFieldTable(detail.current)}</details>
      <details><summary>历次生效更正（${detail.versions.length}）</summary>
        ${detail.versions.map((version) => `<article class="ledger-row-card">
          <strong>第 ${version.version} 版 · ${escapeHtml(version.effective_at)}</strong>
          <p>操作者：${escapeHtml(version.operator)}；依据：${escapeHtml(version.evidence)}</p>
          <p>原因：${escapeHtml(version.error_cause)}；来源：${escapeHtml(version.source)}</p>
          <details><summary>更正前</summary>${renderFieldTable(version.old_value)}</details>
          <details><summary>更正后</summary>${renderFieldTable(version.new_value)}</details>
        </article>`).join("") || "暂无更正版本"}
      </details>
      <form class="site-correction-form">
        <label>更正字段<select name="field" required>${fields.filter((field) => type === "site"
          ? field !== "电信站址编码"
          : !["电信站址编码", "铁塔站址编码", "需求单号", "业务确认单号",
              "报账周期", "账期", "账单月份", "计费账期", "地市", "区县",
              "电信站址名称", "铁塔站址名称"].includes(field))
          .map((field) => `<option value="${escapeHtml(field)}">${escapeHtml(field)}</option>`).join("")}</select></label>
        <label>更正后值<input name="value" required></label>
        <label>核实依据<input name="evidence" required></label>
        <label>操作者<input name="operator" required></label>
        <label>错误原因<input name="error_cause" required></label>
        <label>错误来源<input name="source" required></label>
        <button class="primary-button" type="submit">直接生效并留版本</button>
        <p class="site-correction-result" role="status"></p>
      </form>`;
    const form = panel.querySelector("form");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const status = form.querySelector(".site-correction-result");
      const button = form.querySelector("button");
      button.disabled = true;
      try {
        const saved = await postJson(`/api/local/${collection}/${rowId}/corrections`, {
          batch_id: Number(state.batchId),
          changes: { [data.get("field")]: data.get("value") },
          evidence: data.get("evidence"), operator: data.get("operator"),
          error_cause: data.get("error_cause"), source: data.get("source"),
          idempotency_key: crypto.randomUUID(),
        });
        await showAuthority(rowId, type);
        const refreshed = document.querySelector(`[data-authority-panel="${rowId}"] .site-correction-result`);
        if (refreshed) refreshed.textContent = `第 ${saved.version} 版已生效，可重新执行稽核。`;
      } catch (error) {
        status.textContent = error.message;
        button.disabled = false;
      }
    });
  } catch (error) {
    panel.textContent = error.message;
  }
}

function renderLedgerPagination(data, context) {
  const host = document.querySelector("#ledger-data-pagination");
  const total = Number(data.total || 0);
  const limit = Number(data.limit || PAGE_SIZE);
  const offset = Number(data.offset || 0);
  if (!total) {
    host.innerHTML = "";
    return;
  }
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(Math.ceil(total / limit), 1);
  host.innerHTML = `
    <span>第 ${currentPage}/${totalPages} 页</span>
    <div class="button-row">
      <button class="secondary-button" type="button" data-ledger-page="prev" ${offset <= 0 ? "disabled" : ""}>上一页</button>
      <button class="secondary-button" type="button" data-ledger-page="next" ${offset + limit >= total ? "disabled" : ""}>下一页</button>
    </div>
  `;
  host.querySelector('[data-ledger-page="prev"]')?.addEventListener("click", () => {
    ledgerOffset = Math.max(offset - limit, 0);
    loadLedgerData(context);
  });
  host.querySelector('[data-ledger-page="next"]')?.addEventListener("click", () => {
    ledgerOffset = offset + limit;
    loadLedgerData(context);
  });
}

function renderFieldTable(fields) {
  const entries = Object.entries(fields || {});
  if (!entries.length) return '<div class="empty-state">暂无字段</div>';
  return `
    <div class="field-table-wrap">
      <table class="field-table">
        <tbody>
          ${entries
            .map(
              ([key, value]) => `
                <tr>
                  <th>${escapeHtml(key)}</th>
                  <td title="${escapeHtml(value ?? "")}">${escapeHtml(value ?? "")}</td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}
