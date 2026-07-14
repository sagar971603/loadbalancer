#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
APP_DIR="${APP_DIR:-/root/tools/automation-v2}"
APP_REPO_URL="${APP_REPO_URL:-}"
ENV_FILE="${ENV_FILE:-}"
BUNDLED_APP="$ROOT/app/automation-v2"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ $APP_DIR == /root/tools/automation-v2 ]] || { echo "APP_DIR must remain /root/tools/automation-v2 because the service uses that path." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git openssh-client nginx python3 python3-venv python3-pip curl ca-certificates
install -d -m 0755 "$(dirname "$APP_DIR")"

if [[ -n $APP_REPO_URL ]]; then
  if [[ ! -d $APP_DIR/.git ]]; then
    [[ ! -e $APP_DIR ]] || { echo "$APP_DIR exists but is not a Git checkout." >&2; exit 1; }
    git clone "$APP_REPO_URL" "$APP_DIR"
  else
    git -C "$APP_DIR" pull --ff-only
  fi
else
  [[ -f $BUNDLED_APP/api/main.py ]] || { echo "Bundled application source is missing." >&2; exit 1; }
  install -d -m 0755 "$APP_DIR"
  cp -a "$BUNDLED_APP/." "$APP_DIR/"
fi

[[ -f $APP_DIR/requirements.txt && -f $APP_DIR/api/requirements.txt ]] || { echo "Application requirement files are missing." >&2; exit 1; }
if [[ -n $ENV_FILE ]]; then
  [[ -f $ENV_FILE ]] || { echo "ENV_FILE does not exist." >&2; exit 1; }
  install -D -m 0600 "$ENV_FILE" "$APP_DIR/api/.env"
fi
[[ -f $APP_DIR/api/.env ]] || { echo "Create $APP_DIR/api/.env from backup/backend/app-manifests/.env.example, then rerun." >&2; exit 1; }
chmod 0600 "$APP_DIR/api/.env"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt" -r "$APP_DIR/api/requirements.txt" playwright selenium fake-useragent
PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright "$APP_DIR/venv/bin/python" -m playwright install --with-deps

for target in /etc/nginx/sites-available/automation-v2 /etc/systemd/system/automation-v2.service; do
  [[ -e $target ]] && cp -a "$target" "$target.bak-$(date +%Y%m%d-%H%M%S)"
done
install -m 0644 "$ROOT/backup/backend/nginx/automation-v2" /etc/nginx/sites-available/automation-v2
install -m 0644 "$ROOT/backup/backend/systemd/automation-v2.service" /etc/systemd/system/automation-v2.service
rm -f /etc/nginx/sites-enabled/default
ln -sfn /etc/nginx/sites-available/automation-v2 /etc/nginx/sites-enabled/automation-v2

systemctl daemon-reload
nginx -t
systemctl enable --now nginx
systemctl reload nginx
systemctl enable automation-v2
systemctl restart automation-v2
curl -fsS --retry 10 --retry-delay 2 --retry-connrefused http://127.0.0.1/health
echo "Backend deployed. Add it to the load balancer only after testing from the load-balancer host."
