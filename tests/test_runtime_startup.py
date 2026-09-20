import sys
from pathlib import Path

import pytest

import governance_app.desktop as desktop_module
import governance_app.server as server_module
from governance_app.config import ConfigurationError


@pytest.mark.parametrize(
    ("module", "extra_args", "message"),
    [
        (server_module, [], "DATABASE_URL"),
        (desktop_module, ["--no-browser"], "DATABASE_URL"),
    ],
)
def test_online_mode_refuses_to_start_local_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module, extra_args: list[str], message: str
):
    monkeypatch.setenv("APP_MODE", "online")
    monkeypatch.setattr(
        sys,
        "argv",
        [module.__name__, "--workspace", str(tmp_path), *extra_args],
    )

    def unexpected_local_server(*args, **kwargs):
        pytest.fail("online mode started the local server")

    monkeypatch.setattr(module, "run_server", unexpected_local_server)

    with pytest.raises(ConfigurationError, match=message):
        module.main()

    assert not (tmp_path / "data").exists()


def test_online_server_checks_dependencies_before_binding(tmp_path, monkeypatch):
    config = server_module.load_runtime_config(
        tmp_path,
        {
            "APP_MODE": "online",
            "DATABASE_URL": "postgresql://example/db",
            "OBJECT_STORAGE_BUCKET": "governance",
            "BOOTSTRAP_ADMIN_PASSWORD": "administrator-password",
        },
    )
    checked = []

    def unavailable(config):
        checked.append(config)
        raise ConfigurationError("PostgreSQL unavailable")

    def unexpected_server(*args, **kwargs):
        pytest.fail("server bound before dependency check")

    monkeypatch.setattr(server_module, "check_online_dependencies", unavailable)
    monkeypatch.setattr(server_module, "ThreadingHTTPServer", unexpected_server)
    with pytest.raises(ConfigurationError, match="PostgreSQL unavailable"):
        server_module.run_server(config)
    assert checked == [config]
    assert not (tmp_path / "data").exists()


def test_online_server_exposes_authenticated_business_routes(tmp_path, monkeypatch):
    config = server_module.load_runtime_config(
        tmp_path,
        {
            "APP_MODE": "online",
            "DATABASE_URL": "postgresql://example/db",
            "OBJECT_STORAGE_BUCKET": "governance",
            "BOOTSTRAP_ADMIN_PASSWORD": "administrator-password",
        },
    )
    monkeypatch.setattr(server_module, "check_online_dependencies", lambda _config: None)
    app = server_module.create_app(config)
    assert app.handle_test_request("GET", "/api/health")[0] == 200
    assert app.handle_test_request("GET", "/api/dashboard")[0] == 401
    assert app.handle_test_request("POST", "/api/import", "{}")[0] == 401

    observed = []

    class FakeIdentityStore:
        def initialize(self):
            observed.append("identity initialized")

        def bootstrap(self, **_kwargs):
            observed.append("admin bootstrapped")

    class FakeTaskManager:
        def recover(self):
            observed.append("tasks recovered")

    monkeypatch.setattr(server_module, "initialize_database", lambda _config: observed.append("database initialized"))
    monkeypatch.setattr(server_module, "identity_store_for", lambda _config: FakeIdentityStore())
    monkeypatch.setattr("governance_app.task_runtime.task_manager_for", lambda _config: FakeTaskManager())

    class FakeServer:
        def __init__(self, address, handler):
            observed.append((address, handler))

        def serve_forever(self):
            observed.append("served")

    monkeypatch.setattr(server_module, "ThreadingHTTPServer", FakeServer)
    server_module.run_server(config, port=0)
    assert observed[:4] == [
        "database initialized",
        "identity initialized",
        "admin bootstrapped",
        "tasks recovered",
    ]
    assert observed[-1] == "served"
    assert not (tmp_path / "data").exists()
