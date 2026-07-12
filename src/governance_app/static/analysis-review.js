import { postJson } from "/api.js?v=20260712-1";

const STATUS_OPTIONS = [
  ["pending_export", "待导出"],
  ["pending_correction", "待整改"],
  ["returned", "待复核"],
  ["needs_review", "待复核"],
  ["still_invalid", "仍不合规"],
  ["closed", "已闭环"],
  ["not_required", "无需整改"],
  ["resolved_by_reaudit", "复审已解决"],
];

function escapeAttribute(ctx, value) {
  return ctx.escapeHtml(String(value ?? ""));
}

function amountValue(ctx, value) {
  return value === null || value === undefined ? "" : escapeAttribute(ctx, value);
}

function statusLabel(status) {
  return new Map(STATUS_OPTIONS).get(status) || "待处理";
}

export function statusOptions(selected = "") {
  return [
    '<option value="">全部状态</option>',
    ...STATUS_OPTIONS.map(
      ([value, label]) =>
        `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`,
    ),
  ].join("");
}

export function reviewForm(ctx, row) {
  if (!row.source_issue_code) {
    return `
      <aside class="analysis-review-legacy" role="note">
        <strong>暂不能在线闭环</strong>
        <p>旧版专题机会，请先重新运行专题分析后再闭环</p>
      </aside>
    `;
  }
  const note = row.review_note || row.correction_note || "";
  return `
    <form class="analysis-review-form" data-opportunity-code="${escapeAttribute(ctx, row.opportunity_code)}">
      <div class="analysis-review-heading">
        <div>
          <span class="analysis-review-kicker">核查处理</span>
          <strong>记录最终认定与落实结果</strong>
        </div>
        <span class="analysis-review-status">${escapeAttribute(ctx, statusLabel(row.issue_status))}</span>
      </div>
      <div class="analysis-review-fields">
        <label>
          <span>核实可追回金额（元）</span>
          <input name="verified_recoverable_amount" type="number" min="0" step="0.01" value="${amountValue(ctx, row.verified_recoverable_amount)}" inputmode="decimal">
        </label>
        <label>
          <span>实际落实金额（元）</span>
          <input name="realized_saving_amount" type="number" min="0" step="0.01" value="${amountValue(ctx, row.realized_saving_amount)}" inputmode="decimal">
        </label>
      </div>
      <label class="analysis-review-note">
        <span>核查说明</span>
        <textarea name="review_note" rows="3" placeholder="填写核查依据、退款进展或无需整改原因">${escapeAttribute(ctx, note)}</textarea>
      </label>
      <div class="analysis-review-actions">
        <button class="review-action review-action-review" type="submit" data-status="needs_review">待复核</button>
        <button class="review-action review-action-close" type="submit" data-status="closed">已闭环</button>
        <button class="review-action review-action-invalid" type="submit" data-status="still_invalid">仍不合规</button>
        <button class="review-action review-action-muted" type="submit" data-status="not_required">无需整改</button>
      </div>
      <p class="analysis-review-error" role="alert" aria-live="polite"></p>
    </form>
  `;
}

function optionalAmount(formData, field) {
  const value = String(formData.get(field) ?? "").trim();
  return value === "" ? null : Number(value);
}

export function bindReviewForms(ctx, routeDomain, reload) {
  document.querySelectorAll(".analysis-review-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.dataset.submitting === "true" || !event.submitter) return;
      const status = event.submitter.dataset.status;
      const formData = new FormData(form);
      const errorBox = form.querySelector(".analysis-review-error");
      const buttons = [...form.querySelectorAll("button")];
      const payload = {
        opportunity_code: form.dataset.opportunityCode,
        status,
        verified_recoverable_amount: optionalAmount(formData, "verified_recoverable_amount"),
        realized_saving_amount: optionalAmount(formData, "realized_saving_amount"),
        review_note: String(formData.get("review_note") ?? ""),
      };
      form.dataset.submitting = "true";
      buttons.forEach((button) => {
        button.disabled = true;
      });
      errorBox.textContent = "";
      try {
        await postJson(`/api/batches/${ctx.state.batchId}/${routeDomain}/review`, payload);
        await reload();
      } catch (error) {
        errorBox.textContent = error.message;
      } finally {
        delete form.dataset.submitting;
        buttons.forEach((button) => {
          button.disabled = false;
        });
      }
    });
  });
}
