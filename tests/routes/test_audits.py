import importlib
import importlib.util
import json
from urllib.parse import urlparse

from governance_app.db import connect, initialize_database


def _handler():
    assert importlib.util.find_spec("governance_app.routes.audits") is not None
    return importlib.import_module("governance_app.routes.audits").handle_audit_route


def test_audit_handler_runs_audit(app_config):
    initialize_database(app_config)
    with connect(app_config) as conn:
        conn.execute("insert into import_batches(source_file, name, status) values ('test.xlsx', 'test', 'imported')")

    response = _handler()(app_config, "POST", urlparse("/api/audit"), json.dumps({"batch_id": 1}))

    assert response[0] == 200
    assert json.loads(response[2])["audit_run_id"] == 1


def test_audit_handler_lists_issues_and_validates_rule_settings(app_config):
    initialize_database(app_config)

    issues = _handler()(app_config, "GET", urlparse("/api/issues?batch_id=1"), "")
    invalid_rule = _handler()(app_config, "POST", urlparse("/api/rules/settings"), "{}")

    assert issues[0] == 200
    assert json.loads(issues[2])["issues"] == []
    assert invalid_rule[0] == 400
    assert json.loads(invalid_rule[2])["error"] == "rule_id is required"


def test_audit_handler_returns_none_for_report_domain(app_config):
    assert _handler()(app_config, "POST", urlparse("/api/export"), "{}") is None
