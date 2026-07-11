from urllib.parse import urlparse

from governance_app.routes.system import handle_system_route


def test_system_handler_uses_standard_signature_and_ignores_other_domains(app_config):
    response = handle_system_route(app_config, "GET", urlparse("/api/batches"), "")

    assert response is None


def test_system_handler_returns_health_response(app_config):
    status, _headers, body = handle_system_route(app_config, "GET", urlparse("/api/health"), "")

    assert status == 200
    assert '"ok"' in body
