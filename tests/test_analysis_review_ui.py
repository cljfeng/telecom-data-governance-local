from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "governance_app" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_both_analysis_pages_use_shared_review_module_and_status_filter():
    electricity = _read("electricity-analysis.js")
    tower_rent = _read("tower-rent-analysis.js")
    specialist = _read("specialist-analysis-ui.js")

    for page in (electricity, tower_rent):
        assert 'from "/analysis-review.js?' in page
        assert 'from "/specialist-analysis-ui.js?' in page
        assert "specialistFilterControls" in page
        assert "reviewForm(ctx, row)" in page
        assert "bindReviewForms(ctx," in page
        assert 'shellHeader("待处理清单"' in page
    assert 'query.set("status", status)' in specialist
    assert 'query.set("queue", "actionable")' in specialist


def test_shared_review_module_exposes_complete_review_form_contract():
    shared = _read("analysis-review.js")

    assert "export function statusOptions" in shared
    assert "export function reviewForm" in shared
    assert "export function bindReviewForms" in shared
    for value in ("needs_review", "still_invalid", "closed", "not_required"):
        assert value in shared
    for field in ("verified_recoverable_amount", "realized_saving_amount"):
        assert field in shared
    assert "analysis-review-error" in shared
    assert "event.submitter.dataset.status" in shared
    assert 'button.disabled = true' in shared
    assert 'button.disabled = false' in shared
    assert "旧版专题机会，请先重新运行专题分析后再闭环" in shared


def test_third_phase_exposes_accessible_batch_review_contract():
    shared = _read("analysis-review.js")
    electricity = _read("electricity-analysis.js")
    tower_rent = _read("tower-rent-analysis.js")
    styles = _read("styles.css")

    for contract in ("batchReviewToolbar", "bindBatchReview"):
        assert f"export function {contract}" in shared
    assert "全选当前列表" in shared
    assert "预览批量操作" in shared
    assert "review-batch-preview" in shared
    assert "preview_signature" in shared
    assert 'aria-live="assertive"' in shared
    assert "errorBox.focus()" in shared
    assert "selectAll.indeterminate" in shared
    assert "dialog.showModal()" in shared
    for page in (electricity, tower_rent):
        assert "batchReviewToolbar" in page
        assert "bindBatchReview" in page
    assert ".analysis-review-card.is-selected" in styles
    assert ".batch-review-dialog::backdrop" in styles


def test_issue_status_labels_are_unique_and_shared_by_analysis_pages():
    catalog = _read("issue-status.js")
    shared = _read("analysis-review.js")
    electricity = _read("electricity-analysis.js")
    tower_rent = _read("tower-rent-analysis.js")

    assert '["returned", "已回传待确认"]' in catalog
    assert '["needs_review", "待人工复核"]' in catalog
    assert catalog.count("待人工复核") == 1
    assert "issueStatusOptions" in shared
    assert "issueStatusLabel" in electricity
    assert "issueStatusLabel" in tower_rent
    assert 'returned: "待复核"' not in electricity + tower_rent


def test_first_phase_actions_expose_prerequisite_gates():
    app = _read("app.js")
    electricity = _read("electricity-analysis.js")
    tower_rent = _read("tower-rent-analysis.js")

    assert "approvedPreviewSignature" in app
    assert "请先完成模板预检" in app
    assert "覆盖当前批次将替换" in app
    assert "当前批次还没有台账，请先完成数据导入" in app
    assert "请先完成当前批次稽核，再导出整改包" in app
    for page in (electricity, tower_rent):
        assert "analysis_generated" in page
        assert "disabled aria-describedby" in page


def test_second_phase_uses_shared_summary_filters_and_dashboard_deep_links():
    specialist = _read("specialist-analysis-ui.js")
    app = _read("app.js")
    styles = _read("styles.css")

    for contract in (
        "specialistFilterControls",
        "specialistFilterQuery",
        "specialistSummary",
        "bindSpecialistMetricFilters",
        "initialSpecialistView",
    ):
        assert f"export function {contract}" in specialist
    assert "待处理队列" in specialist
    assert "更多指标" in specialist
    assert 'id="specialist-todos"' in app
    assert "/electricity-analysis/summary" in app
    assert "/tower-rent-analysis/summary" in app
    assert "data-specialist-target" in app
    assert ".specialist-primary-metrics" in styles
    assert ".specialist-todo-grid" in styles


def test_review_cards_collapse_to_one_column_on_mobile():
    styles = _read("styles.css")

    assert ".analysis-review-list" in styles
    assert ".analysis-review-card" in styles
    assert ".analysis-review-fields" in styles
    assert "@media (max-width: 760px)" in styles
    assert "grid-template-columns: 1fr" in styles


def test_visual_polish_keeps_accessibility_and_motion_preferences():
    index = _read("index.html")
    styles = _read("styles.css")

    assert 'class="skip-link" href="#main-content"' in index
    assert 'id="main-content" class="main-content" tabindex="-1"' in index
    assert 'id="global-status" class="sr-only" role="status" aria-live="polite"' in index
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "@media (prefers-contrast: more)" in styles
    assert "--focus-ring:" in styles
    assert "min-height: 44px" in styles
    assert "font-variant-numeric: tabular-nums" in styles


def test_visual_polish_uses_consistent_navigation_icons_and_layering():
    index = _read("index.html")
    styles = _read("styles.css")

    assert index.count('class="nav-icon"') == 12
    assert index.count('aria-hidden="true" focusable="false"') >= 12
    assert ".nav-icon" in styles
    assert ".next-action .todo-strip span" in styles
    assert "@keyframes surface-enter" in styles
    assert ".specialist-todo-card::before" in styles


def test_three_stage_polish_adds_responsive_navigation_and_stateful_routes():
    index = _read("index.html")
    app = _read("app.js")
    styles = _read("styles.css")

    assert 'id="mobile-nav-toggle"' in index
    assert 'id="nav-scrim"' in index
    assert "updateViewLocation" in app
    assert 'setAttribute("aria-current", "page")' in app
    assert 'window.addEventListener("popstate"' in app
    assert "body.nav-open .sidebar" in styles
    assert ".global-error-toast" in styles


def test_three_stage_polish_adds_rule_search_and_specialist_pagination():
    rules = _read("rules.js")
    specialist = _read("specialist-analysis-ui.js")
    ledger = _read("ledger-data.js")

    assert 'id="rule-search"' in rules
    assert 'id="rule-ledger-filter"' in rules
    assert 'aria-pressed="true"' in rules
    assert "specialistPagination" in specialist
    assert 'query.set("limit"' in specialist
    assert 'id="ledger-data-pagination"' in ledger
