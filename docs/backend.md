# Automation V2 backend deployment

This guide creates a new Ubuntu 24.04 backend without changing any existing production server. Add it to the load balancer only after every direct test passes.

## Prerequisites

- A clean Ubuntu 24.04 x86_64 server.
- Root or passwordless sudo access.
- Network access to GitHub and Ubuntu package repositories.
- The `CLIENT_KEY` and `CAPTCHA_API_KEY` values supplied privately by the application owner.

The newtool and registration source are bundled in `app/automation-v2` and `app/eportal-hybrid`. Application data and secrets are deliberately excluded. Do not store the real environment file in Git.

## Recommended two-step deployment

### 1. Clone this repository

```bash
git clone https://github.com/sagar971603/loadbalancer.git
cd loadbalancer
```

### 2. Run the guided setup

```bash
sudo ./scripts/setup-backend.sh
```

Enter `CLIENT_KEY` and `CAPTCHA_API_KEY` when asked. Input is hidden, written to a protected `.env` file, and never stored in this Git checkout. The script installs both applications, Playwright, the five-worker Newtool service, the one-worker registration service, and NGINX; it finishes with both health checks. It does not add the server to the production load balancer.

## Update an existing backend from Git

First remove the backend from production traffic. Then run on that backend:

```bash
cd loadbalancer
git pull --ff-only
sudo ./scripts/deploy-backend.sh
```

The bundled source is refreshed while the existing protected `.env` file is retained. The service is restarted by the deployment script and checked locally. Add the backend to the load balancer again only after validation.

## Manual validation

```bash
systemctl is-enabled automation-v2 registration nginx
systemctl is-active automation-v2 registration nginx
nginx -t
curl -fsS http://127.0.0.1:8009/health
curl -fsS http://127.0.0.1/health
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8002/health
ss -lntp | grep -E ':(80|8002|8009|8010) '
journalctl -u automation-v2 -n 50 --no-pager
journalctl -u registration -n 50 --no-pager
```

Expected:

- `automation-v2`, `registration`, and `nginx` are enabled and active.
- NGINX listens publicly on ports 80 and 8002.
- The applications listen locally on `127.0.0.1:8009` and `127.0.0.1:8010`.
- Both health checks return `healthy`.
- No repeated exceptions appear in the service journal.
- Newtool has one Gunicorn master and five worker processes. Slow portal calls are run outside the WebSocket event loop so they do not block health checks or unrelated connections.
- Registration remains one worker because its browser and OTP session objects are process-local.
- Newtool closes WebSockets after five minutes without a client message and removes their login, forgot-password, and PAN-link session state. Override `WEBSOCKET_IDLE_TIMEOUT_SECONDS` only when a different idle window is deliberately required.
- Newtool allows five active login sessions per outgoing public IP. Extra logins wait up to five minutes on the backend selected by `ip_hash`.

Test Playwright separately:

```bash
cd /root/tools/automation-v2
venv/bin/python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.firefox.launch(headless=True); print(b.version); b.close(); p.stop()"
```

## Security check before real-user testing

```bash
grep -RIn --include='*.py' \
  -E 'print\(.*password|logger\..*password|print\(.*pan' \
  /root/tools/automation-v2
```

The backed-up copy has its known credential-printing statement removed and its sample identities sanitized. The command above is an additional check before real-user testing. Use real credentials only through the production HTTPS/WSS load-balancer endpoint.

## Add the server to production

First, test from the load balancer:

```bash
curl -fsS --max-time 8 http://NEW_BACKEND_IP/health
```

Then, from this infrastructure repository on the load balancer:

```bash
sudo ./scripts/add-backend.sh NEW_BACKEND_IP 80
```

Add registration only after its direct health check succeeds:

```bash
curl -fsS --max-time 8 http://NEW_BACKEND_IP:8002/health
sudo CONFIG=/etc/nginx/sites-available/regpan4.fskindia.com \
  UPSTREAM=regpan4_backend ./scripts/add-backend.sh NEW_BACKEND_IP 8002
```

## Dual public-IP outgoing rotation

This is needed only on a backend that owns two public IPv4 addresses. Each job chooses one local proxy and keeps it for the full browser/session job.

1. Install `tinyproxy` and copy `backup/backend/systemd/tinyproxy-egress@.service` to `/etc/systemd/system/`.
2. Copy `backup/backend/tinyproxy/egress.example.conf` twice to `/etc/tinyproxy/egress-PRIMARY.conf` and `egress-SECONDARY.conf`. Replace `PUBLIC_IP`, `PROXY_PORT`, and `INSTANCE`; use ports `18888` and `18889`.
3. Enable both proxy instances and confirm their outgoing addresses:

   ```bash
   systemctl daemon-reload
   systemctl enable --now tinyproxy-egress@PRIMARY tinyproxy-egress@SECONDARY
   curl --proxy http://127.0.0.1:18888 https://api.ipify.org
   curl --proxy http://127.0.0.1:18889 https://api.ipify.org
   ```

4. Copy `backup/backend/systemd/egress-proxy.conf` as a systemd drop-in for each application service, drain the backend, then restart only those drained services.

The supplied Newtool drop-in sets:

```text
EGRESS_SLOTS_PER_IP=5
EGRESS_QUEUE_TIMEOUT_SECONDS=300
```

This provides ten active Newtool login sessions on a dual-IP backend. Each successful session holds one slot and keeps the same outgoing IP until logout, WebSocket disconnect, five minutes of WebSocket inactivity, session expiry, or service shutdown. Failed logins release their slot immediately.

If all ten slots are busy, another login waits on that same backend for up to 300 seconds. The queue is intentionally local and in-memory while the client WebSocket stays connected; it does not serialize browser/session objects into Redis and does not transfer a queued job to another backend. Linux file locks make the five-per-IP limit process-safe across the five Newtool workers and automatically free slots after a worker crash.

After restart, confirm the settings without exposing secrets:

```bash
curl -fsS http://127.0.0.1:8000/health
systemctl show automation-v2 -p Environment --no-pager
```

The health response must contain `"egress_slots_per_ip":5` and `"egress_ip_count":2` on a dual-IP backend.

Never add workers to registration: its Playwright browser objects and OTP session state are process-local.

## Reboot validation

If Ubuntu reports a pending kernel update, reboot before adding the backend to production:

```bash
reboot
```

After reconnecting:

```bash
uname -r
systemctl is-active automation-v2 nginx
curl -fsS http://127.0.0.1/health
journalctl -u automation-v2 -b -n 30 --no-pager
```

## Backend rollback

Do not troubleshoot a failing backend while it receives production traffic. Remove its upstream line first.

Configuration backups are timestamped beside the installed files:

```text
/etc/nginx/sites-available/automation-v2.bak-<timestamp>
/etc/nginx/sites-available/registration.bak-<timestamp>
/etc/systemd/system/automation-v2.service.bak-<timestamp>
/etc/systemd/system/registration.service.bak-<timestamp>
```

Restore the required file, then validate:

```bash
nginx -t
systemctl daemon-reload
systemctl restart automation-v2
systemctl restart registration
systemctl reload nginx
curl -fsS http://127.0.0.1/health
```
