import { fetchJson, postJson } from "/api.js?v=20260517-1";
import { escapeHtml, formatNumber, withBusy } from "/ui.js?v=20260517-1";

const ledgerLabels = {
  site: "站址",
  tower_rent: "铁塔租费",
  electricity: "电费",
  generator: "发电费",
  all: "跨台账",
};

const severityLabels = {
  high: "高",
  medium: "中",
  low: "低",
};

export async function renderRules({ mainContent, shellHeader, state, refreshBatches }) {
  await refreshBatches?.().catch(() => []);
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("规则设置", "稽核规则")}
      <div id="rules-result" class="result-box">正在加载规则</div>
      <div class="rule-filter-bar" aria-label="规则筛选">
        <div class="rule-filter-search">
          <label class="compact-field"><span>搜索</span><input id="rule-search" type="search" placeholder="规则名称、编号或说明"></label>
          <label class="compact-field"><span>台账</span><select id="rule-ledger-filter"><option value="">全部台账</option>${Object.entries(ledgerLabels).filter(([key]) => key !== "all").map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label>
          <label class="compact-field"><span>风险</span><select id="rule-severity-filter"><option value="">全部风险</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label>
        </div>
        <div class="segmented-control" role="group" aria-label="规则分类">
          <button class="segmented-button is-active" type="button" data-rule-filter="all" aria-pressed="true">全部规则</button>
          <button class="segmented-button" type="button" data-rule-filter="data_quality" aria-pressed="false">基础数据质量</button>
          <button class="segmented-button" type="button" data-rule-filter="problem_audit" aria-pressed="false">问题稽核</button>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>启用</th>
              <th>规则</th>
              <th>台账</th>
              <th>风险</th>
              <th>效果与建议</th>
              <th>阈值</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="rules-table"></tbody>
        </table>
      </div>
    </section>
  `;
  filtersBound = false;
  await loadRules(state?.batchId);
}

let allRuleRows = [];
let activeRuleFilter = "all";
let currentRuleBatchId = null;
let ruleSearch = "";
let ruleLedger = "";
let ruleSeverity = "";
let filtersBound = false;

async function loadRules(batchId) {
  if (batchId !== undefined) currentRuleBatchId = batchId || null;
  const activeBatchId = batchId !== undefined ? batchId : currentRuleBatchId;
  const suffix = activeBatchId ? `?batch_id=${encodeURIComponent(activeBatchId)}` : "";
  const data = await fetchJson(`/api/rules${suffix}`);
  allRuleRows = data.rules || [];
  bindRuleFilters();
  renderFilteredRules(activeBatchId);
}

function bindRuleFilters() {
  if (filtersBound) return;
  filtersBound = true;
  document.querySelectorAll("[data-rule-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      activeRuleFilter = button.dataset.ruleFilter;
      document.querySelectorAll("[data-rule-filter]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
        item.setAttribute("aria-pressed", String(item === button));
      });
      renderFilteredRules(currentRuleBatchId);
    });
  });
  document.querySelector("#rule-search")?.addEventListener("input", (event) => {
    ruleSearch = event.currentTarget.value.trim().toLowerCase();
    renderFilteredRules(currentRuleBatchId);
  });
  document.querySelector("#rule-ledger-filter")?.addEventListener("change", (event) => {
    ruleLedger = event.currentTarget.value;
    renderFilteredRules(currentRuleBatchId);
  });
  document.querySelector("#rule-severity-filter")?.addEventListener("change", (event) => {
    ruleSeverity = event.currentTarget.value;
    renderFilteredRules(currentRuleBatchId);
  });
}

function filteredRules() {
  return allRuleRows.filter((rule) => {
    if (activeRuleFilter !== "all" && rule.category !== activeRuleFilter) return false;
    if (ruleLedger && rule.ledger_type !== ruleLedger) return false;
    if (ruleSeverity && rule.severity !== ruleSeverity) return false;
    if (ruleSearch && !`${rule.rule_id} ${rule.name} ${rule.description}`.toLowerCase().includes(ruleSearch)) return false;
    return true;
  });
}

function renderFilteredRules(activeBatchId) {
  const rows = filteredRules();
  renderRuleRows(rows);
  setRulesResult("success", `显示 ${formatNumber(rows.length)} / ${formatNumber(allRuleRows.length)} 条规则${activeBatchId ? "，已结合当前批次生成效果建议" : ""}`);
}

function renderRuleRows(rules) {
  const tbody = document.querySelector("#rules-table");
  if (!rules.length) {
    tbody.innerHTML = '<tr><td colspan="7">暂无规则</td></tr>';
    return;
  }
  tbody.innerHTML = rules
    .map((rule) => {
      return `
        <tr>
          <td><input type="checkbox" data-rule-enabled="${escapeHtml(rule.rule_id)}" aria-label="启用规则：${escapeHtml(rule.name)}" ${rule.enabled ? "checked" : ""}></td>
          <td>
            <strong>${escapeHtml(rule.name)}</strong>
            <p class="table-note">${escapeHtml(rule.description)}</p>
            <p class="table-note">${escapeHtml(categoryLabel(rule.category))}</p>
          </td>
          <td>${escapeHtml(ledgerLabels[rule.ledger_type] || rule.ledger_type)}</td>
          <td><span class="chip chip-info">${escapeHtml(severityLabels[rule.severity] || rule.severity)}</span></td>
          <td>${renderRuleEffect(rule)}</td>
          <td>${renderRuleParameters(rule)}</td>
          <td>
            <div class="rule-action-stack">
              <button class="text-button" data-save-rule="${escapeHtml(rule.rule_id)}" type="button">保存</button>
              <button class="text-button" data-reset-rule="${escapeHtml(rule.rule_id)}" type="button">恢复默认</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  tbody.querySelectorAll("[data-save-rule]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      const ruleId = button.dataset.saveRule;
      await withBusy(event.currentTarget, "保存中...", async () => {
        const enabled = document.querySelector(`[data-rule-enabled="${CSS.escape(ruleId)}"]`).checked;
        const config = {};
        document.querySelectorAll(`[data-rule-param="${CSS.escape(ruleId)}"]`).forEach((input) => {
          if (input.value !== "") config[input.dataset.paramKey] = Number(input.value);
        });
        await postJson("/api/rules/settings", { rule_id: ruleId, enabled, config });
        setRulesResult("success", `规则已更新：${ruleId}`);
        await loadRules();
      });
    });
  });
  tbody.querySelectorAll("[data-reset-rule]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      const ruleId = button.dataset.resetRule;
      await withBusy(event.currentTarget, "恢复中...", async () => {
        const enabled = document.querySelector(`[data-rule-enabled="${CSS.escape(ruleId)}"]`).checked;
        await postJson("/api/rules/settings", { rule_id: ruleId, enabled, config: {} });
        setRulesResult("success", `已恢复默认阈值：${ruleId}`);
        await loadRules();
      });
    });
  });
}

function renderRuleEffect(rule) {
  const effect = rule.effectiveness || {};
  const recommendation = rule.tuning_recommendation || { level: "neutral", message: "当前批次暂无效果数据" };
  return `
    <div class="rule-effect-cell">
      <div class="mini-grid rule-effect-grid">
        <span>命中 ${formatNumber(effect.total_count || 0)}</span>
        <span>未闭环 ${formatNumber(effect.open_count || 0)}</span>
        <span>无需整改 ${formatNumber(effect.not_required_rate || 0)}%</span>
      </div>
      <p class="table-note recommendation-${escapeHtml(recommendation.level || "neutral")}">${escapeHtml(recommendation.message || "")}</p>
    </div>
  `;
}

function renderRuleParameters(rule) {
  const params = rule.parameters || [];
  if (!params.length) return '<span class="table-note">默认口径</span>';
  return `
    <div class="rule-param-list">
      ${params.map((param) => {
        const value = rule.config?.[param.key] ?? param.default;
        return `
          <label class="rule-param-field">
            <span>${escapeHtml(param.label)}</span>
            <input data-rule-param="${escapeHtml(rule.rule_id)}" data-param-key="${escapeHtml(param.key)}" type="number" step="${escapeHtml(param.step ?? 1)}" value="${escapeHtml(value)}">
            <em>${escapeHtml(param.unit || "")}</em>
          </label>
        `;
      }).join("")}
    </div>
  `;
}

function categoryLabel(category) {
  return {
    data_quality: "基础数据质量核查",
    problem_audit: "问题稽核",
  }[category] || "未分类";
}

function setRulesResult(stateName, content) {
  const result = document.querySelector("#rules-result");
  result.className = `result-box result-${stateName}`;
  result.textContent = content;
}

export const electricityPriceRangeRuleId = "electricity_price_range";
