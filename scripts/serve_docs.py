#!/usr/bin/env python3
"""Dev-only static server for docs/ that disables browser caching.

Plain ``python -m http.server`` lets the browser cache HTML/CSS/JS, which makes
edits appear not to take effect until a hard refresh. This server sends
no-store headers so every reload fetches the current files.
"""

from __future__ import annotations

import http.server
import os
import socketserver

_DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):  # noqa: D401 - stdlib hook
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, *args):  # quieter output
        pass


def main() -> None:
    os.chdir(_DOCS)
    port = int(os.environ.get("PORT", "8899"))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), NoCacheHandler) as httpd:
        print(f"serving docs/ (no-cache) on http://localhost:{port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
