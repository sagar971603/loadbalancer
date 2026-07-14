#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$ROOT/backup/load-balancer/nginx"
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ ${CONFIRM_RESTORE:-} == YES ]] || { echo "Refusing to change NGINX. Run with CONFIRM_RESTORE=YES after reading docs/load-balancer.md." >&2; exit 1; }

missing=0
while read -r certificate; do
  [[ -z $certificate || -f $certificate ]] || { echo "Missing TLS file: $certificate" >&2; missing=1; }
done < <(grep -RhE '^[[:space:]]*(ssl_certificate|ssl_certificate_key|ssl_dhparam)[[:space:]]+' "$SOURCE/sites-available" | awk '{gsub(/;/, "", $2); print $2}' | sort -u)
(( missing == 0 )) || { echo "Restore/reissue certificates outside Git before continuing. Nothing changed." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx curl ca-certificates

BACKUP="/etc/nginx.pre-git-restore-$(date +%Y%m%d-%H%M%S)"
FAILED="/etc/nginx.failed-git-restore-$(date +%Y%m%d-%H%M%S)"
cp -a /etc/nginx "$BACKUP"

rollback() {
  mv /etc/nginx "$FAILED"
  cp -a "$BACKUP" /etc/nginx
  nginx -t && systemctl reload nginx
  echo "Restore failed; previous NGINX configuration restored from $BACKUP. Failed candidate kept at $FAILED" >&2
}

for name in nginx.conf mime.types proxy_params fastcgi_params fastcgi.conf scgi_params uwsgi_params; do
  [[ -f $SOURCE/$name ]] && install -m 0644 "$SOURCE/$name" "/etc/nginx/$name"
done
for dir in conf.d snippets sites-available; do
  mkdir -p "/etc/nginx/$dir"
  cp -a "$SOURCE/$dir/." "/etc/nginx/$dir/"
done
rm -f /etc/nginx/sites-enabled/*
while IFS= read -r site; do
  [[ -z $site ]] || ln -s "/etc/nginx/sites-available/$site" "/etc/nginx/sites-enabled/$site"
done <"$SOURCE/sites-enabled.txt"

nginx -t || { rollback; exit 1; }
systemctl enable nginx
if systemctl is-active --quiet nginx; then
  systemctl reload nginx || { rollback; exit 1; }
else
  systemctl start nginx || { rollback; exit 1; }
fi
systemctl is-active --quiet nginx || { rollback; exit 1; }
echo "NGINX configuration restored. Previous copy: $BACKUP"
