#!/usr/bin/env python3
"""Open Jade as a desktop app window."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

from studio_server import DATA_DIR, THUMB_DIR, StudioHandler


def configure_macos_window_close() -> None:
    """Make only the red window control hide Jade; leave Quit to macOS."""
    from webview.platforms.cocoa import BrowserView
    from Foundation import NO, YES

    class JadeWindowDelegate(BrowserView.WindowDelegate):
        def windowShouldClose_(self, native_window):
            native_window.orderOut_(None)
            return NO

    class JadeAppDelegate(BrowserView.AppDelegate):
        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible_windows):
            if not has_visible_windows:
                for instance in BrowserView.instances.values():
                    instance.window.makeKeyAndOrderFront_(instance.window)
                BrowserView.app.activateIgnoringOtherApps_(YES)
            return YES

    BrowserView.WindowDelegate = JadeWindowDelegate
    BrowserView.AppDelegate = JadeAppDelegate


def main() -> int:
    try:
        import webview
    except ImportError:
        print("Missing dependency: pywebview. Run `python3 -m pip install -r requirements.txt`.")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    configure_macos_window_close()
    server = ThreadingHTTPServer(("127.0.0.1", 0), StudioHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        window = webview.create_window(
            "Jade",
            f"http://{host}:{port}",
            width=1440,
            height=920,
            min_size=(980, 680),
            background_color="#181918",
        )

        webview.start()
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
