# Local Base Data Governance

本项目是省公司本地单机版基础数据治理工具，用于导入站址、铁塔租费、电费、发电费台账，执行稽核，导出地市整改包，并导入整改回传。

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
.venv/bin/python -m pip install -e ".[test]"
PYTHONPATH=src .venv/bin/python -m governance_app.server --workspace . --port 8765
```

启动后在浏览器打开：

```text
http://127.0.0.1:8765
```

显式设置 `PYTHONPATH=src` 可以确保在尚未完成 editable install，或本地虚拟环境未加载 editable `.pth` 文件时仍能启动。

默认数据保存到 `data/governance.sqlite3`，导出文件保存到 `exports/`。不要把真实生产数据提交到 git。

## First Release Scope

- 支持四类台账模板导入：站址、铁塔租费、电费、发电费。
- 支持 SQLite 本地存储，保留导入批次和原始行。
- 支持基础稽核规则和稳定问题编号。
- 支持按地市导出整改问题包。
- 支持导入地市整改回传并更新问题状态。
- 支持专项工作台基础指标和本地数据库备份恢复。
