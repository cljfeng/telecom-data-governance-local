import argparse
import json
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from governance_app.analytics import dashboard_summary
from governance_app.archive import archive_batch
from governance_app.audit_engine import run_audit
from governance_app.backup import create_backup, restore_backup
from governance_app.config import AppConfig
from governance_app.corrections import import_correction_return
from governance_app.db import initialize_database
from governance_app.exporter import export_city_issue_packages
from governance_app.importer import import_workbook
from governance_app.workflow import (
    city_progress,
    create_batch,
    get_batch_workflow,
    list_batches,
    list_issues,
    record_operation,
    set_current_batch,
    update_issue_status,
)


@dataclass(frozen=True)
class LocalApp:
    config: AppConfig

    def handle_test_request(self, method: str, path: str, body: str = "") -> tuple[int, dict[str, str], str]:
        return _route(self.config, method, path, body)


def create_app(config: AppConfig) -> LocalApp:
    return LocalApp(config)


def _route(config: AppConfig, method: str, path: str, body: str = "") -> tuple[int, dict[str, str], str]:
    parsed = urlparse(path)
    if method == "GET" and parsed.path == "/api/health":
        return _json({"status": "ok"})
    if method == "GET" and parsed.path == "/api/dashboard":
        query = parse_qs(parsed.query)
        try:
            batch_id = int(query.get("batch_id", ["1"])[0])
        except ValueError:
            return _json({"error": "invalid batch_id"}, status=400)
        return _json(dashboard_summary(config, batch_id))
    if method == "GET" and parsed.path == "/api/batches":
        return _json({"batches": list_batches(config)})
    if method == "POST" and parsed.path == "/api/batches":
        payload, error = _json_body(body)
        if error:
            return error
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return _json({"error": "name is required"}, status=400)
        try:
            batch_id = create_batch(config, name)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=400)
        return _json({"batch_id": batch_id})
    if method == "POST" and parsed.path == "/api/batches/current":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        try:
            set_current_batch(config, batch_id)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=404)
        return _json({"status": "selected"})
    if method == "GET" and parsed.path == "/api/workflow":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        try:
            return _json(get_batch_workflow(config, batch_id))
        except ValueError as exc:
            return _json({"error": str(exc)}, status=404)
    if method == "GET" and parsed.path == "/api/issues":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        query = parse_qs(parsed.query)
        filters = {key: values[0] for key, values in query.items() if key != "batch_id" and values and values[0]}
        return _json({"issues": list_issues(config, batch_id, filters)})
    if method == "GET" and parsed.path == "/api/city-progress":
        batch_id, error = _batch_id_from_query(parsed.query)
        if error:
            return error
        return _json({"cities": city_progress(config, batch_id)})
    if method == "POST" and parsed.path == "/api/import":
        payload, error = _json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return _json({"error": "path is required"}, status=400)
        result = import_workbook(config, Path(path_value))
        return _json(
            {
                "batch_id": result.batch_id,
                "ledger_counts": result.ledger_counts,
                "errors": [error.__dict__ for error in result.errors],
            },
            status=200 if result.batch_id is not None else 400,
        )
    if method == "POST" and parsed.path == "/api/audit":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        result = run_audit(config, batch_id)
        return _json({"audit_run_id": result.audit_run_id, "issue_count": result.issue_count})
    if method == "POST" and parsed.path == "/api/export":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        paths = export_city_issue_packages(config, batch_id)
        return _json({"paths": [str(path) for path in paths]})
    if method == "POST" and parsed.path == "/api/corrections":
        payload, error = _json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return _json({"error": "path is required"}, status=400)
        result = import_correction_return(config, Path(path_value))
        return _json({"matched_count": result.matched_count, "errors": result.errors}, status=200 if not result.errors else 400)
    if method == "POST" and parsed.path == "/api/issues/status":
        payload, error = _json_body(body)
        if error:
            return error
        issue_code = payload.get("issue_code")
        status_value = payload.get("status")
        if not isinstance(issue_code, str) or not isinstance(status_value, str):
            return _json({"error": "issue_code and status are required"}, status=400)
        try:
            update_issue_status(config, issue_code, status_value)
        except ValueError as exc:
            return _json({"error": str(exc)}, status=404)
        return _json({"status": "updated"})
    if method == "POST" and parsed.path == "/api/archive":
        payload, error = _json_body(body)
        if error:
            return error
        batch_id, error = _batch_id_from_payload(payload)
        if error:
            return error
        path = archive_batch(config, batch_id)
        return _json({"path": str(path)})
    if method == "POST" and parsed.path == "/api/backup":
        path = create_backup(config)
        return _json({"path": str(path)})
    if method == "POST" and parsed.path == "/api/restore":
        payload, error = _json_body(body)
        if error:
            return error
        path_value = payload.get("path")
        if not isinstance(path_value, str) or not path_value:
            return _json({"error": "path is required"}, status=400)
        restore_backup(config, Path(path_value))
        return _json({"status": "restored"})
    return _json({"error": "not found"}, status=404)


def _json(payload: dict, status: int = 200) -> tuple[int, dict[str, str], str]:
    return (
        status,
        {"content-type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False),
    )


def _json_body(body: str) -> tuple[dict, tuple[int, dict[str, str], str] | None]:
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {}, _json({"error": "invalid json"}, status=400)
    if not isinstance(payload, dict):
        return {}, _json({"error": "json object required"}, status=400)
    return payload, None


def _batch_id_from_payload(payload: dict) -> tuple[int, tuple[int, dict[str, str], str] | None]:
    try:
        return int(payload.get("batch_id")), None
    except (TypeError, ValueError):
        return 0, _json({"error": "invalid batch_id"}, status=400)


def _batch_id_from_query(query_string: str) -> tuple[int, tuple[int, dict[str, str], str] | None]:
    query = parse_qs(query_string)
    try:
        return int(query.get("batch_id", ["1"])[0]), None
    except ValueError:
        return 0, _json({"error": "invalid batch_id"}, status=400)


class RequestHandler(SimpleHTTPRequestHandler):
    config: AppConfig

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            status, headers, body = _route(self.config, "GET", self.path)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            status, headers, response_body = _route(self.config, "POST", self.path, body)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response_body.encode("utf-8"))
            return
        status, headers, response_body = _json({"error": "not found"}, status=404)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response_body.encode("utf-8"))


def run_server(config: AppConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
    initialize_database(config)
    configured_handler = type("ConfiguredRequestHandler", (RequestHandler,), {"config": config})
    handler = partial(configured_handler, directory=str(config.static_dir))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Local governance app running at http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    run_server(AppConfig.for_workspace(Path(args.workspace)), args.host, args.port)


if __name__ == "__main__":
    main()
