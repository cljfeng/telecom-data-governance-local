import json
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.db import connect


def record_recent_file(
    config: AppConfig,
    workbook_path: Path,
    kind: str,
    ok: bool,
    ledger_counts: dict[str, int],
    error_count: int,
) -> None:
    with connect(config) as conn:
        conn.execute(
            """
            insert into recent_files(path, kind, ok, ledger_counts_json, error_count, last_used_at)
            values (?, ?, ?, ?, ?, strftime('%Y-%m-%d %H:%M:%f', 'now'))
            on conflict(path) do update set
                kind = excluded.kind,
                ok = excluded.ok,
                ledger_counts_json = excluded.ledger_counts_json,
                error_count = excluded.error_count,
                last_used_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
            """,
            (
                str(workbook_path),
                kind,
                1 if ok else 0,
                json.dumps(ledger_counts, ensure_ascii=False),
                error_count,
            ),
        )


def list_recent_files(config: AppConfig, limit: int = 10) -> list[dict]:
    with connect(config) as conn:
        rows = conn.execute(
            """
            select path, kind, ok, ledger_counts_json, error_count, last_used_at
              from recent_files
             order by last_used_at desc
             limit ?
            """,
            (limit,),
        ).fetchall()
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
