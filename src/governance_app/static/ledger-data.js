import { fetchJson } from "/api.js?v=20260517-1";
import { state } from "/state.js?v=20260517-1";
import { escapeHtml } from "/ui.js?v=20260517-1";

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
      ${shellHeader("数据整理", "Ledger Data", renderBatchSelector())}
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
  document.querySelector("#load-ledger-data").addEventListener("click", () => loadLedgerData({ fieldValue, ledgerLabel }));
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
  const data = await fetchJson(`/api/ledger-rows?${params.toString()}`);
  renderLedgerDataRows(data.rows || [], ledgerLabel);
}

function renderLedgerDataRows(rows, ledgerLabel) {
  const container = document.querySelector("#ledger-data-list");
  if (!rows.length) {
    container.className = "ledger-data-list empty-state";
    container.textContent = "当前筛选条件下暂无台账记录";
    return;
  }
  container.className = "ledger-data-list";
  container.innerHTML = rows
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
                  <summary>${escapeHtml(groupName)}</summary>
                  <dl>
                    ${Object.entries(fields)
                      .map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value ?? "")}</dd></div>`)
                      .join("")}
                  </dl>
                </details>
              `,
            )
            .join("")}
        </article>
      `,
    )
    .join("");
}
