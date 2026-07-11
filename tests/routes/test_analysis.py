import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import connect, initialize_database


def _handler():
    assert importlib.util.find_spec("governance_app.routes.analysis") is not None
    return importlib.import_module("governance_app.routes.analysis").handle_analysis_route


def test_analysis_handler_owns_dynamic_summary_path(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute(
            "insert into import_batches(source_file, name, status) values ('test.xlsx', 'test', 'audited')"
        )

    response = _handler()(app_config, "GET", urlparse("/api/batches/1/electricity-analysis/summary"), "")

    assert response[0] == 200
    assert json.loads(response[2])["batch_id"] == 1


def test_analysis_handler_rejects_invalid_batch_id(app_config):
    response = _handler()(
        app_config,
        "GET",
        urlparse("/api/batches/bad/electricity-analysis/summary"),
        "",
    )

    assert response[0] == 400
    assert json.loads(response[2])["error"] == "invalid batch_id"


def test_analysis_handler_returns_none_for_other_domain(app_config):
    assert _handler()(app_config, "GET", urlparse("/api/health"), "") is None
