# 稽核规则模块化设计

## 背景

当前规则类型、字段别名、电费批量规则和规则元数据已经分别迁入 `rule_types.py`、`rule_fields.py`、`rules/electricity.py` 和 `rule_catalog.py`，但 `audit_rules.py` 仍包含站址、铁塔租费、发电费、跨台账规则及多个规则工厂，文件约 775 行。

`audit_engine.py`、分析服务、报表、工作流和部分测试均通过 `governance_app.audit_rules` 访问规则集合、元数据或行解析函数。本次工作需要整理内部边界，同时保持这个导入路径稳定。

## 目标

- 按业务领域拆分站址、铁塔租费、发电费、跨台账和电费规则。
- 将可复用规则工厂迁入独立模块，避免领域模块互相依赖。
- 将 `audit_rules.py` 缩减为稳定兼容门面和规则集合聚合器。
- 保持规则 ID、顺序、严重程度、阈值、命中字段、文案和建议完全兼容。
- 保持稽核问题编号和专题分析结果兼容。

## 非目标

- 不新增、删除、启停或调整任何业务规则。
- 不修改阈值默认值和规则设置接口。
- 不修改数据库结构、稽核运行流程、问题状态或专题金额计算。
- 不引入注册表装饰器、插件自动发现或动态加载。
- 不将每一条规则拆成独立文件。

## 方案选择

采用“领域模块 + 兼容门面”。各领域模块显式返回有序规则列表，`audit_rules.py` 按当前顺序组合这些列表。相比自动注册方式，显式组合更容易检查顺序、定位依赖和比较拆分前后的规则快照。

## 模块边界

### `rule_types.py`

继续保存：

- `RuleFinding`
- `AuditRule`
- `RuleThresholds`
- `AuditLedgerRow`
- `BatchRuleFinding`
- `BatchAuditRule`

本次不修改这些类型的字段或语义。

### `rule_helpers.py`

继续保存字段读取、文本、数字、日期、月份、账期、中位数和正向费用等基础解析函数。本次只允许补充多个领域都会使用且与具体规则无关的纯函数。

### `rules/factories.py`

负责返回评估函数的通用规则工厂：

- 必填字段。
- 数值范围和数值上限。
- 正数校验。
- 同账期同站址重复正向费用。
- 分组字段一致性。

工厂只接收明确参数并返回无状态函数，不读取全局规则配置。

### `rules/site.py`

负责：

- 站址编码必填。
- 地市必填。
- 站址编码缺失但名称重复。
- 业务台账站址编码在站址主数据中不存在。

提供：

```python
def site_rules(thresholds: RuleThresholds) -> list[AuditRule]: ...
def site_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]: ...
```

阈值参数即使当前未使用也保留，以统一领域入口。

### `rules/tower_rent.py`

负责：

- 挂高超过塔高。
- 同站址塔高不一致。
- 业务确认单产品变化。
- 铁塔和机房共享用户数不一致。
- 四类费用重复计费。
- 产品单元为零但费用非零。
- 共享维护费折扣异常。
- 原产权方电力引入费异常。
- 停租后继续计费。

提供：

```python
def tower_rent_rules(thresholds: RuleThresholds) -> list[AuditRule]: ...
def tower_rent_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]: ...
```

当前没有租费行级规则时，`tower_rent_rules()` 返回空列表。

### `rules/generator.py`

负责：

- 发电时长超过 24 小时的行级规则。
- 缺少发电责任方。
- 有费用但缺少发电日期。
- 发电工单重复。
- 填报时长与起止时间不一致。
- 小时成本异常。

提供：

```python
def generator_rules(thresholds: RuleThresholds) -> list[AuditRule]: ...
def generator_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]: ...
```

### `rules/cross_ledger.py`

负责真正跨领域或适用于全部台账的规则：

- 负金额。
- 费用环比突变。
- 有正向费用但站址主数据不存在。
- 跨台账站址名称不一致。

提供：

```python
def cross_ledger_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]: ...
```

### `rules/electricity.py`

继续保存现有电费批量规则，并新增电费行级入口：

```python
def electricity_rules(thresholds: RuleThresholds) -> list[AuditRule]: ...
def electricity_batch_rules(thresholds: RuleThresholds) -> list[BatchAuditRule]: ...
```

电费行级入口负责电费单价上限和分摊比例范围规则。现有批量规则顺序不变。

### `audit_rules.py`

只保留：

- 从 `rule_types.py` 再导出兼容类型。
- `DEFAULT_THRESHOLDS`。
- `parse_row()`。
- `rule_metadata()`。
- `all_rules()`。
- `all_batch_rules()`。

不得继续定义以 `_tower_`、`_generator_`、`_site_`、`_fee_` 开头的领域规则实现。

## 规则组合顺序

`all_rules()` 必须保持当前顺序：

1. `required_site_code`
2. `required_city`
3. `electricity_price_range`
4. `electricity_share_percent`
5. `generator_duration_over_24h`

`all_batch_rules()` 必须保持当前完整顺序。实现可以从各领域列表建立 `rule_id -> rule` 映射，再按固定 ID 元组输出，避免领域拆分改变历史顺序。

固定顺序由快照测试锁定；新增规则必须显式加入顺序清单。

## 依赖方向

允许的依赖方向：

```text
audit_rules
  -> rules.site / tower_rent / electricity / generator / cross_ledger
  -> rule_types / rule_catalog

rules.*
  -> rules.factories
  -> rule_types / rule_helpers / rule_fields / geo
```

领域模块不得导入 `audit_rules.py`。`rules/factories.py` 不得导入任何领域模块。

## 数据流

1. `audit_engine.py` 调用兼容门面的 `all_rules()` 和 `all_batch_rules()`。
2. 门面用同一个 `RuleThresholds` 实例调用各领域入口。
3. 门面按固定规则 ID 顺序返回最终列表。
4. 稽核引擎继续按现有逻辑筛选台账类型、生成发现项和稳定问题编号。
5. 分析、报表和工作流继续通过 `rule_metadata()` 或 `parse_row()` 使用原入口。

## 兼容性要求

- 以下导入必须继续有效：

```python
from governance_app.audit_rules import (
    AuditLedgerRow,
    AuditRule,
    BatchAuditRule,
    BatchRuleFinding,
    DEFAULT_THRESHOLDS,
    RuleFinding,
    RuleThresholds,
    all_batch_rules,
    all_rules,
    parse_row,
    rule_metadata,
)
```

- `all_rules()` 和 `all_batch_rules()` 返回新列表，不共享可变全局列表。
- 同一阈值配置必须产生与拆分前相同的规则闭包行为。
- 未登记规则元数据的兼容兜底保持不变。

## 测试策略

### 规则快照

保存当前规则清单的以下字段并比较拆分后结果：

- 顺序。
- `rule_id`。
- `ledger_type`。
- `severity`。

默认阈值和自定义阈值各执行一次。

### 领域归属

每个领域模块测试：

- 返回的规则 ID 集合符合领域边界。
- 不包含其他领域规则。
- 当前代表性规则仍命中同一字段、文案和建议。

### 稽核输出兼容

使用同一份样例或构造台账执行稽核，比较：

- 问题数量。
- 问题编号。
- 规则 ID。
- 严重程度。
- 问题说明。
- 建议整改方向。

### 结构约束

- `audit_rules.py` 不再包含领域实现函数。
- 领域模块不导入 `audit_rules.py`。
- 工厂模块不导入领域模块。
- 现有全部测试保持通过。

## 分步迁移

1. 建立规则清单和代表性输出快照。
2. 提取通用规则工厂。
3. 迁移站址规则。
4. 迁移铁塔租费规则。
5. 迁移发电费规则。
6. 迁移跨台账规则。
7. 补齐电费行级入口。
8. 将 `audit_rules.py` 收敛为兼容门面。
9. 运行完整质量门禁和独立审查。

每一步只移动代码，不改变业务表达式，并在对应领域测试通过后单独提交。

## 验收标准

- `audit_rules.py` 成为精简兼容门面。
- 五个领域入口和通用工厂边界清晰，无循环依赖。
- 规则 ID、顺序、台账类型、严重程度、阈值和文案保持兼容。
- 稳定问题编号和专题分析输出不变。
- 当前 194 个测试及新增规则快照、归属和结构测试全部通过。
- Python、全部 JavaScript 和 Shell 质量检查通过。
