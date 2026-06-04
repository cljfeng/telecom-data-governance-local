import shutil
from pathlib import Path
from typing import Any

from governance_app.config import AppConfig
from governance_app.db import connect, initialize_database


def compact_database(config: AppConfig, clear_uploads: bool = False) -> dict[str, Any]:
    initialize_database(config)
    before_bytes = _file_size(config.database_path)
    removed_uploads = _clear_uploads(config.data_dir / "uploads") if clear_uploads else 0
    with connect(config) as conn:
        linked_rows = _link_raw_rows(conn)
        deduplicated_rows = conn.execute(
            """
            update ledger_rows
               set row_json = '{}'
             where raw_row_id is not null
               and row_json <> '{}'
            """
        ).rowcount
    _vacuum(config)
    return {
        "before_bytes": before_bytes,
        "after_bytes": _file_size(config.database_path),
        "removed_uploads": removed_uploads,
        "linked_ledger_rows": linked_rows,
        "deduplicated_ledger_rows": deduplicated_rows,
    }


def _link_raw_rows(conn) -> int:
    conn.execute("drop table if exists temp_ledger_raw_map")
    conn.execute(
        """
        create temp table temp_ledger_raw_map as
        with
        ledger_ranked as (
            select id,
                   batch_id,
                   ledger_type,
                   row_number() over (partition by batch_id, ledger_type order by id) as position
              from ledger_rows
             where raw_row_id is null
        ),
        raw_ranked as (
            select id,
                   batch_id,
                   ledger_type,
                   row_number() over (partition by batch_id, ledger_type order by id) as position
              from raw_rows
        )
        select ledger_ranked.id as ledger_row_id,
               raw_ranked.id as raw_row_id
          from ledger_ranked
          join raw_ranked
            on raw_ranked.batch_id = ledger_ranked.batch_id
           and raw_ranked.ledger_type = ledger_ranked.ledger_type
           and raw_ranked.position = ledger_ranked.position
        """
    )
    linked_rows = conn.execute(
        """
        update ledger_rows
           set raw_row_id = (
               select raw_row_id
                 from temp_ledger_raw_map
                where temp_ledger_raw_map.ledger_row_id = ledger_rows.id
           )
         where raw_row_id is null
           and id in (select ledger_row_id from temp_ledger_raw_map)
        """
    ).rowcount
    conn.execute("drop table temp_ledger_raw_map")
    return linked_rows


def _clear_uploads(upload_dir: Path) -> int:
    if not upload_dir.exists():
        return 0
    removed = 0
    for item in upload_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed += 1
    return removed


def _vacuum(config: AppConfig) -> None:
    with connect(config) as conn:
        conn.execute("vacuum")


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0
