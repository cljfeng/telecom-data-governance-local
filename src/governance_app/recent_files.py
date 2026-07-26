import json
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.database_runtime import database_for
from governance_app.ports.database import Database


def record_recent_file(
    config: AppConfig,
    workbook_path: Path,
    kind: str,
    ok: bool,
    ledger_counts: dict[str, int],
    error_count: int,
    *,
    database: Database | None = None,
) -> None:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        unit_of_work.recent_files.record(
            path=str(workbook_path),
            kind=kind,
            ok=ok,
            ledger_counts_json=json.dumps(ledger_counts, ensure_ascii=False),
            error_count=error_count,
        )


def list_recent_files(
    config: AppConfig,
    limit: int = 10,
    *,
    database: Database | None = None,
) -> list[dict]:
    selected_database = database or database_for(config)
    with selected_database.unit_of_work() as unit_of_work:
        rows = unit_of_work.recent_files.list(limit)
    return [
        {
            "path": row["path"],
            "kind": row["kind"],
            "ok": bool(row["ok"]),
            "ledger_counts": json.loads(row["ledger_counts_json"]),
            "error_count": row["error_count"],
            "last_used_at": row["last_used_at"],
        }
        for row in rows
    ]
