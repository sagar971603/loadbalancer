# Load-balancer deployment and operations

This repository stores NGINX configuration only. It excludes TLS certificates/private keys, logs, databases, runtime data, and applications that happen to run locally on the load-balancer host.

## Current Automation V2 design

The production site is `newtool2.fskindia.com`. Its upstream is `newtool2_backend` in:

```text
/etc/nginx/sites-available/automation_v2
```

The upstream uses:

```nginx
ip_hash;
```

Do not change the balancing method. Automation V2 stores sessions and WebSocket connections in process memory.

## Back up the running load balancer

The backup command copies only selected NGINX text configuration and the enabled-site names. It does not read certificate contents, private keys, logs, or application data, and it does not reload NGINX.

```bash
git clone https://github.com/sagar971603/loadbalancer.git
cd loadbalancer
sudo ./scripts/backup-current.sh load-balancer
./scripts/check.sh
git diff -- backup/load-balancer
```

After review:

```bash
git add backup/load-balancer
git commit -m "Backup load balancer configuration $(date +%F)"
git push
```

## Add a backend

The backend must respond successfully from the load-balancer server:

```bash
curl -fsS --max-time 8 http://NEW_BACKEND_IP:80/health
```

Then run:

```bash
sudo ./scripts/add-backend.sh NEW_BACKEND_IP 80
```

The script is idempotent: if the exact backend already exists, it exits without changing or reloading anything.

It preserves all existing upstream entries and `ip_hash`, creates a timestamped configuration backup, displays the one-line diff, validates NGINX, reloads, and checks that NGINX remains active.

## Verify the new backend receives traffic

Because of `ip_hash`, one client remains on one backend. Use a unique marker:

```bash
curl -fsS "https://newtool2.fskindia.com/health?lb_test=NEW_BACKEND_TEST_$(date +%s)"
```

Copy the exact tag and search on the new backend:

```bash
grep 'NEW_BACKEND_TEST_' /var/log/nginx/access.log | tail
```

A production WebSocket request should appear as status `101`:

```bash
grep 'GET /ws' /var/log/nginx/access.log | tail
```

## Remove a backend

There is intentionally no automatic remove script: removal is rarer and selecting the wrong server is high impact.

1. Back up the site:

   ```bash
   sudo cp -a /etc/nginx/sites-available/automation_v2 \
     /etc/nginx/sites-available/automation_v2.bak-$(date +%Y%m%d-%H%M%S)
   ```

2. Edit only the matching `server IP:PORT ...;` line:

   ```bash
   sudo nano /etc/nginx/sites-available/automation_v2
   ```

3. Review, validate, and reload:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   systemctl is-active nginx
   ```

Never remove `ip_hash` or another backend accidentally.

## Restore a replacement load balancer

### 1. Prepare Ubuntu and clone the repository

```bash
git clone https://github.com/sagar971603/loadbalancer.git
cd loadbalancer
```

### 2. Restore TLS certificates outside Git

The NGINX backup references files under `/etc/letsencrypt`. Certificates and private keys must never be committed.

Restore `/etc/letsencrypt` from an encrypted secret backup, or reissue the certificates after pointing DNS to the replacement server. Confirm every path referenced in `backup/load-balancer/nginx/sites-available/` exists.

Applications proxied to `127.0.0.1` must also be restored from their own repositories before enabling their NGINX sites.

### 3. Apply the NGINX configuration

```bash
sudo CONFIRM_RESTORE=YES ./scripts/restore-load-balancer.sh
```

The explicit confirmation prevents accidental execution on a live server. The script:

- Installs NGINX.
- Stops before editing if any referenced TLS file is missing.
- Copies `/etc/nginx` to a timestamped backup.
- Restores only configuration text from Git.
- Recreates the recorded enabled-site symlinks.
- Runs `nginx -t`.
- Reloads only after validation.
- Restores the previous NGINX directory if validation or reload fails.

## Validate a restored load balancer

```bash
nginx -t
systemctl is-enabled nginx
systemctl is-active nginx
curl -fsS https://newtool2.fskindia.com/health
journalctl -u nginx -n 50 --no-pager
```

Test WebSocket upgrade with a WebSocket client:

```text
wss://newtool2.fskindia.com/ws
```

Expected first message contains `"status":"connected"`.

## Emergency rollback

Every add operation reports its backup filename. Restore it:

```bash
sudo cp -a /etc/nginx/sites-available/automation_v2.bak-TIMESTAMP \
  /etc/nginx/sites-available/automation_v2
sudo nginx -t
sudo systemctl reload nginx
systemctl is-active nginx
```

For a full Git restore failure, `restore-load-balancer.sh` automatically restores the `/etc/nginx.pre-git-restore-TIMESTAMP` directory.

## Monitoring

```bash
tail -f /var/log/nginx/automation_v2.access.log
tail -f /var/log/nginx/automation_v2.error.log
journalctl -u nginx -f
```

Backend-specific traffic is visible in `/var/log/nginx/access.log` on each backend.
