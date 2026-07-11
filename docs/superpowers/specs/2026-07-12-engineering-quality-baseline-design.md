# 第三阶段工程质量基线设计

## 背景

前两个工程治理阶段已经完成可靠性加固、路由模块化和审计规则模块化。当前质量门能够运行测试、Python 字节码编译、JavaScript 语法检查和 Shell 语法检查，但仍缺少三类持续保护：静态缺陷检查、测试覆盖率防倒退和 Python 类型检查。

本阶段采用渐进式硬门。新增检查从启用之日起必须通过，但不进行全项目格式化，也不要求一次性为所有历史函数补齐严格类型。

## 目标

- 使用 Ruff 阻止高价值的 Python 静态缺陷和导入顺序回归。
- 记录真实覆盖率并建立不可下降的最低门槛。
- 对稳定协议边界实施有约束的 mypy 类型检查。
- 让本地 `scripts/check.sh` 和 GitHub Actions 使用同一套质量门。
- 保持应用运行行为、审计规则、数据库结构和用户界面不变。

## 非目标

- 不运行 Ruff formatter，不统一改写历史代码格式。
- 不启用 mypy strict，不要求本阶段覆盖全部业务模块。
- 不为提高覆盖率而删除分支、排除生产模块或编写无业务断言的测试。
- 不修改审计阈值、分析算法、HTTP 响应或数据库迁移。
- 不改变 Windows 打包工作流的触发条件和产物。

## 方案选择

采用“渐进式硬门”，而不是一次性全项目严格治理或仅告警模式。

- 一次性严格治理会把格式、类型和覆盖率修复混入同一变更，审查面过大。
- 仅告警不能阻止质量继续下降，无法形成可持续基线。
- 渐进式硬门可以立即约束新增代码，同时把历史类型治理控制在稳定边界内。

## 依赖与配置

### 测试依赖

在 `project.optional-dependencies.test` 中加入：

- `pytest-cov>=6.0`
- `ruff>=0.11`
- `mypy>=1.15`

这些工具只属于开发和 CI，不进入业务程序运行依赖，也不进入 PyInstaller 的 `package` 依赖。

### Ruff

Ruff 检查 `src` 和 `tests`，目标 Python 版本为 3.12。首期启用：

- `E4`、`E7`、`E9`：导入、语句和运行前可识别的语法问题。
- `F`：未定义名称、未使用导入等 Pyflakes 缺陷。
- `I`：稳定导入顺序。
- `B`：常见 bug 风险。

不启用 formatter。允许对纯机械问题使用 `ruff check --fix`，但每项行为相关修复必须人工检查差异并运行全量测试。

### 覆盖率

pytest 采集 `governance_app` 包的 statement coverage，并输出缺失行：

```text
pytest --cov=governance_app --cov-report=term-missing
```

失败门槛由 `[tool.coverage.report].fail_under` 提供。基线确定规则是：在本阶段代码修改前，对当前 `main` 的 211 项测试测量一次覆盖率；门槛取不高于实测值的最大 5 的整数倍。若实测值低于 70%，门槛直接取实测值向下取整，避免伪造覆盖率。门槛一旦写入配置，本阶段不得降低。

覆盖率配置排除仅用于解释器防御的 `if __name__ == "__main__"` 分支，不排除任何生产模块。

### mypy

mypy 使用 Python 3.12，启用：

- `check_untyped_defs = true`
- `no_implicit_optional = true`
- `warn_unused_ignores = true`
- `warn_redundant_casts = true`
- `warn_return_any = true`
- `ignore_missing_imports = true`

首期检查稳定协议边界：

- `src/governance_app/rule_types.py`
- `src/governance_app/rule_fields.py`
- `src/governance_app/rule_helpers.py`
- `src/governance_app/audit_rules.py`
- `src/governance_app/rules/`
- `src/governance_app/routes/`

允许未注解函数继续存在，但其函数体仍被检查。新增的忽略注释必须带具体错误码，不允许全局关闭错误类别来掩盖问题。

## 统一质量门

`scripts/check.sh` 按以下顺序执行：

1. `ruff check src tests`
2. `mypy`
3. 带覆盖率门槛的 pytest
4. `compileall`
5. 自动发现全部静态 JavaScript 文件并运行 `node --check`
6. 检查 macOS/Linux 构建和启动脚本语法

命令在首个失败处停止。开发者和 CI 都只调用 `scripts/check.sh`，避免本地与远端规则漂移。

GitHub Actions 继续安装 `.[test]`，因此会自动获得三个质量工具。质量工作流无需复制具体 Ruff、mypy 或覆盖率参数。

## 文档

`CONTRIBUTING.md` 说明质量门包含哪些检查，以及如何单独运行 Ruff、mypy 和覆盖率测试。`README.md` 的交付前检查说明同步更新，避免仍将脚本描述为仅运行测试和语法检查。

## 测试策略

### 配置契约测试

新增 `tests/test_quality_gate.py`，解析 `pyproject.toml`、`scripts/check.sh` 和质量工作流，锁定：

- 三个工具位于 test 可选依赖中，不进入运行依赖。
- Ruff 目标版本、启用规则和检查目录符合设计。
- mypy 的目标文件和渐进式选项符合设计。
- 覆盖率门槛存在且大于 0。
- 本地脚本实际执行 Ruff、mypy 和带门槛的 pytest。
- CI 继续通过 `scripts/check.sh` 使用同一入口。

### 行为回归

- 修改质量配置前，现有 211 项测试必须通过。
- Ruff 和 mypy 修复后再次运行现有全量测试。
- 最终运行完整 `scripts/check.sh`，其输出必须同时证明静态检查、类型检查、覆盖率门槛、测试和语法检查通过。

### 防伪检查

- 搜索配置，确认没有新增生产模块覆盖率排除项。
- 搜索 mypy 配置，确认没有 `ignore_errors = true`。
- 搜索 Ruff 配置，确认没有全局 `# noqa` 或全文件忽略来绕过新增质量门。

## 错误处理

- 工具缺失时，`scripts/check.sh` 直接失败并提示缺失模块；安装 `.[test]` 是唯一支持的修复路径。
- 覆盖率低于门槛时，pytest 返回非零状态；不得自动降低门槛。
- 静态检查发现历史问题时，进行范围最小的行为保持修复，并用现有测试验证。
- CI 与本地结果不一致时，以同一 Python 3.12 环境重新安装 `.[test]` 后复现，不在工作流中加入绕过条件。

## 验收标准

- `scripts/check.sh` 在干净环境中包含并通过 Ruff、mypy、覆盖率 pytest、Python/JavaScript/Shell 语法检查。
- 全部原有测试和新增质量门契约测试通过。
- 覆盖率门槛由修改前实测值按规则产生，并在配置中固定。
- Ruff 不执行全项目格式化；mypy 只覆盖定义的稳定边界。
- GitHub Actions 仍只调用统一质量脚本。
- 工作区无未提交文件，第三阶段提交合并到 `main` 并推送到 `origin/main`。
