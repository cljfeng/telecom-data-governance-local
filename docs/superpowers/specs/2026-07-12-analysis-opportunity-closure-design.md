# 专题分析核查闭环设计

## 背景

系统已经具备两类专题分析：电费压降机会和铁塔租费异常线索。专题结果可以在页面查看并导出 Excel，但核查结果仍依赖线下反馈，无法在系统内形成“发现—核查—整改—复核—归档”的完整闭环。

现有通用问题流程已经支持稳定问题编号、整改包导出、整改回传、问题状态、事件日志和归档。本阶段复用这些能力，不建立第二套状态机。

## 目标

- 每条专题机会可以追溯到来源问题编号。
- 专题导出文件可以直接通过现有整改回传入口导入。
- 同时记录核实可追回金额和实际落实金额。
- 在线处理时，问题状态与专题核查结果原子更新。
- 重新稽核或重新生成专题分析时，不丢失人工核查结果。
- 专题页面、汇总和归档报表展示闭环状态与最终成果。

## 非目标

- 不建立独立于 `issues.status` 的专题状态机。
- 不改变现有审计规则、阈值、机会金额测算公式和问题编号算法。
- 不要求普通整改包填写专题字段。
- 不把待核查金额自动认定为损失或实际成果。
- 不引入用户账号、审批人或多级审批流程。
- 不改变现有部分成功的整改回传语义。

## 方案选择

采用“派生专题结果 + 持久核查结果”分离方案。

### 未采用：直接扩展 `analysis_opportunities`

该表会在重新稽核或重新运行专题分析时清理并重建。把人工核查字段放入此表会导致成果丢失。

### 未采用：把专题金额写入 `issues`

通用问题表承载所有规则问题。加入专题专用金额会污染通用模型，并使非专题问题出现无意义字段。

### 采用：独立核查结果表

专题机会继续作为可重建的派生结果；人工核查结果按稳定机会编号独立保存。状态仍从来源问题读取，金额和说明从核查结果表读取。

## 数据模型

数据库版本从 2 升级到 3。

### `analysis_opportunities` 扩展

新增可空字段：

```sql
source_issue_code text
```

新增索引：

```sql
create index idx_analysis_opportunities_source_issue
    on analysis_opportunities(source_issue_code);
```

新生成的电费机会和租费线索必须写入来源问题编号。版本 2 中已有的派生结果保持可读；因缺少来源编号，页面提示重新运行对应专题分析后再进入闭环，不在迁移中猜测关联关系。

### `analysis_opportunity_reviews`

新增持久表：

```sql
create table analysis_opportunity_reviews (
    id integer primary key autoincrement,
    batch_id integer not null references import_batches(id) on delete cascade,
    domain text not null,
    opportunity_code text not null unique,
    opportunity_type text not null,
    source_issue_code text not null references issues(issue_code) on delete cascade,
    estimated_recoverable_amount real not null default 0,
    estimated_saving_amount real not null default 0,
    verified_recoverable_amount real,
    realized_saving_amount real,
    review_note text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    check (estimated_recoverable_amount >= 0),
    check (estimated_saving_amount >= 0),
    check (verified_recoverable_amount is null or verified_recoverable_amount >= 0),
    check (realized_saving_amount is null or realized_saving_amount >= 0)
);
```

索引：

```sql
create index idx_analysis_reviews_batch_domain
    on analysis_opportunity_reviews(batch_id, domain);
create index idx_analysis_reviews_source_issue
    on analysis_opportunity_reviews(source_issue_code);
```

核实金额允许为空，以区分“尚未填写”和“核实为 0”。创建核查记录时同步保存当时的测算可追回金额和测算压降/优惠金额，确保派生机会失效后仍能在归档中对比测算与成果。核查记录一旦创建，不随 `analysis_opportunities` 清理。表内保存领域和机会类型，使失效线索仍可进入历史归档。

## 状态模型

专题状态直接使用来源问题的 `issues.status`：

- `pending_export`：尚未发出整改。
- `pending_correction`：等待回传。
- `returned`：已回传，等待系统或人工判断。
- `needs_review`：已填写核查信息，等待复核。
- `still_invalid`：核查后仍异常，需要继续整改。
- `closed`：已复核闭环。
- `not_required`：核实后无需整改。
- `resolved_by_reaudit`：重新稽核后问题不再命中。

专题表不保存状态副本。所有筛选、汇总和归档均实时关联来源问题，避免两个状态不一致。

## 服务边界

新增 `analysis_reviews.py`，负责：

- 查询机会、来源问题和持久核查结果。
- 校验机会编号、问题编号、批次、领域和归档状态。
- 校验金额为空或非负数字。
- 创建核查记录时复制当前机会的两类测算金额。
- 在已有数据库连接中保存核查结果。
- 为在线请求原子更新问题状态、事件日志和核查结果。
- 为整改回传提供同连接写入函数。

现有问题状态校验继续使用 `IssueStatus` 和 `ISSUE_STATUSES`。将问题状态写入逻辑提取为连接级内部函数，在线专题服务和现有通用状态接口共同复用，不复制状态规则。

## 专题分析生成

电费和租费分析生成机会时，把当前 `issue_code` 写入 `analysis_opportunities.source_issue_code`。

重新运行专题分析：

1. 只清理对应领域的派生机会。
2. 不清理 `analysis_opportunity_reviews`。
3. 以原有稳定算法重新生成机会编号。
4. 查询时按 `opportunity_code` 自动关联原核查结果。

重新稽核会继续清理派生机会，但不删除人工核查结果。如果问题重新出现且生成相同机会编号，原核查结果自动恢复关联；未重新出现的历史结果只在归档成果中展示。

## Excel 导出与回传

### 导出兼容

电费和租费专题工作簿保留现有说明、机会清单、地市汇总和类型汇总，不重命名现有 sheet。

新增 `整改问题清单` sheet。它包含现有整改导入器要求的字段：

- 问题编号
- 整改结果
- 整改说明
- 整改后值

并增加专题字段：

- 机会编号
- 核实可追回金额
- 实际落实金额

工作簿继续提供整改结果下拉选项。说明页明确：测算金额是核查参考，只有核实可追回金额和实际落实金额计入成果。

### 回传识别

普通整改包没有专题字段时，导入行为完全不变。

若 `整改问题清单` 出现任一专题字段，则三个专题字段必须同时存在。每行处理顺序：

1. 验证问题编号和现有整改必填列。
2. 验证机会编号存在，且与问题编号、批次和领域匹配。
3. 验证两类金额为空或为非负数字。
4. 专题金额有填写时，整改结果或整改说明至少填写一项。
5. 在同一数据库事务中更新问题状态、整改字段、问题事件和专题核查结果。

空白专题金额不会覆盖已有金额；明确填写 `0` 会保存为核实结果。错误行写入现有错误列表并跳过，有效行继续处理，保持当前部分成功机制。

`整改说明` 是核查说明的统一来源。专题回传和在线保存会同时写入 `issues.correction_note` 与核查结果表；普通整改回传更新已存在专题核查记录的说明，但不会凭空创建核查记录，避免两个页面显示不同说明。

重复校验继续以问题编号为主。当前每个问题只生成一个专题机会；未来若业务确认需要一对多机会，再单独扩展回传分组语义，本阶段不提前增加复杂度。

## 在线接口

两个专题领域新增统一动作：

```text
POST /api/batches/{batch_id}/{domain}/review
```

其中 `domain` 为现有的：

- `electricity-analysis`
- `tower-rent-analysis`

请求体：

```json
{
  "opportunity_code": "OPP-...",
  "status": "needs_review",
  "verified_recoverable_amount": 1200.5,
  "realized_saving_amount": 800,
  "review_note": "已核对账单并完成退款"
}
```

允许在线提交的状态为：

- `needs_review`
- `still_invalid`
- `closed`
- `not_required`

服务端通过机会编号解析来源问题，不接受客户端另传问题编号。一次事务内完成状态、事件、金额和说明写入；任一校验失败全部回滚。

成功响应返回最新机会核查载荷。不存在返回 400 兼容当前分析路由错误处理；已归档批次返回现有归档错误文案。

## 查询与汇总

电费机会和租费线索接口保留现有字段，并新增：

- `issue_code`
- `issue_status`
- `correction_value`
- `correction_note`
- `verified_recoverable_amount`
- `realized_saving_amount`
- `review_note`
- `reviewed_at`

`reviewed_at` 是核查记录 `updated_at` 的接口别名，不新增第三个时间字段。

机会列表新增 `status` 筛选，映射到来源问题状态。

现有汇总字段保持兼容，并新增：

- 待处理数量。
- 已回传/待复核数量。
- 已闭环数量。
- 核实可追回金额合计。
- 实际落实金额合计。

只有核查结果表中的金额进入最终成果合计；测算金额继续显示为机会规模，不与实际成果混用。

## 页面交互

电费压降和租费异常页面增加：

- 状态筛选。
- 问题状态标签。
- 核实可追回金额。
- 实际落实金额。
- 核查说明。
- 保存核查、确认闭环、仍需整改、无需整改操作。

保存操作调用原子核查接口。请求进行中禁用重复提交；成功后刷新当前列表和汇总，失败时保留用户输入并显示服务端中文错误。

移动端保持单列卡片，不增加横向滚动表格。已有金额口径说明继续显示。

## 归档报表

归档工作簿新增 `专题核查成果` sheet，字段包括：

- 批次、专题领域、机会编号、机会类型。
- 来源问题编号及最终问题状态。
- 地市、站址编码和站址名称。
- 测算可追回金额、测算压降/优惠金额。
- 核实可追回金额、实际落实金额。
- 核查说明和更新时间。

归档读取持久核查结果为主，即使派生机会已因重新稽核失效，仍保留历史人工成果。未产生核查结果的机会不写入成果 sheet，也不新增归档阻断条件；是否可归档继续由现有问题闭环状态决定。

## 错误处理

- 非数字、负数或非有限金额：拒绝当前行或请求，不写入任何字段。
- 机会不存在、来源问题缺失或编号不匹配：拒绝并返回明确中文错误。
- 专题领域与机会领域不匹配：拒绝，防止跨专题写入。
- 批次已归档：拒绝在线处理和回传，保持归档不可变。
- 状态不属于允许集合：拒绝，不通过类型转换绕过运行时校验。
- 数据库写入异常：在线请求整体回滚；Excel 导入事务整体回滚并返回错误。
- 版本 2 旧机会缺少来源编号：允许查看和重新导出原列表，但闭环操作提示先重新运行专题分析。

## 测试策略

### 数据库迁移

- 全新数据库直接初始化到版本 3。
- 版本 2 数据库升级后保留问题、专题机会和整改记录。
- 新字段、核查表、约束和索引存在。
- 非负金额约束生效。
- 重复初始化不重复迁移。

### 核查服务

- 正常保存两类金额、说明和问题状态。
- 空值与明确的 0 语义不同。
- 负数、非有限数、无效状态、错误领域和错误编号被拒绝。
- 状态与核查结果原子提交和回滚。
- 已归档批次拒绝修改。
- 重新生成分析结果后核查数据仍存在。
- 重新稽核解除问题后历史成果仍可归档。

### Excel

- 电费和租费导出均包含兼容的 `整改问题清单`。
- 普通整改包回传保持原行为。
- 专题回传同时更新问题状态和成果金额。
- 专题列缺失、金额非法、编号不匹配和重复行进入错误明细。
- 有效行与错误行混合时保持部分成功。
- 空白金额不覆盖已有结果，0 可以覆盖为 0。

### API 与页面

- 两个专题 review 路径均匹配正确。
- 成功响应包含最新状态和金额。
- 列表按状态筛选。
- 汇总区分测算金额与最终成果。
- 前端保存期间防重复提交，成功刷新，失败保留输入。
- 桌面和移动布局不出现横向溢出。

### 端到端与归档

- 导入—稽核—专题分析—导出—回传—复核—归档完整流程通过。
- 归档工作簿包含 `专题核查成果`。
- 重复稽核后稳定问题、稳定机会和人工核查成果保持一致。
- Ruff、mypy、90% 覆盖率和全部语法检查通过。

## 验收标准

- 专题导出文件可直接通过现有整改回传入口导入。
- 普通整改回传完全兼容。
- 状态只存在于 `issues`，专题不形成平行状态机。
- 两类最终金额持久保存，重新稽核和分析刷新不丢失。
- 在线更新不存在半完成状态。
- 两个专题页面和归档报表展示最终成果。
- 数据库从版本 2 安全升级到版本 3。
- 完整质量门通过，覆盖率不低于 90%。
