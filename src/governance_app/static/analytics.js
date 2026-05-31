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
    metricCard("主要风险", topSeverity?.count || 0, topSeverity?.severity || "暂无", "review"),
  ].join("");
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
