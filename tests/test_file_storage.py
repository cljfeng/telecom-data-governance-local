import pytest

from governance_app.adapters.local_file_storage import LocalFileStorage
from governance_app.file_storage_runtime import file_storage_for


def test_local_file_storage_round_trips_upload_reference(app_config):
    storage = file_storage_for(app_config)

    uploaded = storage.save_upload("../台账.xlsx", b"workbook")
    resolved = storage.resolve(uploaded.file_id)

    assert uploaded.file_id.startswith("uploads:")
    assert uploaded.name.endswith("-台账.xlsx")
    assert resolved == uploaded
    assert resolved.local_path.read_bytes() == b"workbook"


def test_local_file_storage_publishes_managed_export(app_config):
    storage = file_storage_for(app_config)
    report = storage.prepare_export("archive_batch_1/report.xlsx")
    report.write_bytes(b"report")

    published = storage.publish(report)

    assert published.file_id == "exports:archive_batch_1/report.xlsx"
    assert storage.resolve(published.file_id).local_path == report.resolve()


def test_local_file_storage_rejects_unmanaged_and_traversal_paths(
    app_config,
    tmp_path,
):
    storage = LocalFileStorage(
        upload_dir=app_config.data_dir / "uploads",
        export_dir=app_config.export_dir,
        backup_dir=app_config.workspace_dir / "backups",
    )
    unmanaged = tmp_path / "outside.xlsx"
    unmanaged.write_bytes(b"outside")

    with pytest.raises(ValueError, match="受管存储"):
        storage.publish(unmanaged)
    with pytest.raises(ValueError, match="文件引用无效"):
        storage.resolve("uploads:../../outside.xlsx")
    with pytest.raises(ValueError, match="文件引用无效"):
        storage.resolve("unknown:file.xlsx")
    with pytest.raises(ValueError, match="导出路径越界"):
        storage.prepare_export("../outside.xlsx")


def test_local_file_storage_clears_areas_and_preserves_selected_backup(
    app_config,
):
    storage = file_storage_for(app_config)
    first_upload = storage.save_upload("first.xlsx", b"first")
    second_upload = storage.save_upload("second.xlsx", b"second")
    backup_root = app_config.workspace_dir / "backups"
    backup_root.mkdir()
    old_backup = backup_root / "old.sqlite3"
    safety_backup = backup_root / "safety.sqlite3"
    old_backup.write_bytes(b"old")
    safety_backup.write_bytes(b"safety")
    safety_file = storage.publish(safety_backup)

    removed_uploads = storage.clear("uploads")
    removed_backups = storage.clear(
        "backups",
        keep_file_ids={safety_file.file_id},
    )

    assert removed_uploads == 2
    assert not first_upload.local_path.exists()
    assert not second_upload.local_path.exists()
    assert removed_backups == 1
    assert not old_backup.exists()
    assert safety_backup.exists()
