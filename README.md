# Local Base Data Governance

本项目是省公司本地单机版基础数据治理工具，用于导入站址、铁塔租费、电费、发电费台账，执行稽核，导出地市整改包，并导入整改回传。

## Local Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
python -m governance_app.server --workspace .
```

默认数据保存到 `data/governance.sqlite3`，导出文件保存到 `exports/`。不要把真实生产数据提交到 git。
