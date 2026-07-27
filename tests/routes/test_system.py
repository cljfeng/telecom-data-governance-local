from urllib.parse import urlparse

from governance_app.adapters.local_file_storage import LocalFileStorage
from governance_app.routes.system import handle_system_route


def test_system_handler_uses_standard_signature_and_ignores_other_domains(app_config):
    response = handle_system_route(app_config, "GET", urlparse("/api/batches"), "")

    assert response is None


def test_system_handler_returns_health_response(app_config):
    status, _headers, body = handle_system_route(app_config, "GET", urlparse("/api/health"), "")

    assert status == 200
    assert '"ok"' in body


def test_system_handler_downloads_only_managed_file(
    app_config,
    monkeypatch,
):
    storage = LocalFileStorage(
        upload_dir=app_config.data_dir / "uploads",
        export_dir=app_config.export_dir,
        backup_dir=app_config.workspace_dir / "backups",
    )
    stored_file = storage.save_upload("台账.xlsx", b"workbook")
    monkeypatch.setattr(
        "governance_app.routes.system.file_storage_for",
        lambda _config: storage,
    )

    status, headers, body = handle_system_route(
        app_config,
        "GET",
        urlparse(f"/api/files/{stored_file.file_id}"),
        "",
    )
    missing = handle_system_route(
        app_config,
        "GET",
        urlparse("/api/files/uploads:missing.xlsx"),
        "",
    )

    assert status == 200
    assert headers["x-content-type-options"] == "nosniff"
    assert body == b"workbook"
    assert missing[0] == 404
