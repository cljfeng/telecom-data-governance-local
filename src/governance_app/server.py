import argparse
import json
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy.exc import DBAPIError

from governance_app.config import (
    AppConfig,
    ConfigurationError,
    OnlineConfig,
    RuntimeMode,
    load_runtime_config,
)
from governance_app.db import initialize_database
from governance_app.identity_store import identity_store_for
from governance_app.online_runtime import check_online_dependencies
from governance_app.request_context import RequestMetadata, request_context
from governance_app.routes.analysis import handle_analysis_route
from governance_app.routes.audits import handle_audit_route
from governance_app.routes.auth import handle_auth_route
from governance_app.routes.batches import handle_batch_route
from governance_app.routes.common import JsonResponse, json_response
from governance_app.routes.imports import handle_import_route, handle_import_upload
from governance_app.routes.reports import handle_report_route, handle_report_upload
from governance_app.routes.system import handle_system_route
from governance_app.routes.tasks import handle_task_route
from governance_app.security import authorize_request, security_headers

MAX_REQUEST_BODY_BYTES = 100 * 1024 * 1024

ROUTE_HANDLERS = (
    handle_auth_route,
    handle_task_route,
    handle_system_route,
    handle_batch_route,
    handle_import_route,
    handle_audit_route,
    handle_analysis_route,
    handle_report_route,
)

UPLOAD_HANDLERS = (
    handle_import_upload,
    handle_report_upload,
)


@dataclass(frozen=True)
class LocalApp:
    config: AppConfig

    def handle_test_request(
        self,
        method: str,
        path: str,
        body: str = "",
        *,
        headers: dict[str, str] | None = None,
        source_ip: str = "127.0.0.1",
    ) -> JsonResponse:
        return _route(
            self.config,
            method,
            path,
            body,
            headers=headers,
            source_ip=source_ip,
        )

    def handle_test_upload_request(
        self,
        path: str,
        content_type: str,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        source_ip: str = "127.0.0.1",
    ) -> JsonResponse:
        fields, files, error = _multipart_body(content_type, body)
        return error or _route_upload(
            self.config,
            path,
            fields,
            files,
            headers=headers,
            source_ip=source_ip,
        )


def create_app(config: AppConfig | OnlineConfig) -> LocalApp:
    if isinstance(config, OnlineConfig):
        config = config.to_app_config()
    if config.runtime_mode is RuntimeMode.ONLINE:
        if not config.database_url or not config.object_store_bucket:
            raise ConfigurationError("online storage adapters are required")
    else:
        config.require_local_runtime()
    return LocalApp(config)


def _route(
    config: AppConfig,
    method: str,
    path: str,
    body: str = "",
    *,
    headers: dict[str, str] | None = None,
    source_ip: str = "127.0.0.1",
) -> JsonResponse:
    try:
        return _authorized_dispatch(
            config,
            method,
            path,
            body,
            headers=headers or {},
            source_ip=source_ip,
        )
    except Exception as exc:
        if not _is_online_dependency_failure(config, exc):
            raise
        return _dependency_unavailable_response()


def _authorized_dispatch(
    config: AppConfig,
    method: str,
    path: str,
    body: str,
    *,
    headers: dict[str, str],
    source_ip: str,
) -> JsonResponse:
    parsed = urlparse(path)
    normalized_headers = {
        key.lower(): value for key, value in headers.items()
    }
    metadata = RequestMetadata(
        request_id=uuid4().hex,
        method=method,
        path=path,
        source_ip=source_ip,
        user_agent=normalized_headers.get("user-agent", ""),
    )
    started_at = perf_counter()
    principal, error = authorize_request(
        config,
        method,
        parsed,
        body,
        normalized_headers,
    )
    with request_context(principal, metadata):
        response = error or _dispatch_routes(
            config,
            method,
            parsed,
            body,
        )
    response = security_headers(
        response,
        online=config.runtime_mode is RuntimeMode.ONLINE,
    )
    if config.runtime_mode is RuntimeMode.ONLINE:
        try:
            identity_store_for(config).record_request(
                metadata,
                principal,
                status=response[0],
                duration_ms=max(
                    0,
                    int((perf_counter() - started_at) * 1000),
                ),
            )
        except Exception:
            pass
    return response


def _dispatch_routes(
    config: AppConfig,
    method: str,
    parsed,
    body: str,
) -> JsonResponse:
    for handler in ROUTE_HANDLERS:
        response = handler(config, method, parsed, body)
        if response is not None:
            return response
    return json_response({"error": "not found"}, status=404)


def _route_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
    *,
    headers: dict[str, str] | None = None,
    source_ip: str = "127.0.0.1",
) -> JsonResponse:
    try:
        return _authorized_upload(
            config, path, fields, files, headers=headers, source_ip=source_ip
        )
    except Exception as exc:
        if not _is_online_dependency_failure(config, exc):
            raise
        return _dependency_unavailable_response()


def _authorized_upload(
    config: AppConfig,
    path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
    *,
    headers: dict[str, str] | None = None,
    source_ip: str = "127.0.0.1",
) -> JsonResponse:
    authorization_body = json.dumps(fields, ensure_ascii=False)
    parsed = urlparse(path)
    normalized_headers = {
        key.lower(): value
        for key, value in (headers or {}).items()
    }
    metadata = RequestMetadata(
        request_id=uuid4().hex,
        method="POST",
        path=path,
        source_ip=source_ip,
        user_agent=normalized_headers.get("user-agent", ""),
    )
    started_at = perf_counter()
    principal, error = authorize_request(
        config,
        "POST",
        parsed,
        authorization_body,
        normalized_headers,
    )
    with request_context(principal, metadata):
        response = error or _dispatch_uploads(
            config,
            parsed.path,
            fields,
            files,
        )
    response = security_headers(
        response,
        online=config.runtime_mode is RuntimeMode.ONLINE,
    )
    if config.runtime_mode is RuntimeMode.ONLINE:
        try:
            identity_store_for(config).record_request(
                metadata,
                principal,
                status=response[0],
                duration_ms=max(
                    0,
                    int((perf_counter() - started_at) * 1000),
                ),
            )
        except Exception:
            pass
    return response


def _is_online_dependency_failure(config: AppConfig, error: Exception) -> bool:
    if config.runtime_mode is not RuntimeMode.ONLINE:
        return False
    return isinstance(error, DBAPIError) or type(error).__module__.startswith(
        ("psycopg.", "botocore.")
    )


def _dependency_unavailable_response() -> JsonResponse:
    return security_headers(
        json_response({"error": "online storage dependency unavailable"}, status=503),
        online=True,
    )


def _dispatch_uploads(
    config: AppConfig,
    parsed_path: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> JsonResponse:
    for handler in UPLOAD_HANDLERS:
        response = handler(config, parsed_path, fields, files)
        if response is not None:
            return response
    return json_response({"error": "not found"}, status=404)


def _content_length(value: str | None) -> tuple[int | None, JsonResponse | None]:
    try:
        length = int(value) if value is not None else -1
    except ValueError:
        length = -1
    if length < 0:
        return None, json_response({"error": "Content-Length 缺失或无效"}, status=400)
    if length > MAX_REQUEST_BODY_BYTES:
        return None, json_response({"error": "上传内容不能超过 100 MiB"}, status=413)
    return length, None


def _multipart_body(
    content_type: str,
    body: bytes,
) -> tuple[dict[str, str], dict[str, tuple[str, bytes]], JsonResponse | None]:
    if "multipart/form-data" not in content_type:
        return {}, {}, json_response({"error": "content-type must be multipart/form-data"}, status=400)
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    if not message.is_multipart():
        return {}, {}, json_response({"error": "invalid multipart body"}, status=400)
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not name:
            continue
        filename = part.get_filename()
        content = part.get_payload(decode=True)
        if not isinstance(content, bytes):
            content = b""
        if filename:
            files[name] = (filename, content)
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = content.decode(charset, errors="replace")
    return fields, files, None


class RequestHandler(SimpleHTTPRequestHandler):
    config: AppConfig
    server_version = "Governance"
    sys_version = ""

    @staticmethod
    def extra_static_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'self'"
            ),
        }

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            for key, value in self.extra_static_headers().items():
                self.send_header(key, value)
            if self.config.runtime_mode is RuntimeMode.ONLINE:
                self.send_header(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains",
                )
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._write_response(
                _route(
                    self.config,
                    "GET",
                    self.path,
                    headers=dict(self.headers.items()),
                    source_ip=self.client_address[0],
                )
            )
            return
        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        if not self.path.startswith("/api/"):
            self._write_response(json_response({"error": "not found"}, status=404))
            return
        length, error = _content_length(self.headers.get("content-length"))
        if error is not None:
            self._write_response(error)
            return
        content_type = self.headers.get("content-type", "")
        raw_body = self.rfile.read(length)
        if content_type.startswith("multipart/form-data"):
            fields, files, multipart_error = _multipart_body(content_type, raw_body)
            response = multipart_error or _route_upload(
                self.config,
                self.path,
                fields,
                files,
                headers=dict(self.headers.items()),
                source_ip=self.client_address[0],
            )
        else:
            response = _route(
                self.config,
                "POST",
                self.path,
                raw_body.decode("utf-8"),
                headers=dict(self.headers.items()),
                source_ip=self.client_address[0],
            )
        self._write_response(response)

    def _write_response(self, response: JsonResponse) -> None:
        status, headers, body = response
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))


def run_server(config: AppConfig | OnlineConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
    if isinstance(config, OnlineConfig):
        check_online_dependencies(config)
        config = config.to_app_config()
    create_app(config)
    initialize_database(config)
    if config.runtime_mode is RuntimeMode.ONLINE:
        store = identity_store_for(config)
        store.initialize()
        if (
            not config.bootstrap_admin_username
            or not config.bootstrap_admin_password
        ):
            raise RuntimeError(
                "online bootstrap administrator is not configured"
            )
        store.bootstrap(
            username=config.bootstrap_admin_username,
            password=config.bootstrap_admin_password,
        )
        from governance_app.task_runtime import task_manager_for

        task_manager_for(config).recover()
    configured_handler = type("ConfiguredRequestHandler", (RequestHandler,), {"config": config})
    handler = partial(configured_handler, directory=str(config.static_dir))
    server = ThreadingHTTPServer((host, port), handler)
    print(
        f"{config.runtime_mode.value.capitalize()} governance app "
        f"running at http://{host}:{port}"
    )
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    run_server(load_runtime_config(Path(args.workspace)), args.host, args.port)


if __name__ == "__main__":
    main()
