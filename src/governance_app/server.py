import argparse
import json
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from governance_app.analytics import dashboard_summary
from governance_app.config import AppConfig
from governance_app.db import initialize_database


@dataclass(frozen=True)
class LocalApp:
    config: AppConfig

    def handle_test_request(self, method: str, path: str) -> tuple[int, dict[str, str], str]:
        return _route(self.config, method, path)


def create_app(config: AppConfig) -> LocalApp:
    return LocalApp(config)


def _route(config: AppConfig, method: str, path: str) -> tuple[int, dict[str, str], str]:
    parsed = urlparse(path)
    if method == "GET" and parsed.path == "/api/health":
        return _json({"status": "ok"})
    if method == "GET" and parsed.path == "/api/dashboard":
        query = parse_qs(parsed.query)
        batch_id = int(query.get("batch_id", ["1"])[0])
        return _json(dashboard_summary(config, batch_id))
    return _json({"error": "not found"}, status=404)


def _json(payload: dict, status: int = 200) -> tuple[int, dict[str, str], str]:
    return (
        status,
        {"content-type": "application/json; charset=utf-8"},
        json.dumps(payload, ensure_ascii=False),
    )


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
