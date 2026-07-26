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
    app_config.export_dir.mkdir(parents=True)
    report = app_config.export_dir / "archive_batch_1" / "report.xlsx"
    report.parent.mkdir()
    report.write_bytes(b"report")
    storage = file_storage_for(app_config)

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
