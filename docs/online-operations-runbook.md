# 在线平台部署、迁移与恢复手册

## 上线前

1. 在隔离环境准备 PostgreSQL、S3 兼容对象存储和 HTTPS 反向代理。
2. 从密钥管理系统注入数据库、对象存储和引导管理员凭据。
3. 访问 `/api/health` 检查进程存活，访问 `/api/ready` 检查数据库和对象存储就绪。
4. 使用引导管理员登录，创建正式组织与管理员账号，然后轮换引导密码。
5. 验证组织 A 无法访问组织 B 的批次、问题、任务和文件引用。

## SQLite 迁移演练

先对脱敏副本执行只读盘点：

```bash
python -m governance_app.online_admin migrate \
  --sqlite /safe-copy/governance.sqlite3 \
  --postgres-url "$DATABASE_URL" \
  --dry-run
```

目标 PostgreSQL 业务表必须为空。正式执行会在一个目标事务中分批写入，逐表比较源端和
目标端行数，并校准序列；任一步失败都会回滚整个目标事务：

```bash
python -m governance_app.online_admin migrate \
  --sqlite /safe-copy/governance.sqlite3 \
  --postgres-url "$DATABASE_URL"
```

迁移期间保留原 SQLite 工作区只读，不覆盖原文件。回滚时停止在线入口，切回原本地工作区；
确认问题后创建全新空 PostgreSQL 数据库再次演练，不在失败目标库上继续追加。

## 备份与恢复

定时任务设置 `DATABASE_URL`、`OBJECT_STORAGE_ALIAS` 和专用 `BACKUP_OUTPUT_DIR` 后运行：

```bash
scripts/backup_online.sh
```

脚本生成 PostgreSQL custom dump、对象镜像和数据库 SHA-256。备份目录应写入异地不可变
存储并配置保留策略。恢复会清理目标数据库和对象，因此只允许在维护窗口执行：

```bash
CONFIRM_RESTORE=RESTORE \
RESTORE_SOURCE_DIR=/verified/backup \
scripts/restore_online.sh
```

恢复后必须重新检查 `/api/ready`，抽查组织权限、批次数量、最近审计日志和文件下载。

## 监控与审计

- 负载均衡器监控 `/api/health`，编排平台监控 `/api/ready`。
- 对连续就绪失败、HTTP 5xx、任务最终失败和登录锁定配置告警。
- 请求审计记录请求 ID、用户、组织、来源 IP、状态、耗时和关联任务。
- PostgreSQL、对象存储和审计日志的保留周期由生产合规要求确定，不得由应用节点本地盘承担。

## 灰度与生产

先在灰度环境导入脱敏数据，完成权限越权、迁移回滚、备份恢复和长任务重启测试。生产开放
仍需人工验收；在验收通过前，不应将 8765 端口直接映射到公网。
