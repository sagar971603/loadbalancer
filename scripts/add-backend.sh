#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG="${CONFIG:-/etc/nginx/sites-available/automation_v2}"
UPSTREAM="${UPSTREAM:-newtool2_backend}"
BACKEND="${1:?Usage: sudo $0 <backend-ip-or-host> [port]}"
PORT="${2:-80}"

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ $BACKEND =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid backend: $BACKEND" >&2; exit 1; }
[[ $PORT =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo "Invalid port: $PORT" >&2; exit 1; }
[[ -f $CONFIG ]] || { echo "Missing $CONFIG" >&2; exit 1; }
grep -qE '^[[:space:]]*ip_hash;' "$CONFIG" || { echo "ip_hash is missing; stopping." >&2; exit 1; }
grep -qE "^[[:space:]]*upstream[[:space:]]+$UPSTREAM[[:space:]]*\\{" "$CONFIG" || { echo "Upstream $UPSTREAM not found." >&2; exit 1; }

LINE="    server $BACKEND:$PORT max_fails=3 fail_timeout=30s;"
grep -qF "$LINE" "$CONFIG" && { echo "$BACKEND:$PORT is already configured."; exit 0; }
curl -fsS --max-time 8 "http://$BACKEND:$PORT/health" >/dev/null || { echo "Backend health check failed; nothing changed." >&2; exit 1; }

BACKUP="$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cp -a "$CONFIG" "$BACKUP"

awk -v wanted="$UPSTREAM" -v line="$LINE" '
  $1 == "upstream" && $2 == wanted { inside=1 }
  inside && /^}/ { print line; inside=0; added=1 }
  { print }
  END { if (!added) exit 42 }
' "$CONFIG" >"$TMP" || { echo "Could not edit upstream; backup: $BACKUP" >&2; exit 1; }

cat "$TMP" >"$CONFIG"
diff -u "$BACKUP" "$CONFIG" || true

if ! nginx -t; then
  cp -a "$BACKUP" "$CONFIG"
  echo "nginx -t failed; original configuration restored." >&2
  exit 1
fi

if ! systemctl reload nginx || ! systemctl is-active --quiet nginx; then
  cp -a "$BACKUP" "$CONFIG"
  nginx -t && systemctl reload nginx
  echo "Reload failed; original restored." >&2
  exit 1
fi
echo "Added $BACKEND:$PORT. Backup: $BACKUP"
