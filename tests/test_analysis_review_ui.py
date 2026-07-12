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


def test_review_cards_collapse_to_one_column_on_mobile():
    styles = _read("styles.css")

    assert ".analysis-review-list" in styles
    assert ".analysis-review-card" in styles
    assert ".analysis-review-fields" in styles
    assert "@media (max-width: 760px)" in styles
    assert "grid-template-columns: 1fr" in styles
