import json
import sqlite3

import pytest

from governance_app.analysis_reviews import (
    load_review_payload_in_conn,
    match_opportunity_in_conn,
    optional_nonnegative_amount,
    review_payload_fields,
    review_summary_in_conn,
    save_opportunity_review,
    sync_existing_review_note_in_conn,
    upsert_review_in_conn,
)
from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.electricity_analysis import run_electricity_analysis
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook


def _electricity_opportunity(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    with connect(app_config) as conn:
        raw = conn.execute(
            "select id, row_json from raw_rows where batch_id = ? and ledger_type = 'electricity'",
            (imported.batch_id,),
        ).fetchone()
        row = json.loads(raw["row_json"])
        row.update(
            {
                "电费单价": 1.2,
                "用电量": 100,
                "电费金额": 300,
                "供电方式": "转供电",
                "转供电合同情况": "无",
            }
        )
        conn.execute(
            "update raw_rows set row_json = ? where id = ?",
            (json.dumps(row, ensure_ascii=False), raw["id"]),
        )
    run_audit(app_config, imported.batch_id)
    run_electricity_analysis(app_config, imported.batch_id)
    with connect(app_config) as conn:
        opportunities = conn.execute(
            """
            select opportunity_code, ledger_row_id, source_rule_ids_json
              from analysis_opportunities
             where batch_id = ? and domain = 'electricity'
             order by id
            """,
            (imported.batch_id,),
        ).fetchall()
        selected = None
        for opportunity in opportunities:
            rule_id = json.loads(opportunity["source_rule_ids_json"])[0]
            issue = conn.execute(
                """
                select i.issue_code
                  from issues i
                  join audit_results ar on ar.id = i.audit_result_id
                 where i.batch_id = ? and i.rule_id = ? and ar.ledger_row_id = ?
                 order by i.id
                 limit 1
                """,
                (imported.batch_id, rule_id, opportunity["ledger_row_id"]),
            ).fetchone()
            if issue is not None:
                selected = (opportunity["opportunity_code"], issue["issue_code"])
                break
        assert selected is not None
        opportunity_code, issue_code = selected
        conn.execute(
            "update analysis_opportunities set source_issue_code = null where batch_id = ?", (imported.batch_id,)
        )
        conn.execute(
            "update analysis_opportunities set source_issue_code = ? where opportunity_code = ?",
            (issue_code, opportunity_code),
        )
    return imported.batch_id, opportunity_code, issue_code


def test_save_opportunity_review_persists_status_amounts_note_and_estimates(
    app_config, sample_workbook
):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)

    saved = save_opportunity_review(
        app_config,
        batch_id,
        "electricity-analysis",
        {
            "opportunity_code": opportunity_code,
            "status": "needs_review",
            "verified_recoverable_amount": 0,
            "realized_saving_amount": 800,
            "review_note": " 已核对账单 ",
        },
    )

    assert saved == {
        "opportunity_code": opportunity_code,
        "issue_code": issue_code,
        "issue_status": "needs_review",
        "correction_value": None,
        "correction_note": "已核对账单",
        "verified_recoverable_amount": 0.0,
        "realized_saving_amount": 800.0,
        "review_note": "已核对账单",
        "reviewed_at": saved["reviewed_at"],
    }
    assert saved["reviewed_at"]
    with connect(app_config) as conn:
        opportunity = conn.execute(
            "select recoverable_amount, saving_opportunity_amount from analysis_opportunities where opportunity_code = ?",
            (opportunity_code,),
        ).fetchone()
        review = conn.execute(
            """
            select estimated_recoverable_amount, estimated_saving_amount
              from analysis_opportunity_reviews
             where opportunity_code = ?
            """,
            (opportunity_code,),
        ).fetchone()
        event = conn.execute(
            """
            select source, note
              from issue_events e
              join issues i on i.id = e.issue_id
             where i.issue_code = ?
             order by e.id desc
             limit 1
            """,
            (issue_code,),
        ).fetchone()
    assert tuple(review) == tuple(opportunity)
    assert tuple(event) == ("analysis_review", f"保存专题核查：{opportunity_code}")


def test_blank_amount_preserves_existing_value_while_zero_overwrites(
    app_config, sample_workbook
):
    batch_id, opportunity_code, _ = _electricity_opportunity(app_config, sample_workbook)
    base_payload = {
        "opportunity_code": opportunity_code,
        "status": "needs_review",
        "verified_recoverable_amount": 1200.5,
        "realized_saving_amount": 800,
        "review_note": "首次核查",
    }
    save_opportunity_review(app_config, batch_id, "electricity-analysis", base_payload)

    saved = save_opportunity_review(
        app_config,
        batch_id,
        "electricity-analysis",
        {
            **base_payload,
            "verified_recoverable_amount": "  ",
            "realized_saving_amount": 0,
            "review_note": "二次核查",
        },
    )

    assert saved["verified_recoverable_amount"] == 1200.5
    assert saved["realized_saving_amount"] == 0.0
    assert saved["review_note"] == "二次核查"


def test_save_opportunity_review_advances_distributed_batch_through_return_flow(
    app_config, sample_workbook
):
    batch_id, opportunity_code, _ = _electricity_opportunity(
        app_config, sample_workbook
    )
    export_city_issue_packages(app_config, batch_id)

    save_opportunity_review(
        app_config,
        batch_id,
        "electricity-analysis",
        {
            "opportunity_code": opportunity_code,
            "status": "closed",
            "review_note": "在线核查闭环",
        },
    )

    with connect(app_config) as conn:
        batch_status = conn.execute(
            "select status from import_batches where id = ?", (batch_id,)
        ).fetchone()[0]
    assert batch_status == "returning"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("  ", None), ("1,200.556", 1200.56), ("1，200", 1200.0), (0, 0.0)],
)
def test_optional_nonnegative_amount_accepts_blank_and_nonnegative_values(value, expected):
    assert optional_nonnegative_amount(value, "核实可追回金额") == expected


@pytest.mark.parametrize("value", [-1, "NaN", "Infinity", "-Infinity", True, object()])
def test_optional_nonnegative_amount_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="核实可追回金额必须是非负数字"):
        optional_nonnegative_amount(value, "核实可追回金额")


@pytest.mark.parametrize("field", ["verified_recoverable_amount", "realized_saving_amount"])
@pytest.mark.parametrize("value", [-1, "NaN", "Infinity"])
def test_save_opportunity_review_rejects_invalid_amounts(
    app_config, sample_workbook, field, value
):
    batch_id, opportunity_code, _ = _electricity_opportunity(app_config, sample_workbook)
    payload = {
        "opportunity_code": opportunity_code,
        "status": "closed",
        "verified_recoverable_amount": 1,
        "realized_saving_amount": 2,
        "review_note": "已完成核查",
        field: value,
    }

    with pytest.raises(ValueError, match="必须是非负数字"):
        save_opportunity_review(app_config, batch_id, "electricity-analysis", payload)

    with connect(app_config) as conn:
        assert conn.execute("select count(*) from analysis_opportunity_reviews").fetchone()[0] == 0


@pytest.mark.parametrize("status", [None, "pending_export", "returned", "invalid"])
def test_save_opportunity_review_rejects_invalid_online_status(
    app_config, sample_workbook, status
):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)

    with pytest.raises(ValueError, match="专题核查状态无效"):
        save_opportunity_review(
            app_config,
            batch_id,
            "electricity-analysis",
            {"opportunity_code": opportunity_code, "status": status},
        )

    with connect(app_config) as conn:
        issue_status = conn.execute(
            "select status from issues where issue_code = ?", (issue_code,)
        ).fetchone()[0]
    assert issue_status == "pending_export"


def test_save_opportunity_review_requires_opportunity_code(app_config):
    with pytest.raises(ValueError, match="机会编号不能为空"):
        save_opportunity_review(
            app_config,
            1,
            "electricity-analysis",
            {"status": "closed", "opportunity_code": "  "},
        )


@pytest.mark.parametrize("route_domain", ["tower-rent-analysis", "unsupported-analysis"])
def test_save_opportunity_review_rejects_wrong_domain(
    app_config, sample_workbook, route_domain
):
    batch_id, opportunity_code, _ = _electricity_opportunity(app_config, sample_workbook)

    with pytest.raises(ValueError, match="机会不存在或不属于当前批次专题"):
        save_opportunity_review(
            app_config,
            batch_id,
            route_domain,
            {"opportunity_code": opportunity_code, "status": "closed"},
        )


def test_match_opportunity_validates_batch_legacy_issue_and_ledger_domain(
    app_config, sample_workbook
):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)
    with connect(app_config) as conn:
        matched = match_opportunity_in_conn(
            conn,
            opportunity_code,
            batch_id=batch_id,
            route_domain="electricity-analysis",
            expected_issue_code=issue_code,
        )
        assert matched["source_issue_code"] == issue_code

        with pytest.raises(ValueError, match="机会不存在或不属于当前批次专题"):
            match_opportunity_in_conn(conn, opportunity_code, batch_id=batch_id + 1)
        with pytest.raises(ValueError, match="专题机会与问题编号不匹配"):
            match_opportunity_in_conn(
                conn, opportunity_code, expected_issue_code="ISSUE-NOT-MATCHED"
            )

        conn.execute(
            "update issues set ledger_type = 'tower_rent' where issue_code = ?", (issue_code,)
        )
        with pytest.raises(ValueError, match="专题机会领域与来源问题不匹配"):
            match_opportunity_in_conn(conn, opportunity_code, expected_issue_code=issue_code)

        conn.execute(
            "update issues set ledger_type = 'electricity' where issue_code = ?", (issue_code,)
        )
        conn.execute(
            "update analysis_opportunities set source_issue_code = null where opportunity_code = ?",
            (opportunity_code,),
        )
        with pytest.raises(ValueError, match="旧版专题机会缺少来源问题，请先重新运行专题分析"):
            match_opportunity_in_conn(conn, opportunity_code)


def test_save_opportunity_review_rejects_archived_batch(app_config, sample_workbook):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)
    with connect(app_config) as conn:
        conn.execute(
            "update import_batches set status = 'archived', is_archived = 1 where id = ?",
            (batch_id,),
        )

    with pytest.raises(ValueError, match="批次已归档，不能修改专题核查结果"):
        save_opportunity_review(
            app_config,
            batch_id,
            "electricity-analysis",
            {"opportunity_code": opportunity_code, "status": "closed"},
        )

    with connect(app_config) as conn:
        assert conn.execute(
            "select status from issues where issue_code = ?", (issue_code,)
        ).fetchone()[0] == "pending_export"


def test_review_write_failure_rolls_back_status_event_and_review(
    app_config, sample_workbook
):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)
    with connect(app_config) as conn:
        original_status = conn.execute(
            "select status from issues where issue_code = ?", (issue_code,)
        ).fetchone()[0]
        original_event_count = conn.execute("select count(*) from issue_events").fetchone()[0]
        conn.execute(
            """
            create trigger fail_analysis_review before insert on analysis_opportunity_reviews
            begin select raise(abort, 'forced review failure'); end
            """
        )
    valid_payload = {
        "opportunity_code": opportunity_code,
        "status": "closed",
        "verified_recoverable_amount": 1200.5,
        "realized_saving_amount": 800,
        "review_note": "已完成核查",
    }

    with pytest.raises(sqlite3.IntegrityError, match="forced review failure"):
        save_opportunity_review(
            app_config, batch_id, "electricity-analysis", valid_payload
        )

    with connect(app_config) as conn:
        assert conn.execute(
            "select status from issues where issue_code = ?", (issue_code,)
        ).fetchone()[0] == original_status
        assert conn.execute("select count(*) from issue_events").fetchone()[0] == original_event_count
        assert conn.execute("select count(*) from analysis_opportunity_reviews").fetchone()[0] == 0


def test_upsert_and_sync_note_only_update_existing_reviews(app_config, sample_workbook):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)
    with connect(app_config) as conn:
        sync_existing_review_note_in_conn(conn, issue_code, "不应创建")
        assert conn.execute("select count(*) from analysis_opportunity_reviews").fetchone()[0] == 0

        opportunity = match_opportunity_in_conn(
            conn,
            opportunity_code,
            batch_id=batch_id,
            route_domain="electricity-analysis",
        )
        upsert_review_in_conn(conn, opportunity, 100, 200, "初始说明")
        upsert_review_in_conn(conn, opportunity, None, 0, "更新说明")
        sync_existing_review_note_in_conn(conn, issue_code, "普通回传说明")
        review = conn.execute(
            """
            select verified_recoverable_amount, realized_saving_amount, review_note
              from analysis_opportunity_reviews
             where opportunity_code = ?
            """,
            (opportunity_code,),
        ).fetchone()
    assert tuple(review) == (100.0, 0.0, "普通回传说明")


def test_load_review_payload_and_review_payload_fields(app_config, sample_workbook):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)
    with connect(app_config) as conn:
        before = load_review_payload_in_conn(conn, opportunity_code)
    assert before == {
        "opportunity_code": opportunity_code,
        "issue_code": issue_code,
        "issue_status": "pending_export",
        "correction_value": None,
        "correction_note": None,
        "verified_recoverable_amount": None,
        "realized_saving_amount": None,
        "review_note": None,
        "reviewed_at": None,
    }
    assert review_payload_fields(before) == {
        "issue_code": issue_code,
        "issue_status": "pending_export",
        "correction_value": None,
        "correction_note": None,
        "verified_recoverable_amount": None,
        "realized_saving_amount": None,
        "review_note": None,
        "reviewed_at": None,
    }


@pytest.mark.parametrize(
    ("status", "count_field", "detail_field"),
    [
        ("pending_export", "pending_count", None),
        ("pending_correction", "pending_count", None),
        ("still_invalid", "pending_count", None),
        ("returned", "review_count", "returned_count"),
        ("needs_review", "review_count", "needs_review_count"),
        ("closed", "closed_count", None),
        ("not_required", "closed_count", None),
        ("resolved_by_reaudit", "closed_count", None),
    ],
)
def test_review_summary_groups_issue_statuses_and_sums_review_amounts(
    app_config, sample_workbook, status, count_field, detail_field
):
    batch_id, opportunity_code, issue_code = _electricity_opportunity(app_config, sample_workbook)
    with connect(app_config) as conn:
        opportunity = match_opportunity_in_conn(conn, opportunity_code)
        upsert_review_in_conn(conn, opportunity, 1200.556, 800.444, "核查完成")
        conn.execute("update issues set status = ? where issue_code = ?", (status, issue_code))

        summary = review_summary_in_conn(conn, batch_id, "electricity")

    expected = {
        "pending_count": 0,
        "returned_count": 0,
        "needs_review_count": 0,
        "review_count": 0,
        "closed_count": 0,
        "verified_recoverable_amount": 1200.56,
        "realized_saving_amount": 800.44,
        count_field: 1,
    }
    if detail_field:
        expected[detail_field] = 1
    assert summary == expected
