# 在线基础设施配置

在线数据库、文件存储、身份权限与后台任务均已接入。生产部署必须置于 HTTPS 反向代理
之后，不能直接把内置 HTTP 端口暴露到公网。

## PostgreSQL

安装在线依赖：

```bash
python -m pip install -e ".[online]"
```

应用使用 `postgresql+psycopg` 驱动、连接池预检和事务单元。首次初始化会在 PostgreSQL
事务级 advisory lock 内创建 `schema_migrations`，然后按版本应用结构迁移。配置必须
使用 `postgresql://` 或 `postgresql+psycopg://` URL。

## S3 兼容对象存储

文件存储支持 AWS S3、MinIO 及实现 S3 API 的服务。需要配置 bucket，可选配置 endpoint、
region 和对象前缀。上传和生成文件写入对象元数据中的 SHA-256；下载到服务端暂存目录时
会重新校验。客户端只获得受管 `file_id` 和 `/api/files/{file_id}` 下载地址，不接触服务端
绝对路径或对象存储凭据。

## 身份与数据权限

内置角色包括省级业务管理员、市州管理员、平台管理员、稽核人员和整改人员。启动配置中的
`BOOTSTRAP_ADMIN_USERNAME` 创建省级业务管理员；省级人员维护市州及区县目录和用户，
市州管理员仅维护本辖区普通用户。平台管理员单独通过
`python -m governance_app.online_admin provision-platform-admin --postgres-url <DATABASE_URL> --username <账号>`
创建，密码由终端安全提示输入，只拥有系统配置权限。旧 `organization_admin` 账号按市州范围兼容。

服务端会话使用不落盘明文的
随机令牌，浏览器使用 `HttpOnly + Secure + SameSite=Lax` Cookie，写请求同时校验 CSRF；
手机端可使用登录接口返回的 Bearer Token。连续五次密码错误后账号锁定十五分钟。

批次在创建或导入成功后绑定发起人的组织。省级业务管理员可访问全省批次；
其他业务角色只能访问本组织批次；平台管理员不能访问业务批次。对象存储 key 同样带组织域，
不能通过猜测文件引用跨域下载。记录级市州、区县隔离属于后续议题，当前批次权限不能替代它。

## 后台任务

在线模式下，导入、稽核、专题分析、报表导出和归档会返回 `202` 与任务编号。任务状态、
进度、重试次数、错误和结果均持久化；服务重启会恢复排队或中断任务。同一组织可以通过
`idempotency_key` 避免重复提交。

## 启动配置

复制 `deploy/online.env.example` 到安全的密钥管理系统，不要提交真实值。最小配置包括：

- `DATABASE_URL`
- `OBJECT_STORAGE_BUCKET`、endpoint 与区域
- 对象存储访问凭据
- `BOOTSTRAP_ADMIN_USERNAME` 与至少 12 位随机密码
- `SESSION_TTL_SECONDS` 和 `TASK_WORKERS`

本地联调可运行 `docker compose --env-file <安全配置> -f docker-compose.online.yml up`。
完整生产检查、迁移和恢复步骤见 `docs/online-operations-runbook.md`。
