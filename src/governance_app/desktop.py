import argparse
import threading
import time
import webbrowser
from pathlib import Path

from governance_app.config import AppConfig
from governance_app.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    config = AppConfig.from_environment(Path(args.workspace))
    if not args.no_browser:
        url = f"http://{args.host}:{args.port}"
        threading.Thread(target=_open_browser_later, args=(url,), daemon=True).start()
    run_server(config, args.host, args.port)


def _open_browser_later(url: str) -> None:
    time.sleep(0.8)
    webbrowser.open(url)


if __name__ == "__main__":
    main()
