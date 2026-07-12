# 专题分析核查闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让电费压降机会和铁塔租费异常线索复用现有问题状态与整改回传流程，持久保存核实金额、实际落实金额和核查说明，并在页面与归档中形成完整闭环。

**Architecture:** `analysis_opportunities` 仍是可重建的派生结果，只新增来源问题编号；新表 `analysis_opportunity_reviews` 按稳定机会编号保存人工成果。所有状态只写入 `issues.status`，在线核查和 Excel 回传通过同一个连接级状态写入函数，在单个 SQLite 事务中同步状态、事件、说明和成果金额。

**Tech Stack:** Python 3.12、SQLite、openpyxl、标准库 HTTP 路由、原生 ES modules、pytest、Ruff、mypy、pytest-cov。

## Global Constraints

- 数据库版本必须从 `2` 升级到 `3`，版本 2 数据不能丢失。
- 不新增第三方运行时依赖。
- `issues.status` 是唯一状态来源，不在专题表中保存状态副本。
- 普通整改包没有专题列时，现有行为必须完全不变。
- 空白专题金额不覆盖历史值，明确填写 `0` 必须保存为 `0`。
- 在线核查一次请求必须整体成功或整体回滚；Excel 继续保持“校验错误行跳过、有效行继续”，数据库异常则整次事务回滚。
- 当前一个问题只对应一个专题机会，不扩展一对多回传语义。
- 桌面和移动端均可操作，移动端不得新增横向滚动表格。
- 全量检查必须通过，覆盖率不得低于 `90%`。

---

## 文件结构

- Create: `src/governance_app/analysis_reviews.py` — 专题核查校验、机会匹配、成果持久化和在线原子提交。
- Create: `src/governance_app/static/analysis-review.js` — 两个专题页面共用的状态标签、核查表单和提交行为。
- Create: `tests/test_analysis_reviews.py` — 核查服务、金额语义、原子性和刷新持久性测试。
- Create: `tests/test_analysis_review_ui.py` — 前端模块与两个页面的静态契约测试。
- Modify: `src/governance_app/migrations.py` — 版本 3 字段、核查表、约束和索引。
- Modify: `src/governance_app/workflow.py` — 提取连接级问题状态写入函数。
- Modify: `src/governance_app/electricity_analysis.py` — 关联问题、成果查询、汇总和兼容整改 sheet。
- Modify: `src/governance_app/tower_rent_analysis.py` — 关联问题、成果查询、汇总和兼容整改 sheet。
- Modify: `src/governance_app/corrections.py` — 识别可选专题列并在同一事务写入成果。
- Modify: `src/governance_app/exporter.py` — 暴露共用的 Excel 公式注入防护函数。
- Modify: `src/governance_app/routes/analysis.py` — 新增两个领域共用的 `review` 动作。
- Modify: `src/governance_app/static/electricity-analysis.js` — 状态筛选、成果指标和核查操作。
- Modify: `src/governance_app/static/tower-rent-analysis.js` — 状态筛选、成果指标和核查操作。
- Modify: `src/governance_app/static/styles.css` — 核查卡片、表单、状态和移动端布局。
- Modify: `src/governance_app/archive.py` — 新增 `专题核查成果` sheet。
- Modify: `tests/test_db.py` — 版本 3 初始化、升级、约束和索引测试。
- Modify: `tests/test_workflow.py` — 连接级状态更新与归档保护测试。
- Modify: `tests/test_electricity_analysis.py` — 电费来源关联、查询、汇总和导出测试。
- Modify: `tests/test_tower_rent_analysis.py` — 租费来源关联、查询、汇总和导出测试。
- Modify: `tests/test_export_and_corrections.py` — 专题回传兼容、校验和部分成功测试。
- Modify: `tests/routes/test_analysis.py` — review 路径、请求体和错误响应测试。
- Modify: `tests/test_analytics_backup.py` — 专题成果归档测试。
- Modify: `tests/test_end_to_end.py` — 完整闭环回归测试。

### Task 1: 数据库版本 3 与持久核查表

**Files:**
- Modify: `src/governance_app/migrations.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: `Migration`、`_ensure_column()`、`_execute_script()`。
- Produces: `SCHEMA_VERSION = 3`、`analysis_opportunities.source_issue_code`、`analysis_opportunity_reviews`。

- [ ] **Step 1: 写版本 3 初始化与升级失败测试**

在 `tests/test_db.py` 增加以下断言；升级测试先用 `apply_migrations(conn, MIGRATIONS[:2])` 创建真实版本 2 数据，再调用 `initialize_database()`：

```python
def test_initialize_database_creates_analysis_review_schema(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        opportunity_columns = {row["name"] for row in conn.execute("pragma table_info(analysis_opportunities)")}
        review_columns = {row["name"] for row in conn.execute("pragma table_info(analysis_opportunity_reviews)")}
        review_indexes = {row["name"] for row in conn.execute("pragma index_list(analysis_opportunity_reviews)")}
    assert "source_issue_code" in opportunity_columns
    assert {
        "batch_id", "domain", "opportunity_code", "opportunity_type", "source_issue_code",
        "estimated_recoverable_amount", "estimated_saving_amount",
        "verified_recoverable_amount", "realized_saving_amount", "review_note",
        "created_at", "updated_at",
    }.issubset(review_columns)
    assert {"idx_analysis_reviews_batch_domain", "idx_analysis_reviews_source_issue"}.issubset(review_indexes)


def test_version_two_upgrade_preserves_existing_opportunity(app_config):
    app_config.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(app_config.database_path)
    conn.row_factory = sqlite3.Row
    apply_migrations(conn, MIGRATIONS[:2])
    conn.execute("insert into import_batches(id, source_file, status) values (1, 'legacy.xlsx', 'audited')")
    conn.execute(
        """insert into analysis_opportunities(
               batch_id, domain, opportunity_code, opportunity_type, severity, confidence,
               source_rule_ids_json, message, suggestion
           ) values (1, 'electricity', 'legacy-opp', '高电价', 'high', 'high', '[]', 'm', 's')"""
    )
    conn.commit()
    conn.close()
    initialize_database(app_config)
    with connect(app_config) as upgraded:
        row = upgraded.execute(
            "select opportunity_code, source_issue_code from analysis_opportunities where opportunity_code = 'legacy-opp'"
        ).fetchone()
    assert dict(row) == {"opportunity_code": "legacy-opp", "source_issue_code": None}


def test_analysis_review_amounts_must_be_nonnegative(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute("pragma foreign_keys = off")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                """insert into analysis_opportunity_reviews(
                       batch_id, domain, opportunity_code, opportunity_type, source_issue_code,
                       verified_recoverable_amount
                   ) values (1, 'electricity', 'opp', '高电价', 'issue', -1)"""
            )
```

- [ ] **Step 2: 运行测试确认先失败**

Run: `.venv/bin/python -m pytest tests/test_db.py -q`

Expected: schema 版本仍为 2，且新字段或新表不存在。

- [ ] **Step 3: 实现版本 3 迁移**

在 `src/governance_app/migrations.py` 中把版本号改为 3，加入迁移函数并登记：

```python
SCHEMA_VERSION = 3


def _upgrade_to_version_3(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "analysis_opportunities", "source_issue_code", "text")
    _execute_script(
        conn,
        """
        create index if not exists idx_analysis_opportunities_source_issue
            on analysis_opportunities(source_issue_code);
        create table if not exists analysis_opportunity_reviews (
            id integer primary key autoincrement,
            batch_id integer not null references import_batches(id) on delete cascade,
            domain text not null,
            opportunity_code text not null unique,
            opportunity_type text not null,
            source_issue_code text not null references issues(issue_code) on delete cascade,
            estimated_recoverable_amount real not null default 0 check (estimated_recoverable_amount >= 0),
            estimated_saving_amount real not null default 0 check (estimated_saving_amount >= 0),
            verified_recoverable_amount real check (verified_recoverable_amount is null or verified_recoverable_amount >= 0),
            realized_saving_amount real check (realized_saving_amount is null or realized_saving_amount >= 0),
            review_note text,
            created_at text not null default current_timestamp,
            updated_at text not null default current_timestamp
        );
        create index if not exists idx_analysis_reviews_batch_domain
            on analysis_opportunity_reviews(batch_id, domain);
        create index if not exists idx_analysis_reviews_source_issue
            on analysis_opportunity_reviews(source_issue_code);
        """,
    )


MIGRATIONS = (
    Migration(1, _create_version_1_schema),
    Migration(2, _upgrade_to_version_2),
    Migration(3, _upgrade_to_version_3),
)
```

- [ ] **Step 4: 运行数据库测试**

Run: `.venv/bin/python -m pytest tests/test_db.py -q`

Expected: 全部通过，最新迁移记录为 3，重复初始化不新增迁移。

- [ ] **Step 5: 提交**

```bash
git add src/governance_app/migrations.py tests/test_db.py
git commit -m "Add analysis review schema"
```

### Task 2: 统一连接级问题状态写入

**Files:**
- Modify: `src/governance_app/workflow.py`
- Modify: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `ISSUE_STATUSES`、`IssueStatus`、SQLite connection。
- Produces: `update_issue_status_in_conn(conn, issue_code, status, *, source, event_note, correction_value=None, correction_note=None, update_correction_fields=False) -> sqlite3.Row`。

- [ ] **Step 1: 写连接级状态更新测试**

在 `tests/test_workflow.py` 新增测试，创建问题后直接在现有连接中调用新函数，并验证状态、说明和事件同时可见；另一个测试把批次设为归档并断言拒绝：

```python
row = update_issue_status_in_conn(
    conn,
    issue_code,
    "needs_review",
    source="analysis_review",
    event_note="保存专题核查",
    correction_note="已核对账单",
    update_correction_fields=True,
)
assert row["batch_id"] == batch_id
saved = conn.execute("select status, correction_note from issues where issue_code = ?", (issue_code,)).fetchone()
event = conn.execute("select source, to_status from issue_events where issue_id = ? order by id desc", (row["id"],)).fetchone()
assert tuple(saved) == ("needs_review", "已核对账单")
assert tuple(event) == ("analysis_review", "needs_review")
```

- [ ] **Step 2: 运行定向测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_workflow.py -q`

Expected: 导入 `update_issue_status_in_conn` 失败。

- [ ] **Step 3: 提取连接级函数并复用**

在 `src/governance_app/workflow.py` 导入 `sqlite3`，实现以下函数；现有 `update_issue_status()` 只负责打开连接并调用它：

```python
def update_issue_status_in_conn(
    conn: sqlite3.Connection,
    issue_code: str,
    status: IssueStatus,
    *,
    source: str,
    event_note: str,
    correction_value: str | None = None,
    correction_note: str | None = None,
    update_correction_fields: bool = False,
) -> sqlite3.Row:
    if status not in ISSUE_STATUSES:
        raise ValueError("invalid issue status")
    row = conn.execute(
        """select i.id, i.batch_id, i.status, b.is_archived
             from issues i join import_batches b on b.id = i.batch_id
            where i.issue_code = ?""",
        (issue_code,),
    ).fetchone()
    if row is None:
        raise ValueError("issue not found")
    if row["is_archived"]:
        raise ValueError("batch is archived")
    if update_correction_fields:
        conn.execute(
            """update issues
                  set status = ?, correction_value = ?, correction_note = ?, updated_at = current_timestamp
                where issue_code = ?""",
            (status, correction_value, correction_note, issue_code),
        )
    else:
        conn.execute(
            "update issues set status = ?, updated_at = current_timestamp where issue_code = ?",
            (status, issue_code),
        )
    conn.execute(
        "insert into issue_events(issue_id, from_status, to_status, source, note) values (?, ?, ?, ?, ?)",
        (row["id"], row["status"], status, source, event_note),
    )
    return row
```

`update_issue_status()` 使用 `source="manual"`、`event_note=f"人工更新问题状态：{issue_code}"`，并保留原操作日志文案。

- [ ] **Step 4: 运行工作流和路由回归测试**

Run: `.venv/bin/python -m pytest tests/test_workflow.py tests/routes/test_audits.py -q`

Expected: 新旧状态更新测试全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/governance_app/workflow.py tests/test_workflow.py
git commit -m "Share atomic issue status updates"
```

### Task 3: 专题核查服务与原子写入

**Files:**
- Create: `src/governance_app/analysis_reviews.py`
- Create: `tests/test_analysis_reviews.py`

**Interfaces:**
- Consumes: `update_issue_status_in_conn()`、`analysis_opportunities`、`analysis_opportunity_reviews`。
- Produces: `save_opportunity_review(config, batch_id, route_domain, payload)`、`match_opportunity_in_conn(conn, opportunity_code, *, batch_id=None, route_domain=None, expected_issue_code=None)`、`upsert_review_in_conn(conn, opportunity, verified, realized, note)`、`sync_existing_review_note_in_conn(conn, issue_code, note)`、`load_review_payload_in_conn(conn, opportunity_code)`、`review_summary_in_conn(conn, batch_id, storage_domain)`、`review_payload_fields(row)`。

- [ ] **Step 1: 写服务行为与原子回滚测试**

测试文件使用现有导入、稽核和电费分析流程建立真实机会，覆盖：两类金额与说明正常保存、0 与空白不同、负数/NaN/Infinity/错误领域/错误状态拒绝、归档拒绝、数据库触发器造成核查写入失败时问题状态不变：

```python
saved = save_opportunity_review(
    app_config,
    batch_id,
    "electricity-analysis",
    {
        "opportunity_code": opportunity_code,
        "status": "needs_review",
        "verified_recoverable_amount": 0,
        "realized_saving_amount": 800,
        "review_note": "已核对账单",
    },
)
assert saved["issue_status"] == "needs_review"
assert saved["verified_recoverable_amount"] == 0.0
assert saved["realized_saving_amount"] == 800.0

with connect(app_config) as conn:
    conn.execute(
        """create trigger fail_analysis_review before insert on analysis_opportunity_reviews
             begin select raise(abort, 'forced review failure'); end"""
    )
valid_payload = {
    "opportunity_code": opportunity_code,
    "status": "closed",
    "verified_recoverable_amount": 1200.5,
    "realized_saving_amount": 800,
    "review_note": "已完成核查",
}
with pytest.raises(sqlite3.IntegrityError, match="forced review failure"):
    save_opportunity_review(app_config, batch_id, "electricity-analysis", valid_payload)
with connect(app_config) as conn:
    assert conn.execute("select status from issues where issue_code = ?", (issue_code,)).fetchone()["status"] == original_status
```

- [ ] **Step 2: 运行测试确认服务尚不存在**

Run: `.venv/bin/python -m pytest tests/test_analysis_reviews.py -q`

Expected: `governance_app.analysis_reviews` 导入失败。

- [ ] **Step 3: 实现金额校验、机会匹配和 upsert**

在新模块中定义固定映射和状态集合：

```python
ROUTE_TO_STORAGE_DOMAIN = {
    "electricity-analysis": "electricity",
    "tower-rent-analysis": "tower_rent",
}
ONLINE_REVIEW_STATUSES = {"needs_review", "still_invalid", "closed", "not_required"}


def optional_nonnegative_amount(value: object, label: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是非负数字")
    try:
        number = float(str(value).replace(",", "").replace("，", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是非负数字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label}必须是非负数字")
    return round(number, 2)
```

`match_opportunity_in_conn(conn, opportunity_code, *, batch_id=None, route_domain=None, expected_issue_code=None)` 必须通过一次 join 校验来源问题、归档状态以及所有非空的期望参数。在线请求传入批次和路由领域；Excel 回传传入问题编号，并校验机会领域与来源问题 `ledger_type` 一致。错误文案分别为“机会不存在或不属于当前批次专题”“旧版专题机会缺少来源问题，请先重新运行专题分析”“专题机会与问题编号不匹配”“专题机会领域与来源问题不匹配”“批次已归档，不能修改专题核查结果”。

`upsert_review_in_conn()` 插入时复制 `recoverable_amount` 与 `saving_opportunity_amount`；冲突更新时使用 `coalesce(excluded.verified_recoverable_amount, analysis_opportunity_reviews.verified_recoverable_amount)` 和对应的实际落实金额，保证 Excel 空白不覆盖、0 能覆盖，并总是同步 `review_note` 与 `updated_at`。

- [ ] **Step 4: 实现在线原子保存与查询辅助函数**

```python
def save_opportunity_review(
    config: AppConfig,
    batch_id: int,
    route_domain: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    status = payload.get("status")
    if status not in ONLINE_REVIEW_STATUSES:
        raise ValueError("专题核查状态无效")
    opportunity_code = str(payload.get("opportunity_code") or "").strip()
    if not opportunity_code:
        raise ValueError("机会编号不能为空")
    verified = optional_nonnegative_amount(payload.get("verified_recoverable_amount"), "核实可追回金额")
    realized = optional_nonnegative_amount(payload.get("realized_saving_amount"), "实际落实金额")
    note = str(payload.get("review_note") or "").strip()
    with connect(config) as conn:
        opportunity = match_opportunity_in_conn(
            conn,
            opportunity_code,
            batch_id=batch_id,
            route_domain=route_domain,
        )
        update_issue_status_in_conn(
            conn,
            opportunity["source_issue_code"],
            cast(IssueStatus, status),
            source="analysis_review",
            event_note=f"保存专题核查：{opportunity_code}",
            correction_note=note,
            update_correction_fields=True,
        )
        upsert_review_in_conn(conn, opportunity, verified, realized, note)
        return load_review_payload_in_conn(conn, opportunity_code)
```

`review_summary_in_conn()` 对当前专题机会关联问题和核查表，返回固定字段：

```python
{
    "pending_count": pending_export + pending_correction + still_invalid,
    "review_count": returned + needs_review,
    "closed_count": closed + not_required + resolved_by_reaudit,
    "verified_recoverable_amount": round(sum_verified, 2),
    "realized_saving_amount": round(sum_realized, 2),
}
```

`sync_existing_review_note_in_conn()` 只更新已存在核查行，不创建新行。

- [ ] **Step 5: 运行核查服务测试**

Run: `.venv/bin/python -m pytest tests/test_analysis_reviews.py -q`

Expected: 全部通过，包括触发器回滚后状态、事件和核查行均未改变。

- [ ] **Step 6: 提交**

```bash
git add src/governance_app/analysis_reviews.py tests/test_analysis_reviews.py
git commit -m "Add atomic analysis review service"
```

### Task 4: 两类专题来源关联、查询、汇总与兼容导出

**Files:**
- Modify: `src/governance_app/electricity_analysis.py`
- Modify: `src/governance_app/tower_rent_analysis.py`
- Modify: `src/governance_app/exporter.py`
- Modify: `tests/test_electricity_analysis.py`
- Modify: `tests/test_tower_rent_analysis.py`

**Interfaces:**
- Consumes: `review_summary_in_conn()`、`review_payload_fields()`。
- Produces: 每条机会的 `issue_code`、`issue_status`、整改与核查字段，`status` 筛选，成果汇总，以及 `exporter.append_analysis_correction_sheet()` 生成的 `整改问题清单`。

- [ ] **Step 1: 写两个领域的对称失败测试**

分别在现有测试文件中增加：

```python
run_electricity_analysis(app_config, batch_id)
row = get_electricity_opportunities(app_config, batch_id)[0]
assert row["issue_code"]
assert row["issue_status"] == "pending_export"
assert row["verified_recoverable_amount"] is None
assert get_electricity_opportunities(app_config, batch_id, {"status": "closed"}) == []

path = export_electricity_opportunities(app_config, batch_id)
wb = load_workbook(path)
assert "整改问题清单" in wb.sheetnames
headers = [cell.value for cell in wb["整改问题清单"][1]]
assert headers == ["问题编号", "整改结果", "整改说明", "整改后值", "机会编号", "核实可追回金额", "实际落实金额"]
```

租费测试调用对应函数并断言完全相同的闭环字段。另加刷新持久性测试：先保存核查结果，再重新运行分析，机会编号与核查金额不变。

- [ ] **Step 2: 运行两个专题测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_electricity_analysis.py tests/test_tower_rent_analysis.py -q`

Expected: 来源编号、核查字段和新 sheet 断言失败。

- [ ] **Step 3: 写入来源问题并关联查询**

两个 `_issue_rows()` 查询都加入 `i.issue_code`；两个 insert 都加入 `source_issue_code`。列表 SQL 改为明确别名 join：

```sql
select ao.*,
       i.issue_code, i.status as issue_status, i.correction_value, i.correction_note,
       r.verified_recoverable_amount, r.realized_saving_amount,
       r.review_note, r.updated_at as reviewed_at
  from analysis_opportunities ao
  left join issues i on i.issue_code = ao.source_issue_code
  left join analysis_opportunity_reviews r on r.opportunity_code = ao.opportunity_code
 where ao.batch_id = ? and ao.domain = ?
```

现有筛选列统一加 `ao.` 前缀；`status` 单独生成 `i.status = ?`。两个 payload 函数调用 `review_payload_fields(row)`，旧版空来源机会返回 `issue_code=None` 且保持可查看。

- [ ] **Step 4: 扩展汇总并新增整改兼容 sheet**

两个 summary 返回值合并 `review_summary_in_conn(conn, batch_id, DOMAIN)`。在 `exporter.py` 新增 `append_analysis_correction_sheet(wb, opportunities)`，集中创建固定七列的 `整改问题清单`；只为已有来源问题编号的新版机会写行，每行预填问题编号、机会编号、已有说明和已有成果金额，整改结果与整改后值留空。两个专题导出函数在保存前调用该函数。旧版空来源机会仍保留在原“机会清单”，说明页提示先重新运行专题分析后再闭环。下拉验证使用：

```python
validation = DataValidation(
    type="list",
    formula1='"已整改,无需整改,情况说明,退回确认"',
    allow_blank=True,
)
ws.add_data_validation(validation)
if ws.max_row >= 2:
    validation.add(f"B2:B{ws.max_row}")
```

说明页追加“测算金额仅供核查参考，只有核实可追回金额和实际落实金额计入成果”。所有写入 Excel 的字符串继续经过防公式注入处理；将 `_excel_safe()` 重命名为 `exporter.excel_safe()`，原通用整改导出和 `append_analysis_correction_sheet()` 共同复用。

- [ ] **Step 5: 运行专题与通用导出回归测试**

Run: `.venv/bin/python -m pytest tests/test_electricity_analysis.py tests/test_tower_rent_analysis.py tests/test_export_and_corrections.py -q`

Expected: 两类专题新字段、新 sheet、刷新持久性和原通用整改导出全部通过。

- [ ] **Step 6: 提交**

```bash
git add src/governance_app/electricity_analysis.py src/governance_app/tower_rent_analysis.py src/governance_app/exporter.py tests/test_electricity_analysis.py tests/test_tower_rent_analysis.py tests/test_export_and_corrections.py
git commit -m "Link analysis opportunities to issue reviews"
```

### Task 5: 整改回传兼容专题成果

**Files:**
- Modify: `src/governance_app/corrections.py`
- Modify: `tests/test_export_and_corrections.py`

**Interfaces:**
- Consumes: `match_opportunity_in_conn()`、`optional_nonnegative_amount()`、`upsert_review_in_conn()`、`sync_existing_review_note_in_conn()`、`update_issue_status_in_conn()`。
- Produces: 普通与专题工作簿共用的部分成功导入。

- [ ] **Step 1: 写专题列兼容和行级错误测试**

以 Task 4 导出的电费专题工作簿为输入，覆盖：正常写入、空白不覆盖、0 覆盖、缺少一个专题列、非法金额、机会/问题不匹配、重复问题编号、有效行与错误行混合。核心成功断言：

```python
result = import_correction_return(app_config, path)
assert result.matched_count == 1
assert result.errors == []
with connect(app_config) as conn:
    issue = conn.execute("select status, correction_note from issues where issue_code = ?", (issue_code,)).fetchone()
    review = conn.execute(
        "select verified_recoverable_amount, realized_saving_amount, review_note from analysis_opportunity_reviews"
    ).fetchone()
assert tuple(issue) == ("needs_review", "已完成核查")
assert tuple(review) == (1200.5, 800.0, "已完成核查")
```

普通整改测试增加断言：没有专题列时不会创建核查记录；如果该问题已有核查记录，普通回传会同步 `review_note`。

- [ ] **Step 2: 运行整改回传测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_export_and_corrections.py -q`

Expected: 专题金额未写入，缺列和非法金额未产生预期错误。

- [ ] **Step 3: 实现专题列识别和逐行校验**

在读取 header 后加入：

```python
specialist_headers = ["机会编号", "核实可追回金额", "实际落实金额"]
present_specialist_headers = [name for name in specialist_headers if name in index]
if present_specialist_headers and len(present_specialist_headers) != len(specialist_headers):
    missing_specialist = [name for name in specialist_headers if name not in index]
    errors = [f"缺少专题回填列：{name}" for name in missing_specialist]
    _record_return(config, workbook_path, 0, errors, [])
    return CorrectionImportResult(matched_count=0, errors=errors)
is_specialist = len(present_specialist_headers) == len(specialist_headers)
```

每行先完成全部纯校验，再执行写入。专题行调用 `match_opportunity_in_conn(conn, opportunity_code, expected_issue_code=issue_code)` 匹配当前问题；金额错误分别用 `errors.append(f"第{row_number}行核实可追回金额必须是非负数字")` 和 `errors.append(f"第{row_number}行实际落实金额必须是非负数字")` 记录，然后继续下一行。专题金额有值而整改结果与说明都空时用 `errors.append(f"第{row_number}行填写专题金额后必须填写整改结果或整改说明")` 记录。

- [ ] **Step 4: 将问题与核查写入放入同一行事务路径**

用 `update_issue_status_in_conn()` 替换当前手写 issue update/event；专题行随后调用 `upsert_review_in_conn()`。普通行调用 `sync_existing_review_note_in_conn()`。两者都使用同一个外层 `with connect(config) as conn`，因此行级校验不会写入，任何 SQLite 异常会由连接上下文整体回滚。

保留 `seen_issue_codes`、`auto_review`、`review_warnings`、`transition_batch_in_conn()` 和 `correction_returns` 日志的既有语义。

- [ ] **Step 5: 运行整改与端到端回归测试**

Run: `.venv/bin/python -m pytest tests/test_export_and_corrections.py tests/test_end_to_end.py -q`

Expected: 专题与普通回传全部通过，混合文件只跳过错误行。

- [ ] **Step 6: 提交**

```bash
git add src/governance_app/corrections.py tests/test_export_and_corrections.py
git commit -m "Import specialist review outcomes"
```

### Task 6: Review API 与两个专题页面

**Files:**
- Modify: `src/governance_app/routes/analysis.py`
- Modify: `tests/routes/test_analysis.py`
- Create: `src/governance_app/static/analysis-review.js`
- Modify: `src/governance_app/static/electricity-analysis.js`
- Modify: `src/governance_app/static/tower-rent-analysis.js`
- Modify: `src/governance_app/static/styles.css`
- Create: `tests/test_analysis_review_ui.py`

**Interfaces:**
- Consumes: `save_opportunity_review()`、列表与汇总新增字段。
- Produces: `POST /api/batches/{batch_id}/{domain}/review` 和可响应式核查界面。

- [ ] **Step 1: 写 review 路由失败测试**

在 `tests/routes/test_analysis.py` 用真实批次与机会测试两个领域；至少断言成功、无效 JSON、缺机会编号、无效状态、错误领域机会和归档批次：

```python
response = _handler()(
    app_config,
    "POST",
    urlparse(f"/api/batches/{batch_id}/electricity-analysis/review"),
    json.dumps({
        "opportunity_code": opportunity_code,
        "status": "closed",
        "verified_recoverable_amount": 1200.5,
        "realized_saving_amount": 800,
        "review_note": "已退款并完成优化",
    }),
)
assert response[0] == 200
assert json.loads(response[2])["issue_status"] == "closed"
```

- [ ] **Step 2: 实现路由动作**

将 `_ACTIONS` 扩展为包含 `review`，不要再丢弃 `body`。在 `handle_analysis_route()` 已解析出 `domain` 后、分派两个现有领域响应函数之前统一处理：

```python
if method == "POST" and action == "review":
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("请求内容不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    return json_response(save_opportunity_review(config, batch_id, domain, payload))
```

两个现有领域响应函数签名保持不变；未匹配方法仍返回 404，所有业务 `ValueError` 仍映射 400。

- [ ] **Step 3: 运行路由测试**

Run: `.venv/bin/python -m pytest tests/routes/test_analysis.py tests/test_server.py -q`

Expected: review 成功和错误路径通过，原分析端点不回归。

- [ ] **Step 4: 写前端静态契约测试**

`tests/test_analysis_review_ui.py` 读取三个 JS 文件和 CSS，断言：两个页面都导入共享模块、都有 `status` 查询参数与状态筛选；共享模块包含四个状态动作、两个金额字段、失败消息容器；CSS 包含移动端单列规则。

```python
assert 'query.set("status", status)' in electricity
assert 'query.set("status", status)' in tower_rent
for value in ("needs_review", "still_invalid", "closed", "not_required"):
    assert value in shared
assert "grid-template-columns: 1fr" in styles
```

- [ ] **Step 5: 实现共享核查表单模块**

`analysis-review.js` 导出 `statusOptions()`、`reviewForm(ctx, row)`、`bindReviewForms(ctx, routeDomain, reload)`。来源问题编号为空时，`reviewForm()` 只显示“旧版专题机会，请先重新运行专题分析后再闭环”，不渲染提交按钮。其他表单用 `data-opportunity-code` 定位机会，两个金额 input 使用 `type="number" min="0" step="0.01"`，textarea 预填 `review_note || correction_note`。四个按钮分别提交 `needs_review`、`closed`、`still_invalid`、`not_required`。

提交时从 `event.submitter.dataset.status` 取状态，将空金额转为 `null`、非空转 `Number`；提交期间禁用当前表单所有 button。成功后调用 `reload()`；失败时只更新当前 `.analysis-review-error`，不重绘表单，因此用户输入保留；`finally` 恢复按钮。

- [ ] **Step 6: 改造两个页面并添加响应式样式**

两个页面新增状态筛选，加载时把 `status` 加入 URL；汇总增加“待处理”“待复核”“已闭环”“核实可追回”“实际落实”指标。机会列表改为卡片网格，每张卡展示问题编号、状态、测算金额和 `reviewForm()`，加载完成后调用 `bindReviewForms()`。

CSS 使用：

```css
.analysis-review-list { display: grid; gap: 1rem; }
.analysis-review-card { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(18rem, 1fr); gap: 1rem; }
.analysis-review-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; }
@media (max-width: 760px) {
  .analysis-review-card,
  .analysis-review-fields { grid-template-columns: 1fr; }
}
```

更新两个页面 import 的 cache query 版本，防止旧静态资源滞留。

- [ ] **Step 7: 运行前端契约与语法检查**

Run: `.venv/bin/python -m pytest tests/test_analysis_review_ui.py tests/routes/test_analysis.py -q`

Run: `for module in src/governance_app/static/*.js; do node --check "$module"; done`

Expected: Python 契约测试通过，所有 JS 文件语法检查退出码为 0。

- [ ] **Step 8: 提交**

```bash
git add src/governance_app/routes/analysis.py tests/routes/test_analysis.py src/governance_app/static/analysis-review.js src/governance_app/static/electricity-analysis.js src/governance_app/static/tower-rent-analysis.js src/governance_app/static/styles.css tests/test_analysis_review_ui.py
git commit -m "Add specialist review interface"
```

### Task 7: 专题核查成果归档

**Files:**
- Modify: `src/governance_app/archive.py`
- Modify: `tests/test_analytics_backup.py`

**Interfaces:**
- Consumes: `analysis_opportunity_reviews` 持久快照和来源问题最终状态。
- Produces: 归档工作簿 `专题核查成果` sheet。

- [ ] **Step 1: 写含历史失效机会的归档失败测试**

建立已核查机会后删除对应 `analysis_opportunities` 行，把问题状态设为 `closed`、批次设为 `returning`，执行归档并断言 sheet 仍含成果：

```python
path = archive_batch(app_config, batch_id)
wb = load_workbook(path, data_only=True)
assert "专题核查成果" in wb.sheetnames
ws = wb["专题核查成果"]
headers = [cell.value for cell in ws[1]]
assert headers == [
    "批次", "专题领域", "机会编号", "机会类型", "来源问题编号", "最终问题状态",
    "地市", "站址编码", "站址名称", "测算可追回金额", "测算压降/优惠金额",
    "核实可追回金额", "实际落实金额", "核查说明", "更新时间",
]
assert ws["C2"].value == opportunity_code
assert ws["L2"].value == 1200.5
```

- [ ] **Step 2: 运行归档测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_analytics_backup.py -q`

Expected: 工作簿中不存在 `专题核查成果`。

- [ ] **Step 3: 从持久核查表生成归档 sheet**

在 `archive_batch()` 的同一个读取连接中创建 sheet，并以核查表为主表：

```sql
select r.batch_id, r.domain, r.opportunity_code, r.opportunity_type,
       r.source_issue_code, i.status as issue_status,
       coalesce(i.city, '未填地市') as city,
       i.telecom_site_code, i.telecom_site_name,
       r.estimated_recoverable_amount, r.estimated_saving_amount,
       r.verified_recoverable_amount, r.realized_saving_amount,
       r.review_note, r.updated_at
  from analysis_opportunity_reviews r
  join issues i on i.issue_code = r.source_issue_code
 where r.batch_id = ?
 order by r.domain, r.opportunity_code
```

领域显示为“电费压降”或“铁塔租费”；状态复用 `_status_label()`。即使没有成果行，也创建只有 header 的 sheet；不改变 `archive_precheck()` 的阻断条件。

- [ ] **Step 4: 运行归档与备份测试**

Run: `.venv/bin/python -m pytest tests/test_analytics_backup.py -q`

Expected: 新成果 sheet、原归档 sheet 和归档锁定行为全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/governance_app/archive.py tests/test_analytics_backup.py
git commit -m "Archive specialist review outcomes"
```

### Task 8: 完整闭环、质量门与交付复核

**Files:**
- Modify: `tests/test_end_to_end.py`
- Modify: `README.md`（仅当现有操作说明包含专题导出或整改回传步骤时）

**Interfaces:**
- Consumes: 前七个任务的数据库、服务、Excel、API、页面和归档能力。
- Produces: 可复现的全流程验收证据。

- [ ] **Step 1: 写完整业务闭环测试**

在 `tests/test_end_to_end.py` 新增单个完整流程：导入 → 稽核 → 电费专题分析 → 专题 Excel 导出 → 填写整改结果/说明/两类金额 → 使用现有整改回传导入 → API 复核为 `closed` → 重新运行稽核和专题分析 → 归档。最终断言：

```python
assert refreshed["opportunity_code"] == opportunity_code
assert refreshed["issue_status"] == "closed"
assert refreshed["verified_recoverable_amount"] == 1200.5
assert refreshed["realized_saving_amount"] == 800.0
assert load_workbook(archive_path)["专题核查成果"].max_row == 2
```

同一测试前半段先导入一个没有专题列的普通整改包，断言没有核查行，以证明兼容性。

- [ ] **Step 2: 运行完整测试确认任何遗漏**

Run: `.venv/bin/python -m pytest tests/test_end_to_end.py -q`

Expected: 完整闭环通过；若失败，只修复该失败揭示的真实集成缺口，不扩大功能范围。

- [ ] **Step 3: 更新必要的用户说明**

若 `README.md` 已有专题分析/整改回传操作段落，在对应位置追加三句明确说明：专题导出新增 `整改问题清单`、仍从原整改回传入口导入、测算金额不等于核实成果。若 README 没有相关操作段落，本步骤不创建新的长篇使用手册。

- [ ] **Step 4: 运行全量质量门**

Run: `scripts/check.sh`

Expected:

```text
Ruff: pass
mypy: pass
pytest: all tests passed
coverage: >= 90.00%
compileall: pass
all JavaScript syntax checks: pass
all shell syntax checks: pass
```

- [ ] **Step 5: 检查迁移与工作区状态**

Run: `git diff --check`

Run: `git status --short`

Expected: `git diff --check` 无输出；`git status --short` 只列出本任务预期文件。

- [ ] **Step 6: 提交最终集成测试与文档**

```bash
git add tests/test_end_to_end.py README.md
git commit -m "Verify specialist review closure flow"
```

- [ ] **Step 7: 按验收表做最终复核**

逐项确认：专题文件可从现有入口回传；普通回传不变；状态只在 `issues`；两类金额刷新后保留；在线更新可回滚；两个页面可操作；历史成果可归档；版本 2 可升级；全量覆盖率不低于 90%。完成后再进入代码审查与分支集成流程。
