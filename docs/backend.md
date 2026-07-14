# Automation V2 backend deployment

This guide creates a new Ubuntu 24.04 backend without changing any existing production server. Add it to the load balancer only after every direct test passes.

## Prerequisites

- A clean Ubuntu 24.04 x86_64 server.
- Root or passwordless sudo access.
- Network access to GitHub and Ubuntu package repositories.
- The `CLIENT_KEY` and `CAPTCHA_API_KEY` values supplied privately by the application owner.

The Python source is bundled in `app/automation-v2`. Application data and secrets are deliberately excluded. Do not store the real environment file in Git.

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

Enter `CLIENT_KEY` and `CAPTCHA_API_KEY` when asked. Input is hidden, written to a protected `.env` file, and never stored in this Git checkout. The script installs the bundled application, system packages, Python packages, Playwright browsers, systemd, and NGINX; it validates NGINX and finishes with a health check. It does not add the server to the production load balancer.

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
systemctl is-enabled automation-v2 nginx
systemctl is-active automation-v2 nginx
nginx -t
curl -fsS http://127.0.0.1:8009/health
curl -fsS http://127.0.0.1/health
ss -lntp | grep -E ':(80|8009) '
journalctl -u automation-v2 -n 50 --no-pager
```

Expected:

- `automation-v2` and `nginx` are enabled and active.
- NGINX listens publicly on port 80.
- Uvicorn listens only on `127.0.0.1:8009`.
- Both health checks return `healthy`.
- No repeated exceptions appear in the service journal.

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
/etc/systemd/system/automation-v2.service.bak-<timestamp>
```

Restore the required file, then validate:

```bash
nginx -t
systemctl daemon-reload
systemctl restart automation-v2
systemctl reload nginx
curl -fsS http://127.0.0.1/health
```
