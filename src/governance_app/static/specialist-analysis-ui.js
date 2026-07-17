import { issueStatusOptions } from "/issue-status.js?v=20260717-1";

export function specialistFilterControls(prefix, initialView = "actionable") {
  return `
    <div class="toolbar specialist-filter-bar">
      <label class="compact-field"><span>异常类型</span><select id="${prefix}-type-filter"><option value="">全部类型</option></select></label>
      <label class="compact-field"><span>置信度</span><select id="${prefix}-confidence-filter"><option value="">全部置信度</option></select></label>
      <label class="compact-field"><span>处理视图</span><select id="${prefix}-view-filter">
        <option value="actionable"${initialView === "actionable" ? " selected" : ""}>待处理队列</option>
        <option value="all"${initialView === "all" ? " selected" : ""}>全部记录</option>
        <option value="needs_review"${initialView === "needs_review" ? " selected" : ""}>待人工复核</option>
        <option value="verified"${initialView === "verified" ? " selected" : ""}>已核实金额</option>
        <option value="realized"${initialView === "realized" ? " selected" : ""}>已实际落实</option>
      </select></label>
      <label class="compact-field"><span>问题状态</span><select id="${prefix}-status-filter">${issueStatusOptions()}</select></label>
      <button id="apply-${prefix}-filters" class="secondary-button" type="button">筛选</button>
    </div>
  `;
}

export function specialistFilterQuery(prefix) {
  const query = new URLSearchParams();
  const type = document.querySelector(`#${prefix}-type-filter`)?.value || "";
  const confidence = document.querySelector(`#${prefix}-confidence-filter`)?.value || "";
  const view = document.querySelector(`#${prefix}-view-filter`)?.value || "actionable";
  const status = document.querySelector(`#${prefix}-status-filter`)?.value || "";
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  if (status) query.set("status", status);
  else if (view === "actionable") query.set("queue", "actionable");
  else if (view === "needs_review") query.set("status", "needs_review");
  else if (view === "verified") query.set("review", "verified");
  else if (view === "realized") query.set("review", "realized");
  return query;
}

export function specialistSummary(primary, secondary) {
  const cards = primary
    .map(
      (item) => `
        <button class="metric-card metric-${item.tone || "neutral"} specialist-metric-button" type="button" data-specialist-view="${item.view}">
          <span class="metric-label">${item.label}</span>
          <strong class="metric-value">${item.value}</strong>
          <span class="metric-note">${item.note}</span>
          <span class="metric-spark" aria-hidden="true"></span>
        </button>
      `,
    )
    .join("");
  const details = secondary.map((item) => item.html).join("");
  return `
    <div class="metric-grid specialist-primary-metrics">${cards}</div>
    <details class="specialist-more-metrics">
      <summary>更多指标 <span>${secondary.length} 项</span></summary>
      <div class="metric-grid">${details}</div>
    </details>
  `;
}

export function bindSpecialistMetricFilters(prefix, reload) {
  document.querySelectorAll("[data-specialist-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      const view = document.querySelector(`#${prefix}-view-filter`);
      const status = document.querySelector(`#${prefix}-status-filter`);
      if (!view || !status) return;
      view.value = button.dataset.specialistView;
      status.value = "";
      await reload();
      document.querySelector(`#${prefix}-queue-heading`)?.scrollIntoView({ block: "start" });
    });
  });
}

export function initialSpecialistView(ctx, key) {
  ctx.state.specialistViews ||= {};
  return ctx.state.specialistViews[key] || "actionable";
}

export function rememberSpecialistView(ctx, key, prefix) {
  ctx.state.specialistViews ||= {};
  ctx.state.specialistViews[key] = document.querySelector(`#${prefix}-view-filter`)?.value || "actionable";
}
