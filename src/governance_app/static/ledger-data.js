import { fetchJson } from "/api.js?v=20260517-1";
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
        </article>
      `,
    )
    .join("")}
  `;
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
