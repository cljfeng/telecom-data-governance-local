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

export async function renderRules({ mainContent, shellHeader }) {
  mainContent.innerHTML = `
    <section class="card">
      ${shellHeader("规则设置", "稽核规则")}
      <div id="rules-result" class="result-box">正在加载规则</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>启用</th>
              <th>规则</th>
              <th>台账</th>
              <th>风险</th>
              <th>阈值</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="rules-table"></tbody>
        </table>
      </div>
    </section>
  `;
  await loadRules();
}

async function loadRules() {
  const data = await fetchJson("/api/rules");
  renderRuleRows(data.rules || []);
  setRulesResult("success", `已加载 ${formatNumber((data.rules || []).length)} 条规则`);
}

function renderRuleRows(rules) {
  const tbody = document.querySelector("#rules-table");
  if (!rules.length) {
    tbody.innerHTML = '<tr><td colspan="6">暂无规则</td></tr>';
    return;
  }
  tbody.innerHTML = rules
    .map((rule) => {
      const isPriceRange = rule.rule_id === "electricity_price_range";
      const maxValue = rule.config?.max ?? "";
      return `
        <tr>
          <td><input type="checkbox" data-rule-enabled="${escapeHtml(rule.rule_id)}" ${rule.enabled ? "checked" : ""}></td>
          <td>
            <strong>${escapeHtml(rule.name)}</strong>
            <p class="table-note">${escapeHtml(rule.description)}</p>
          </td>
          <td>${escapeHtml(ledgerLabels[rule.ledger_type] || rule.ledger_type)}</td>
          <td><span class="chip chip-info">${escapeHtml(severityLabels[rule.severity] || rule.severity)}</span></td>
          <td>
            ${
              isPriceRange
                ? `<div class="inline-fields"><input data-rule-max="${escapeHtml(rule.rule_id)}" type="number" step="0.01" placeholder="高于阈值" value="${escapeHtml(maxValue)}"></div>`
                : '<span class="table-note">默认口径</span>'
            }
          </td>
          <td><button class="text-button" data-save-rule="${escapeHtml(rule.rule_id)}" type="button">保存</button></td>
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
        if (ruleId === "electricity_price_range") {
          const maxValue = document.querySelector(`[data-rule-max="${CSS.escape(ruleId)}"]`).value;
          if (maxValue !== "") config.max = Number(maxValue);
        }
        await postJson("/api/rules/settings", { rule_id: ruleId, enabled, config });
        setRulesResult("success", `规则已更新：${ruleId}`);
        await loadRules();
      });
    });
  });
}

function setRulesResult(stateName, content) {
  const result = document.querySelector("#rules-result");
  result.className = `result-box result-${stateName}`;
  result.textContent = content;
}

export const electricityPriceRangeRuleId = "electricity_price_range";
