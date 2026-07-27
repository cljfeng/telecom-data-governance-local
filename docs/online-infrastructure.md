# 在线基础设施配置

在线数据库与文件存储适配器已经实现，但在线 HTTP 服务仍保持关闭，直到身份认证和
数据授权完成。这样可以验证基础设施而不会把无鉴权接口暴露到网络。

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

当前在线组件可通过 `AppConfig.for_online_workspace(...)` 在集成测试或受控环境中装配。
正式启动入口仍会拒绝 `APP_MODE=online`；完成登录、组织、角色和数据范围权限后再解除。
