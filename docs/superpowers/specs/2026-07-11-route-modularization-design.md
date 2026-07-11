# 后端路由模块化设计

## 背景

当前应用使用 Python 标准库 `ThreadingHTTPServer` 提供本地 API 和静态页面。业务服务已经按导入、稽核、流程、分析和归档拆分，但大部分 HTTP 路由仍集中在 `server.py`。随着电费与租费专题、规则治理和可靠性保护持续增加，集中路由已经成为理解变更范围和控制回归风险的主要瓶颈。

现有 `routes/system.py` 已验证“领域处理器匹配请求，未匹配时返回 `None`”的拆分方式可以保持当前技术栈和测试接口。本次工作将沿用这一模式完成剩余后端路由拆分。

## 目标

- 将批次、导入、稽核问题、专题分析和报表归档路由迁入独立领域模块。
- 让 `server.py` 只负责 HTTP 适配、静态文件、请求解析和统一路由调度。
- 保持所有 API 路径、状态码、成功响应、错误文案和前端行为兼容。
- 保持 `LocalApp.handle_test_request` 与上传测试接口兼容。
- 为后续工作流、规则和专题分析服务模块化建立稳定协议边界。

## 非目标

- 不更换 `http.server`，不引入 FastAPI、Flask 或其他运行时依赖。
- 不修改数据库结构、业务状态机、稽核规则或专题金额口径。
- 不修改前端页面、导航或 API 调用方式。
- 不重构业务服务内部实现。
- 不建立通用 Web 框架或复杂路由注册 DSL。

## 方案选择

采用链式领域处理器。每个处理器接收相同的协议层参数，匹配到本领域请求时返回 JSON 响应，不匹配时返回 `None`。统一调度器按照固定顺序调用处理器，第一个非 `None` 响应结束匹配。

不采用集中路由注册表，因为现有 API 包含动态批次路径、查询参数、JSON 请求和 Multipart 上传，注册表需要额外发明参数绑定层。不引入外部框架，因为本轮目标是降低耦合而非迁移技术栈。

## 模块边界

### `server.py`

保留：

- `LocalApp` 和 `create_app`。
- `_route` 和 `_route_upload` 统一调度入口。
- `RequestHandler`、`run_server` 和命令行入口。
- HTTP 请求长度校验、Multipart 解码和静态文件服务。

移出：

- 所有具体业务服务导入。
- 具体 API 路径判断。
- 规则效果统计和专题路径解析。
- 文件落盘与导入响应组装。

### `routes/common.py`

只包含协议层共享能力：

- `JsonResponse` 类型别名。
- `json_response(payload, status=200)`。
- `json_body(body)`。
- `batch_id_from_payload(payload)`。
- `batch_id_from_query(query_string)`。
- `pagination_from_query(query)`。
- `save_uploaded_workbook(config, filename, content)`，统一处理安全文件名、扩展名和上传目录。

不得导入具体业务服务。

### `routes/batches.py`

负责：

- `GET/POST /api/batches`
- `POST /api/batches/current`
- `GET /api/workflow`
- `GET /api/dashboard`
- `GET /api/city-progress`
- `GET /api/ledger-rows`

### `routes/imports.py`

负责：

- `POST /api/import`
- `POST /api/import/preview`
- `GET /api/import/recent`
- `POST /api/import/upload`
- `POST /api/import/preview/upload`
- 导入上传请求与导入响应组装。

正式导入继续使用工作区重操作锁；预检不占用锁。

### `routes/audits.py`

负责：

- `POST /api/audit`
- `GET /api/issues`
- `GET /api/issue-groups`
- `GET /api/rules`
- `POST /api/rules/settings`
- `POST /api/issues/status`
- `POST /api/issues/group-status`

规则效果、参数元数据和调优提示随路由一起迁入本模块，后续规则服务拆分时再建立独立服务边界。

### `routes/analysis.py`

负责：

- `/api/batches/{batch_id}/electricity-analysis/{action}`
- `/api/batches/{batch_id}/tower-rent-analysis/{action}`
- 动态分析路径解析。
- 专题筛选参数组装。

支持动作继续限定为 `run`、`summary`、`opportunities` 和 `export`。

### `routes/reports.py`

负责：

- `POST /api/export`
- `POST /api/reports/notice`
- `POST /api/corrections`
- `POST /api/corrections/upload`
- `POST /api/archive`
- `GET /api/archive/precheck`

归档继续使用重操作锁。整改回传保持现有错误和状态响应。

### `routes/system.py`

继续负责：

- 健康检查和版本。
- 本地设置。
- 备份、恢复、维护和系统复位。

改为直接复用 `routes/common.py`，不再由 `server.py` 注入 JSON 辅助函数。

## 处理器接口

普通领域处理器统一为：

```python
def handle_*_route(
    config: AppConfig,
    method: str,
    parsed: ParseResult,
    body: str,
) -> JsonResponse | None:
    ...
```

上传处理器统一为：

```python
def handle_*_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> JsonResponse | None:
    ...
```

处理器不得返回“本领域未找到”的 404 来阻止后续模块匹配；仅当请求明确属于本领域但动作无效时返回 404。完全不属于本领域时返回 `None`。

## 路由调度顺序

普通请求采用固定顺序：

1. system
2. batches
3. imports
4. audits
5. analysis
6. reports

上传请求采用固定顺序：

1. imports
2. reports

全部处理器返回 `None` 时，由 `server.py` 返回统一 JSON 404。

## 数据流

1. `RequestHandler` 校验 `Content-Length`，超限请求在读取正文前返回 413。
2. JSON 请求以原始字符串传给普通路由处理器，由需要请求体的处理器调用 `json_body`。
3. Multipart 请求由 `server.py` 解码为字段和文件字节，再交给上传处理器。
4. 领域处理器完成参数校验、调用现有业务服务并组装兼容响应。
5. 业务异常继续在当前路由边界转换为对应 400、404 或 409。

## 错误兼容

- 未知 API：404，`{"error": "not found"}`。
- 非法 JSON：400，保持现有文案。
- 非法批次 ID：400，保持现有文案。
- 批次或问题不存在：保持现有 400/404 行为。
- 重操作冲突：409，保持现有中文提示。
- 上传格式或 Excel 读取错误：保持现有 400 响应。
- 上传超过 100 MiB：仍由 `RequestHandler` 在路由前返回 413。

## 测试策略

### 领域归属测试

每个处理器至少覆盖：

- 一个成功匹配的代表性路径。
- 一个其他领域路径并断言返回 `None`。
- 一个本领域非法参数或不存在资源响应。

### 兼容回归

- 保留现有 `tests/test_server.py` 测试，证明公共入口响应不变。
- 新增 `tests/routes/`，按领域测试处理器边界。
- 上传测试继续通过 `LocalApp.handle_test_upload_request` 走完整调度。
- 完整业务流测试继续验证导入、稽核、导出和回传。

### 结构约束

- 增加测试确保 `server.py` 不再导入导入器、稽核引擎、工作流、专题分析和归档业务服务。
- 增加测试确保普通和上传调度顺序固定。
- `scripts/check.sh` 继续作为唯一完整质量门禁。

## 分步迁移

1. 创建 `routes/common.py`，迁移纯协议辅助函数。
2. 迁移专题分析路由，验证动态路径。
3. 迁移批次与看板路由。
4. 迁移导入和上传路由。
5. 迁移稽核、问题和规则路由。
6. 迁移导出、整改和归档路由。
7. 调整 `system.py` 使用公共辅助函数。
8. 删除 `server.py` 中不再使用的业务导入和辅助实现。

每一步保持公共 `_route` 接口可运行，并执行对应测试后单独提交。

## 验收标准

- 所有现有 API 路径、状态码和响应字段兼容。
- `server.py` 不直接导入具体业务服务。
- 每个领域处理器只处理自己的路径，非本领域请求返回 `None`。
- JSON 和 Multipart 公共入口保持兼容。
- 现有 172 个测试及新增路由测试全部通过。
- Python 编译、所有 JavaScript 模块和 Shell 脚本检查通过。
