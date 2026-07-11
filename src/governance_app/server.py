import argparse
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from governance_app.config import AppConfig
from governance_app.db import initialize_database
from governance_app.operation_guard import exclusive_operation
from governance_app.routes.analysis import handle_analysis_route
from governance_app.routes.audits import handle_audit_route
from governance_app.routes.batches import handle_batch_route
from governance_app.routes.common import JsonResponse, json_response
from governance_app.routes.imports import handle_import_route, handle_import_upload
from governance_app.routes.reports import handle_report_route, handle_report_upload
from governance_app.routes.system import handle_system_route

MAX_REQUEST_BODY_BYTES = 100 * 1024 * 1024

ROUTE_HANDLERS = (
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

    def handle_test_request(self, method: str, path: str, body: str = "") -> JsonResponse:
        return _route(self.config, method, path, body)

    def handle_test_upload_request(
        self,
        path: str,
        content_type: str,
        body: bytes,
    ) -> JsonResponse:
        fields, files, error = _multipart_body(content_type, body)
        return error or _route_upload(self.config, path, fields, files)


def create_app(config: AppConfig) -> LocalApp:
    return LocalApp(config)


def _route(config: AppConfig, method: str, path: str, body: str = "") -> JsonResponse:
    parsed = urlparse(path)
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
) -> JsonResponse:
    parsed_path = urlparse(path).path
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
        if not name:
            continue
        filename = part.get_filename()
        content = part.get_payload(decode=True) or b""
        if filename:
            files[name] = (filename, content)
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[name] = content.decode(charset, errors="replace")
    return fields, files, None


class RequestHandler(SimpleHTTPRequestHandler):
    config: AppConfig

    @staticmethod
    def extra_static_headers() -> dict[str, str]:
        return {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            for key, value in self.extra_static_headers().items():
                self.send_header(key, value)
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._write_response(_route(self.config, "GET", self.path))
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
            response = multipart_error or _route_upload(self.config, self.path, fields, files)
        else:
            response = _route(self.config, "POST", self.path, raw_body.decode("utf-8"))
        self._write_response(response)

    def _write_response(self, response: JsonResponse) -> None:
        status, headers, body = response
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


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
