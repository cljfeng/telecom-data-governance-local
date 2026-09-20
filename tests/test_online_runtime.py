import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from governance_app.config import ConfigurationError, OnlineConfig
from governance_app.online_runtime import check_online_dependencies


def _config() -> OnlineConfig:
    return OnlineConfig(
        "postgresql://db.example/governance",
        "governance",
        Path(tempfile.gettempdir()) / "governance-online-test",
        "administrator-password",
    )


def test_online_dependency_check_reaches_both_stores(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, statement):
            calls.append(statement)

    def connect(url, **kwargs):
        calls.append((url, kwargs))
        return Connection()

    class Storage:
        def head_bucket(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *_args, **_kwargs: Storage()))

    check_online_dependencies(_config())

    assert calls == [
        ("postgresql://db.example/governance", {"connect_timeout": 5}),
        "SELECT 1",
        {"Bucket": "governance"},
    ]


def test_online_dependency_check_accepts_sqlalchemy_postgres_url(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, _statement):
            pass

    def connect(url, **_kwargs):
        calls.append(url)
        return Connection()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: SimpleNamespace(head_bucket=lambda **_kwargs: None)),
    )
    config = OnlineConfig.from_environment(
        {
            "DATABASE_URL": "postgresql+psycopg://db.example/governance",
            "OBJECT_STORAGE_BUCKET": "governance",
            "BOOTSTRAP_ADMIN_PASSWORD": "administrator-password",
        }
    )

    check_online_dependencies(config)

    assert calls == ["postgresql://db.example/governance"]


@pytest.mark.parametrize("failed_store", ["database", "object storage"])
def test_online_dependency_check_rejects_unavailable_store(monkeypatch, failed_store):
    class Connection:
        def __enter__(self):
            if failed_store == "database":
                raise OSError("unavailable")
            return self

        def __exit__(self, *_args):
            pass

        def execute(self, _statement):
            pass

    def head_bucket(**_kwargs):
        raise OSError("unavailable")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda *_args, **_kwargs: Connection()))
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: SimpleNamespace(head_bucket=head_bucket)),
    )

    with pytest.raises(ConfigurationError, match="PostgreSQL|object storage") as error:
        check_online_dependencies(_config())
    expected = "PostgreSQL" if failed_store == "database" else "object storage"
    assert expected in str(error.value)
