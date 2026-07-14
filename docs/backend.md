# Automation V2 backend deployment

This guide creates a new Ubuntu 24.04 backend without changing any existing production server. Add it to the load balancer only after every direct test passes.

## Prerequisites

- A clean Ubuntu 24.04 x86_64 server.
- Root or passwordless sudo access.
- Network access to GitHub and Ubuntu package repositories.
- The private Automation V2 application repository URL.
- A secure environment file containing `CLIENT_KEY` and `CAPTCHA_API_KEY`.
- This infrastructure repository.

Do not store the real environment file in Git.

## Recommended three-step deployment

### 1. Clone this repository

```bash
git clone https://github.com/sagar971603/loadbalancer.git
cd loadbalancer
```

### 2. Place the environment file securely

Create it from the safe key-only example:

```bash
cp backup/backend/app-manifests/.env.example /root/automation-v2.env
chmod 600 /root/automation-v2.env
nano /root/automation-v2.env
```

Fill the values directly on the server. Do not paste them into Git, tickets, or deployment logs.

### 3. Run the deployment

```bash
sudo APP_REPO_URL="https://github.com/OWNER/AUTOMATION-V2.git" \
     ENV_FILE="/root/automation-v2.env" \
     ./scripts/deploy-backend.sh
```

The script installs base packages, clones the application, recreates the virtual environment, installs both requirements files, installs Playwright browsers and Linux libraries, installs systemd/NGINX configuration, validates NGINX, starts both services, and checks `/health`.

For a private application repository, use an SSH deploy key or a short-lived GitHub token configured outside this repository.

## Existing application directory

If application code already exists at `/root/tools/automation-v2`:

```bash
sudo ENV_FILE="/root/automation-v2.env" ./scripts/deploy-backend.sh
```

To use a different directory:

```bash
sudo APP_DIR="/opt/automation-v2" \
     APP_REPO_URL="https://github.com/OWNER/AUTOMATION-V2.git" \
     ENV_FILE="/root/automation-v2.env" \
     ./scripts/deploy-backend.sh
```

If `APP_DIR` changes, update the backed-up systemd file before deployment because its paths currently use `/root/tools/automation-v2`.

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

Remove or redact credential logging, including any `print(pan, password)` statement. Use real credentials only through the production HTTPS/WSS load-balancer endpoint.

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

## Updating an existing backend

Remove it from the load-balancer upstream first, validate and reload NGINX, then update the backend application from its own repository:

```bash
cd /root/tools/automation-v2
git pull --ff-only
venv/bin/python -m pip install -r requirements.txt -r api/requirements.txt
venv/bin/python -m playwright install --with-deps
systemctl restart automation-v2
curl -fsS http://127.0.0.1/health
```

Add it back only after the direct health and browser tests succeed.

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
