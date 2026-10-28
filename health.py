"""A tiny HTTP health endpoint for the Telegram bot.

The bot uses long-polling, so it never binds a port by itself. PaaS platforms
(Render, Railway, Fly) expect a web service to listen on $PORT and answer a
health check, so we serve one alongside the poller.

GET /health  ->  200 {"status": "ok", ...}
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthServer:
    def __init__(self, port, host="0.0.0.0", state=None, service="agentkit"):
        self.port = int(port)
        self.host = host
        self.state = state if state is not None else {}
        self.service = service
        self.started_at = time.time()
        self._httpd = None
        self._thread = None

    def payload(self):
        data = {
            "status": "ok",
            "service": self.service,
            "uptime_s": round(time.time() - self.started_at, 1),
        }
        data.update(self.state)
        return data

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.split("?")[0] not in ("/", "/health", "/healthz"):
                    return self._send(404, {"error": "not found"})
                return self._send(200, server.payload())

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()

            def _send(self, code, body):
                raw = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt, *args):
                return  # keep platform logs free of health-check noise

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="health", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def port_from_env(default=None):
    """$PORT wins (PaaS), then AGENT_PORT, then default."""
    for name in ("PORT", "AGENT_PORT"):
        raw = os.environ.get(name)
        if raw:
            try:
                return int(raw)
            except ValueError:
                raise SystemExit(f"{name} must be a number, got {raw!r}")
    return default
