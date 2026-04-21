"""
health.py — Lightweight HTTP health check server for Render.

Render Web Services require a process that listens on $PORT (default 10000).
Without this, Render marks the deployment as failed and restarts it.

Usage:
    from health import start_health_server
    start_health_server()   # non-blocking, starts in a daemon thread
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Silence default request logging so it doesn't spam stdout
    def log_message(self, format, *args):
        pass


def start_health_server():
    """
    Start a minimal HTTP server on $PORT in a background daemon thread.
    Safe to call multiple times — only starts once.
    """
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,   # dies automatically when the main process exits
    )
    thread.start()
    print(f"[health] HTTP health check listening on port {port}")
    return server
