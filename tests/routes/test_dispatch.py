import json
from pathlib import Path

import governance_app.server as server


def test_server_contains_only_protocol_and_route_imports():
    source = (Path(__file__).parents[2] / "src" / "governance_app" / "server.py").read_text(encoding="utf-8")
    forbidden = (
        "governance_app.analytics",
        "governance_app.archive",
        "governance_app.audit_engine",
        "governance_app.corrections",
        "governance_app.electricity_analysis",
        "governance_app.exporter",
        "governance_app.importer",
        "governance_app.rule_settings",
        "governance_app.tower_rent_analysis",
        "governance_app.workflow",
    )

    assert all(name not in source for name in forbidden)


def test_route_handler_order_is_explicit():
    assert [handler.__name__ for handler in server.ROUTE_HANDLERS] == [
        "handle_system_route",
        "handle_batch_route",
        "handle_import_route",
        "handle_audit_route",
        "handle_analysis_route",
        "handle_report_route",
    ]
    assert [handler.__name__ for handler in server.UPLOAD_HANDLERS] == [
        "handle_import_upload",
        "handle_report_upload",
    ]


def test_dispatchers_share_fallback_not_found(app_config):
    ordinary = server._route(app_config, "GET", "/api/unknown")
    uploaded = server._route_upload(app_config, "/api/unknown", {}, {})

    assert ordinary[0] == uploaded[0] == 404
    assert json.loads(ordinary[2]) == json.loads(uploaded[2]) == {"error": "not found"}
