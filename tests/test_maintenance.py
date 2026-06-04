import json

from governance_app.db import connect, initialize_database
from governance_app.importer import import_workbook
from governance_app.maintenance import compact_database


def test_compact_database_clears_upload_cache_and_deduplicates_ledger_json(app_config, sample_workbook):
    initialize_database(app_config)
    import_workbook(app_config, sample_workbook)
    upload_dir = app_config.data_dir / "uploads"
    upload_dir.mkdir(parents=True)
    cached_file = upload_dir / "cached.xlsx"
    cached_file.write_bytes(b"cache")
    with connect(app_config) as conn:
        raw = conn.execute("select id, row_json from raw_rows where ledger_type = 'electricity'").fetchone()
        conn.execute(
            "update ledger_rows set raw_row_id = null, row_json = ? where ledger_type = 'electricity'",
            (raw["row_json"],),
        )

    result = compact_database(app_config, clear_uploads=True)

    assert result["removed_uploads"] == 1
    assert not cached_file.exists()
    assert result["deduplicated_ledger_rows"] >= 1
    assert result["after_bytes"] <= result["before_bytes"]
    with connect(app_config) as conn:
        row = conn.execute("select raw_row_id, row_json from ledger_rows where ledger_type = 'electricity'").fetchone()
    assert row["raw_row_id"] == raw["id"]
    assert json.loads(row["row_json"]) == {}
