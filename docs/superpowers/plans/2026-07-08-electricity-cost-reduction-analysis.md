# Electricity Cost Reduction Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local single-machine electricity cost reduction analysis module that turns existing electricity audit findings into a business-facing opportunity list with recoverable amount and saving opportunity amount.

**Architecture:** Add a reusable `analysis_opportunities` table, then implement an electricity-focused analysis service that reads `ledger_rows`, `raw_rows`, and `issues`, computes opportunity rows, and exposes summary/list/export APIs. Add a static frontend view that calls these APIs and fits the existing local workbench navigation and card/table style.

**Tech Stack:** Python 3, SQLite, stdlib HTTP server routing, openpyxl, vanilla ES modules, pytest.

---

## File Structure

- Modify `src/governance_app/db.py`: create `analysis_opportunities` and supporting indexes during database initialization.
- Create `src/governance_app/electricity_analysis.py`: all electricity opportunity generation, summary/list queries, and Excel export logic.
- Modify `src/governance_app/server.py`: add `/api/batches/{batch_id}/electricity-analysis/*` route dispatch and JSON error mapping.
- Create `src/governance_app/static/electricity-analysis.js`: render the new page, run analysis, list opportunities, and export Excel.
- Modify `src/governance_app/static/app.js`: register the new view and delegate rendering to the new module.
- Modify `src/governance_app/static/index.html`: add the navigation button.
- Modify `src/governance_app/static/styles.css`: add compact table/filter/status styles only if existing classes are insufficient.
- Create `tests/test_electricity_analysis.py`: service-level tests for generation, amount calculation, refresh behavior, summary, filters, and export.
- Modify `tests/test_server.py`: API contract tests for run, summary, list, and export endpoints.

## Task 1: Database Foundation

**Files:**
- Modify: `src/governance_app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing schema test**

Append this test to `tests/test_db.py`:

```python
def test_initialize_database_creates_analysis_opportunities(app_config):
    initialize_database(app_config)

    with connect(app_config) as conn:
        columns = {row["name"] for row in conn.execute("pragma table_info(analysis_opportunities)")}
        indexes = {row["name"] for row in conn.execute("pragma index_list(analysis_opportunities)")}

    assert {
        "id",
        "batch_id",
        "ledger_row_id",
        "domain",
        "opportunity_code",
        "opportunity_type",
        "severity",
        "city",
        "district",
        "telecom_site_code",
        "telecom_site_name",
        "period",
        "meter_no",
        "current_amount",
        "reference_amount",
        "recoverable_amount",
        "saving_opportunity_amount",
        "confidence",
        "source_rule_ids_json",
        "message",
        "suggestion",
        "created_at",
    }.issubset(columns)
    assert "idx_analysis_opportunities_batch_domain_type" in indexes
    assert "idx_analysis_opportunities_batch_city" in indexes
```

- [ ] **Step 2: Run the failing test**

Run: `PYTHONPATH=src pytest tests/test_db.py::test_initialize_database_creates_analysis_opportunities -v`

Expected: FAIL because `analysis_opportunities` does not exist.

- [ ] **Step 3: Add the table and indexes**

In `initialize_database()` inside `src/governance_app/db.py`, add this SQL block after the `issues` table:

```sql
            create table if not exists analysis_opportunities (
                id integer primary key autoincrement,
                batch_id integer not null references import_batches(id) on delete cascade,
                ledger_row_id integer references ledger_rows(id) on delete cascade,
                domain text not null,
                opportunity_code text not null unique,
                opportunity_type text not null,
                severity text not null,
                city text,
                district text,
                telecom_site_code text,
                telecom_site_name text,
                period text,
                meter_no text,
                current_amount real not null default 0,
                reference_amount real not null default 0,
                recoverable_amount real not null default 0,
                saving_opportunity_amount real not null default 0,
                confidence text not null,
                source_rule_ids_json text not null default '[]',
                message text not null,
                suggestion text not null,
                created_at text not null default current_timestamp
            );

            create index if not exists idx_analysis_opportunities_batch_domain_type
                on analysis_opportunities(batch_id, domain, opportunity_type);

            create index if not exists idx_analysis_opportunities_batch_city
                on analysis_opportunities(batch_id, city);
```

- [ ] **Step 4: Run schema tests**

Run: `PYTHONPATH=src pytest tests/test_db.py::test_initialize_database_creates_analysis_opportunities tests/test_db.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/governance_app/db.py tests/test_db.py
git commit -m "Add analysis opportunities table"
```

## Task 2: Electricity Analysis Service Core

**Files:**
- Create: `src/governance_app/electricity_analysis.py`
- Test: `tests/test_electricity_analysis.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_electricity_analysis.py` with:

```python
import json

from openpyxl import load_workbook

from governance_app.audit_engine import run_audit
from governance_app.db import connect, initialize_database
from governance_app.electricity_analysis import (
    export_electricity_opportunities,
    get_electricity_opportunities,
    get_electricity_summary,
    run_electricity_analysis,
)
from governance_app.importer import import_workbook


def _import_and_audit(app_config, sample_workbook):
    initialize_database(app_config)
    imported = import_workbook(app_config, sample_workbook)
    run_audit(app_config, imported.batch_id)
    return imported.batch_id


def test_run_electricity_analysis_creates_recoverable_opportunities(app_config, sample_workbook):
    batch_id = _import_and_audit(app_config, sample_workbook)

    result = run_electricity_analysis(app_config, batch_id)

    opportunities = get_electricity_opportunities(app_config, batch_id)
    assert result["opportunity_count"] == len(opportunities)
    assert any(item["recoverable_amount"] >= 0 for item in opportunities)
    assert all(item["domain"] == "electricity" for item in opportunities)
    assert all("source_rule_ids" in item for item in opportunities)


def test_run_electricity_analysis_refreshes_existing_rows(app_config, sample_workbook):
    batch_id = _import_and_audit(app_config, sample_workbook)

    first = run_electricity_analysis(app_config, batch_id)
    second = run_electricity_analysis(app_config, batch_id)

    with connect(app_config) as conn:
        count = conn.execute(
            "select count(*) as c from analysis_opportunities where batch_id = ? and domain = 'electricity'",
            (batch_id,),
        ).fetchone()["c"]
    assert count == second["opportunity_count"]
    assert first["opportunity_count"] == second["opportunity_count"]


def test_electricity_summary_groups_amounts(app_config, sample_workbook):
    batch_id = _import_and_audit(app_config, sample_workbook)
    run_electricity_analysis(app_config, batch_id)

    summary = get_electricity_summary(app_config, batch_id)

    assert summary["batch_id"] == batch_id
    assert "total_electricity_amount" in summary
    assert "recoverable_amount" in summary
    assert "saving_opportunity_amount" in summary
    assert isinstance(summary["city_rankings"], list)
    assert isinstance(summary["type_breakdown"], list)


def test_electricity_opportunity_filters(app_config, sample_workbook):
    batch_id = _import_and_audit(app_config, sample_workbook)
    run_electricity_analysis(app_config, batch_id)
    all_rows = get_electricity_opportunities(app_config, batch_id)
    if not all_rows:
        return

    filtered = get_electricity_opportunities(
        app_config,
        batch_id,
        filters={"opportunity_type": all_rows[0]["opportunity_type"], "confidence": all_rows[0]["confidence"]},
    )

    assert filtered
    assert all(row["opportunity_type"] == all_rows[0]["opportunity_type"] for row in filtered)
    assert all(row["confidence"] == all_rows[0]["confidence"] for row in filtered)


def test_export_electricity_opportunities_writes_workbook(app_config, sample_workbook):
    batch_id = _import_and_audit(app_config, sample_workbook)
    run_electricity_analysis(app_config, batch_id)

    path = export_electricity_opportunities(app_config, batch_id)

    assert path.exists()
    assert "电费压降机会清单" in path.name
    wb = load_workbook(path)
    assert {"填写说明", "机会清单", "地市汇总", "异常分类汇总"}.issubset(set(wb.sheetnames))
```

- [ ] **Step 2: Run tests to verify import failure**

Run: `PYTHONPATH=src pytest tests/test_electricity_analysis.py -v`

Expected: FAIL with `ModuleNotFoundError` or import errors for `governance_app.electricity_analysis`.

- [ ] **Step 3: Implement service dataclass helpers**

Create `src/governance_app/electricity_analysis.py` with these imports and helpers:

```python
import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from governance_app.audit_rules import parse_row, rule_metadata
from governance_app.config import AppConfig
from governance_app.db import connect
from governance_app.geo import normalize_city
from governance_app.rule_fields import (
    ELECTRICITY_AMOUNT_FIELDS,
    METER_PERIOD_END_FIELDS,
    METER_PERIOD_START_FIELDS,
    PERIOD_FIELDS,
    PRICE_FIELDS,
    SUPPLY_FIELDS,
    TRANSFER_CONTRACT_FIELDS,
    USAGE_FIELDS,
)
from governance_app.rule_helpers import _datetime_value, _first_value, _number, _period_key, _text


ELECTRICITY_DOMAIN = "electricity"

RECOVERABLE_RULE_TYPES = {
    "electricity_duplicate_payment": "重复报账",
    "electricity_period_overlap": "时段重叠",
    "electricity_zero_usage_positive_fee": "零电量有费用",
    "electricity_amount_calculation_mismatch": "金额计算偏差",
    "electricity_lump_sum_still_reimbursed": "包干重复报账",
}

SAVING_RULE_TYPES = {
    "electricity_price_range": "高电价",
    "electricity_price_commercial_range": "高电价",
    "electricity_price_city_supply_outlier": "高电价",
    "electricity_usage_spike_drop": "异常增长",
    "electricity_transfer_without_contract": "转供电异常",
}


def _money(value: Any) -> float:
    number = _number(value)
    return round(float(number or 0), 2)


def _first_money(row: dict[str, Any]) -> float:
    return _money(_first_value(row, ELECTRICITY_AMOUNT_FIELDS))


def _usage(row: dict[str, Any]) -> float:
    return _money(_first_value(row, USAGE_FIELDS))


def _price(row: dict[str, Any]) -> float:
    return _money(_first_value(row, PRICE_FIELDS))


def _period(row: dict[str, Any]) -> str:
    return _period_key(_first_value(row, PERIOD_FIELDS)) or ""


def _meter_no(row: dict[str, Any]) -> str:
    return _text(row.get("电表户号")) or ""


def _opportunity_code(batch_id: int, ledger_row_id: int, opportunity_type: str, rule_id: str) -> str:
    digest = hashlib.sha1(f"{batch_id}:{ledger_row_id}:{opportunity_type}:{rule_id}".encode("utf-8")).hexdigest()[:10]
    return f"OPP-{batch_id}-{digest}"
```

- [ ] **Step 4: Implement run and query functions**

Add these public functions to `src/governance_app/electricity_analysis.py`:

```python
def run_electricity_analysis(config: AppConfig, batch_id: int) -> dict[str, int]:
    with connect(config) as conn:
        batch = conn.execute("select id, status, is_archived from import_batches where id = ?", (batch_id,)).fetchone()
        if batch is None:
            raise ValueError("批次不存在")
        if batch["is_archived"]:
            raise ValueError("归档批次不允许刷新电费压降分析")
        if batch["status"] not in {"audited", "exported", "returned", "archived"}:
            raise ValueError("请先执行稽核，再生成电费压降分析")
        electricity_count = conn.execute(
            "select count(*) as c from ledger_rows where batch_id = ? and ledger_type = 'electricity'",
            (batch_id,),
        ).fetchone()["c"]
        if not electricity_count:
            raise ValueError("当前批次没有电费台账，无法生成电费压降分析")

        conn.execute("delete from analysis_opportunities where batch_id = ? and domain = ?", (batch_id, ELECTRICITY_DOMAIN))
        rows = _issue_rows(conn, batch_id)
        inserted = 0
        for issue in rows:
            row_data = parse_row(issue["row_json"])
            payload = _opportunity_from_issue(batch_id, issue, row_data)
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
                    ELECTRICITY_DOMAIN,
                    payload["opportunity_code"],
                    payload["opportunity_type"],
                    issue["severity"],
                    normalize_city(issue["city"]),
                    issue["district"],
                    issue["telecom_site_code"],
                    issue["telecom_site_name"],
                    payload["period"],
                    payload["meter_no"],
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
            (batch_id, "electricity_analysis", f"生成电费压降机会 {inserted} 条"),
        )
        return {"opportunity_count": inserted}


def get_electricity_summary(config: AppConfig, batch_id: int) -> dict[str, Any]:
    with connect(config) as conn:
        _require_batch(conn, batch_id)
        ledger = conn.execute(
            """
            select count(*) as row_count,
                   count(distinct telecom_site_code) as site_count
              from ledger_rows
             where batch_id = ? and ledger_type = 'electricity'
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
             where lr.batch_id = ? and lr.ledger_type = 'electricity'
            """,
            (batch_id,),
        ):
            total_amount += _first_money(parse_row(row["row_json"]))
        amount_row = conn.execute(
            """
            select count(*) as opportunity_count,
                   count(distinct telecom_site_code) as abnormal_site_count,
                   coalesce(sum(recoverable_amount), 0) as recoverable_amount,
                   coalesce(sum(saving_opportunity_amount), 0) as saving_opportunity_amount,
                   sum(case when severity = 'high' then 1 else 0 end) as high_risk_count
              from analysis_opportunities
             where batch_id = ? and domain = ?
            """,
            (batch_id, ELECTRICITY_DOMAIN),
        ).fetchone()
        return {
            "batch_id": batch_id,
            "ledger_row_count": int(ledger["row_count"] or 0),
            "site_count": int(ledger["site_count"] or 0),
            "total_electricity_amount": round(total_amount, 2),
            "abnormal_site_count": int(amount_row["abnormal_site_count"] or 0),
            "opportunity_count": int(amount_row["opportunity_count"] or 0),
            "recoverable_amount": round(float(amount_row["recoverable_amount"] or 0), 2),
            "saving_opportunity_amount": round(float(amount_row["saving_opportunity_amount"] or 0), 2),
            "high_risk_count": int(amount_row["high_risk_count"] or 0),
            "city_rankings": _city_rankings(conn, batch_id),
            "type_breakdown": _type_breakdown(conn, batch_id),
        }
```

- [ ] **Step 5: Implement private row mapping functions**

Add these helpers to the same file:

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
         where i.batch_id = ? and i.ledger_type = 'electricity'
         order by i.id
        """,
        (batch_id,),
    ).fetchall()


def _opportunity_from_issue(batch_id: int, issue, row: dict[str, Any]) -> dict[str, Any] | None:
    rule_id = issue["rule_id"]
    opportunity_type = RECOVERABLE_RULE_TYPES.get(rule_id) or SAVING_RULE_TYPES.get(rule_id)
    if opportunity_type is None:
        return None
    current_amount = _first_money(row)
    recoverable_amount = _recoverable_amount(rule_id, current_amount, row)
    saving_amount = _saving_amount(rule_id, current_amount, row)
    confidence = "high" if rule_id in RECOVERABLE_RULE_TYPES else "medium"
    return {
        "opportunity_code": _opportunity_code(batch_id, issue["ledger_row_id"], opportunity_type, rule_id),
        "opportunity_type": opportunity_type,
        "period": _period(row),
        "meter_no": _meter_no(row),
        "current_amount": current_amount,
        "reference_amount": round(max(current_amount - recoverable_amount - saving_amount, 0), 2),
        "recoverable_amount": recoverable_amount,
        "saving_opportunity_amount": saving_amount,
        "confidence": confidence,
        "message": issue["message"],
        "suggestion": _suggestion(rule_id, issue["suggestion"]),
    }


def _recoverable_amount(rule_id: str, current_amount: float, row: dict[str, Any]) -> float:
    if rule_id in {"electricity_duplicate_payment", "electricity_period_overlap", "electricity_zero_usage_positive_fee", "electricity_lump_sum_still_reimbursed"}:
        return current_amount
    if rule_id == "electricity_amount_calculation_mismatch":
        usage = _usage(row)
        price = _price(row)
        share = _number(row.get("分摊比例(%)"))
        share_factor = (float(share) / 100) if share is not None else 1
        expected = usage * price * share_factor
        return round(max(current_amount - expected, 0), 2)
    return 0.0


def _saving_amount(rule_id: str, current_amount: float, row: dict[str, Any]) -> float:
    if rule_id in {"electricity_price_range", "electricity_price_commercial_range", "electricity_price_city_supply_outlier"}:
        usage = _usage(row)
        price = _price(row)
        reference_price = min(price, 0.9)
        return round(max((price - reference_price) * usage, 0), 2)
    if rule_id in {"electricity_usage_spike_drop", "electricity_transfer_without_contract"}:
        return current_amount
    return 0.0


def _suggestion(rule_id: str, fallback: str) -> str:
    action = {
        "electricity_duplicate_payment": "核实同站址同账期是否重复报账，确认后追回重复支付金额",
        "electricity_period_overlap": "核实抄表起止日期，按重叠周期扣减或追回费用",
        "electricity_zero_usage_positive_fee": "核实零电量费用性质，排除固定费用后追回异常支出",
        "electricity_amount_calculation_mismatch": "复核电量、单价和分摊比例，按差额调整",
        "electricity_lump_sum_still_reimbursed": "核对包干口径，避免包干费用和报账费用重复发生",
        "electricity_price_range": "核实电价依据，推动直供电、合同重谈或转供电降价",
        "electricity_price_commercial_range": "核实电价依据，推动直供电、合同重谈或转供电降价",
        "electricity_price_city_supply_outlier": "对比同区县同供电方式参考价，核实偏高原因",
        "electricity_usage_spike_drop": "核查异常增长原因，排查设备空载、倍率错误和抄表异常",
        "electricity_transfer_without_contract": "补充转供电合同，核实价格依据和加价合理性",
    }.get(rule_id)
    return action or fallback
```

- [ ] **Step 6: Implement list, summary helpers, and export**

Add:

```python
def get_electricity_opportunities(config: AppConfig, batch_id: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    where = ["batch_id = ?", "domain = ?"]
    params: list[Any] = [batch_id, ELECTRICITY_DOMAIN]
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
             order by recoverable_amount desc, saving_opportunity_amount desc, id
            """,
            params,
        ).fetchall()
    return [_opportunity_payload(row) for row in rows]


def export_electricity_opportunities(config: AppConfig, batch_id: int) -> Path:
    summary = get_electricity_summary(config, batch_id)
    opportunities = get_electricity_opportunities(config, batch_id)
    config.export_dir.mkdir(parents=True, exist_ok=True)
    path = config.export_dir / f"批次{batch_id}_电费压降机会清单.xlsx"
    wb = Workbook()
    guide = wb.active
    guide.title = "填写说明"
    guide.append(["电费压降机会清单"])
    guide.append(["可追回金额用于相对确定的问题；压降机会金额用于疑似优化空间，不等同于确定损失。"])
    ws = wb.create_sheet("机会清单")
    ws.append(["机会编号", "地市", "区县", "站址编码", "站址名称", "电表户号", "账期", "异常类型", "风险等级", "当前金额", "参考金额", "可追回金额", "压降机会金额", "置信度", "来源规则", "问题说明", "建议动作"])
    for item in opportunities:
        ws.append([
            item["opportunity_code"],
            item["city"],
            item["district"],
            item["telecom_site_code"],
            item["telecom_site_name"],
            item["meter_no"],
            item["period"],
            item["opportunity_type"],
            item["severity"],
            item["current_amount"],
            item["reference_amount"],
            item["recoverable_amount"],
            item["saving_opportunity_amount"],
            item["confidence"],
            ",".join(item["source_rule_ids"]),
            item["message"],
            item["suggestion"],
        ])
    city = wb.create_sheet("地市汇总")
    city.append(["地市", "机会数量", "可追回金额", "压降机会金额"])
    for item in summary["city_rankings"]:
        city.append([item["city"], item["opportunity_count"], item["recoverable_amount"], item["saving_opportunity_amount"]])
    type_ws = wb.create_sheet("异常分类汇总")
    type_ws.append(["异常类型", "机会数量", "可追回金额", "压降机会金额"])
    for item in summary["type_breakdown"]:
        type_ws.append([item["opportunity_type"], item["opportunity_count"], item["recoverable_amount"], item["saving_opportunity_amount"]])
    wb.save(path)
    return path


def _require_batch(conn, batch_id: int) -> None:
    if conn.execute("select 1 from import_batches where id = ?", (batch_id,)).fetchone() is None:
        raise ValueError("批次不存在")


def _opportunity_payload(row) -> dict[str, Any]:
    payload = dict(row)
    payload["source_rule_ids"] = json.loads(payload.pop("source_rule_ids_json") or "[]")
    for field in ("current_amount", "reference_amount", "recoverable_amount", "saving_opportunity_amount"):
        payload[field] = round(float(payload[field] or 0), 2)
    return payload


def _city_rankings(conn, batch_id: int) -> list[dict[str, Any]]:
    return [
        {
            "city": normalize_city(row["city"]),
            "opportunity_count": int(row["opportunity_count"] or 0),
            "recoverable_amount": round(float(row["recoverable_amount"] or 0), 2),
            "saving_opportunity_amount": round(float(row["saving_opportunity_amount"] or 0), 2),
        }
        for row in conn.execute(
            """
            select coalesce(city, '未填地市') as city,
                   count(*) as opportunity_count,
                   coalesce(sum(recoverable_amount), 0) as recoverable_amount,
                   coalesce(sum(saving_opportunity_amount), 0) as saving_opportunity_amount
              from analysis_opportunities
             where batch_id = ? and domain = ?
             group by coalesce(city, '未填地市')
             order by recoverable_amount desc, saving_opportunity_amount desc, opportunity_count desc
            """,
            (batch_id, ELECTRICITY_DOMAIN),
        )
    ]


def _type_breakdown(conn, batch_id: int) -> list[dict[str, Any]]:
    return [
        {
            "opportunity_type": row["opportunity_type"],
            "opportunity_count": int(row["opportunity_count"] or 0),
            "recoverable_amount": round(float(row["recoverable_amount"] or 0), 2),
            "saving_opportunity_amount": round(float(row["saving_opportunity_amount"] or 0), 2),
        }
        for row in conn.execute(
            """
            select opportunity_type,
                   count(*) as opportunity_count,
                   coalesce(sum(recoverable_amount), 0) as recoverable_amount,
                   coalesce(sum(saving_opportunity_amount), 0) as saving_opportunity_amount
              from analysis_opportunities
             where batch_id = ? and domain = ?
             group by opportunity_type
             order by recoverable_amount desc, saving_opportunity_amount desc, opportunity_count desc
            """,
            (batch_id, ELECTRICITY_DOMAIN),
        )
    ]
```

- [ ] **Step 7: Run service tests**

Run: `PYTHONPATH=src pytest tests/test_electricity_analysis.py -v`

Expected: PASS. If sample data produces zero opportunities, adjust the test fixture rows inside the tests by updating `raw_rows` before `run_audit()` to trigger one existing electricity rule.

- [ ] **Step 8: Commit**

```bash
git add src/governance_app/electricity_analysis.py tests/test_electricity_analysis.py
git commit -m "Add electricity analysis service"
```

## Task 3: API Endpoints

**Files:**
- Modify: `src/governance_app/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write failing endpoint tests**

Append to `tests/test_server.py`:

```python
def test_electricity_analysis_endpoints(app_config, sample_workbook):
    initialize_database(app_config)
    app = create_app(app_config)
    app.handle_test_request("POST", "/api/import", json.dumps({"path": str(sample_workbook)}))
    app.handle_test_request("POST", "/api/audit", json.dumps({"batch_id": 1}))

    status, _headers, body = app.handle_test_request("POST", "/api/batches/1/electricity-analysis/run")
    assert status == 200
    assert "opportunity_count" in json.loads(body)

    status, _headers, body = app.handle_test_request("GET", "/api/batches/1/electricity-analysis/summary")
    assert status == 200
    assert json.loads(body)["batch_id"] == 1

    status, _headers, body = app.handle_test_request("GET", "/api/batches/1/electricity-analysis/opportunities")
    assert status == 200
    assert "opportunities" in json.loads(body)

    status, _headers, body = app.handle_test_request("POST", "/api/batches/1/electricity-analysis/export")
    assert status == 200
    assert json.loads(body)["path"].endswith(".xlsx")


def test_electricity_analysis_rejects_invalid_batch_path(app_config):
    initialize_database(app_config)
    app = create_app(app_config)

    status, _headers, body = app.handle_test_request("POST", "/api/batches/not-a-number/electricity-analysis/run")

    assert status == 400
    assert json.loads(body)["error"] == "invalid batch_id"
```

- [ ] **Step 2: Run tests to verify 404**

Run: `PYTHONPATH=src pytest tests/test_server.py::test_electricity_analysis_endpoints tests/test_server.py::test_electricity_analysis_rejects_invalid_batch_path -v`

Expected: FAIL with `not found`.

- [ ] **Step 3: Import service functions**

Add to the import section of `src/governance_app/server.py`:

```python
from governance_app.electricity_analysis import (
    export_electricity_opportunities,
    get_electricity_opportunities,
    get_electricity_summary,
    run_electricity_analysis,
)
```

- [ ] **Step 4: Add route helper**

Add this helper near `_batch_id_from_query`:

```python
def _electricity_analysis_path(path: str) -> tuple[int, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "api" or parts[1] != "batches" or parts[3] not in {"run", "summary", "opportunities", "export"}:
        return None
    if parts[2] == "":
        raise ValueError("invalid batch_id")
    try:
        batch_id = int(parts[2])
    except ValueError as exc:
        raise ValueError("invalid batch_id") from exc
    return batch_id, parts[3]
```

- [ ] **Step 5: Dispatch routes**

Near the top of `_route()` after system routes, add:

```python
    try:
        electricity_path = _electricity_analysis_path(parsed.path)
    except ValueError as exc:
        return _json({"error": str(exc)}, status=400)
    if electricity_path is not None:
        batch_id, action = electricity_path
        try:
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
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"error": "not found"}, status=404)
```

- [ ] **Step 6: Run endpoint tests**

Run: `PYTHONPATH=src pytest tests/test_server.py::test_electricity_analysis_endpoints tests/test_server.py::test_electricity_analysis_rejects_invalid_batch_path -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/governance_app/server.py tests/test_server.py
git commit -m "Expose electricity analysis endpoints"
```

## Task 4: Frontend Electricity Analysis View

**Files:**
- Create: `src/governance_app/static/electricity-analysis.js`
- Modify: `src/governance_app/static/app.js`
- Modify: `src/governance_app/static/index.html`
- Modify: `src/governance_app/static/styles.css`

- [ ] **Step 1: Add the navigation button**

In `src/governance_app/static/index.html`, under the “归档分析” group and before “分析报表”, add:

```html
        <button class="nav-button" type="button" data-view="electricityAnalysis">电费压降分析</button>
```

- [ ] **Step 2: Wire the view in `app.js`**

Add import:

```javascript
import { renderElectricityAnalysis } from "/electricity-analysis.js?v=20260708-1";
```

Add to `views`:

```javascript
  electricityAnalysis: "电费压降分析",
```

Add to `activateView(view)` before reports:

```javascript
  if (view === "electricityAnalysis") return renderElectricityAnalysis({
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

- [ ] **Step 3: Create the page module**

Create `src/governance_app/static/electricity-analysis.js`:

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

export async function renderElectricityAnalysis(ctx) {
  await ctx.refreshBatches().catch(() => []);
  const batch = ctx.currentBatch();
  if (!batch) {
    ctx.renderNoBatchPrompt("还没有可分析的批次。");
    return;
  }
  ctx.mainContent.innerHTML = `
    <section class="card">
      ${ctx.shellHeader("电费压降分析", `${batch.batch_code || `#${batch.id}`} ${batch.name}`, ctx.renderBatchSelector())}
      <div class="button-row">
        <button id="run-electricity-analysis" class="primary-button" type="button">生成分析</button>
        <button id="export-electricity-analysis" class="secondary-button" type="button">导出 Excel</button>
      </div>
      <div id="electricity-analysis-result" class="result-box">可先生成分析，再查看压降机会清单。</div>
    </section>
    <section class="card metric-section">
      <div id="electricity-summary" class="metric-grid"></div>
    </section>
    <div class="dashboard-grid">
      <section class="card">
        ${ctx.shellHeader("异常分类", "分类")}
        <div id="electricity-type-breakdown" class="risk-summary"></div>
      </section>
      <section class="card">
        ${ctx.shellHeader("地市排行", "地市")}
        <div id="electricity-city-ranking" class="risk-summary"></div>
      </section>
    </div>
    <section class="card">
      ${ctx.shellHeader("压降机会清单", "机会")}
      <div class="toolbar">
        <label class="compact-field"><span>异常类型</span><select id="electricity-type-filter"><option value="">全部类型</option></select></label>
        <label class="compact-field"><span>置信度</span><select id="electricity-confidence-filter"><option value="">全部置信度</option></select></label>
        <button id="apply-electricity-filters" class="secondary-button" type="button">筛选</button>
      </div>
      <div class="table-wrap"><table><thead><tr><th>地市</th><th>站址</th><th>账期</th><th>类型</th><th>当前金额</th><th>可追回</th><th>压降机会</th><th>置信度</th><th>建议动作</th></tr></thead><tbody id="electricity-opportunity-table"><tr><td colspan="9">正在加载</td></tr></tbody></table></div>
    </section>
  `;
  ctx.bindBatchSelector(() => renderElectricityAnalysis(ctx));
  document.querySelector("#run-electricity-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "生成中...", async () => {
      const result = document.querySelector("#electricity-analysis-result");
      result.className = "result-box result-pending";
      result.textContent = "正在生成电费压降机会...";
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/run`, {});
        result.className = "result-box result-success";
        result.textContent = `已生成 ${ctx.formatNumber(data.opportunity_count)} 条电费压降机会。`;
        await loadElectricityAnalysisData(ctx);
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#export-electricity-analysis").addEventListener("click", async (event) => {
    await ctx.withBusy(event.currentTarget, "导出中...", async () => {
      const result = document.querySelector("#electricity-analysis-result");
      try {
        const data = await postJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/export`, {});
        result.className = "result-box result-success";
        result.textContent = `已导出：${data.path}`;
      } catch (error) {
        result.className = "result-box result-error";
        result.textContent = error.message;
      }
    });
  });
  document.querySelector("#apply-electricity-filters").addEventListener("click", () => loadElectricityAnalysisData(ctx));
  await loadElectricityAnalysisData(ctx);
}

async function loadElectricityAnalysisData(ctx) {
  const type = document.querySelector("#electricity-type-filter")?.value || "";
  const confidence = document.querySelector("#electricity-confidence-filter")?.value || "";
  const query = new URLSearchParams();
  if (type) query.set("opportunity_type", type);
  if (confidence) query.set("confidence", confidence);
  try {
    const [summary, list] = await Promise.all([
      fetchJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/summary`),
      fetchJson(`/api/batches/${ctx.state.batchId}/electricity-analysis/opportunities${query.toString() ? `?${query}` : ""}`),
    ]);
    renderSummary(ctx, summary);
    renderBreakdown(ctx, summary);
    renderRows(ctx, list.opportunities || []);
  } catch (error) {
    document.querySelector("#electricity-summary").innerHTML = [
      ctx.metricCard("电费总额", 0, "等待分析", "info"),
      ctx.metricCard("异常站址", 0, "等待分析", "warning"),
      ctx.metricCard("可追回金额", 0, "等待分析", "danger"),
      ctx.metricCard("压降机会金额", 0, "等待分析", "success"),
    ].join("");
    document.querySelector("#electricity-opportunity-table").innerHTML = `<tr><td colspan="9">${ctx.escapeHtml(error.message)}</td></tr>`;
  }
}

function renderSummary(ctx, summary) {
  document.querySelector("#electricity-summary").innerHTML = [
    ctx.metricCard("电费总额", money(summary.total_electricity_amount), `电费记录 ${ctx.formatNumber(summary.ledger_row_count)}`, "info"),
    ctx.metricCard("异常站址", summary.abnormal_site_count, `机会 ${ctx.formatNumber(summary.opportunity_count)} 条`, "warning"),
    ctx.metricCard("可追回金额", money(summary.recoverable_amount), "相对确定问题", "danger"),
    ctx.metricCard("压降机会金额", money(summary.saving_opportunity_amount), `高风险 ${ctx.formatNumber(summary.high_risk_count)} 条`, "success"),
  ].join("");
}

function renderBreakdown(ctx, summary) {
  document.querySelector("#electricity-type-breakdown").innerHTML = (summary.type_breakdown || []).map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.opportunity_type)}</strong><span>${ctx.formatNumber(row.opportunity_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 压降 ${money(row.saving_opportunity_amount)}</span></div>`).join("") || '<div class="empty-state">暂无异常分类</div>';
  document.querySelector("#electricity-city-ranking").innerHTML = (summary.city_rankings || []).map((row) => `<div class="risk-row"><div><strong>${ctx.escapeHtml(row.city)}</strong><span>${ctx.formatNumber(row.opportunity_count)} 条</span></div><span>追回 ${money(row.recoverable_amount)} / 压降 ${money(row.saving_opportunity_amount)}</span></div>`).join("") || '<div class="empty-state">暂无地市排行</div>';
}

function renderRows(ctx, rows) {
  const typeFilter = document.querySelector("#electricity-type-filter");
  const confidenceFilter = document.querySelector("#electricity-confidence-filter");
  if (typeFilter && typeFilter.options.length <= 1) typeFilter.innerHTML = optionRows(rows, "opportunity_type", "类型");
  if (confidenceFilter && confidenceFilter.options.length <= 1) confidenceFilter.innerHTML = optionRows(rows, "confidence", "置信度");
  const tbody = document.querySelector("#electricity-opportunity-table");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="9">暂无电费压降机会。可以先点击“生成分析”。</td></tr>';
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
      <td>${money(row.saving_opportunity_amount)}</td>
      <td>${ctx.escapeHtml(row.confidence)}</td>
      <td>${ctx.escapeHtml(row.suggestion)}</td>
    </tr>
  `).join("");
}
```

- [ ] **Step 4: Run frontend syntax check**

Run: `node --check src/governance_app/static/electricity-analysis.js && node --check src/governance_app/static/app.js`

Expected: PASS.

- [ ] **Step 5: Run app smoke tests**

Run: `scripts/check.sh`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/governance_app/static/electricity-analysis.js src/governance_app/static/app.js src/governance_app/static/index.html src/governance_app/static/styles.css
git commit -m "Add electricity analysis frontend"
```

## Task 5: Full Verification and Polish

**Files:**
- Modify only files touched by previous tasks if verification finds an issue.

- [ ] **Step 1: Run the complete test suite**

Run: `scripts/check.sh`

Expected: PASS.

- [ ] **Step 2: Manually exercise the local workflow**

Run: `scripts/start.sh`

Expected: local service starts and prints the browser URL. In the browser, import a sample workbook, run audit, open “电费压降分析”, click “生成分析”, see summary cards and opportunity table, and click “导出 Excel”.

- [ ] **Step 3: Inspect exported workbook**

Open the generated file under `exports/` and confirm these sheets exist:

```text
填写说明
机会清单
地市汇总
异常分类汇总
```

Confirm the “机会清单” header includes:

```text
机会编号, 地市, 区县, 站址编码, 站址名称, 电表户号, 账期, 异常类型, 风险等级, 当前金额, 参考金额, 可追回金额, 压降机会金额, 置信度, 来源规则, 问题说明, 建议动作
```

- [ ] **Step 4: Commit verification fixes if any**

If verification required edits:

```bash
git add src tests
git commit -m "Polish electricity analysis workflow"
```

If no edits were needed, do not create an empty commit.

## Self-Review

- Spec coverage: database table, service generation, dual amount fields, summary, filters, export, frontend view, and error handling are covered.
- Deferred by design: richer second-phase analytics such as multi-period median and deeper idle-site heuristics are not in this first implementation plan.
- Type consistency: `recoverable_amount`, `saving_opportunity_amount`, `source_rule_ids_json`, and `source_rule_ids` names are consistent across schema, service, API payload, export, and frontend.
