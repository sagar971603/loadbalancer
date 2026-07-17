#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$ROOT/dashboard"
NGINX_CONFIG=/etc/nginx/sites-available/automation_v2
STAMP=$(date +%Y%m%d-%H%M%S)
RESTORE=/root/restore-points/lb-dashboard-$STAMP

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ -f $NGINX_CONFIG ]] || { echo "Missing $NGINX_CONFIG" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required." >&2; exit 1; }

install -d -m 0700 "$RESTORE"
cp -a "$NGINX_CONFIG" "$RESTORE/"
[[ -e /etc/systemd/system/lb-dashboard.service ]] && cp -a /etc/systemd/system/lb-dashboard.service "$RESTORE/"
[[ -e /etc/nginx/lb-dashboard.htpasswd ]] && cp -a /etc/nginx/lb-dashboard.htpasswd "$RESTORE/"

id lb-dashboard >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin lb-dashboard
install -d -m 0700 /var/backups/lb-dashboard
install -d -o root -g lb-dashboard -m 0750 /opt/lb-dashboard
install -o root -g lb-dashboard -m 0640 "$SOURCE/server.py" "$SOURCE/index.html" "$SOURCE/styles.css" "$SOURCE/app.js" /opt/lb-dashboard/
install -o root -g root -m 0755 "$SOURCE/lb-dashboard-control" /usr/local/sbin/lb-dashboard-control
install -o root -g root -m 0644 "$SOURCE/lb-dashboard.service" /etc/systemd/system/lb-dashboard.service
install -o root -g root -m 0440 "$SOURCE/lb-dashboard.sudoers" /etc/sudoers.d/lb-dashboard
visudo -cf /etc/sudoers.d/lb-dashboard >/dev/null

if [[ ! -f /etc/nginx/lb-dashboard.htpasswd ]]; then
  PASSWORD=${DASHBOARD_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 22)}
  HASH=$(openssl passwd -6 "$PASSWORD")
  printf 'admin:%s\n' "$HASH" > /etc/nginx/lb-dashboard.htpasswd
  chmod 0640 /etc/nginx/lb-dashboard.htpasswd
  chown root:www-data /etc/nginx/lb-dashboard.htpasswd
  printf 'DASHBOARD_USERNAME=admin\nDASHBOARD_PASSWORD=%s\n' "$PASSWORD"
fi

if ! grep -q 'location \^~ /server-control/' "$NGINX_CONFIG"; then
  TMP=$(mktemp)
  trap 'rm -f "$TMP"' EXIT
  python3 - "$NGINX_CONFIG" "$SOURCE/nginx-location.conf" > "$TMP" <<'PY'
import sys
from pathlib import Path

config = Path(sys.argv[1])
location = Path(sys.argv[2]).read_text().rstrip()
text = config.read_text()
needle = "    location / {"
if text.count(needle) != 1:
    raise SystemExit("Expected exactly one main location block")
print(text.replace(needle, "\n".join("    " + line if line else "" for line in location.splitlines()) + "\n\n" + needle, 1), end="")
PY
  install -m 0644 "$TMP" "$NGINX_CONFIG"
fi

systemctl daemon-reload
systemctl enable --now lb-dashboard
curl -fsS --retry 5 --retry-delay 1 http://127.0.0.1:9090/health >/dev/null
if ! nginx -t; then
  cp -a "$RESTORE/automation_v2" "$NGINX_CONFIG"
  echo "NGINX validation failed; original configuration restored." >&2
  exit 1
fi
systemctl reload nginx
echo "Dashboard deployed. Restore point: $RESTORE"
