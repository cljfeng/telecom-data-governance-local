# 贡献指南

感谢你愿意一起开发这个项目。为了让协作更顺畅，请尽量按下面的流程提交改动。

## 开发环境

本项目使用 Python 3.12。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test,package]"
```

Windows PowerShell 可使用：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test,package]"
```

## 本地运行

```bash
scripts/start.sh
```

默认访问地址：

```text
http://127.0.0.1:8765
```

## 提交前检查

提交前请至少运行：

```bash
scripts/check.sh
```

如果改动涉及打包，也请验证对应平台的构建脚本：

```bash
scripts/build_app.sh
```

Windows 打包脚本：

```powershell
.\scripts\build_app.ps1
```

## 分支规范

不要直接向默认分支提交代码。请从最新默认分支创建功能分支：

```bash
git pull
git switch -c feature/short-description
```

建议分支命名：

- `feature/...`：新功能
- `fix/...`：问题修复
- `docs/...`：文档调整
- `refactor/...`：不改变行为的代码整理

## 提交规范

提交信息保持简短、明确，建议使用英文动词开头，例如：

```text
Add import preview validation
Fix duplicate issue export rows
Update contributor guide
```

一次提交尽量只做一类事情，避免把无关改动混在一起。

## Pull Request 流程

1. 从默认分支创建新分支。
2. 完成改动并运行检查。
3. 推送分支到 GitHub。
4. 创建 Pull Request。
5. 在 PR 描述里说明改了什么、为什么改、如何验证。
6. 等待 review，通过后再合并。

PR 描述建议包含：

```markdown
## 变更内容

## 验证方式

## 备注
```

## 不要提交的内容

请不要提交以下内容：

- `.venv/`、`.uv-cache/`、`.uv-python/`
- `data/`、`exports/`、`backups/`
- Excel、CSV、SQLite 数据库文件
- `.env`、密钥、token、账号密码
- 构建产物和本地日志

这些内容已经在 `.gitignore` 中排除。如果确实需要提供示例数据，请先脱敏，并放在明确的示例目录中。

## 代码风格

- 优先沿用现有代码结构和命名方式。
- 尽量保持函数职责清晰，避免一次改动跨太多模块。
- 面向用户的文案请保持简洁、明确。
- 涉及数据处理、导入、导出、审计规则的改动，应补充或更新测试。

## 发布

Release 由维护者创建。普通功能开发完成后，请通过 Pull Request 合并到默认分支，不要直接创建 tag 或 Release。
