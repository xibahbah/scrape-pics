#!/usr/bin/env python3
"""Open Palette Studio as a desktop app window."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

from studio_server import DATA_DIR, THUMB_DIR, StudioHandler


def main() -> int:
    try:
        import webview
    except ImportError:
        print("Missing dependency: pywebview. Run `python3 -m pip install -r requirements.txt`.")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), StudioHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        webview.create_window(
            "Palette Studio",
            f"http://{host}:{port}",
            width=1440,
            height=920,
            min_size=(980, 680),
            background_color="#111214",
        )
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
