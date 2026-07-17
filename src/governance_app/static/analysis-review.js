import { postJson } from "/api.js?v=20260712-1";
import { issueStatusLabel, issueStatusOptions } from "/issue-status.js?v=20260717-1";

function escapeAttribute(ctx, value) {
  return ctx.escapeHtml(String(value ?? ""));
}

function amountValue(ctx, value) {
  return value === null || value === undefined ? "" : escapeAttribute(ctx, value);
}

export function statusOptions(selected = "") {
  return issueStatusOptions(selected);
}

export function batchReviewToolbar(prefix) {
  return `
    <section class="batch-review-toolbar" id="${prefix}-batch-toolbar" aria-label="批量复核工具栏">
      <label class="batch-review-select-all">
        <input id="${prefix}-select-all" type="checkbox">
        <span>全选当前列表</span>
      </label>
      <span id="${prefix}-selected-count" class="batch-review-count" aria-live="polite">已选 0 条</span>
      <label class="compact-field"><span>批量处理结果</span><select id="${prefix}-batch-status">
        <option value="closed">确认闭环</option>
        <option value="still_invalid">仍需整改</option>
        <option value="not_required">无需整改</option>
        <option value="needs_review">提交人工复核</option>
      </select></label>
      <label class="batch-review-note"><span>统一核查说明</span><input id="${prefix}-batch-note" type="text" maxlength="500" placeholder="必填，用于全部所选记录"></label>
      <button id="${prefix}-batch-preview" class="secondary-button" type="button" disabled>预览批量操作</button>
      <p id="${prefix}-batch-result" class="batch-review-result" role="status" aria-live="polite" tabindex="-1"></p>
    </section>
    <dialog id="${prefix}-batch-dialog" class="batch-review-dialog" aria-labelledby="${prefix}-batch-dialog-title">
      <div class="batch-review-dialog-body">
        <p class="eyebrow">影响预览</p>
        <h3 id="${prefix}-batch-dialog-title">确认批量复核</h3>
        <div id="${prefix}-batch-preview-content"></div>
        <div class="button-row">
          <button id="${prefix}-batch-confirm" class="primary-button" type="button">确认执行</button>
          <button id="${prefix}-batch-cancel" class="secondary-button" type="button">取消</button>
        </div>
      </div>
    </dialog>
  `;
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
        <div class="analysis-review-heading-actions">
          <label class="analysis-review-select">
            <input class="analysis-review-select-input" type="checkbox" value="${escapeAttribute(ctx, row.opportunity_code)}" aria-label="选择专题记录 ${escapeAttribute(ctx, row.opportunity_code)}">
            <span>批量选择</span>
          </label>
          <span class="analysis-review-status">${escapeAttribute(ctx, issueStatusLabel(row.issue_status))}</span>
        </div>
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
        <button class="review-action review-action-review" type="submit" data-status="needs_review">提交人工复核</button>
        <button class="review-action review-action-close" type="submit" data-status="closed">确认闭环</button>
        <button class="review-action review-action-invalid" type="submit" data-status="still_invalid">仍需整改</button>
        <button class="review-action review-action-muted" type="submit" data-status="not_required">无需整改</button>
      </div>
      <p class="analysis-review-error" role="alert" aria-live="assertive" tabindex="-1"></p>
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
        errorBox.focus();
      } finally {
        delete form.dataset.submitting;
        buttons.forEach((button) => {
          button.disabled = false;
        });
      }
    });
  });
}

function selectedCodes(prefix) {
  return [...document.querySelectorAll(`#${prefix}-batch-toolbar ~ .analysis-review-list .analysis-review-select-input:checked, #${prefix}-opportunity-list .analysis-review-select-input:checked, #${prefix}-clue-list .analysis-review-select-input:checked`)].map((input) => input.value);
}

function syncBatchSelection(prefix) {
  const inputs = [...document.querySelectorAll(".analysis-review-select-input")];
  const selected = inputs.filter((input) => input.checked);
  const selectAll = document.querySelector(`#${prefix}-select-all`);
  const previewButton = document.querySelector(`#${prefix}-batch-preview`);
  document.querySelector(`#${prefix}-selected-count`).textContent = `已选 ${selected.length} 条`;
  previewButton.disabled = selected.length === 0;
  selectAll.checked = inputs.length > 0 && selected.length === inputs.length;
  selectAll.indeterminate = selected.length > 0 && selected.length < inputs.length;
  inputs.forEach((input) => input.closest(".analysis-review-card")?.classList.toggle("is-selected", input.checked));
}

export function bindBatchReview(ctx, routeDomain, prefix, reload) {
  const toolbar = document.querySelector(`#${prefix}-batch-toolbar`);
  if (!toolbar) return;
  document.querySelectorAll(".analysis-review-select-input").forEach((input) => {
    input.addEventListener("change", () => syncBatchSelection(prefix));
  });
  syncBatchSelection(prefix);
  if (toolbar.dataset.bound === "true") return;
  toolbar.dataset.bound = "true";

  const selectAll = document.querySelector(`#${prefix}-select-all`);
  const previewButton = document.querySelector(`#${prefix}-batch-preview`);
  const result = document.querySelector(`#${prefix}-batch-result`);
  const dialog = document.querySelector(`#${prefix}-batch-dialog`);
  const confirmButton = document.querySelector(`#${prefix}-batch-confirm`);
  const cancelButton = document.querySelector(`#${prefix}-batch-cancel`);
  const previewContent = document.querySelector(`#${prefix}-batch-preview-content`);

  selectAll.addEventListener("change", () => {
    document.querySelectorAll(".analysis-review-select-input").forEach((input) => {
      input.checked = selectAll.checked;
    });
    syncBatchSelection(prefix);
  });
  cancelButton.addEventListener("click", () => dialog.close());
  previewButton.addEventListener("click", async () => {
    const opportunityCodes = selectedCodes(prefix);
    const status = document.querySelector(`#${prefix}-batch-status`).value;
    const reviewNote = document.querySelector(`#${prefix}-batch-note`).value.trim();
    result.textContent = "";
    if (!reviewNote) {
      result.textContent = "请填写统一核查说明后再预览。";
      document.querySelector(`#${prefix}-batch-note`).focus();
      return;
    }
    previewButton.disabled = true;
    try {
      const preview = await postJson(`/api/batches/${ctx.state.batchId}/${routeDomain}/review-batch-preview`, {
        opportunity_codes: opportunityCodes,
        status,
        review_note: reviewNote,
      });
      confirmButton.dataset.previewSignature = preview.preview_signature;
      confirmButton.disabled = preview.eligible_count === 0;
      previewContent.innerHTML = `
        <p>将把 <strong>${preview.eligible_count}</strong> 条记录更新为“${escapeAttribute(ctx, issueStatusLabel(status))}”，统一写入本次核查说明。</p>
        <dl class="batch-review-preview-stats">
          <div><dt>已选择</dt><dd>${preview.selected_count}</dd></div>
          <div><dt>可处理</dt><dd>${preview.eligible_count}</dd></div>
          <div><dt>无法处理</dt><dd>${preview.blocked_count}</dd></div>
        </dl>
        ${preview.blocked_count ? `<details><summary>查看无法处理明细</summary><ul>${preview.blocked.map((item) => `<li>${escapeAttribute(ctx, item.opportunity_code)}：${escapeAttribute(ctx, item.error)}</li>`).join("")}</ul></details>` : ""}
      `;
      dialog.showModal();
      confirmButton.focus();
    } catch (error) {
      result.textContent = error.message;
      result.focus();
    } finally {
      previewButton.disabled = selectedCodes(prefix).length === 0;
    }
  });
  confirmButton.addEventListener("click", async () => {
    const payload = {
      opportunity_codes: selectedCodes(prefix),
      status: document.querySelector(`#${prefix}-batch-status`).value,
      review_note: document.querySelector(`#${prefix}-batch-note`).value.trim(),
      confirmed: true,
      preview_signature: confirmButton.dataset.previewSignature,
    };
    confirmButton.disabled = true;
    try {
      const saved = await postJson(`/api/batches/${ctx.state.batchId}/${routeDomain}/review-batch`, payload);
      dialog.close();
      await reload();
      const refreshedResult = document.querySelector(`#${prefix}-batch-result`);
      refreshedResult.textContent = `批量处理完成：成功 ${saved.success_count} 条，失败 ${saved.failed_count} 条。`;
      refreshedResult.focus();
    } catch (error) {
      previewContent.insertAdjacentHTML("beforeend", `<p class="analysis-review-error" role="alert" tabindex="-1">${escapeAttribute(ctx, error.message)}</p>`);
      previewContent.querySelector(".analysis-review-error:last-child")?.focus();
    } finally {
      confirmButton.disabled = false;
    }
  });
}
