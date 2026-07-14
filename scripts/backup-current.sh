#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:?Usage: sudo $0 load-balancer|backend}"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

copy_file() {
  local source=$1 target=$2
  [[ -f $source ]] || return 0
  install -D -m 0644 "$source" "$target"
}

case "$MODE" in
  load-balancer)
    DEST="$ROOT/backup/load-balancer/nginx"
    mkdir -p "$DEST" "$DEST/conf.d" "$DEST/snippets" "$DEST/sites-available"
    find "$DEST/conf.d" "$DEST/snippets" "$DEST/sites-available" -maxdepth 1 -type f -delete
    for name in nginx.conf mime.types proxy_params fastcgi_params fastcgi.conf scgi_params uwsgi_params; do
      copy_file "/etc/nginx/$name" "$DEST/$name"
    done
    find /etc/nginx/conf.d /etc/nginx/snippets /etc/nginx/sites-available \
      -maxdepth 1 -type f ! -name '*.bak-*' -print0 2>/dev/null |
      while IFS= read -r -d '' file; do
        subdir=$(basename "$(dirname "$file")")
        copy_file "$file" "$DEST/$subdir/$(basename "$file")"
      done
    find /etc/nginx/sites-enabled -maxdepth 1 -type l -printf '%f\n' | sort >"$DEST/sites-enabled.txt"
    ;;
  backend)
    copy_file /etc/nginx/sites-available/automation-v2 "$ROOT/backup/backend/nginx/automation-v2"
    copy_file /etc/systemd/system/automation-v2.service "$ROOT/backup/backend/systemd/automation-v2.service"
    mkdir -p "$ROOT/backup/backend/app-manifests"
    for file in /root/tools/automation-v2/requirements.txt /root/tools/automation-v2/api/requirements.txt; do
      [[ -f $file ]] && copy_file "$file" "$ROOT/backup/backend/app-manifests/$(basename "$(dirname "$file")")-$(basename "$file")"
    done
    if [[ -f /root/tools/automation-v2/api/.env ]]; then
      sed -E 's/[[:space:]]*=.*$/=/' /root/tools/automation-v2/api/.env >"$ROOT/backup/backend/app-manifests/.env.example"
      chmod 0644 "$ROOT/backup/backend/app-manifests/.env.example"
    fi
    ;;
  *) echo "Mode must be load-balancer or backend." >&2; exit 1 ;;
esac

echo "Configuration-only backup updated under $ROOT/backup/$MODE"
