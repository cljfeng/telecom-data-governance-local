# Tower Rent Anomaly Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local single-machine tower rent anomaly analysis module that turns existing tower rent audit findings into a business-facing abnormal clue list with recoverable, discount-realization, and review-needed amounts.

**Architecture:** Reuse the existing `analysis_opportunities` table with `domain = "tower_rent"` and mirror the electricity analysis shape where it helps. Add a focused tower rent service, route it through local API endpoints, and add a static frontend page named “租费异常分析” with an “异常线索清单”.

**Tech Stack:** Python 3, SQLite, stdlib HTTP server routing, openpyxl, vanilla ES modules, pytest.

---

## File Structure

- Create `src/governance_app/tower_rent_analysis.py`: generate, summarize, list, and export tower rent anomaly clues.
- Modify `src/governance_app/server.py`: add `/api/batches/{batch_id}/tower-rent-analysis/*` route dispatch.
- Create `src/governance_app/static/tower-rent-analysis.js`: render the “租费异常分析” page.
- Modify `src/governance_app/static/app.js`: register and render the new view.
- Modify `src/governance_app/static/index.html`: add the navigation button.
- Create `tests/test_tower_rent_analysis.py`: service-level tests.
- Modify `tests/test_server.py`: API contract tests.

## Task 1: Tower Rent Analysis Service

**Files:**
- Create: `src/governance_app/tower_rent_analysis.py`
- Test: `tests/test_tower_rent_analysis.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_tower_rent_analysis.py`:

```python
import json

from openpyxl import load_workbook

from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.tower_rent_analysis import (
    export_tower_rent_clues,
    get_tower_rent_clues,
    get_tower_rent_summary,
    run_tower_rent_analysis,
)


def _create_batch(conn):
    return conn.execute(
        "insert into import_batches(source_file, name, batch_code, status) values (?, ?, ?, ?)",
        ("tower-rent-test.xlsx", "租费测试批次", "BATCH-TOWER", "imported"),
    ).lastrowid


def _insert_row(conn, batch_id, row):
    row_json = json.dumps(row, ensure_ascii=False)
    raw_id = conn.execute(
        "insert into raw_rows(batch_id, ledger_type, sheet_name, row_number, row_json) values (?, 'tower_rent', '铁塔租费台账', ?, ?)",
        (batch_id, row.get("_row_number", 2), row_json),
    ).lastrowid
    return conn.execute(
        """
        insert into ledger_rows(
            batch_id, ledger_type, city, district, telecom_site_code, telecom_site_name,
            tower_site_code, tower_site_name, raw_row_id, row_json, sheet_name, row_number
        ) values (?, 'tower_rent', ?, ?, ?, ?, ?, ?, ?, '{}', '铁塔租费台账', ?)
        """,
        (
            batch_id,
            row.get("地市"),
            row.get("区县"),
            row.get("电信站址编码"),
            row.get("电信站址名称"),
            row.get("铁塔站址编码"),
            row.get("铁塔站址名称"),
            raw_id,
            row.get("_row_number", 2),
        ),
    ).lastrowid


def _audited_tower_batch(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        batch_id = _create_batch(conn)
        rows = [
            {
                "_row_number": 2,
                "地市": "杭州",
                "区县": "西湖",
                "电信站址编码": "T001",
                "电信站址名称": "一站",
                "铁塔站址编码": "TT001",
                "铁塔站址名称": "铁塔一站",
                "账期": "2026-03",
                "订单号": "O001",
                "业务确认单号": "B001",
                "铁塔产品": "普通地面塔A",
                "铁塔共享用户数": 2,
                "机房产品": "自建机房",
                "机房共享用户数": 1,
                "维护费(元/年)": 100,
                "场地费(元/年)": 200,
                "电力引入费(元/年)": 300,
                "产品服务费合计（元/年）（不含税）": 500,
                "铁塔产品单元数": 0,
                "机房产品单元数": 0,
                "配套产品单元数": 0,
                "铁塔共享信息": "共享",
                "维护费共享折扣": 0.9,
                "站址共享信息": "原产权方",
                "挂高": 45,
                "塔高": 40,
            },
            {
                "_row_number": 3,
                "地市": "杭州",
                "区县": "西湖",
                "电信站址编码": "T001",
                "电信站址名称": "一站",
                "铁塔站址编码": "TT001",
                "铁塔站址名称": "铁塔一站",
                "账期": "2026-03",
                "订单号": "O002",
                "业务确认单号": "B001",
                "铁塔产品": "普通地面塔A",
                "铁塔共享用户数": 3,
                "机房产品": "自建机房",
                "机房共享用户数": 2,
                "维护费(元/年)": 50,
                "场地费(元/年)": 100,
                "电力引入费(元/年)": 100,
                "产品服务费合计（元/年）（不含税）": 0,
                "铁塔产品单元数": 1,
                "机房产品单元数": 0,
                "配套产品单元数": 0,
                "铁塔共享信息": "共享",
                "维护费共享折扣": 0.7,
                "站址共享信息": "共享方",
                "挂高": 35,
                "塔高": 35,
            },
        ]
        for row in rows:
            _insert_row(conn, batch_id, row)
    run_audit(app_config, batch_id)
    return batch_id


def test_run_tower_rent_analysis_creates_clues(app_config):
    batch_id = _audited_tower_batch(app_config)

    result = run_tower_rent_analysis(app_config, batch_id)
    clues = get_tower_rent_clues(app_config, batch_id)

    assert result["clue_count"] == len(clues)
    assert result["clue_count"] >= 5
    assert all(item["domain"] == "tower_rent" for item in clues)
    assert any(item["recoverable_amount"] > 0 for item in clues)
    assert any(item["review_amount"] > 0 for item in clues)


def test_run_tower_rent_analysis_refreshes_existing_rows(app_config):
    batch_id = _audited_tower_batch(app_config)

    first = run_tower_rent_analysis(app_config, batch_id)
    second = run_tower_rent_analysis(app_config, batch_id)

    with connect(app_config) as conn:
        count = conn.execute(
            "select count(*) as c from analysis_opportunities where batch_id = ? and domain = 'tower_rent'",
            (batch_id,),
        ).fetchone()["c"]
    assert count == second["clue_count"]
    assert first["clue_count"] == second["clue_count"]


def test_tower_rent_summary_groups_amounts(app_config):
    batch_id = _audited_tower_batch(app_config)
    run_tower_rent_analysis(app_config, batch_id)

    summary = get_tower_rent_summary(app_config, batch_id)

    assert summary["batch_id"] == batch_id
    assert summary["total_rent_amount"] == 1250
    assert summary["recoverable_amount"] > 0
    assert summary["review_amount"] > 0
    assert isinstance(summary["city_rankings"], list)
    assert isinstance(summary["type_breakdown"], list)


def test_tower_rent_clue_filters(app_config):
    batch_id = _audited_tower_batch(app_config)
    run_tower_rent_analysis(app_config, batch_id)
    all_rows = get_tower_rent_clues(app_config, batch_id)

    filtered = get_tower_rent_clues(
        app_config,
        batch_id,
        filters={"opportunity_type": all_rows[0]["opportunity_type"], "confidence": all_rows[0]["confidence"]},
    )

    assert filtered
    assert all(row["opportunity_type"] == all_rows[0]["opportunity_type"] for row in filtered)
    assert all(row["confidence"] == all_rows[0]["confidence"] for row in filtered)


def test_export_tower_rent_clues_writes_workbook(app_config):
    batch_id = _audited_tower_batch(app_config)
    run_tower_rent_analysis(app_config, batch_id)

    path = export_tower_rent_clues(app_config, batch_id)

    assert path.exists()
    assert "租费异常线索清单" in path.name
    wb = load_workbook(path)
    assert {"填写说明", "异常线索清单", "地市汇总", "异常分类汇总"}.issubset(set(wb.sheetnames))
    headers = [cell.value for cell in wb["异常线索清单"][1]]
    assert "预计可追回金额" in headers
    assert "优惠落实金额" in headers
    assert "待核查金额" in headers
```

- [ ] **Step 2: Run tests to verify import failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_tower_rent_analysis.py -v`

Expected: FAIL with `ModuleNotFoundError` for `governance_app.tower_rent_analysis`.

- [ ] **Step 3: Implement `tower_rent_analysis.py`**

Create `src/governance_app/tower_rent_analysis.py`:

```python
import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from governance_app.audit_rules import parse_row
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.geo import normalize_city
from governance_app.rule_fields import (
    AMOUNT_FIELD_KEYWORDS,
    MAINTENANCE_DISCOUNT_FIELDS,
    PERIOD_FIELDS,
    POWER_INTRO_FEE_FIELDS,
    PRODUCT_SERVICE_FEE_FIELDS,
    TOWER_FEE_FIELDS,
)
from governance_app.rule_helpers import _first_value, _number, _period_key, _positive_or_zero_field, _text

TOWER_RENT_DOMAIN = "tower_rent"

RECOVERABLE_RULE_TYPES = {
    "tower_duplicate_product_service_fee": "重复计费",
    "tower_duplicate_maintenance_fee": "重复计费",
    "tower_duplicate_site_fee": "重复计费",
    "tower_duplicate_power_intro_fee": "重复计费",
    "tower_product_units_zero_fee_nonzero": "产品单元异常",
    "tower_original_owner_power_intro_fee_nonzero": "原产权方费用异常",
    "tower_stopped_site_still_charged": "停租仍计费",
    "tower_charged_after_stop_period": "停租仍计费",
    "fee_paid_without_master_site": "无站址仍付费",
}

DISCOUNT_RULE_TYPES = {
    "tower_maintenance_discount_not_lowest": "共享折扣异常",
}

REVIEW_RULE_TYPES = {
    "tower_product_shared_users_inconsistent": "共享用户数异常",
    "tower_room_shared_users_inconsistent": "共享用户数异常",
    "tower_confirmation_product_changed": "产品属性异常",
    "tower_mount_height_exceeds_tower_height": "基础属性异常",
    "tower_site_height_inconsistent": "基础属性异常",
    "fee_amount_period_spike": "金额环比突变",
}


def run_tower_rent_analysis(config: AppConfig, batch_id: int) -> dict[str, int]:
    with connect(config) as conn:
        batch = conn.execute("select id, status, is_archived from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("批次不存在")
        if batch["is_archived"]:
            raise ValueError("归档批次不允许刷新租费异常分析")
        if batch["status"] not in {"audited", "exported", "returned", "archived"}:
            raise ValueError("请先执行稽核，再生成租费异常分析")
        rent_count = conn.execute(
            "select count(*) as c from ledger_rows where batch_id = ? and ledger_type = 'tower_rent'",
            (batch_id,),
        ).fetchone()["c"]
        if not rent_count:
            raise ValueError("当前批次没有铁塔租费台账，无法生成租费异常分析")

        conn.execute("delete from analysis_opportunities where batch_id = ? and domain = ?", (batch_id, TOWER_RENT_DOMAIN))
        inserted = 0
        for issue in _issue_rows(conn, batch_id):
            row_data = parse_row(issue["row_json"])
            payload = _clue_from_issue(batch_id, issue, row_data)
            if payload is None:
                continue
            conn.execute(
                """
                insert into analysis_opportunities(
                    batch_id, ledger_row_id, domain, opportunity_code, opportunity_type, severity,
                    city, district, telecom_site_code, telecom_site_name, period, meter_no,
                    current_amount, reference_amount, recoverable_amount, saving_opportunity_amount,
                    confidence, source_rule_ids_json, message, suggestion
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    issue["ledger_row_id"],
                    TOWER_RENT_DOMAIN,
                    payload["opportunity_code"],
                    payload["opportunity_type"],
                    issue["severity"],
                    normalize_city(issue["city"]),
                    issue["district"],
                    issue["telecom_site_code"],
                    issue["telecom_site_name"],
                    payload["period"],
                    "",
                    payload["current_amount"],
                    payload["reference_amount"],
                    payload["recoverable_amount"],
                    payload["saving_opportunity_amount"],
                    payload["confidence"],
                    json.dumps([issue["rule_id"]], ensure_ascii=False),
                    payload["message"],
                    payload["suggestion"],
                ),
            )
            inserted += 1
        conn.execute(
            "insert into operation_logs(batch_id, operation, message) values (?, ?, ?)",
            (batch_id, "tower_rent_analysis", f"生成租费异常线索 {inserted} 条"),
        )
        return {"clue_count": inserted}
```

- [ ] **Step 4: Add query and export functions**

Add the remaining public functions to `src/governance_app/tower_rent_analysis.py`:

```python
def get_tower_rent_summary(config: AppConfig, batch_id: int) -> dict[str, Any]:
    with connect(config) as conn:
        _require_batch(conn, batch_id)
        ledger = conn.execute(
            """
            select count(*) as row_count,
                   count(distinct telecom_site_code) as site_count
              from ledger_rows
             where batch_id = ? and ledger_type = 'tower_rent'
            """,
            (batch_id,),
        ).fetchone()
        total_amount = 0.0
        for row in conn.execute(
            """
            select case
                       when lr.row_json is not null and lr.row_json <> '{}' then lr.row_json
                       else coalesce(rr.row_json, lr.row_json)
                   end as row_json
              from ledger_rows lr
              left join raw_rows rr on rr.id = lr.raw_row_id
             where lr.batch_id = ? and lr.ledger_type = 'tower_rent'
            """,
            (batch_id,),
        ):
            total_amount += _total_rent_amount(parse_row(row["row_json"]))
        amount_row = conn.execute(
            """
            select count(*) as clue_count,
                   count(distinct telecom_site_code) as abnormal_site_count,
                   coalesce(sum(current_amount), 0) as current_amount,
                   coalesce(sum(recoverable_amount), 0) as recoverable_amount,
                   coalesce(sum(saving_opportunity_amount), 0) as saving_opportunity_amount,
                   sum(case when severity = 'high' then 1 else 0 end) as high_risk_count
              from analysis_opportunities
             where batch_id = ? and domain = ?
            """,
            (batch_id, TOWER_RENT_DOMAIN),
        ).fetchone()
        recoverable = float(amount_row["recoverable_amount"] or 0)
        discount = float(amount_row["saving_opportunity_amount"] or 0)
        current = float(amount_row["current_amount"] or 0)
        return {
            "batch_id": batch_id,
            "ledger_row_count": int(ledger["row_count"] or 0),
            "site_count": int(ledger["site_count"] or 0),
            "total_rent_amount": round(total_amount, 2),
            "abnormal_site_count": int(amount_row["abnormal_site_count"] or 0),
            "clue_count": int(amount_row["clue_count"] or 0),
            "recoverable_amount": round(recoverable, 2),
            "discount_realization_amount": round(discount, 2),
            "review_amount": round(max(current - recoverable - discount, 0), 2),
            "high_risk_count": int(amount_row["high_risk_count"] or 0),
            "city_rankings": _city_rankings(conn, batch_id),
            "type_breakdown": _type_breakdown(conn, batch_id),
        }


def get_tower_rent_clues(config: AppConfig, batch_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    where = ["batch_id = ?", "domain = ?"]
    params: list[Any] = [batch_id, TOWER_RENT_DOMAIN]
    for key in ("city", "opportunity_type", "severity", "confidence"):
        value = filters.get(key)
        if value:
            where.append(f"{key} = ?")
            params.append(value)
    with connect(config) as conn:
        _require_batch(conn, batch_id)
        rows = conn.execute(
            f"""
            select *
              from analysis_opportunities
             where {" and ".join(where)}
             order by recoverable_amount desc, saving_opportunity_amount desc, current_amount desc, id
            """,
            params,
        ).fetchall()
    return [_clue_payload(row) for row in rows]


def export_tower_rent_clues(config: AppConfig, batch_id: int) -> Path:
    summary = get_tower_rent_summary(config, batch_id)
    clues = get_tower_rent_clues(config, batch_id)
    config.export_dir.mkdir(parents=True, exist_ok=True)
    path = config.export_dir / f"批次{batch_id}_租费异常线索清单.xlsx"
    wb = Workbook()
    guide = wb.active
    guide.title = "填写说明"
    guide.append(["租费异常线索清单"])
    guide.append(["预计可追回金额用于相对确定的问题；优惠落实金额用于折扣优惠类线索；待核查金额代表核查范围，不等同于确定损失。"])
    ws = wb.create_sheet("异常线索清单")
    ws.append(["线索编号", "地市", "区县", "站址编码", "站址名称", "账期", "异常类型", "风险等级", "当前金额", "参考金额", "预计可追回金额", "优惠落实金额", "待核查金额", "置信度", "来源规则", "问题说明", "建议动作"])
    for item in clues:
        ws.append([
            item["opportunity_code"],
            item["city"],
            item["district"],
            item["telecom_site_code"],
            item["telecom_site_name"],
            item["period"],
            item["opportunity_type"],
            item["severity"],
            item["current_amount"],
            item["reference_amount"],
            item["recoverable_amount"],
            item["discount_realization_amount"],
            item["review_amount"],
            item["confidence"],
            ",".join(item["source_rule_ids"]),
            item["message"],
            item["suggestion"],
        ])
    city = wb.create_sheet("地市汇总")
    city.append(["地市", "线索数量", "预计可追回金额", "优惠落实金额", "待核查金额"])
    for item in summary["city_rankings"]:
        city.append([item["city"], item["clue_count"], item["recoverable_amount"], item["discount_realization_amount"], item["review_amount"]])
    type_ws = wb.create_sheet("异常分类汇总")
    type_ws.append(["异常类型", "线索数量", "预计可追回金额", "优惠落实金额", "待核查金额"])
    for item in summary["type_breakdown"]:
        type_ws.append([item["opportunity_type"], item["clue_count"], item["recoverable_amount"], item["discount_realization_amount"], item["review_amount"]])
    wb.save(path)
    return path
```

- [ ] **Step 5: Add private helpers**

Add the private helpers:

```python
def _issue_rows(conn, batch_id: int):
    return conn.execute(
        """
        select i.id, i.rule_id, i.severity, i.city, i.district, i.telecom_site_code, i.telecom_site_name,
               i.message, i.suggestion, ar.ledger_row_id,
               case
                   when lr.row_json is not null and lr.row_json <> '{}' then lr.row_json
                   else coalesce(rr.row_json, lr.row_json)
               end as row_json
          from issues i
          join audit_results ar on ar.id = i.audit_result_id
          join ledger_rows lr on lr.id = ar.ledger_row_id
          left join raw_rows rr on rr.id = lr.raw_row_id
         where i.batch_id = ? and i.ledger_type = 'tower_rent'
         order by i.id
        """,
        (batch_id,),
    ).fetchall()


def _clue_from_issue(batch_id: int, issue, row: dict[str, Any]) -> dict[str, Any] | None:
    rule_id = issue["rule_id"]
    opportunity_type = RECOVERABLE_RULE_TYPES.get(rule_id) or DISCOUNT_RULE_TYPES.get(rule_id) or REVIEW_RULE_TYPES.get(rule_id)
    if opportunity_type is None:
        return None
    current_amount = _amount_for_rule(rule_id, row)
    recoverable = current_amount if rule_id in RECOVERABLE_RULE_TYPES else 0.0
    discount = _discount_amount(rule_id, current_amount, row)
    confidence = "high" if rule_id in RECOVERABLE_RULE_TYPES else "medium"
    if rule_id in REVIEW_RULE_TYPES:
        confidence = "medium" if current_amount else "low"
    return {
        "opportunity_code": _clue_code(batch_id, issue["ledger_row_id"], opportunity_type, rule_id),
        "opportunity_type": opportunity_type,
        "period": _period(row),
        "current_amount": current_amount,
        "reference_amount": round(max(current_amount - recoverable - discount, 0), 2),
        "recoverable_amount": recoverable,
        "saving_opportunity_amount": discount,
        "confidence": confidence,
        "message": issue["message"],
        "suggestion": _suggestion(rule_id, issue["suggestion"]),
    }


def _amount_for_rule(rule_id: str, row: dict[str, Any]) -> float:
    field_map = {
        "tower_duplicate_product_service_fee": PRODUCT_SERVICE_FEE_FIELDS,
        "tower_duplicate_maintenance_fee": ("维护费(元/年)", "维护费"),
        "tower_duplicate_site_fee": ("场地费(元/年)", "场地费"),
        "tower_duplicate_power_intro_fee": POWER_INTRO_FEE_FIELDS,
        "tower_original_owner_power_intro_fee_nonzero": POWER_INTRO_FEE_FIELDS,
        "tower_product_units_zero_fee_nonzero": PRODUCT_SERVICE_FEE_FIELDS,
    }
    fields = field_map.get(rule_id)
    if fields:
        return _money(_first_value(row, fields))
    return _total_rent_amount(row)


def _discount_amount(rule_id: str, current_amount: float, row: dict[str, Any]) -> float:
    if rule_id != "tower_maintenance_discount_not_lowest":
        return 0.0
    maintenance = _money(_first_value(row, ("维护费(元/年)", "维护费")))
    discount = _number(_first_value(row, MAINTENANCE_DISCOUNT_FIELDS))
    if maintenance <= 0 or discount is None or float(discount) <= 0:
        return 0.0
    reference_discount = 0.7
    return round(max(maintenance - (maintenance / float(discount)) * reference_discount, 0), 2)


def _total_rent_amount(row: dict[str, Any]) -> float:
    total = 0.0
    for field in TOWER_FEE_FIELDS:
        total += _money(row.get(field))
    if total:
        return round(total, 2)
    for key, value in row.items():
        if any(keyword in str(key) for keyword in AMOUNT_FIELD_KEYWORDS):
            total += _money(value)
    return round(total, 2)


def _require_batch(conn, batch_id: int) -> None:
    if conn.execute("select 1 from import_batches where id = ?", (batch_id,)).fetchone() is None:
        raise ValueError("批次不存在")


def _clue_payload(row) -> dict[str, Any]:
    payload = dict(row)
    payload["source_rule_ids"] = json.loads(payload.pop("source_rule_ids_json") or "[]")
    for field in ("current_amount", "reference_amount", "recoverable_amount", "saving_opportunity_amount"):
        payload[field] = round(float(payload[field] or 0), 2)
    payload["discount_realization_amount"] = payload["saving_opportunity_amount"]
    payload["review_amount"] = round(max(payload["current_amount"] - payload["recoverable_amount"] - payload["discount_realization_amount"], 0), 2)
    return payload


def _city_rankings(conn, batch_id: int) -> list[dict[str, Any]]:
    return [_summary_payload(row, "city") for row in conn.execute(
        """
        select coalesce(city, '未填地市') as city,
               count(*) as clue_count,
               coalesce(sum(current_amount), 0) as current_amount,
               coalesce(sum(recoverable_amount), 0) as recoverable_amount,
               coalesce(sum(saving_opportunity_amount), 0) as saving_opportunity_amount
          from analysis_opportunities
         where batch_id = ? and domain = ?
         group by coalesce(city, '未填地市')
         order by recoverable_amount desc, saving_opportunity_amount desc, current_amount desc
        """,
        (batch_id, TOWER_RENT_DOMAIN),
    )]


def _type_breakdown(conn, batch_id: int) -> list[dict[str, Any]]:
    return [_summary_payload(row, "opportunity_type") for row in conn.execute(
        """
        select opportunity_type,
               count(*) as clue_count,
               coalesce(sum(current_amount), 0) as current_amount,
               coalesce(sum(recoverable_amount), 0) as recoverable_amount,
               coalesce(sum(saving_opportunity_amount), 0) as saving_opportunity_amount
          from analysis_opportunities
         where batch_id = ? and domain = ?
         group by opportunity_type
         order by recoverable_amount desc, saving_opportunity_amount desc, current_amount desc
        """,
        (batch_id, TOWER_RENT_DOMAIN),
    )]


def _summary_payload(row, label_field: str) -> dict[str, Any]:
    current = float(row["current_amount"] or 0)
    recoverable = float(row["recoverable_amount"] or 0)
    discount = float(row["saving_opportunity_amount"] or 0)
    label = normalize_city(row[label_field]) if label_field == "city" else row[label_field]
    return {
        label_field: label,
        "clue_count": int(row["clue_count"] or 0),
        "recoverable_amount": round(recoverable, 2),
        "discount_realization_amount": round(discount, 2),
        "review_amount": round(max(current - recoverable - discount, 0), 2),
    }


def _money(value: Any) -> float:
    number = _number(value)
    return round(float(number or 0), 2)


def _period(row: dict[str, Any]) -> str:
    return _period_key(_first_value(row, PERIOD_FIELDS)) or ""


def _clue_code(batch_id: int, ledger_row_id: int, opportunity_type: str, rule_id: str) -> str:
    digest = hashlib.sha1(f"{batch_id}:{ledger_row_id}:{opportunity_type}:{rule_id}".encode("utf-8")).hexdigest()[:10]
    return f"RENT-{batch_id}-{digest}"


def _suggestion(rule_id: str, fallback: str) -> str:
    action = {
        "tower_duplicate_product_service_fee": "核实同账期同站址产品服务费是否重复计费，确认后追回重复费用",
        "tower_duplicate_maintenance_fee": "核实维护费是否重复计费，确认后追回重复费用",
        "tower_duplicate_site_fee": "核实场地费是否重复计费，确认后追回重复费用",
        "tower_duplicate_power_intro_fee": "核实电力引入费是否重复计费，确认后追回重复费用",
        "tower_product_units_zero_fee_nonzero": "核对产品单元数和费用生成口径，产品单元为零时应停止或冲减费用",
        "tower_original_owner_power_intro_fee_nonzero": "核实原产权方站址电力引入费收取依据，确认后冲减或追回",
        "tower_stopped_site_still_charged": "核实停租状态、账期和费用生成口径，确认后停止计费或追回",
        "tower_charged_after_stop_period": "核实停租日期和账期，确认后追回停租后费用",
        "tower_maintenance_discount_not_lowest": "核对共享折扣政策和适用用户数，推动维护费优惠落实",
        "tower_product_shared_users_inconsistent": "核对共享用户数、业务确认单和合同计费参数",
        "tower_room_shared_users_inconsistent": "核对机房共享用户数、业务确认单和合同计费参数",
        "tower_confirmation_product_changed": "核对业务确认单产品变更依据",
        "tower_mount_height_exceeds_tower_height": "核对塔高和挂高基础属性，复核是否影响租费计价",
        "tower_site_height_inconsistent": "统一同站址塔高基础属性，复核是否影响租费计价",
        "fee_paid_without_master_site": "暂停支付并核实站址主数据、费用依据和报账归属",
        "fee_amount_period_spike": "核对账期费用、调账冲销和重复计费原因",
    }.get(rule_id)
    return action or fallback
```

- [ ] **Step 6: Run service tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_tower_rent_analysis.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/governance_app/tower_rent_analysis.py tests/test_tower_rent_analysis.py
git commit -m "Add tower rent analysis service"
```

## Task 2: Tower Rent API Endpoints

**Files:**
- Modify: `src/governance_app/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing endpoint tests**

Append to `tests/test_server.py`:

```python
def test_tower_rent_analysis_rejects_invalid_batch_path(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, _headers, body = app.handle_test_request("POST", "/api/batches/not-a-number/tower-rent-analysis/run")

    assert status == 400
    assert json.loads(body)["error"] == "invalid batch_id"


def test_tower_rent_analysis_endpoint_reports_missing_batch(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, _headers, body = app.handle_test_request("GET", "/api/batches/999/tower-rent-analysis/summary")

    assert status == 400
    assert json.loads(body)["error"] == "批次不存在"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_server.py::test_tower_rent_analysis_rejects_invalid_batch_path tests/test_server.py::test_tower_rent_analysis_endpoint_reports_missing_batch -v`

Expected: FAIL with 404 because routes do not exist.

- [ ] **Step 3: Import service functions**

In `src/governance_app/server.py`, add:

```python
from governance_app.tower_rent_analysis import (
    export_tower_rent_clues,
    get_tower_rent_clues,
    get_tower_rent_summary,
    run_tower_rent_analysis,
)
```

- [ ] **Step 4: Generalize the analysis path helper**

Replace `_electricity_analysis_path()` with:

```python
def _analysis_path(path: str) -> tuple[str, int, str] | None:
    parts = path.strip("/").split("/")
    actions = {"run", "summary", "opportunities", "export"}
    domains = {"electricity-analysis", "tower-rent-analysis"}
    if len(parts) < 4 or parts[:2] != ["api", "batches"]:
        return None
    if len(parts) != 5 or parts[3] not in domains or parts[4] not in actions:
        return None
    try:
        batch_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("invalid batch_id") from exc
    return parts[3], batch_id, parts[4]
```

- [ ] **Step 5: Dispatch both analysis domains**

In `_route()`, replace the electricity-specific dispatch block with:

```python
    try:
        analysis_path = _analysis_path(parsed.path)
    except ValueError as exc:
        return _json({"error": str(exc)}, status=400)
    if analysis_path is not None:
        domain, batch_id, action = analysis_path
        try:
            if domain == "electricity-analysis":
                if method == "POST" and action == "run":
                    return _json(run_electricity_analysis(config, batch_id))
                if method == "GET" and action == "summary":
                    return _json(get_electricity_summary(config, batch_id))
                if method == "GET" and action == "opportunities":
                    query = parse_qs(parsed.query)
                    filters = {key: values[0] for key, values in query.items() if values and values[0]}
                    return _json({"opportunities": get_electricity_opportunities(config, batch_id, filters=filters)})
                if method == "POST" and action == "export":
                    path_value = export_electricity_opportunities(config, batch_id)
                    return _json({"path": str(path_value)})
            if domain == "tower-rent-analysis":
                if method == "POST" and action == "run":
                    return _json(run_tower_rent_analysis(config, batch_id))
                if method == "GET" and action == "summary":
                    return _json(get_tower_rent_summary(config, batch_id))
                if method == "GET" and action == "opportunities":
                    query = parse_qs(parsed.query)
                    filters = {key: values[0] for key, values in query.items() if values and values[0]}
                    return _json({"opportunities": get_tower_rent_clues(config, batch_id, filters=filters)})
                if method == "POST" and action == "export":
                    path_value = export_tower_rent_clues(config, batch_id)
                    return _json({"path": str(path_value)})
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"error": "not found"}, status=404)
```

- [ ] **Step 6: Run endpoint tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_server.py::test_electricity_analysis_endpoints tests/test_server.py::test_tower_rent_analysis_rejects_invalid_batch_path tests/test_server.py::test_tower_rent_analysis_endpoint_reports_missing_batch -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/governance_app/server.py tests/test_server.py
git commit -m "Expose tower rent analysis endpoints"
```

## Task 3: Tower Rent Frontend View

**Files:**
- Create: `src/governance_app/static/tower-rent-analysis.js`
- Modify: `src/governance_app/static/app.js`
- Modify: `src/governance_app/static/index.html`

- [ ] **Step 1: Create the page module**

Create `src/governance_app/static/tower-rent-analysis.js`:

```javascript
import { fetchJson, postJson } from "/api.js?v=20260517-1";

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function optionRows(rows, field, label) {
  const values = [...new Set(rows.map((row) => row[field]).filter(Boolean))];
  return [`<option value="">全部${label}</option>`, ...values.map((value) => `<option value="${value}">${value}</option>`)].join("");
}

export async function renderTowerRentAnalysis(ctx) {
  await ctx.refreshBatches().catch(() => []);
  const batch = ctx.currentBatch();
  if (!batch) {
    ctx.renderNoBatchPrompt("还没有可分析的批次。");
    return;
  }
  ctx.mainContent.innerHTML = `
    <section class="card">
      ${ctx.shellHeader("租费异常分析", `${batch.batch_code || `#${batch.id}`} ${batch.name}`, ctx.renderBatchSelector())}
      <div class="button-row">
        <button id="run-tower-rent-analysis" class="primary-button" type="button">生成分析</button>
        <button id="export-tower-rent-analysis" class="secondary-button" type="button">导出 Excel</button>
      </div>
      <div id="tower-rent-analysis-result" class="result-box">可先生成分析，再查看异常线索清单。</div>
    </section>
    <section class="card metric-section">
      <div id="tower-rent-summary" class="metric-grid"></div>
    </section>
    <div class="dashboard-grid">
      <section class="card">${ctx.shellHeader("异常分类", "分类")}<div id="tower-rent-type-breakdown" class="risk-summary"></div></section>
      <section class="card">${ctx.shellHeader("地市排行", "地市")}<div id="tower-rent-city-ranking" class="risk-summary"></div></section>
    </div>
    <section class="card">
      ${ctx.shellHeader("异常线索清单", "线索")}
      <div class="toolbar">
        <label class="compact-field"><span>异常类型</span><select id="tower-rent-type-filter"><option value="">全部类型</option></select></label>
        <label class="compact-field"><span>置信度</span><select id="tower-rent-confidence-filter"><option value="">全部置信度</option></select></label>
        <button id="apply-tower-rent-filters" class="secondary-button" type="button">筛选</button>
      </div>
      <div class="table-wrap"><table><thead><tr><th>地市</th><th>站址</th><th>账期</th><th>类型</th><th>当前金额</th><th>预计可追回</th><th>优惠落实</th><th>待核查</th><th>置信度</th><th>建议动作</th></tr></thead><tbody id="tower-rent-clue-table"><tr><td colspan="10">正在加载</td></tr></tbody></table></div>
    </section>
  `;
  ctx.bindBatchSelector(() => renderTowerRentAnalysis(ctx));
  document.querySelector("#run-tower-rent-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "生成中...", async () => {
      const result = document.querySelector("#tower-rent-analysis-result");
      result.className = "result-box result-pending";
      result.textContent = "正在生成租费异常线索...";
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/run`, {});
        result.className = "result-box result-success";
        result.textContent = `已生成 ${ctx.formatNumber(data.clue_count)} 条租费异常线索。`;
        await loadTowerRentAnalysisData(ctx);
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#export-tower-rent-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "导出中...", async () => {
      const result = document.querySelector("#tower-rent-analysis-result");
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/export`, {});
        result.className = "result-box result-success";
        result.textContent = `已导出：${data.path}`;
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#apply-tower-rent-filters").addEventListener("click", () => loadTowerRentAnalysisData(ctx));
  await loadTowerRentAnalysisData(ctx);
}

async function loadTowerRentAnalysisData(ctx) {
  const type = document.querySelector("#tower-rent-type-filter")?.value || "";
  const confidence = document.querySelector("#tower-rent-confidence-filter")?.value || "";
  const query = new URLSearchParams();
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  try {
    const [summary, list] = await Promise.all([
      fetchJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/summary`),
      fetchJson(`/api/batches/${ctx.state.batchId}/tower-rent-analysis/opportunities${query.toString() ? `?${query}` : ""}`),
    ]);
    renderSummary(ctx, summary);
    renderBreakdown(ctx, summary);
    renderRows(ctx, list.opportunities || []);
  } catch (error) {
    document.querySelector("#tower-rent-summary").innerHTML = [
      ctx.metricCard("租费总额", 0, "等待分析", "info"),
      ctx.metricCard("异常站址", 0, "等待分析", "warning"),
      ctx.metricCard("预计可追回金额", 0, "等待分析", "danger"),
      ctx.metricCard("优惠落实金额", 0, "等待分析", "success"),
      ctx.metricCard("待核查金额", 0, "等待分析", "review"),
    ].join("");
    document.querySelector("#tower-rent-clue-table").innerHTML = `<tr><td colspan="10">${ctx.escapeHtml(error.message)}</td></tr>`;
  }
}

function renderSummary(ctx, summary) {
  document.querySelector("#tower-rent-summary").innerHTML = [
    ctx.metricCard("租费总额", summary.total_rent_amount, `租费记录 ${ctx.formatNumber(summary.ledger_row_count)}`, "info"),
    ctx.metricCard("异常站址", summary.abnormal_site_count, `线索 ${ctx.formatNumber(summary.clue_count)} 条`, "warning"),
    ctx.metricCard("预计可追回金额", summary.recoverable_amount, "相对确定问题", "danger"),
    ctx.metricCard("优惠落实金额", summary.discount_realization_amount, "共享折扣等优惠线索", "success"),
    ctx.metricCard("待核查金额", summary.review_amount, `高风险 ${ctx.formatNumber(summary.high_risk_count)} 条`, "review"),
  ].join("");
}

function renderBreakdown(ctx, summary) {
  document.querySelector("#tower-rent-type-breakdown").innerHTML =
    (summary.type_breakdown || []).map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.opportunity_type)}</strong><span>${ctx.formatNumber(row.clue_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 优惠 ${money(row.discount_realization_amount)} / 待核查 ${money(row.review_amount)}</span></div>`).join("") || '<div class="empty-state">暂无异常分类</div>';
  document.querySelector("#tower-rent-city-ranking").innerHTML =
    (summary.city_rankings || []).map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.city)}</strong><span>${ctx.formatNumber(row.clue_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 优惠 ${money(row.discount_realization_amount)} / 待核查 ${money(row.review_amount)}</span></div>`).join("") || '<div class="empty-state">暂无地市排行</div>';
}

function renderRows(ctx, rows) {
  const typeFilter = document.querySelector("#tower-rent-type-filter");
  const confidenceFilter = document.querySelector("#tower-rent-confidence-filter");
  if (typeFilter && typeFilter.options.length <= 1) typeFilter.innerHTML = optionRows(rows, "opportunity_type", "类型");
  if (confidenceFilter && confidenceFilter.options.length <= 1) confidenceFilter.innerHTML = optionRows(rows, "confidence", "置信度");
  const tbody = document.querySelector("#tower-rent-clue-table");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10">暂无租费异常线索。可以先点击“生成分析”。</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${ctx.escapeHtml(row.city || "未填地市")}</td>
      <td><strong>${ctx.escapeHtml(row.telecom_site_code || "")}</strong><br>${ctx.escapeHtml(row.telecom_site_name || "")}</td>
      <td>${ctx.escapeHtml(row.period || "")}</td>
      <td>${ctx.escapeHtml(row.opportunity_type)}</td>
      <td>${money(row.current_amount)}</td>
      <td>${money(row.recoverable_amount)}</td>
      <td>${money(row.discount_realization_amount)}</td>
      <td>${money(row.review_amount)}</td>
      <td>${ctx.escapeHtml(row.confidence)}</td>
      <td>${ctx.escapeHtml(row.suggestion)}</td>
    </tr>
  `).join("");
}
```

- [ ] **Step 2: Wire the view in `app.js`**

In `src/governance_app/static/app.js`, add:

```javascript
import { renderTowerRentAnalysis } from "/tower-rent-analysis.js?v=20260708-1";
```

Add to `views`:

```javascript
  towerRentAnalysis: "租费异常分析",
```

Add to `activateView(view)` after `electricityAnalysis`:

```javascript
  if (view === "towerRentAnalysis")
    return renderTowerRentAnalysis({
      mainContent,
      state,
      refreshBatches,
      currentBatch,
      renderNoBatchPrompt,
      renderBatchSelector,
      bindBatchSelector,
      shellHeader,
      metricCard,
      escapeHtml,
      formatNumber,
      withBusy,
    });
```

- [ ] **Step 3: Add navigation**

In `src/governance_app/static/index.html`, under “归档分析” and after 电费压降分析, add:

```html
        <button class="nav-button" type="button" data-view="towerRentAnalysis">租费异常分析</button>
```

- [ ] **Step 4: Run frontend syntax check**

Run: `node --check src/governance_app/static/tower-rent-analysis.js && node --check src/governance_app/static/app.js`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governance_app/static/tower-rent-analysis.js src/governance_app/static/app.js src/governance_app/static/index.html
git commit -m "Add tower rent analysis frontend"
```

## Task 4: Full Verification

**Files:**
- Modify only files touched by previous tasks if verification finds an issue.

- [ ] **Step 1: Run service tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_tower_rent_analysis.py tests/test_electricity_analysis.py -v`

Expected: PASS.

- [ ] **Step 2: Run server tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_server.py -v`

Expected: PASS.

- [ ] **Step 3: Run complete project check**

Run: `scripts/check.sh`

Expected: PASS.

- [ ] **Step 4: Commit verification fixes if any**

If verification required edits:

```bash
git add src tests
git commit -m "Polish tower rent analysis workflow"
```

If no edits were needed, do not create an empty commit.

## Self-Review

- Spec coverage: service, API, frontend, export, naming, dual business amount labels, and review-needed amount are covered.
- Database scope: no new table or column is required because the plan reuses `analysis_opportunities` as specified.
- Deferred by design: shared helper extraction and richer contract-based discount calculations are second-phase work, not part of this first implementation.
- Type consistency: internal storage uses `recoverable_amount` and `saving_opportunity_amount`; API payload adds `discount_realization_amount` and `review_amount` for the rent-specific business labels.
