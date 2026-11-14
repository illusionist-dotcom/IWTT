"""Browser-window desktop launcher for the local IWTT app."""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time
import webbrowser
from pathlib import Path

from .ai import provider_from_environment
from .server import IWTTHTTPServer, RequestHandler
from .store import APP_VERSION, Vault


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IWTT"
    return Path.home() / ".iwtt"


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the local IWTT desktop app")
    parser.add_argument("--data-dir", default=os.environ.get("IWTT_DATA_DIR", str(default_data_dir())))
    parser.add_argument("--no-browser", action="store_true", help="start the local app without opening a browser")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    provider = provider_from_environment()
    vault = Vault(data_dir / "thought-vault.sqlite3", ai_provider=provider)
    server = IWTTHTTPServer(("127.0.0.1", 0), RequestHandler, vault)
    url = f"http://127.0.0.1:{server.server_port}/"
    worker = threading.Thread(target=server.serve_forever, name="IWTT local server", daemon=True)
    worker.start()
    print(f"IWTT v{APP_VERSION} is running at {url}")
    print(f"Your private vault is stored at: {vault.db_path}")
    print("Keep this window open while using IWTT. Press Ctrl+C here to stop it.")
    if not args.no_browser:
        time.sleep(0.25)
        webbrowser.open(url)
    try:
        while worker.is_alive():
            worker.join(timeout=1)
    except KeyboardInterrupt:
        print("Stopping IWTT...")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
