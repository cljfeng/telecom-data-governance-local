from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "governance_app" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_both_analysis_pages_use_shared_review_module_and_status_filter():
    electricity = _read("electricity-analysis.js")
    tower_rent = _read("tower-rent-analysis.js")

    for page in (electricity, tower_rent):
        assert 'from "/analysis-review.js?' in page
        assert 'query.set("status", status)' in page
        assert "status-filter" in page
        assert "reviewForm(ctx, row)" in page
        assert "bindReviewForms(ctx," in page


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


def test_review_cards_collapse_to_one_column_on_mobile():
    styles = _read("styles.css")

    assert ".analysis-review-list" in styles
    assert ".analysis-review-card" in styles
    assert ".analysis-review-fields" in styles
    assert "@media (max-width: 760px)" in styles
    assert "grid-template-columns: 1fr" in styles
