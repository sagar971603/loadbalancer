#!/usr/bin/env python3
"""Session-aware Registration router for the production NGINX load balancer."""

from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOST = os.getenv("REG_ROUTER_HOST", "127.0.0.1")
PORT = int(os.getenv("REG_ROUTER_PORT", "18002"))
NGINX_CONFIG = Path(os.getenv("REG_ROUTER_NGINX_CONFIG", "/etc/nginx/sites-available/regpan4.fskindia.com"))
QUEUE_TIMEOUT = float(os.getenv("REG_ROUTER_QUEUE_TIMEOUT", "90"))
FORWARD_TIMEOUT = float(os.getenv("REG_ROUTER_FORWARD_TIMEOUT", "330"))
BACKEND_COOLDOWN = float(os.getenv("REG_ROUTER_BACKEND_COOLDOWN", "120"))
SLOTS_PER_WEIGHT = int(os.getenv("REG_ROUTER_SLOTS_PER_WEIGHT", "5"))
MAX_BODY = int(os.getenv("REG_ROUTER_MAX_BODY", str(1024 * 1024)))
MAX_TRACKED_SESSIONS = int(os.getenv("REG_ROUTER_MAX_TRACKED_SESSIONS", "10000"))

# One incoming address per physical machine. NGINX weight is the number of
# healthy outgoing IPs on that machine, so five slots per IP stays balanced.
KNOWN_ENDPOINTS = {
    "217.217.249.145:8002": "a",
    "217.216.78.35:8002": "b",
    "217.216.78.96:8002": "c",
    "147.93.171.241:8002": "d",
}
LEGACY_BACKEND = "b"
SESSION_RE = re.compile(r"^([a-z0-9-]+)~(.+)$")
SERVER_RE = re.compile(r"^\s*server\s+([^;\s]+)([^;]*);\s*$")
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "content-length",
}
NETWORK_ERROR_TEXT = "NetworkError when attempting to fetch resource"

LOCK = threading.Lock()
PENDING: dict[str, int] = {}
COOLDOWN_UNTIL: dict[str, float] = {}
SESSION_ROUTES: dict[str, str] = {}
TIE_CURSOR = 0


def configured_backends() -> dict[str, dict]:
    """Read enabled Registration peers and capacities from the live NGINX file."""
    result: dict[str, dict] = {}
    inside = False
    for line in NGINX_CONFIG.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*upstream\s+regpan4_backend\s*{", line):
            inside = True
            continue
        if inside and re.match(r"^\s*}", line):
            break
        if not inside:
            continue
        match = SERVER_RE.match(line)
        if not match:
            continue
        endpoint, options = match.groups()
        route = KNOWN_ENDPOINTS.get(endpoint)
        if not route:
            host, separator, port = endpoint.rpartition(":")
            if not separator or port != "8002" or not re.fullmatch(r"[0-9a-fA-F:.]+", host):
                continue
            route = "n" + host.replace(".", "-").replace(":", "-")
        weight_match = re.search(r"(?:^|\s)weight=(\d+)(?:\s|$)", options)
        weight = int(weight_match.group(1)) if weight_match else 1
        result[route] = {
            "endpoint": endpoint,
            "capacity": weight * SLOTS_PER_WEIGHT,
            "enabled": not bool(re.search(r"(?:^|\s)down(?:\s|$)", options)),
        }
    return result


def backend_health(endpoint: str) -> tuple[bool, int]:
    host, port_text = endpoint.rsplit(":", 1)
    connection = http.client.HTTPConnection(host, int(port_text), timeout=2)
    try:
        connection.request("GET", "/health", headers={"Connection": "close"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        active = int(payload.get("sessions", {}).get("active_registration_sessions", 0))
        return response.status == 200 and payload.get("status") == "healthy", active
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException):
        return False, 0
    finally:
        connection.close()


def choose_backend() -> tuple[str, dict] | None:
    """Reserve the next weighted backend; caller must release PENDING."""
    global TIE_CURSOR
    with LOCK:
        configured = configured_backends()
        candidates = []
        for route, backend in configured.items():
            if not backend["enabled"]:
                continue
            if COOLDOWN_UNTIL.get(route, 0) > time.monotonic():
                continue
            COOLDOWN_UNTIL.pop(route, None)
            healthy, active = backend_health(backend["endpoint"])
            used = active + PENDING.get(route, 0)
            if healthy and used < backend["capacity"]:
                weight = max(1, backend["capacity"] // SLOTS_PER_WEIGHT)
                candidates.extend((route, backend) for _ in range(weight))
        if not candidates:
            return None
        route, backend = candidates[TIE_CURSOR % len(candidates)]
        TIE_CURSOR += 1
        PENDING[route] = PENDING.get(route, 0) + 1
        return route, backend


def release_backend(route: str) -> None:
    with LOCK:
        PENDING[route] = max(0, PENDING.get(route, 0) - 1)


def cool_down_backend(route: str) -> None:
    with LOCK:
        COOLDOWN_UNTIL[route] = max(
            COOLDOWN_UNTIL.get(route, 0), time.monotonic() + BACKEND_COOLDOWN
        )


def is_network_error(payload) -> bool:
    return NETWORK_ERROR_TEXT in json.dumps(payload, separators=(",", ":"))


def is_safe_init_retry(payload) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("success") is False
        and payload.get("message") == "Registration initialization failed"
        and is_network_error(payload)
    )


def session_route(body: bytes) -> tuple[str | None, bytes]:
    """Return encoded route and strip its prefix before backend validation."""
    try:
        payload = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return None, body
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str):
        return None, body
    match = SESSION_RE.match(session_id)
    if not match:
        with LOCK:
            return SESSION_ROUTES.get(session_id), body
    route, original = match.groups()
    payload["session_id"] = original
    return route, json.dumps(payload, separators=(",", ":")).encode()


def prefix_sessions(value, route: str):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "session_id" and isinstance(item, str) and not SESSION_RE.match(item):
                with LOCK:
                    SESSION_ROUTES.pop(item, None)
                    SESSION_ROUTES[item] = route
                    if len(SESSION_ROUTES) > MAX_TRACKED_SESSIONS:
                        SESSION_ROUTES.pop(next(iter(SESSION_ROUTES)))
                result[key] = f"{route}~{item}"
            else:
                result[key] = prefix_sessions(item, route)
        return result
    if isinstance(value, list):
        return [prefix_sessions(item, route) for item in value]
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "FSKRegistrationRouter/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"detail": "Invalid Content-Length"})
            return None
        if length < 0 or length > MAX_BODY:
            self.send_json(413, {"detail": "Request body is too large"})
            return None
        return self.rfile.read(length)

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/health":
            return self.forward("GET", b"", LEGACY_BACKEND, reserved=False)
        configured = configured_backends()
        backends = {}
        for route, backend in configured.items():
            healthy, active = backend_health(backend["endpoint"])
            with LOCK:
                cooldown = max(0, COOLDOWN_UNTIL.get(route, 0) - time.monotonic())
            available = backend["enabled"] and healthy and cooldown == 0
            backends[route] = {
                **backend,
                "healthy": available,
                "backend_healthy": healthy,
                "cooldown_seconds": round(cooldown),
                "active": active,
                "pending": PENDING.get(route, 0),
            }
        healthy_count = sum(1 for item in backends.values() if item["healthy"])
        self.send_json(200 if healthy_count else 503, {
            "status": "healthy" if healthy_count else "unhealthy",
            "router": "session-aware-weighted-round-robin-circuit-breaker",
            "backends": backends,
        })

    def do_POST(self) -> None:
        body = self.read_body()
        if body is None:
            return
        path = urlsplit(self.path).path
        if path == "/api/v1/registration/init":
            if not self.headers.get("X-API-Key"):
                return self.send_json(401, {"detail": "Missing API key"})
            deadline = time.monotonic() + QUEUE_TIMEOUT
            while time.monotonic() < deadline:
                choice = choose_backend()
                if choice is None:
                    time.sleep(0.5)
                    continue
                route, _ = choice
                if self.forward("POST", body, route, reserved=True, retry_safe_init=True):
                    return
            return self.send_json(503, {"detail": "All Registration IP slots are busy or cooling down; retry shortly"})
        route, stripped = session_route(body)
        return self.forward("POST", stripped, route or LEGACY_BACKEND, reserved=False)

    def forward(
        self, method: str, body: bytes, route: str, reserved: bool, retry_safe_init: bool = False
    ) -> bool:
        configured = configured_backends()
        # Existing unprefixed sessions continue to the former ip_hash backend.
        backend = configured.get(route)
        if backend is None and route == LEGACY_BACKEND:
            endpoint = "217.216.78.35:8002"
        elif backend is None:
            self.send_json(503, {"detail": "Registration backend is disabled or unavailable"})
            return True
        else:
            endpoint = backend["endpoint"]
        host, port_text = endpoint.rsplit(":", 1)
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_HEADERS}
        headers["Host"] = host
        headers["Connection"] = "close"
        headers["Accept-Encoding"] = "identity"
        headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPConnection(host, int(port_text), timeout=FORWARD_TIMEOUT)
        try:
            connection.request(method, self.path, body=body if method != "GET" else None, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            content_type = response.getheader("Content-Type", "")
            if "application/json" in content_type:
                try:
                    payload = json.loads(response_body)
                    if is_network_error(payload):
                        cool_down_backend(route)
                        if retry_safe_init and is_safe_init_retry(payload):
                            return False
                    response_body = json.dumps(prefix_sessions(payload, route), separators=(",", ":")).encode()
                except (ValueError, json.JSONDecodeError):
                    pass
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("X-Registration-Route", route)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)
            return True
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            self.send_json(502, {"detail": f"Registration backend unavailable: {type(exc).__name__}"})
            return True
        finally:
            connection.close()
            if reserved:
                release_backend(route)


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
