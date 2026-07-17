#!/usr/bin/env python3
"""Small localhost-only status/control API for the production load balancer."""

from __future__ import annotations

import concurrent.futures
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 9090
ROOT = Path(__file__).resolve().parent
CONTROL = "/usr/bin/sudo -n /usr/local/sbin/lb-dashboard-control"

SERVICES = {
    "newtool": {
        "label": "Newtool2",
        "config": "/etc/nginx/sites-available/automation_v2",
        "upstream": "newtool2_backend",
    },
    "registration": {
        "label": "Registration",
        "config": "/etc/nginx/sites-available/regpan4.fskindia.com",
        "upstream": "regpan4_backend",
    },
}

MACHINES = [
    {"id": "node-a", "label": "Backend A", "ips": ["217.217.249.145", "147.93.168.214"]},
    {"id": "node-b", "label": "Backend B", "ips": ["217.216.78.35", "147.93.168.221"]},
    {"id": "node-c", "label": "Backend C", "ips": ["217.216.78.96", "147.93.171.116"]},
    {"id": "node-d", "label": "Backend D", "ips": ["147.93.171.254"]},
]


def parse_upstream(path: str, upstream: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    inside = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if re.match(rf"^\s*upstream\s+{re.escape(upstream)}\s*{{", line):
            inside = True
            continue
        if inside and re.match(r"^\s*}", line):
            break
        if not inside:
            continue
        match = re.match(r"^\s*server\s+([^:;\s]+):(\d+)\b(.*);\s*$", line)
        if match:
            ip, port, options = match.groups()
            rows[ip] = {
                "port": int(port),
                "enabled": not bool(re.search(r"(?:^|\s)down(?:\s|$)", options)),
            }
    return rows


def health_url(service: str, ip: str, port: int) -> str:
    return f"http://{ip}:{port}/health"


def fetch_health(service: str, ip: str, port: int) -> dict:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(health_url(service, ip, port), timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            latency = round((time.monotonic() - started) * 1000)
            healthy = 200 <= response.status < 300 and payload.get("status") == "healthy"
            if service == "newtool":
                details = {
                    "active_sessions": int(payload.get("active_sessions", 0)),
                    "forgot_password_sessions": int(payload.get("forgot_password_sessions", 0)),
                    "websockets": int(payload.get("websocket_connections", 0)),
                }
            else:
                sessions = payload.get("sessions", {})
                metrics = payload.get("metrics") or {}
                details = {
                    "active_sessions": int(sessions.get("active_registration_sessions", 0)),
                    "stages": sessions.get("session_stages", {}),
                    "total_requests": int(metrics.get("total_requests", 0)),
                    "total_errors": int(metrics.get("total_errors", 0)),
                    "uptime_seconds": int(metrics.get("uptime_seconds", 0)),
                }
            return {"healthy": healthy, "latency_ms": latency, "details": details, "error": None}
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {
            "healthy": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "details": {},
            "error": str(exc)[:160],
        }


def build_status() -> dict:
    configured = {
        name: parse_upstream(info["config"], info["upstream"])
        for name, info in SERVICES.items()
    }
    checks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as pool:
        futures = {}
        for service, entries in configured.items():
            for ip, entry in entries.items():
                future = pool.submit(fetch_health, service, ip, entry["port"])
                futures[future] = (service, ip, entry)
        for future in concurrent.futures.as_completed(futures):
            service, ip, entry = futures[future]
            checks.append({"service": service, "ip": ip, **entry, **future.result()})

    peers: dict[str, int] = {}
    try:
        result = subprocess.run(
            ["/usr/bin/ss", "-Htan", "state", "established"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        for line in result.stdout.splitlines():
            columns = line.split()
            if columns:
                peers[columns[-1]] = peers.get(columns[-1], 0) + 1
    except (OSError, subprocess.SubprocessError):
        pass
    for row in checks:
        row["live_connections"] = peers.get(f'{row["ip"]}:{row["port"]}', 0)

    by_key = {(row["service"], row["ip"]): row for row in checks}
    machines = []
    for machine in MACHINES:
        rows = []
        for ip in machine["ips"]:
            for service in SERVICES:
                row = by_key.get((service, ip))
                if row:
                    rows.append(row)
        machines.append({**machine, "rows": rows})
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "machines": machines,
        "note": "Live connections are counted at the load balancer. Session values are one sampled application worker, not a machine-wide total.",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "FSKDashboard/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_asset(self, name: str, mime: str) -> None:
        body = (ROOT / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self.serve_asset("index.html", "text/html; charset=utf-8")
        if path == "/styles.css":
            return self.serve_asset("styles.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self.serve_asset("app.js", "application/javascript; charset=utf-8")
        if path == "/api/status":
            try:
                return self.send_json(200, build_status())
            except Exception as exc:
                return self.send_json(500, {"error": str(exc)[:200]})
        if path == "/health":
            return self.send_json(200, {"status": "healthy"})
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/api/toggle":
            return self.send_error(404)
        if self.headers.get("X-Dashboard-Action") != "toggle":
            return self.send_json(403, {"error": "Missing action confirmation header"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 1024:
                raise ValueError("Invalid request size")
            request = json.loads(self.rfile.read(length))
            service = request.get("service")
            ip = request.get("ip")
            action = request.get("action")
            if service not in SERVICES or action not in {"enable", "disable"}:
                raise ValueError("Invalid service or action")
            if ip not in {item for machine in MACHINES for item in machine["ips"]}:
                raise ValueError("Invalid backend IP")
            command = CONTROL.split() + [service, ip, action]
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            if result.returncode:
                return self.send_json(409, {"error": (result.stderr or result.stdout).strip()[:500]})
            return self.send_json(200, {"ok": True, "message": result.stdout.strip(), "status": build_status()})
        except (ValueError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"error": str(exc)})
        except subprocess.TimeoutExpired:
            return self.send_json(504, {"error": "Control operation timed out"})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
