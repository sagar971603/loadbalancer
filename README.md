# FSK India load balancer and Automation V2 deployment

This repository is the source-and-configuration backup and deployment kit for:

- The production NGINX load balancer.
- The Automation V2 Python application source.
- The registration API source.
- Automation V2 backend servers.
- Adding or removing a backend safely while preserving `ip_hash` sticky sessions.

It intentionally does **not** contain application data, `.env` values, passwords, API keys, TLS private keys, certificates, logs, PDFs, browser downloads, caches, or virtual environments. Example identities and passwords in the source backup were sanitized.

## What is included

```text
app/automation-v2/       Newtool application source and requirements
app/eportal-hybrid/      Registration application source and requirements
backup/
  load-balancer/nginx/   Current NGINX configuration snapshot
  backend/nginx/         Backend reverse-proxy configuration
  backend/systemd/       Automation V2 service definition
  backend/app-manifests/ Safe environment/dependency manifests only
scripts/
  backup-current.sh      Refresh configuration-only backups
  deploy-backend.sh      Build an Automation V2 backend
  setup-backend.sh       Guided first-time backend setup
  add-backend.sh         Safely add one backend to production
  restore-load-balancer.sh
                         Restore NGINX on a replacement load balancer
  check.sh               Syntax and safety checks
  deploy-dashboard.sh    Install/update the protected traffic dashboard
dashboard/               Dashboard UI, status service and safe route helper
router/                  Session-aware weighted Registration router and tests
docs/
  backend.md             Complete backend deployment guide
  load-balancer.md       Complete load-balancer guide and rollback
```

## Current traffic path

```text
Client HTTPS/WSS
        |
        v
newtool2.fskindia.com load balancer (NGINX + ip_hash)
        |
        v
Backend NGINX on port 80
        |
        v
FastAPI on 127.0.0.1:8009 (5 workers)
```

Registration passes through the session-aware router on `127.0.0.1:18002`. New `/init` jobs go to the least-used healthy outgoing-IP capacity; the route is encoded in the returned session ID so every OTP follow-up returns to the same one-worker Registration service. Backend port `8002` proxies to that service on `127.0.0.1:8010`.

`ip_hash` must remain enabled for Automation V2. Registration stickiness is handled by the Registration router; do not replace it with direct NGINX round-robin.

Each healthy outgoing Registration IP has five browser-session slots. A dual-IP backend is configured with `weight=2` and therefore receives ten slots; a single healthy-IP backend uses `weight=1` and receives five. Only one incoming address per physical machine belongs in the Registration upstream.

## Current six-machine capacity

| Backend | Incoming route | Outgoing IPs | Newtool slots | Registration slots |
|---|---|---|---:|---:|
| A | `217.217.249.145` | `.145`, `147.93.168.214` | 10 | 10 |
| B | `217.216.78.35` | `.35`, `147.93.168.221` | 10 | 0 (Registration route disabled) |
| C | `217.216.78.96` | `.96`, `147.93.171.116` | 10 | 5 (`.96` Registration disabled) |
| D | `147.93.171.241` | `.241` (`.254` disabled) | 5 | 5 |
| E | `147.93.169.153` | `.153`, `147.93.171.244` | 10 | 10 |
| F | `147.93.171.101` | `.101`, `147.93.171.245` | 10 | 10 |

Total capacity is 55 simultaneous Newtool sessions and 40 simultaneous Registration sessions. The load balancer contains one incoming route per physical machine; its weight equals the number of usable outgoing IPs so capacity is shared evenly per outgoing IP.

## Newtool session capacity and waiting

Newtool admits at most five logged-in sessions per outgoing public IP. A dual-IP backend therefore has ten active-session slots. The five dual-IP backends provide 50 slots, and Backend D contributes five more through `.241`, for 55 active Newtool sessions in total. Backend D's `.254` address remains disabled and is not counted.

When all slots on the backend selected by `ip_hash` are occupied (ten on a dual-IP backend or five on Backend D), a new login waits for a slot for up to 300 seconds. The wait is local to that selected backend; it is not a Redis/global queue and the job is not moved to another backend. The client must keep its WebSocket connected while waiting.

A successful login keeps the same outgoing IP and its slot for the whole session. Logout, WebSocket disconnect, five-minute WebSocket inactivity, login failure, expiry, or a service stop releases the slot. Linux process locks enforce the limit across all five Gunicorn workers and are released automatically if a worker exits. Slow portal calls run outside the WebSocket event loop so health checks and other connections remain responsive.

## Fastest safe workflows

### Production traffic dashboard

Open [https://newtool2.fskindia.com/server-control/](https://newtool2.fskindia.com/server-control/) with the separately stored dashboard login. It shows all six physical machines, incoming application routes, response time, live load-balancer connections, and per-outgoing-IP active/limit counters for both applications.

Route controls create a timestamped backup, refuse to disable the last backend, preserve `ip_hash`, validate NGINX, and restore the original automatically if validation or reload fails. Disabling an incoming route does not disable a dual-IP backend's outgoing proxy.

Deploy or update it on the load balancer with:

```bash
sudo ./scripts/deploy-dashboard.sh
```

### A. Add a healthy backend to production - one command

Run on the load balancer after confirming `http://NEW_IP/health` works:

```bash
sudo ./scripts/add-backend.sh NEW_IP 80
```

The script:

1. Confirms the backend health check.
2. Confirms `ip_hash` and the expected upstream exist.
3. Creates a timestamped backup.
4. Adds exactly one server line.
5. Runs `nginx -t`.
6. Reloads NGINX only after validation.
7. Restores the original automatically if validation or reload fails.

Example:

```bash
sudo ./scripts/add-backend.sh 147.93.171.254 80
```

### B. Deploy a new Automation V2 backend - two steps

1. Clone this repository on the new Ubuntu server:

   ```bash
   git clone https://github.com/sagar971603/loadbalancer.git
   cd loadbalancer
   ```

2. Run the guided setup:

   ```bash
   sudo ./scripts/setup-backend.sh
   ```

The setup asks privately for `CLIENT_KEY` and `CAPTCHA_API_KEY`; typed values are hidden. It installs both bundled applications, Python packages, Playwright, NGINX, and both system services, then performs health checks. It does not change the load balancer.

After deployment, test from the load balancer:

```bash
curl -fsS http://NEW_BACKEND_IP/health
```

Then add it with workflow A.

Add Registration after `http://NEW_BACKEND_IP:8002/health` succeeds. Follow the router-specific steps instead of adding every IP alias as a separate upstream server:

- [Registration router deployment and adding a backend](docs/registration-router.md)

To update an existing backend after a future Git change:

```bash
cd loadbalancer
git pull --ff-only
sudo ./scripts/deploy-backend.sh
```

The existing protected `.env` file is reused.

### C. Restore a replacement load balancer - three steps

1. Point DNS or prepare the replacement Ubuntu server, then clone this repository.
2. Restore or reissue TLS certificates securely outside Git.
3. Run:

   ```bash
   cd loadbalancer
   sudo CONFIRM_RESTORE=YES ./scripts/restore-load-balancer.sh
   ```

The restore refuses to continue if a referenced certificate is missing. It backs up `/etc/nginx`, validates the restored configuration, reloads only after `nginx -t` succeeds, and automatically restores the previous configuration on failure.

Local applications that run directly on the load-balancer host are not stored here and must be restored from their own application repositories.

## Refresh the Git backup

These commands copy configuration only. They do not restart or reload services.

On the load balancer:

```bash
sudo ./scripts/backup-current.sh load-balancer
./scripts/check.sh
git add backup
git commit -m "Backup load balancer configuration"
git push
```

On an Automation V2 backend:

```bash
sudo ./scripts/backup-current.sh backend
./scripts/check.sh
git add backup/backend
git commit -m "Backup backend configuration"
git push
```

Review `git diff --cached` before every commit. Never force-add ignored files.

## Prove traffic reaches a new backend

Send a unique request through production:

```bash
curl -fsS "https://newtool2.fskindia.com/health?lb_test=UNIQUE_TAG"
```

On the new backend:

```bash
grep 'UNIQUE_TAG' /var/log/nginx/access.log
```

For WebSockets, connect to:

```text
wss://newtool2.fskindia.com/ws
```

Then confirm an HTTP `101` entry on the backend:

```bash
grep 'GET /ws' /var/log/nginx/access.log | tail
```

## Safety rules

- Always inspect `git diff` before deploying or committing.
- Never commit `.env`, certificate files, passwords, private keys, logs, PDFs, or runtime data.
- Never remove or replace Newtool's `ip_hash` without redesigning application session storage.
- Never bypass the Registration router with direct round-robin; OTP calls must follow its encoded session route.
- Run `nginx -t` before every reload.
- Reload NGINX; do not restart it for a configuration-only change.
- Add a backend only after its direct health check succeeds from the load balancer.
- Remove a backend from the upstream before rebuilding or rebooting it.
- Rotate temporary SSH credentials after deployment.
- Keep real PAN/password credentials out of Git and deployment logs.

## Detailed guides

- [Backend deployment](docs/backend.md)
- [Load-balancer deployment and operations](docs/load-balancer.md)
- [Registration router and backend capacity](docs/registration-router.md)
