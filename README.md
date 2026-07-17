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
FastAPI on 127.0.0.1:8009
```

Registration uses the same sticky-session design through backend port `8002`, which proxies to the one-worker registration service on `127.0.0.1:8010`.

`ip_hash` must remain enabled. Automation V2 keeps sessions and WebSocket state in memory, so a client must remain on the same backend.

## Fastest safe workflows

### Production traffic dashboard

Open [https://newtool2.fskindia.com/server-control/](https://newtool2.fskindia.com/server-control/) with the separately stored dashboard login. It shows all 14 application routes, response time, live load-balancer connections, sampled application sessions, WebSockets, errors, and enabled/disabled state.

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

Add registration after `http://NEW_BACKEND_IP:8002/health` succeeds:

```bash
sudo CONFIG=/etc/nginx/sites-available/regpan4.fskindia.com \
  UPSTREAM=regpan4_backend ./scripts/add-backend.sh NEW_IP 8002
```

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
- Never remove or replace `ip_hash` without redesigning application session storage.
- Run `nginx -t` before every reload.
- Reload NGINX; do not restart it for a configuration-only change.
- Add a backend only after its direct health check succeeds from the load balancer.
- Remove a backend from the upstream before rebuilding or rebooting it.
- Rotate temporary SSH credentials after deployment.
- Keep real PAN/password credentials out of Git and deployment logs.

## Detailed guides

- [Backend deployment](docs/backend.md)
- [Load-balancer deployment and operations](docs/load-balancer.md)
